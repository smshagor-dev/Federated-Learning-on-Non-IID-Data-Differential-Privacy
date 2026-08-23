"""Core federated-learning experiment runtime."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import random
import secrets
import time
from typing import Dict, Iterable, List

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader

from data.partitioner import (
    extract_targets,
    get_dataset,
    partition_dirichlet,
    partition_iid,
    partition_pathological,
    partition_quantity_skew,
    plot_distribution,
)
from federated.client import Client
from federated.dp_accountant import (
    MomentsAccountant,
    compose_rdp_curves,
    rdp_to_epsilon,
)
from federated.server import (
    SUPPORTED_AGGREGATION_WEIGHTING,
    SUPPORTED_ALGORITHMS,
    Server,
)
from models.networks import build_model
from utils.logger import CSVLogger, generate_all_plots
from utils.metrics import (
    compute_client_drift,
    compute_weight_variance,
    evaluate_global,
)
from utils.partition_metrics import write_partition_artifacts

SUPPORTED_SAMPLING_STRATEGIES = ("poisson", "fixed_without_replacement")
SUPPORTED_PARTITIONS = ("iid", "dirichlet", "pathological", "quantity_skew")


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def resolve_device(requested: str) -> torch.device:
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(requested)


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Federated Learning on Non-IID Data with Differential Privacy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", type=str, default="config.yaml")
    parser.add_argument(
        "--algo",
        type=str,
        default=None,
        choices=list(SUPPORTED_ALGORITHMS) + ["all"],
        help="Aggregation algorithm",
    )
    parser.add_argument(
        "--partition",
        type=str,
        default=None,
        choices=list(SUPPORTED_PARTITIONS),
        help="Client partition strategy",
    )
    parser.add_argument("--alpha", type=float, default=None)
    parser.add_argument("--classes-per-client", type=int, default=None)
    parser.add_argument("--quantity-skew-sigma", type=float, default=None)
    parser.add_argument(
        "--dp", type=str, default=None, choices=["on", "off"]
    )
    parser.add_argument("--noise", type=float, default=None)
    parser.add_argument("--rounds", type=int, default=None)
    parser.add_argument(
        "--dataset", type=str, default=None, choices=["CIFAR10", "MNIST"]
    )
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--results-dir", type=str, default=None)
    parser.add_argument("--gui", action="store_true")
    parser.add_argument("--cli", action="store_true")
    return parser.parse_args(argv)


def should_launch_gui(args: argparse.Namespace, argv: list[str] | None = None) -> bool:
    effective_argv = argv if argv is not None else []
    if args.cli:
        return False
    if args.gui:
        return True
    return len(effective_argv) == 0


def apply_overrides(config: dict, args: argparse.Namespace) -> dict:
    config = copy.deepcopy(config)
    if args.algo is not None:
        config["algorithm"]["name"] = args.algo
    if args.partition is not None:
        config["data"]["partition"] = args.partition
    if args.alpha is not None:
        config["data"]["alpha"] = args.alpha
    if args.classes_per_client is not None:
        config["data"]["classes_per_client"] = args.classes_per_client
    if args.quantity_skew_sigma is not None:
        config["data"]["quantity_skew_sigma"] = args.quantity_skew_sigma
    if args.dp is not None:
        config["dp"]["enabled"] = args.dp == "on"
    if args.noise is not None:
        config["dp"]["noise_multiplier"] = args.noise
    if args.rounds is not None:
        config["federated"]["rounds"] = args.rounds
    if args.dataset is not None:
        config["data"]["dataset"] = args.dataset
    if args.seed is not None:
        config["system"]["seed"] = args.seed
    if args.results_dir is not None:
        config["system"]["results_dir"] = args.results_dir
    return config


def _normalize_config(config: dict) -> tuple[dict, list[str]]:
    normalized = copy.deepcopy(config)
    warnings: list[str] = []

    normalized.setdefault("optimizer", {})
    normalized.setdefault("dp", {})
    normalized.setdefault("federated", {})
    normalized.setdefault("algorithm", {})
    normalized.setdefault("system", {})
    normalized.setdefault("data", {})

    dp_cfg = normalized["dp"]
    opt_cfg = normalized["optimizer"]
    fed_cfg = normalized["federated"]
    data_cfg = normalized["data"]

    if "update_clip_norm" not in dp_cfg and "max_grad_norm" in dp_cfg:
        legacy_clip = dp_cfg["max_grad_norm"]
        dp_cfg["update_clip_norm"] = legacy_clip
        if "grad_clip_norm" not in opt_cfg or opt_cfg["grad_clip_norm"] is None:
            opt_cfg["grad_clip_norm"] = legacy_clip
        warnings.append(
            "Deprecated config field `dp.max_grad_norm` detected. It was migrated "
            "to `dp.update_clip_norm` and `optimizer.grad_clip_norm`."
        )

    opt_cfg.setdefault("grad_clip_norm", None)
    fed_cfg.setdefault("sampling_strategy", "poisson")
    fed_cfg.setdefault("aggregation_weighting", "uniform")
    dp_cfg.setdefault("deterministic_noise_for_testing", False)
    dp_cfg.setdefault("test_noise_seed", None)
    data_cfg.setdefault("partition", "dirichlet")
    data_cfg.setdefault("alpha", 0.1)
    data_cfg.setdefault("classes_per_client", 2)
    data_cfg.setdefault("quantity_skew_sigma", 1.0)
    data_cfg.setdefault("min_partition_size", 10)
    return normalized, warnings


def validate_config(config: dict) -> tuple[dict, list[str]]:
    normalized, warnings = _normalize_config(config)
    fed_cfg = normalized["federated"]
    data_cfg = normalized["data"]
    opt_cfg = normalized["optimizer"]
    dp_cfg = normalized["dp"]
    model_cfg = normalized["model"]
    algo_cfg = normalized["algorithm"]

    weighting = str(fed_cfg["aggregation_weighting"]).lower()
    sampling_strategy = str(fed_cfg["sampling_strategy"]).lower()
    algorithm = str(algo_cfg["name"]).lower()
    partition = str(data_cfg["partition"]).lower()
    sample_rate = float(fed_cfg["sample_rate"])
    update_clip_norm = float(dp_cfg["update_clip_norm"])
    noise_multiplier = float(dp_cfg["noise_multiplier"])
    delta = float(dp_cfg["target_delta"])
    grad_clip_norm = opt_cfg.get("grad_clip_norm")

    if weighting not in SUPPORTED_AGGREGATION_WEIGHTING:
        raise ValueError(
            f"Unsupported federated.aggregation_weighting={weighting!r}. "
            f"Use one of {SUPPORTED_AGGREGATION_WEIGHTING}."
        )
    if sampling_strategy not in SUPPORTED_SAMPLING_STRATEGIES:
        raise ValueError(
            f"Unsupported federated.sampling_strategy={sampling_strategy!r}. "
            f"Use one of {SUPPORTED_SAMPLING_STRATEGIES}."
        )
    if algorithm not in (*SUPPORTED_ALGORITHMS, "all"):
        raise ValueError(
            f"Unsupported algorithm.name={algorithm!r}. "
            f"Use one of {(*SUPPORTED_ALGORITHMS, 'all')}."
        )
    if partition not in SUPPORTED_PARTITIONS:
        raise ValueError(
            f"Unsupported data.partition={partition!r}. "
            f"Use one of {SUPPORTED_PARTITIONS}."
        )
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("federated.sample_rate must lie in [0, 1].")
    if int(fed_cfg["num_clients"]) <= 0:
        raise ValueError("federated.num_clients must be > 0.")
    if int(fed_cfg["rounds"]) <= 0:
        raise ValueError("federated.rounds must be > 0.")
    if int(fed_cfg["local_epochs"]) <= 0:
        raise ValueError("federated.local_epochs must be > 0.")
    if int(fed_cfg["batch_size"]) <= 0:
        raise ValueError("federated.batch_size must be > 0.")
    if float(fed_cfg["server_lr"]) <= 0.0:
        raise ValueError("federated.server_lr must be > 0.")
    if float(opt_cfg["lr"]) <= 0.0:
        raise ValueError("optimizer.lr must be > 0.")
    if grad_clip_norm is not None and float(grad_clip_norm) <= 0.0:
        raise ValueError("optimizer.grad_clip_norm must be > 0 when provided.")
    if update_clip_norm <= 0.0:
        raise ValueError("dp.update_clip_norm must be > 0.")
    if noise_multiplier < 0.0:
        raise ValueError("dp.noise_multiplier must be >= 0.")
    if not 0.0 < delta < 1.0:
        raise ValueError("dp.target_delta must lie in (0, 1).")
    if float(data_cfg["alpha"]) <= 0.0:
        raise ValueError("data.alpha must be > 0.")
    if int(data_cfg["classes_per_client"]) <= 0:
        raise ValueError("data.classes_per_client must be > 0.")
    if float(data_cfg["quantity_skew_sigma"]) < 0.0:
        raise ValueError("data.quantity_skew_sigma must be >= 0.")
    if int(data_cfg["min_partition_size"]) <= 0:
        raise ValueError("data.min_partition_size must be > 0.")
    if int(model_cfg["group_norm_groups"]) <= 0:
        raise ValueError("model.group_norm_groups must be > 0.")
    if 32 % int(model_cfg["group_norm_groups"]) != 0:
        raise ValueError(
            "model.group_norm_groups must divide GroupNorm channel counts."
        )
    if int(model_cfg["group_norm_groups"]) > 32:
        raise ValueError(
            "model.group_norm_groups must not exceed the smallest channel count."
        )
    if sampling_strategy == "fixed_without_replacement" and bool(dp_cfg["enabled"]):
        raise ValueError(
            "DP accounting is only implemented for Poisson client sampling."
        )
    if weighting == "sample_count" and bool(dp_cfg["enabled"]):
        raise ValueError(
            "DP-enabled root runtime supports uniform client weighting only."
        )
    if weighting == "sample_count" and algorithm in {"scaffold", "all"}:
        raise ValueError(
            "Sample-count weighting is not supported for SCAFFOLD or algorithm='all'."
        )
    if bool(dp_cfg["deterministic_noise_for_testing"]) and dp_cfg.get(
        "test_noise_seed"
    ) is None:
        raise ValueError(
            "dp.test_noise_seed is required when deterministic test noise is enabled."
        )

    normalized["federated"]["aggregation_weighting"] = weighting
    normalized["federated"]["sampling_strategy"] = sampling_strategy
    normalized["algorithm"]["name"] = algorithm
    normalized["data"]["partition"] = partition
    return normalized, warnings


def _sample_client_ids(
    *,
    num_clients: int,
    sample_rate: float,
    strategy: str,
    sampler: random.Random,
) -> list[int]:
    if strategy == "poisson":
        return [
            client_id
            for client_id in range(num_clients)
            if sampler.random() < sample_rate
        ]
    if sample_rate <= 0.0:
        return []
    if sample_rate >= 1.0:
        return list(range(num_clients))
    cohort_size = int(round(sample_rate * num_clients))
    cohort_size = min(num_clients, max(0, cohort_size))
    return sampler.sample(range(num_clients), cohort_size)


def _make_privacy_noise_generator(dp_cfg: dict) -> tuple[torch.Generator | None, str]:
    if not bool(dp_cfg["enabled"]):
        return None, "not_applicable"
    generator = torch.Generator(device="cpu")
    if bool(dp_cfg["deterministic_noise_for_testing"]):
        generator.manual_seed(int(dp_cfg["test_noise_seed"]))
        return generator, "simulation_only"
    generator.manual_seed(secrets.randbits(63))
    return generator, "estimated"


def run_experiment(
    algorithm: str,
    config: dict,
    train_set,
    test_loader: DataLoader,
    client_dict: Dict[int, np.ndarray],
    num_classes: int,
    in_channels: int,
    device: torch.device,
) -> dict:
    set_seed(int(config["system"]["seed"]))

    fed_cfg = config["federated"]
    dp_cfg = config["dp"]
    num_clients = int(fed_cfg["num_clients"])
    rounds = int(fed_cfg["rounds"])
    sample_rate = float(fed_cfg["sample_rate"])
    sampling_strategy = str(fed_cfg["sampling_strategy"])
    aggregation_weighting = str(fed_cfg["aggregation_weighting"])
    results_dir = config["system"]["results_dir"]
    dp_enabled = bool(dp_cfg["enabled"])

    global_model = build_model(
        config["model"]["name"],
        num_classes=num_classes,
        in_channels=in_channels,
        group_norm_groups=int(config["model"]["group_norm_groups"]),
    )
    privacy_generator, privacy_status = _make_privacy_noise_generator(dp_cfg)
    server = Server(
        model=global_model,
        num_clients=num_clients,
        algorithm=algorithm,
        server_lr=float(fed_cfg["server_lr"]),
        device=device,
        aggregation_weighting=aggregation_weighting,
        dp_enabled=dp_enabled,
        noise_multiplier=float(dp_cfg["noise_multiplier"]),
        update_clip_norm=float(dp_cfg["update_clip_norm"]),
        privacy_noise_generator=privacy_generator,
    )
    clients = [
        Client(client_id, train_set, client_dict[client_id], config, device)
        for client_id in range(num_clients)
    ]
    scratch_model = build_model(
        config["model"]["name"],
        num_classes=num_classes,
        in_channels=in_channels,
        group_norm_groups=int(config["model"]["group_norm_groups"]),
    )

    accountant = None
    if dp_enabled:
        accountant = MomentsAccountant(
            noise_multiplier=float(dp_cfg["noise_multiplier"]),
            sample_rate=sample_rate,
            target_delta=float(dp_cfg["target_delta"]),
        )

    sampler = random.Random(int(config["system"]["seed"]))
    csv_path = os.path.join(results_dir, f"run_{algorithm}.csv")
    logger = CSVLogger(csv_path)

    history: List[dict] = []
    start = time.time()
    print(
        f"\n=== {algorithm.upper()} | {rounds} rounds | "
        f"sampling={sampling_strategy} q={sample_rate:.3f} | "
        f"weighting={aggregation_weighting} | DP={'on' if dp_enabled else 'off'} ==="
    )

    for round_id in range(1, rounds + 1):
        selected = _sample_client_ids(
            num_clients=num_clients,
            sample_rate=sample_rate,
            strategy=sampling_strategy,
            sampler=sampler,
        )
        global_state = server.broadcast()

        client_results = []
        for client_id in selected:
            c_global, c_local = server.get_control_variates(client_id)
            result = clients[client_id].train(
                model=scratch_model,
                global_state=global_state,
                algorithm=algorithm,
                c_global=c_global,
                c_local=c_local,
            )
            client_results.append(result)

        aggregate_stats = server.aggregate(client_results)

        epsilon_value = math.nan
        total_rdp: list[float] | None = None
        if accountant is not None:
            accountant.step()
            estimate = accountant.estimate()
            epsilon_value = estimate.epsilon
            total_rdp = estimate.total_rdp.tolist()

        test_loss, test_acc = evaluate_global(server.model, test_loader, device)
        weight_var = compute_weight_variance(
            [result["local_state"] for result in client_results]
        )
        raw_drift = compute_client_drift(
            [result["raw_delta"] for result in client_results]
        )
        clipped_drift = compute_client_drift(
            [result["clipped_delta"] for result in client_results]
        )
        mean_unclipped_update_norm = (
            float(np.mean([result["unclipped_update_norm"] for result in client_results]))
            if client_results
            else 0.0
        )
        mean_clipping_factor = (
            float(np.mean([result["clipping_factor"] for result in client_results]))
            if client_results
            else 1.0
        )
        fraction_clients_clipped = (
            float(
                np.mean(
                    [1.0 if result["was_clipped"] else 0.0 for result in client_results]
                )
            )
            if client_results
            else 0.0
        )
        avg_client_loss = (
            float(np.mean([result["avg_loss"] for result in client_results]))
            if client_results
            else 0.0
        )
        participation_rate = len(selected) / float(num_clients)

        row = {
            "round": round_id,
            "algorithm": algorithm,
            "cohort_size": len(selected),
            "participation_rate": f"{participation_rate:.6f}",
            "test_acc": f"{test_acc:.6f}",
            "test_loss": f"{test_loss:.6f}",
            "epsilon": ""
            if not dp_enabled
            else (f"{epsilon_value:.6f}" if math.isfinite(epsilon_value) else "inf"),
            "weight_variance": f"{weight_var:.8e}",
            "raw_client_drift": f"{raw_drift:.8e}",
            "clipped_client_drift": f"{clipped_drift:.8e}",
            "mean_unclipped_update_norm": f"{mean_unclipped_update_norm:.8e}",
            "mean_clipping_factor": f"{mean_clipping_factor:.8e}",
            "fraction_clients_clipped": f"{fraction_clients_clipped:.8e}",
            "aggregate_noise_norm": f"{aggregate_stats['aggregate_noise_norm']:.8e}",
            "avg_client_loss": f"{avg_client_loss:.6f}",
        }
        logger.log(row)
        history.append(
            {
                "round": round_id,
                "cohort_size": len(selected),
                "participation_rate": participation_rate,
                "test_acc": test_acc,
                "test_loss": test_loss,
                "epsilon": epsilon_value,
                "weight_variance": weight_var,
                "raw_client_drift": raw_drift,
                "clipped_client_drift": clipped_drift,
                "mean_unclipped_update_norm": mean_unclipped_update_norm,
                "mean_clipping_factor": mean_clipping_factor,
                "fraction_clients_clipped": fraction_clients_clipped,
                "aggregate_noise_norm": aggregate_stats["aggregate_noise_norm"],
                "rdp": total_rdp,
            }
        )

        epsilon_text = (
            "n/a"
            if not dp_enabled
            else (f"{epsilon_value:6.2f}" if math.isfinite(epsilon_value) else "inf")
        )
        print(
            f"[{algorithm:8s}] round {round_id:3d}/{rounds} | "
            f"cohort {len(selected):3d}/{num_clients} | "
            f"acc {test_acc * 100:5.2f}% | loss {test_loss:.4f} | "
            f"eps {epsilon_text} | raw drift {raw_drift:.3e}"
        )

    logger.close()
    elapsed = time.time() - start
    if not history:
        raise RuntimeError("experiment completed without any round history")
    accuracies = [item["test_acc"] for item in history]
    return {
        "algorithm": algorithm,
        "csv_path": csv_path,
        "final_acc": accuracies[-1],
        "best_acc": max(accuracies),
        "final_loss": history[-1]["test_loss"],
        "final_epsilon": history[-1]["epsilon"],
        "mean_raw_drift": float(
            np.mean([item["raw_client_drift"] for item in history])
        ),
        "mean_clipped_drift": float(
            np.mean([item["clipped_client_drift"] for item in history])
        ),
        "mean_weight_var": float(np.mean([item["weight_variance"] for item in history])),
        "mean_update_norm": float(
            np.mean([item["mean_unclipped_update_norm"] for item in history])
        ),
        "mean_clipping_factor": float(
            np.mean([item["mean_clipping_factor"] for item in history])
        ),
        "mean_fraction_clipped": float(
            np.mean([item["fraction_clients_clipped"] for item in history])
        ),
        "mean_aggregate_noise_norm": float(
            np.mean([item["aggregate_noise_norm"] for item in history])
        ),
        "elapsed_sec": elapsed,
        "rounds_completed": len(history),
        "rdp_orders": accountant.orders if accountant is not None else [],
        "final_total_rdp": history[-1]["rdp"],
        "privacy_status": privacy_status,
    }


def _format_epsilon(epsilon: float, *, dp_enabled: bool) -> str:
    if not dp_enabled:
        return "not applicable"
    if math.isinf(epsilon):
        return "infinite"
    if math.isnan(epsilon):
        return "not available"
    return f"{epsilon:.2f}"


def _partition_description(data_cfg: dict) -> str:
    strategy = data_cfg["partition"]
    if strategy == "dirichlet":
        return f"dirichlet alpha={data_cfg['alpha']}"
    if strategy == "pathological":
        return f"pathological classes_per_client={data_cfg['classes_per_client']}"
    if strategy == "quantity_skew":
        return f"quantity_skew sigma={data_cfg['quantity_skew_sigma']}"
    return "iid"


def build_summary_table(summaries: List[dict], config: dict) -> str:
    fed_cfg = config["federated"]
    dp_cfg = config["dp"]
    dp_enabled = bool(dp_cfg["enabled"])
    lines = [
        "# Experiment Summary",
        "",
        f"- **Dataset:** {config['data']['dataset']}",
        f"- **Partition:** {_partition_description(config['data'])}",
        f"- **Clients:** {fed_cfg['num_clients']} total, sample rate "
        f"{fed_cfg['sample_rate']}, {fed_cfg['rounds']} rounds, "
        f"{fed_cfg['local_epochs']} local epochs",
        f"- **Sampling strategy:** {fed_cfg['sampling_strategy']}",
        f"- **Aggregation weighting:** {fed_cfg['aggregation_weighting']}",
        f"- **Differential Privacy:** enabled={dp_enabled}, update clip="
        f"{dp_cfg['update_clip_norm']}, sigma={dp_cfg['noise_multiplier']}, "
        f"delta={dp_cfg['target_delta']}",
        f"- **Privacy guarantee status:** "
        f"{'not_applicable' if not dp_enabled else summaries[0]['privacy_status']}",
        "",
        "| Algorithm | Final Acc | Best Acc | Final Loss | Final Epsilon | "
        "Mean Raw Drift | Mean Clipped Drift | Mean Weight Var | Time (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for summary in summaries:
        lines.append(
            f"| {summary['algorithm'].upper()} "
            f"| {summary['final_acc'] * 100:.2f}% "
            f"| {summary['best_acc'] * 100:.2f}% "
            f"| {summary['final_loss']:.4f} "
            f"| {_format_epsilon(summary['final_epsilon'], dp_enabled=dp_enabled)} "
            f"| {summary['mean_raw_drift']:.3e} "
            f"| {summary['mean_clipped_drift']:.3e} "
            f"| {summary['mean_weight_var']:.3e} "
            f"| {summary['elapsed_sec']:.0f} |"
        )

    if dp_enabled:
        rdp_curves = [
            summary["final_total_rdp"]
            for summary in summaries
            if summary["final_total_rdp"] is not None
        ]
        if rdp_curves:
            composed_rdp = compose_rdp_curves(rdp_curves)
            composed_epsilon, _ = rdp_to_epsilon(
                orders=summaries[0]["rdp_orders"],
                total_rdp=composed_rdp,
                delta=float(dp_cfg["target_delta"]),
            )
            lines.extend(
                [
                    "",
                    "## Privacy Composition",
                    "",
                    f"- **Composed epsilon for all released outputs:** "
                    f"{composed_epsilon:.2f}",
                    f"- **Delta:** {dp_cfg['target_delta']}",
                    "",
                    "The composed value applies only when multiple algorithm outputs "
                    "from this invocation are all treated as released outputs.",
                ]
            )
    return "\n".join(lines)


def write_client_distribution_csv(
    client_dict: Dict[int, np.ndarray], train_set, save_path: str
) -> str:
    labels = extract_targets(train_set)
    unique_labels = sorted(int(value) for value in np.unique(labels))
    with open(save_path, "w", encoding="utf-8") as handle:
        header = [
            "client_id",
            "sample_count",
            *[f"class_{label}" for label in unique_labels],
        ]
        handle.write(",".join(header) + "\n")
        for client_id, indices in sorted(client_dict.items()):
            client_labels = labels[np.asarray(indices, dtype=int)]
            counts = [
                str(int(np.sum(client_labels == label))) for label in unique_labels
            ]
            handle.write(",".join([str(client_id), str(len(indices)), *counts]) + "\n")
    return save_path


def _partition_parameters(data_cfg: dict) -> dict[str, object]:
    strategy = data_cfg["partition"]
    if strategy == "dirichlet":
        return {"alpha": float(data_cfg["alpha"])}
    if strategy == "pathological":
        return {"classes_per_client": int(data_cfg["classes_per_client"])}
    if strategy == "quantity_skew":
        return {"quantity_skew_sigma": float(data_cfg["quantity_skew_sigma"])}
    return {}


def _build_partition(train_set, config: dict) -> Dict[int, np.ndarray]:
    data_cfg = config["data"]
    num_clients = int(config["federated"]["num_clients"])
    seed = int(config["system"]["seed"])
    minimum = int(data_cfg["min_partition_size"])
    strategy = data_cfg["partition"]

    if strategy == "iid":
        return partition_iid(
            train_set,
            num_clients=num_clients,
            seed=seed,
            min_partition_size=minimum,
        )
    if strategy == "dirichlet":
        return partition_dirichlet(
            train_set,
            num_clients=num_clients,
            alpha=float(data_cfg["alpha"]),
            seed=seed,
            min_partition_size=minimum,
        )
    if strategy == "pathological":
        return partition_pathological(
            train_set,
            num_clients=num_clients,
            classes_per_client=int(data_cfg["classes_per_client"]),
            seed=seed,
            min_partition_size=minimum,
        )
    if strategy == "quantity_skew":
        return partition_quantity_skew(
            train_set,
            num_clients=num_clients,
            quantity_skew_sigma=float(data_cfg["quantity_skew_sigma"]),
            seed=seed,
            min_partition_size=minimum,
        )
    raise ValueError(f"Unknown partition {strategy!r}")


def _json_safe(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def run_cli(config: dict) -> str:
    config, warnings = validate_config(config)
    seed = int(config["system"]["seed"])
    set_seed(seed)
    device = resolve_device(config["system"]["device"])
    results_dir = config["system"]["results_dir"]
    os.makedirs(results_dir, exist_ok=True)

    print(f"Device: {device} | Seed: {seed}")
    for warning in warnings:
        print(f"WARNING: {warning}")

    data_cfg = config["data"]
    train_set, test_set, num_classes, in_channels = get_dataset(
        data_cfg["dataset"],
        data_cfg["data_root"],
    )
    test_loader = DataLoader(
        test_set,
        batch_size=int(config["evaluation"]["eval_batch_size"]),
        shuffle=False,
        num_workers=0,
    )

    client_dict = _build_partition(train_set, config)
    partition_dir = os.path.join(results_dir, "partition")
    manifest_path, indices_path, partition_manifest = write_partition_artifacts(
        client_dict=client_dict,
        dataset=train_set,
        dataset_name=str(data_cfg["dataset"]),
        strategy=str(data_cfg["partition"]),
        seed=seed,
        parameters=_partition_parameters(data_cfg),
        output_dir=partition_dir,
    )

    dist_path = plot_distribution(
        client_dict,
        train_set,
        num_classes,
        save_path=os.path.join(results_dir, "distribution.png"),
    )
    distribution_csv = write_client_distribution_csv(
        client_dict,
        train_set,
        os.path.join(results_dir, "client_distribution.csv"),
    )
    print(f"Partition plot saved -> {dist_path}")
    print(f"Client distribution saved -> {distribution_csv}")
    print(f"Partition manifest saved -> {manifest_path}")
    print(f"Exact partition indices saved -> {indices_path}")

    algo_setting = config["algorithm"]["name"].lower()
    algorithms = (
        list(SUPPORTED_ALGORITHMS) if algo_setting == "all" else [algo_setting]
    )

    summaries: List[dict] = []
    for algorithm in algorithms:
        summaries.append(
            run_experiment(
                algorithm=algorithm,
                config=config,
                train_set=train_set,
                test_loader=test_loader,
                client_dict=client_dict,
                num_classes=num_classes,
                in_channels=in_channels,
                device=device,
            )
        )

    run_csvs = {summary["algorithm"]: summary["csv_path"] for summary in summaries}
    plots = generate_all_plots(
        results_dir,
        run_csvs=run_csvs,
        dp_enabled=bool(config["dp"]["enabled"]),
    )
    for plot in plots:
        print(f"Plot saved -> {plot}")

    summary_md = build_summary_table(summaries, config)
    summary_path = os.path.join(results_dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as handle:
        handle.write(summary_md + "\n")

    machine_summary_path = os.path.join(results_dir, "summary.json")
    machine_summary = {
        "schema_version": 1,
        "dataset": data_cfg["dataset"],
        "partition": partition_manifest,
        "seed": seed,
        "dp": {
            "enabled": bool(config["dp"]["enabled"]),
            "target_epsilon": config["dp"].get("target_epsilon"),
            "target_delta": config["dp"].get("target_delta"),
            "noise_multiplier": config["dp"].get("noise_multiplier"),
            "privacy_parameter_source": config["dp"].get("privacy_parameter_source"),
            "calibrated_epsilon": config["dp"].get("calibrated_epsilon"),
        },
        "runs": summaries,
    }
    with open(machine_summary_path, "w", encoding="utf-8") as handle:
        json.dump(_json_safe(machine_summary), handle, indent=2, sort_keys=True)
        handle.write("\n")

    print("\n" + summary_md)
    print(f"\nSummary written -> {summary_path}")
    print(f"Machine summary written -> {machine_summary_path}")
    return summary_path
