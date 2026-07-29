"""Core federated-learning experiment runtime.

This module preserves the original research execution path while keeping the
desktop application separate from the training logic.
"""

from __future__ import annotations

import argparse
import copy
import math
import os
import random
import time
from typing import Dict, List

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
from federated.dp_accountant import MomentsAccountant
from federated.server import SUPPORTED_ALGORITHMS, Server
from models.networks import build_model
from utils.logger import CSVLogger, generate_all_plots
from utils.metrics import (
    compute_client_drift,
    compute_weight_variance,
    evaluate_global,
)


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
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to YAML configuration file",
    )
    parser.add_argument(
        "--algo",
        type=str,
        default=None,
        choices=list(SUPPORTED_ALGORITHMS) + ["all"],
        help="Aggregation algorithm (or 'all' to compare)",
    )
    parser.add_argument(
        "--alpha",
        type=float,
        default=None,
        help="Dirichlet concentration parameter",
    )
    parser.add_argument(
        "--dp",
        type=str,
        default=None,
        choices=["on", "off"],
        help="Enable/disable differential privacy",
    )
    parser.add_argument(
        "--noise",
        type=float,
        default=None,
        help="DP noise multiplier sigma",
    )
    parser.add_argument(
        "--rounds",
        type=int,
        default=None,
        help="Number of communication rounds",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        choices=["CIFAR10", "MNIST"],
        help="Dataset name",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Launch the desktop dashboard",
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Run the experiment in terminal mode without opening the dashboard",
    )
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
    cohort_size = max(1, int(round(sample_rate * num_clients)))
    results_dir = config["system"]["results_dir"]
    dp_enabled = bool(dp_cfg["enabled"])

    global_model = build_model(
        config["model"]["name"],
        num_classes=num_classes,
        in_channels=in_channels,
        group_norm_groups=int(config["model"]["group_norm_groups"]),
    )
    server = Server(
        model=global_model,
        num_clients=num_clients,
        algorithm=algorithm,
        server_lr=float(fed_cfg["server_lr"]),
        device=device,
    )
    clients = [
        Client(cid, train_set, client_dict[cid], config, device)
        for cid in range(num_clients)
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
        f"{cohort_size}/{num_clients} clients/round | DP={'on' if dp_enabled else 'off'} ==="
    )

    for rnd in range(1, rounds + 1):
        selected = sampler.sample(range(num_clients), cohort_size)
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

        server.aggregate(client_results)

        epsilon = float("inf")
        if accountant is not None:
            accountant.step()
            epsilon = accountant.get_epsilon()

        test_loss, test_acc = evaluate_global(server.model, test_loader, device)
        weight_var = compute_weight_variance([r["local_state"] for r in client_results])
        drift = compute_client_drift([r["delta"] for r in client_results])
        avg_client_loss = float(np.mean([r["avg_loss"] for r in client_results]))

        row = {
            "round": rnd,
            "algorithm": algorithm,
            "test_acc": f"{test_acc:.6f}",
            "test_loss": f"{test_loss:.6f}",
            "epsilon": f"{epsilon:.6f}" if np.isfinite(epsilon) else "inf",
            "weight_variance": f"{weight_var:.8e}",
            "client_drift": f"{drift:.8e}",
            "avg_client_loss": f"{avg_client_loss:.6f}",
        }
        logger.log(row)
        history.append(
            {
                "round": rnd,
                "test_acc": test_acc,
                "test_loss": test_loss,
                "epsilon": epsilon,
                "weight_variance": weight_var,
                "client_drift": drift,
            }
        )

        eps_str = f"{epsilon:6.2f}" if np.isfinite(epsilon) else "   inf"
        print(
            f"[{algorithm:8s}] round {rnd:3d}/{rounds} | "
            f"acc {test_acc * 100:5.2f}% | loss {test_loss:.4f} | "
            f"eps {eps_str} | drift {drift:.3e}"
        )

    logger.close()
    elapsed = time.time() - start

    accs = [item["test_acc"] for item in history]
    return {
        "algorithm": algorithm,
        "csv_path": csv_path,
        "final_acc": accs[-1],
        "best_acc": max(accs),
        "final_loss": history[-1]["test_loss"],
        "final_epsilon": history[-1]["epsilon"],
        "mean_drift": float(np.mean([item["client_drift"] for item in history])),
        "mean_weight_var": float(np.mean([item["weight_variance"] for item in history])),
        "elapsed_sec": elapsed,
    }


def build_summary_table(summaries: List[dict], config: dict) -> str:
    dp_cfg = config["dp"]
    lines = [
        "# Experiment Summary",
        "",
        f"- **Dataset:** {config['data']['dataset']} "
        f"({config['data']['partition']}, alpha={config['data']['alpha']})",
        f"- **Clients:** {config['federated']['num_clients']} total, "
        f"sample rate {config['federated']['sample_rate']}, "
        f"{config['federated']['rounds']} rounds, "
        f"{config['federated']['local_epochs']} local epochs",
        f"- **DP:** enabled={dp_cfg['enabled']}, C={dp_cfg['max_grad_norm']}, "
        f"sigma={dp_cfg['noise_multiplier']}, delta={dp_cfg['target_delta']}",
        "",
        "| Algorithm | Final Acc | Best Acc | Final Loss | Final Epsilon | "
        "Mean Client Drift | Mean Weight Var | Time (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for summary in summaries:
        eps = (
            f"{summary['final_epsilon']:.2f}"
            if np.isfinite(summary["final_epsilon"])
            else "inf (DP off)"
        )
        lines.append(
            f"| {summary['algorithm'].upper()} "
            f"| {summary['final_acc'] * 100:.2f}% "
            f"| {summary['best_acc'] * 100:.2f}% "
            f"| {summary['final_loss']:.4f} "
            f"| {eps} "
            f"| {summary['mean_drift']:.3e} "
            f"| {summary['mean_weight_var']:.3e} "
            f"| {summary['elapsed_sec']:.0f} |"
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
    seed = int(config["system"]["seed"])
    set_seed(seed)
    device = resolve_device(config["system"]["device"])
    results_dir = config["system"]["results_dir"]
    os.makedirs(results_dir, exist_ok=True)

    print(f"Device: {device} | Seed: {seed}")

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
