"""Core federated-learning experiment runtime."""

from __future__ import annotations

import argparse
import copy
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
    get_dataset,
    partition_dirichlet,
    partition_pathological,
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
    compute_mean_update_norm,
    compute_weight_variance,
    evaluate_global,
)


SUPPORTED_SAMPLING_STRATEGIES = ("poisson", "fixed_without_replacement")


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
    parser.add_argument("--config", type=str, default="config.yaml", help="Path to YAML configuration file")
    parser.add_argument("--algo", type=str, default=None, choices=list(SUPPORTED_ALGORITHMS) + ["all"], help="Aggregation algorithm (or 'all' to compare)")
    parser.add_argument("--alpha", type=float, default=None, help="Dirichlet concentration parameter")
    parser.add_argument("--dp", type=str, default=None, choices=["on", "off"], help="Enable/disable differential privacy")
    parser.add_argument("--noise", type=float, default=None, help="DP noise multiplier sigma")
    parser.add_argument("--rounds", type=int, default=None, help="Number of communication rounds")
    parser.add_argument("--dataset", type=str, default=None, choices=["CIFAR10", "MNIST"], help="Dataset name")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--gui", action="store_true", help="Launch the desktop dashboard")
    parser.add_argument("--cli", action="store_true", help="Run the experiment in terminal mode without opening the dashboard")
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
    if args.alpha is not None:
        config["data"]["alpha"] = args.alpha
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
    return config


def _normalize_config(config: dict) -> tuple[dict, list[str]]:
    normalized = copy.deepcopy(config)
    warnings: list[str] = []

    normalized.setdefault("optimizer", {})
    normalized.setdefault("dp", {})
    normalized.setdefault("federated", {})
    normalized.setdefault("algorithm", {})
    normalized.setdefault("system", {})

    dp_cfg = normalized["dp"]
    opt_cfg = normalized["optimizer"]
    fed_cfg = normalized["federated"]

    if "update_clip_norm" not in dp_cfg and "max_grad_norm" in dp_cfg:
        legacy_clip = dp_cfg["max_grad_norm"]
        dp_cfg["update_clip_norm"] = legacy_clip
        if "grad_clip_norm" not in opt_cfg or opt_cfg["grad_clip_norm"] is None:
            opt_cfg["grad_clip_norm"] = legacy_clip
        warnings.append(
            "Deprecated config field `dp.max_grad_norm` detected. "
            "It was migrated to both `dp.update_clip_norm` and `optimizer.grad_clip_norm` "
            "for compatibility; update your YAML explicitly."
        )
    opt_cfg.setdefault("grad_clip_norm", None)
    fed_cfg.setdefault("sampling_strategy", "poisson")
    fed_cfg.setdefault("aggregation_weighting", "uniform")
    dp_cfg.setdefault("deterministic_noise_for_testing", False)
    dp_cfg.setdefault("test_noise_seed", None)
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
    if int(data_cfg["min_partition_size"]) <= 0:
        raise ValueError("data.min_partition_size must be > 0.")
    if int(model_cfg["group_norm_groups"]) <= 0:
        raise ValueError("model.group_norm_groups must be > 0.")
    if 32 % int(model_cfg["group_norm_groups"]) != 0:
        raise ValueError("model.group_norm_groups must divide the GroupNorm channel counts (32/64/128).")
    if int(model_cfg["group_norm_groups"]) > 32:
        raise ValueError("model.group_norm_groups must not exceed the smallest GroupNorm channel count (32).")
    if sampling_strategy == "fixed_without_replacement" and bool(dp_cfg["enabled"]):
        raise ValueError(
            "DP accounting is only implemented for federated.sampling_strategy='poisson'. "
            "Disable DP or switch to Poisson sampling."
        )
    if weighting == "sample_count" and bool(dp_cfg["enabled"]):
        raise ValueError(
            "DP-enabled root runtime currently supports only uniform client weighting. "
            "Set federated.aggregation_weighting='uniform' or disable DP."
        )
    if weighting == "sample_count" and algorithm in {"scaffold", "all"}:
        raise ValueError(
            "Sample-count weighting is not supported for SCAFFOLD or algorithm='all'. "
            "Use federated.aggregation_weighting='uniform'."
        )
    if bool(dp_cfg["deterministic_noise_for_testing"]) and dp_cfg.get("test_noise_seed") is None:
        raise ValueError(
            "dp.test_noise_seed is required when dp.deterministic_noise_for_testing=true."
        )
    normalized["federated"]["aggregation_weighting"] = weighting
    normalized["federated"]["sampling_strategy"] = sampling_strategy
    normalized["algorithm"]["name"] = algorithm
    return normalized, warnings


def _sample_client_ids(
    *,
    num_clients: int,
    sample_rate: float,
    strategy: str,
    sampler: random.Random,
) -> list[int]:
    if strategy == "poisson":
        return [client_id for client_id in range(num_clients) if sampler.random() < sample_rate]
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
    clients = [Client(cid, train_set, client_dict[cid], config, device) for cid in range(num_clients)]
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

    for rnd in range(1, rounds + 1):
        selected = _sample_client_ids(
            num_clients=num_clients,
            sample_rate=sample_rate,
            strategy=sampling_strategy,
            sampler=sampler,
        )
        global_state = server.broadcast()

        client_results = []
        for cid in selected:
            c_global, c_local = server.get_control_variates(cid)
            result = clients[cid].train(
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
        weight_var = compute_weight_variance([result["local_state"] for result in client_results])
        raw_drift = compute_client_drift([result["raw_delta"] for result in client_results])
        clipped_drift = compute_client_drift([result["clipped_delta"] for result in client_results])
        mean_unclipped_update_norm = float(
            np.mean([result["unclipped_update_norm"] for result in client_results])
        ) if client_results else 0.0
        mean_clipping_factor = float(
            np.mean([result["clipping_factor"] for result in client_results])
        ) if client_results else 1.0
        fraction_clients_clipped = float(
            np.mean([1.0 if result["was_clipped"] else 0.0 for result in client_results])
        ) if client_results else 0.0
        avg_client_loss = float(np.mean([result["avg_loss"] for result in client_results])) if client_results else 0.0
        participation_rate = (len(selected) / float(num_clients)) if num_clients > 0 else 0.0

        row = {
            "round": rnd,
            "algorithm": algorithm,
            "cohort_size": len(selected),
            "participation_rate": f"{participation_rate:.6f}",
            "test_acc": f"{test_acc:.6f}",
            "test_loss": f"{test_loss:.6f}",
            "epsilon": "" if not dp_enabled else (f"{epsilon_value:.6f}" if math.isfinite(epsilon_value) else "inf"),
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
                "round": rnd,
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

        epsilon_text = "n/a" if not dp_enabled else (f"{epsilon_value:6.2f}" if math.isfinite(epsilon_value) else "   inf")
        print(
            f"[{algorithm:8s}] round {rnd:3d}/{rounds} | "
            f"cohort {len(selected):3d}/{num_clients} | "
            f"acc {test_acc * 100:5.2f}% | loss {test_loss:.4f} | "
            f"eps {epsilon_text} | raw drift {raw_drift:.3e}"
        )

    logger.close()
    elapsed = time.time() - start
    accuracies = [item["test_acc"] for item in history]
    final_epsilon = history[-1]["epsilon"] if history else math.nan
    return {
        "algorithm": algorithm,
        "csv_path": csv_path,
        "final_acc": accuracies[-1],
        "best_acc": max(accuracies),
        "final_loss": history[-1]["test_loss"],
        "final_epsilon": final_epsilon,
        "mean_raw_drift": float(np.mean([item["raw_client_drift"] for item in history])),
        "mean_clipped_drift": float(np.mean([item["clipped_client_drift"] for item in history])),
        "mean_weight_var": float(np.mean([item["weight_variance"] for item in history])),
        "mean_update_norm": float(np.mean([item["mean_unclipped_update_norm"] for item in history])),
        "mean_clipping_factor": float(np.mean([item["mean_clipping_factor"] for item in history])),
        "mean_fraction_clipped": float(np.mean([item["fraction_clients_clipped"] for item in history])),
        "mean_aggregate_noise_norm": float(np.mean([item["aggregate_noise_norm"] for item in history])),
        "elapsed_sec": elapsed,
        "rdp_orders": accountant.orders if accountant is not None else [],
        "final_total_rdp": history[-1]["rdp"] if history and history[-1]["rdp"] is not None else None,
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


def build_summary_table(summaries: List[dict], config: dict) -> str:
    fed_cfg = config["federated"]
    dp_cfg = config["dp"]
    dp_enabled = bool(dp_cfg["enabled"])
    lines = [
        "# Experiment Summary",
        "",
        f"- **Dataset:** {config['data']['dataset']} ({config['data']['partition']}, alpha={config['data']['alpha']})",
        f"- **Clients:** {fed_cfg['num_clients']} total, sample rate {fed_cfg['sample_rate']}, {fed_cfg['rounds']} rounds, {fed_cfg['local_epochs']} local epochs",
        f"- **Sampling strategy:** {fed_cfg['sampling_strategy']}",
        f"- **Aggregation weighting:** {fed_cfg['aggregation_weighting']}",
        f"- **Differential Privacy:** enabled={dp_enabled}, update clip={dp_cfg['update_clip_norm']}, sigma={dp_cfg['noise_multiplier']}, delta={dp_cfg['target_delta']}",
        f"- **Privacy guarantee status:** {'not_applicable' if not dp_enabled else summaries[0]['privacy_status']}",
        "",
        "| Algorithm | Final Acc | Best Acc | Final Loss | Final Epsilon | Mean Raw Drift | Mean Clipped Drift | Mean Weight Var | Time (s) |",
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
                    f"- **FedAvg epsilon:** {_format_epsilon(next((s['final_epsilon'] for s in summaries if s['algorithm'] == 'fedavg'), math.nan), dp_enabled=True)}",
                    f"- **FedProx epsilon:** {_format_epsilon(next((s['final_epsilon'] for s in summaries if s['algorithm'] == 'fedprox'), math.nan), dp_enabled=True)}",
                    f"- **SCAFFOLD epsilon:** {_format_epsilon(next((s['final_epsilon'] for s in summaries if s['algorithm'] == 'scaffold'), math.nan), dp_enabled=True)}",
                    f"- **Composed epsilon for all released outputs:** {composed_epsilon:.2f}",
                    f"- **Delta:** {dp_cfg['target_delta']}",
                    f"- **Sampling strategy:** {fed_cfg['sampling_strategy']}",
                    f"- **Aggregation weighting:** {fed_cfg['aggregation_weighting']}",
                    f"- **Privacy guarantee status:** {summaries[0]['privacy_status']}",
                    "",
                    "Reported central client-level privacy estimate under the documented Poisson-sampling and trusted-server assumptions.",
                ]
            )
    return "\n".join(lines)


def write_client_distribution_csv(client_dict: Dict[int, np.ndarray], train_set, save_path: str) -> str:
    labels = np.asarray(train_set.targets)
    unique_labels = sorted(int(value) for value in np.unique(labels))
    with open(save_path, "w", encoding="utf-8") as handle:
        header = ["client_id", "sample_count", *[f"class_{label}" for label in unique_labels]]
        handle.write(",".join(header) + "\n")
        for client_id, indices in sorted(client_dict.items()):
            client_labels = labels[np.asarray(indices, dtype=int)]
            counts = [str(int(np.sum(client_labels == label))) for label in unique_labels]
            row = [str(client_id), str(len(indices)), *counts]
            handle.write(",".join(row) + "\n")
    return save_path


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

    num_clients = int(config["federated"]["num_clients"])
    if data_cfg["partition"] == "dirichlet":
        client_dict = partition_dirichlet(
            train_set,
            num_clients=num_clients,
            alpha=float(data_cfg["alpha"]),
            seed=seed,
            min_partition_size=int(data_cfg["min_partition_size"]),
        )
    elif data_cfg["partition"] == "pathological":
        client_dict = partition_pathological(
            train_set,
            num_clients=num_clients,
            classes_per_client=int(data_cfg["classes_per_client"]),
            seed=seed,
        )
    else:
        raise ValueError(f"Unknown partition '{data_cfg['partition']}'")

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

    algo_setting = config["algorithm"]["name"].lower()
    algorithms = list(SUPPORTED_ALGORITHMS) if algo_setting == "all" else [algo_setting]

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

    print("\n" + summary_md)
    print(f"\nSummary written -> {summary_path}")
    return summary_path
