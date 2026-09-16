#!/usr/bin/env python3
"""Live, local federated-learning simulator using the real root Client/Server path.

The simulator is intentionally small enough to run without downloading a dataset.
It generates a labeled synthetic dataset in memory, partitions it across simulated
clients, performs real local PyTorch optimization through ``federated.client``,
and aggregates model deltas through ``federated.server``.

Raw client samples never enter the Server object. Each Client owns a DataLoader
backed by only its assigned dataset indices; the server receives model-update
results and metadata, matching the data-flow boundary of the root runtime.
"""

from __future__ import annotations

import argparse
import io
import math
import random
import sys
import time
from dataclasses import dataclass
from typing import Callable, TextIO

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

from data.partitioner import partition_dirichlet, partition_iid
from federated.client import Client
from federated.dp_accountant import MomentsAccountant
from federated.server import Server


class SyntheticFederatedDataset(Dataset):
    """Small deterministic classification dataset with a torchvision-like API."""

    def __init__(
        self,
        *,
        samples: int,
        features: int,
        classes: int,
        seed: int,
    ) -> None:
        if samples < classes:
            raise ValueError("samples must be >= classes")
        if features < 2:
            raise ValueError("features must be >= 2")
        if classes < 2:
            raise ValueError("classes must be >= 2")

        generator = torch.Generator().manual_seed(seed)
        labels = torch.arange(samples, dtype=torch.long) % classes
        permutation = torch.randperm(samples, generator=generator)
        labels = labels[permutation]

        prototypes = torch.randn(
            classes,
            features,
            generator=generator,
        ) * 2.5
        noise = torch.randn(samples, features, generator=generator) * 0.85
        self.features = prototypes[labels] + noise
        self.targets = labels

    def __len__(self) -> int:
        return int(self.targets.numel())

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.features[index], self.targets[index]


class TinyClassifier(nn.Module):
    """Fast model used only by the live simulator."""

    def __init__(self, features: int, classes: int) -> None:
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(features, 24),
            nn.ReLU(),
            nn.Linear(24, classes),
        )

    def forward(self, inputs: torch.Tensor) -> torch.Tensor:
        return self.network(inputs)


@dataclass(frozen=True)
class SimulationConfig:
    clients: int = 6
    rounds: int = 5
    samples_per_client: int = 80
    features: int = 8
    classes: int = 4
    sample_rate: float = 0.67
    partition: str = "dirichlet"
    alpha: float = 0.3
    algorithm: str = "fedavg"
    local_epochs: int = 1
    batch_size: int = 32
    learning_rate: float = 0.08
    server_lr: float = 1.0
    fedprox_mu: float = 0.01
    dp_enabled: bool = False
    noise_multiplier: float = 0.8
    clip_norm: float = 1.0
    seed: int = 42
    device: str = "auto"
    delay: float = 0.10
    clear_screen: bool = True


def resolve_device(name: str) -> torch.device:
    if name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(name)


def validate_config(config: SimulationConfig) -> None:
    if config.clients < 2:
        raise ValueError("clients must be >= 2")
    if config.rounds < 1:
        raise ValueError("rounds must be >= 1")
    if config.samples_per_client < 4:
        raise ValueError("samples-per-client must be >= 4")
    if not 0.0 < config.sample_rate <= 1.0:
        raise ValueError("sample-rate must lie in (0, 1]")
    if config.partition not in {"iid", "dirichlet"}:
        raise ValueError("partition must be 'iid' or 'dirichlet'")
    if config.alpha <= 0.0:
        raise ValueError("alpha must be > 0")
    if config.algorithm not in {"fedavg", "fedprox", "scaffold"}:
        raise ValueError("algorithm must be fedavg, fedprox, or scaffold")
    if config.algorithm == "scaffold" and config.dp_enabled:
        raise ValueError("DP-enabled SCAFFOLD is unsupported by the root runtime")
    if config.dp_enabled and config.noise_multiplier <= 0.0:
        raise ValueError("noise-multiplier must be > 0 when DP is enabled")
    if config.clip_norm <= 0.0:
        raise ValueError("clip-norm must be > 0")
    if config.delay < 0.0:
        raise ValueError("delay must be >= 0")


def client_config(config: SimulationConfig) -> dict:
    """Build the subset of root configuration consumed by Client.train()."""
    return {
        "federated": {
            "batch_size": config.batch_size,
            "local_epochs": config.local_epochs,
        },
        "optimizer": {
            "lr": config.learning_rate,
            "momentum": 0.0,
            "weight_decay": 0.0,
            "grad_clip_norm": None,
        },
        "dp": {
            "enabled": config.dp_enabled,
            "update_clip_norm": config.clip_norm,
        },
        "algorithm": {"mu": config.fedprox_mu},
    }


def evaluate(
    model: nn.Module,
    loader: DataLoader,
    device: torch.device,
) -> tuple[float, float]:
    model.eval()
    total_loss = 0.0
    correct = 0
    total = 0
    loss_fn = nn.CrossEntropyLoss(reduction="sum")
    with torch.no_grad():
        for inputs, labels in loader:
            inputs = inputs.to(device)
            labels = labels.to(device)
            outputs = model(inputs)
            total_loss += float(loss_fn(outputs, labels).item())
            correct += int((outputs.argmax(dim=1) == labels).sum().item())
            total += int(labels.numel())
    return total_loss / max(1, total), correct / max(1, total)


def label_summary(
    dataset: SyntheticFederatedDataset,
    indices: np.ndarray,
) -> str:
    labels, counts = np.unique(dataset.targets[indices].numpy(), return_counts=True)
    return " ".join(
        f"c{int(label)}={int(count)}"
        for label, count in zip(labels, counts, strict=True)
    )


def maybe_clear(stream: TextIO, enabled: bool) -> None:
    if enabled and stream.isatty():
        stream.write("\033[2J\033[H")


def emit(stream: TextIO, message: str = "") -> None:
    stream.write(message + "\n")
    stream.flush()


def render_header(
    stream: TextIO,
    *,
    config: SimulationConfig,
    device: torch.device,
) -> None:
    emit(stream, "Federated Learning Live Simulator")
    emit(stream, "=" * 78)
    emit(stream, "DATA SOURCE     synthetic://local/generated (in-memory tensors)")
    emit(
        stream,
        "DATA OWNERSHIP  partitioner assigns sample indices; each client reads only "
        "its Subset",
    )
    emit(
        stream,
        "ML TRAINING     Client.train() -> local PyTorch DataLoader -> local optimizer",
    )
    emit(
        stream,
        "TO SERVER       model delta + sample count + training metadata (not raw data)",
    )
    emit(
        stream,
        "AGGREGATION     Server.aggregate() -> global model update",
    )
    emit(
        stream,
        f"RUN             {config.algorithm} | {config.partition} | "
        f"clients={config.clients} | rounds={config.rounds} | device={device}",
    )
    emit(
        stream,
        f"PRIVACY         {'client-level clipping + central Gaussian noise' if config.dp_enabled else 'disabled'}",
    )
    emit(stream, "=" * 78)


def run_simulation(
    config: SimulationConfig,
    *,
    stream: TextIO = sys.stdout,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """Run real local training and server aggregation with live event output."""
    validate_config(config)
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)

    device = resolve_device(config.device)
    total_samples = config.clients * config.samples_per_client
    dataset = SyntheticFederatedDataset(
        samples=total_samples,
        features=config.features,
        classes=config.classes,
        seed=config.seed,
    )

    if config.partition == "iid":
        partitions = partition_iid(
            dataset,
            config.clients,
            seed=config.seed,
            min_partition_size=2,
        )
    else:
        partitions = partition_dirichlet(
            dataset,
            config.clients,
            alpha=config.alpha,
            seed=config.seed,
            min_partition_size=2,
        )

    client_cfg = client_config(config)
    clients = [
        Client(
            client_id,
            dataset,
            partitions[client_id],
            client_cfg,
            device,
        )
        for client_id in range(config.clients)
    ]

    model = TinyClassifier(config.features, config.classes)
    noise_generator = None
    if config.dp_enabled:
        noise_generator = torch.Generator(device="cpu")
        noise_generator.manual_seed(config.seed + 10_000)

    server = Server(
        model=model,
        num_clients=config.clients,
        algorithm=config.algorithm,
        server_lr=config.server_lr,
        device=device,
        aggregation_weighting="uniform",
        dp_enabled=config.dp_enabled,
        noise_multiplier=config.noise_multiplier,
        update_clip_norm=config.clip_norm,
        privacy_noise_generator=noise_generator,
    )
    scratch_model = TinyClassifier(config.features, config.classes)
    evaluation_loader = DataLoader(dataset, batch_size=128, shuffle=False)
    accountant = None
    if config.dp_enabled:
        accountant = MomentsAccountant(
            noise_multiplier=config.noise_multiplier,
            sample_rate=config.sample_rate,
            target_delta=1e-5,
        )

    sampler = random.Random(config.seed)
    maybe_clear(stream, config.clear_screen)
    render_header(stream, config=config, device=device)
    emit(stream, "Client data partitions")
    for client_id in range(config.clients):
        indices = partitions[client_id]
        emit(
            stream,
            f"  client-{client_id:<2} samples={len(indices):<4} "
            f"labels=[{label_summary(dataset, indices)}]",
        )
    emit(stream)

    history: list[dict] = []
    for round_id in range(1, config.rounds + 1):
        selected = [
            client_id
            for client_id in range(config.clients)
            if sampler.random() < config.sample_rate
        ]
        if not selected:
            selected = [sampler.randrange(config.clients)]

        emit(stream, f"ROUND {round_id}/{config.rounds}")
        emit(stream, f"  selected clients: {selected}")
        global_state = server.broadcast()
        results = []

        for client_id in selected:
            client = clients[client_id]
            emit(
                stream,
                f"  -> client-{client_id}: read {client.num_samples} local samples; "
                "train locally",
            )
            c_global, c_local = server.get_control_variates(client_id)
            result = client.train(
                model=scratch_model,
                global_state=global_state,
                algorithm=config.algorithm,
                c_global=c_global,
                c_local=c_local,
            )
            results.append(result)
            emit(
                stream,
                f"  <- client-{client_id}: delta returned | "
                f"loss={result['avg_loss']:.4f} | "
                f"update_norm={result['unclipped_update_norm']:.4f}",
            )
            if config.dp_enabled:
                emit(
                    stream,
                    f"     clipping_factor={result['clipping_factor']:.4f} | "
                    f"clipped={result['was_clipped']}",
                )
            sleep_fn(config.delay)

        aggregate_stats = server.aggregate(results)
        epsilon = math.nan
        if accountant is not None:
            accountant.step()
            epsilon = float(accountant.estimate().epsilon)

        eval_loss, eval_accuracy = evaluate(
            server.model,
            evaluation_loader,
            device,
        )
        row = {
            "round": round_id,
            "selected": tuple(selected),
            "cohort_size": len(selected),
            "eval_loss": eval_loss,
            "eval_accuracy": eval_accuracy,
            "epsilon": epsilon,
            "aggregate_noise_norm": float(
                aggregate_stats["aggregate_noise_norm"]
            ),
        }
        history.append(row)
        emit(
            stream,
            f"  SERVER aggregate -> global model v{server.round_count} | "
            f"accuracy={eval_accuracy * 100:.2f}% | loss={eval_loss:.4f}",
        )
        if config.dp_enabled:
            emit(
                stream,
                f"  DP epsilon≈{epsilon:.4f} (delta=1e-5, simulator accounting) | "
                f"noise_norm={row['aggregate_noise_norm']:.4f}",
            )
        emit(stream)
        sleep_fn(config.delay)

    final = history[-1]
    emit(stream, "SIMULATION COMPLETE")
    emit(
        stream,
        f"  final global accuracy={final['eval_accuracy'] * 100:.2f}% | "
        f"rounds={config.rounds}",
    )
    emit(
        stream,
        "  privacy boundary: raw client samples stayed inside each Client DataLoader; "
        "only model updates were aggregated",
    )
    return {
        "dataset_samples": len(dataset),
        "history": history,
        "final_accuracy": float(final["eval_accuracy"]),
        "server_rounds": server.round_count,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run a live, real-training FL simulation using the repository's root "
            "Client and Server implementation."
        )
    )
    parser.add_argument("--clients", type=int, default=6)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--samples-per-client", type=int, default=80)
    parser.add_argument("--sample-rate", type=float, default=0.67)
    parser.add_argument("--partition", choices=["iid", "dirichlet"], default="dirichlet")
    parser.add_argument("--alpha", type=float, default=0.3)
    parser.add_argument(
        "--algorithm",
        choices=["fedavg", "fedprox", "scaffold"],
        default="fedavg",
    )
    parser.add_argument("--local-epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--learning-rate", type=float, default=0.08)
    parser.add_argument("--dp", action="store_true")
    parser.add_argument("--noise-multiplier", type=float, default=0.8)
    parser.add_argument("--clip-norm", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--delay", type=float, default=0.10)
    parser.add_argument("--no-clear", action="store_true")
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Run a tiny one-round, zero-delay validation suitable for CI.",
    )
    return parser.parse_args(argv)


def config_from_args(args: argparse.Namespace) -> SimulationConfig:
    if args.smoke:
        return SimulationConfig(
            clients=3,
            rounds=1,
            samples_per_client=16,
            sample_rate=0.67,
            partition="iid",
            local_epochs=1,
            batch_size=16,
            seed=args.seed,
            device="cpu",
            delay=0.0,
            clear_screen=False,
        )
    return SimulationConfig(
        clients=args.clients,
        rounds=args.rounds,
        samples_per_client=args.samples_per_client,
        sample_rate=args.sample_rate,
        partition=args.partition,
        alpha=args.alpha,
        algorithm=args.algorithm,
        local_epochs=args.local_epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        dp_enabled=args.dp,
        noise_multiplier=args.noise_multiplier,
        clip_norm=args.clip_norm,
        seed=args.seed,
        device=args.device,
        delay=args.delay,
        clear_screen=not args.no_clear,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = config_from_args(args)
    try:
        run_simulation(config)
    except (ValueError, RuntimeError) as exc:
        print(f"simulator error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
