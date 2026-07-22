"""Federated Learning on Non-IID Data with Differential Privacy.

Orchestrates the full simulation: dataset partitioning, per-round client
sampling and local training, server aggregation (FedAvg / FedProx / SCAFFOLD),
privacy accounting, metric logging, plotting, and a final Markdown summary.

Examples
--------
    python main.py                                   # config.yaml defaults
    python main.py --algo scaffold --rounds 100
    python main.py --algo all --alpha 0.1 --noise 0.8
    python main.py --dp off --algo fedavg            # non-private baseline
"""

from __future__ import annotations

import argparse
import copy
import os
import random
import subprocess
import sys
import time
import threading
from typing import Dict, List

import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

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


# ---------------------------------------------------------------------- #
# Setup helpers                                                          #
# ---------------------------------------------------------------------- #
def set_seed(seed: int) -> None:
    """Fix every RNG we rely on for full reproducibility."""
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
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Federated Learning on Non-IID Data with Differential Privacy",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to YAML configuration file")
    parser.add_argument("--algo", type=str, default=None,
                        choices=list(SUPPORTED_ALGORITHMS) + ["all"],
                        help="Aggregation algorithm (or 'all' to compare)")
    parser.add_argument("--alpha", type=float, default=None,
                        help="Dirichlet concentration parameter")
    parser.add_argument("--dp", type=str, default=None, choices=["on", "off"],
                        help="Enable/disable differential privacy")
    parser.add_argument("--noise", type=float, default=None,
                        help="DP noise multiplier sigma")
    parser.add_argument("--rounds", type=int, default=None,
                        help="Number of communication rounds")
    parser.add_argument("--dataset", type=str, default=None,
                        choices=["CIFAR10", "MNIST"], help="Dataset name")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--gui", action="store_true",
                        help="Launch a desktop GUI for configuring and running experiments")
    parser.add_argument("--cli", action="store_true",
                        help="Run the experiment in terminal mode without opening the GUI")
    return parser.parse_args()


def apply_overrides(config: dict, args: argparse.Namespace) -> dict:
    """CLI arguments take precedence over config.yaml."""
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


# ---------------------------------------------------------------------- #
# Single-algorithm experiment                                            #
# ---------------------------------------------------------------------- #
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
    """Run one full FL simulation for ``algorithm`` and return summary stats."""
    set_seed(int(config["system"]["seed"]))  # identical init across algorithms

    fed_cfg = config["federated"]
    dp_cfg = config["dp"]
    num_clients = int(fed_cfg["num_clients"])
    rounds = int(fed_cfg["rounds"])
    sample_rate = float(fed_cfg["sample_rate"])
    cohort_size = max(1, int(round(sample_rate * num_clients)))
    results_dir = config["system"]["results_dir"]
    dp_enabled = bool(dp_cfg["enabled"])

    # --- Build global model, server, clients, accountant ---------------
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
    print(f"\n=== {algorithm.upper()} | {rounds} rounds | "
          f"{cohort_size}/{num_clients} clients/round | DP={'on' if dp_enabled else 'off'} ===")

    # --- Communication rounds ------------------------------------------
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
        weight_var = compute_weight_variance(
            [r["local_state"] for r in client_results]
        )
        drift = compute_client_drift([r["delta"] for r in client_results])
        avg_client_loss = float(
            np.mean([r["avg_loss"] for r in client_results])
        )

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
            f"acc {test_acc*100:5.2f}% | loss {test_loss:.4f} | "
            f"eps {eps_str} | drift {drift:.3e}"
        )

    logger.close()
    elapsed = time.time() - start

    accs = [h["test_acc"] for h in history]
    return {
        "algorithm": algorithm,
        "csv_path": csv_path,
        "final_acc": accs[-1],
        "best_acc": max(accs),
        "final_loss": history[-1]["test_loss"],
        "final_epsilon": history[-1]["epsilon"],
        "mean_drift": float(np.mean([h["client_drift"] for h in history])),
        "mean_weight_var": float(np.mean([h["weight_variance"] for h in history])),
        "elapsed_sec": elapsed,
    }


# ---------------------------------------------------------------------- #
# Markdown summary                                                       #
# ---------------------------------------------------------------------- #
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
        "| Algorithm | Final Acc | Best Acc | Final Loss | Final ε | "
        "Mean Client Drift | Mean Weight Var | Time (s) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        eps = (
            f"{s['final_epsilon']:.2f}"
            if np.isfinite(s["final_epsilon"])
            else "∞ (DP off)"
        )
        lines.append(
            f"| {s['algorithm'].upper()} "
            f"| {s['final_acc']*100:.2f}% "
            f"| {s['best_acc']*100:.2f}% "
            f"| {s['final_loss']:.4f} "
            f"| {eps} "
            f"| {s['mean_drift']:.3e} "
            f"| {s['mean_weight_var']:.3e} "
            f"| {s['elapsed_sec']:.0f} |"
        )
    return "\n".join(lines)


class ExperimentGUI:
    """Small Tkinter front-end for launching experiments from main.py."""

    def __init__(self, root: tk.Tk, script_path: str, config_path: str) -> None:
        self.root = root
        self.script_path = script_path
        self.config_path = config_path
        self.process: subprocess.Popen[str] | None = None
        self.active_results_dir = "results"
        self.runtime_config_path: str | None = None

        self.root.title("Federated DP Research Runner")
        self.root.geometry("1180x780")
        self.root.minsize(980, 680)

        self.status_var = tk.StringVar(value="Ready")
        self._build_layout()
        self.reload_from_config()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header = ttk.Frame(self.root, padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)

        ttk.Label(
            header,
            text="Federated Learning on Non-IID Data with Differential Privacy",
            font=("Segoe UI", 16, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Configure the experiment, run main.py, and inspect logs and outputs from one place.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        content = ttk.Panedwindow(self.root, orient="horizontal")
        content.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))

        left = ttk.Frame(content, padding=12)
        right = ttk.Frame(content, padding=12)
        content.add(left, weight=4)
        content.add(right, weight=5)

        self._build_form(left)
        self._build_output_panel(right)

        footer = ttk.Frame(self.root, padding=(16, 0, 16, 12))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_var).grid(row=0, column=0, sticky="w")

    def _build_form(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)

        self.config_var = tk.StringVar(value=self.config_path)
        self.results_dir_var = tk.StringVar()
        self.device_var = tk.StringVar()
        self.seed_var = tk.StringVar()
        self.dataset_var = tk.StringVar()
        self.partition_var = tk.StringVar()
        self.alpha_var = tk.StringVar()
        self.classes_per_client_var = tk.StringVar()
        self.min_partition_size_var = tk.StringVar()
        self.num_clients_var = tk.StringVar()
        self.sample_rate_var = tk.StringVar()
        self.rounds_var = tk.StringVar()
        self.local_epochs_var = tk.StringVar()
        self.batch_size_var = tk.StringVar()
        self.server_lr_var = tk.StringVar()
        self.optimizer_lr_var = tk.StringVar()
        self.momentum_var = tk.StringVar()
        self.weight_decay_var = tk.StringVar()
        self.algorithm_var = tk.StringVar()
        self.mu_var = tk.StringVar()
        self.dp_enabled_var = tk.BooleanVar()
        self.max_grad_norm_var = tk.StringVar()
        self.noise_var = tk.StringVar()
        self.delta_var = tk.StringVar()
        self.eval_batch_size_var = tk.StringVar()

        row = 0
        ttk.Label(parent, text="Config file", font=("Segoe UI", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w"
        )
        row += 1
        config_frame = ttk.Frame(parent)
        config_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(6, 10))
        config_frame.columnconfigure(0, weight=1)
        ttk.Entry(config_frame, textvariable=self.config_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(config_frame, text="Browse", command=self._browse_config).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(config_frame, text="Reload", command=self.reload_from_config).grid(row=0, column=2, padx=(8, 0))
        row += 1

        row = self._section(parent, row, "System")
        row = self._entry(parent, row, "Results directory", self.results_dir_var)
        row = self._combobox(parent, row, "Device", self.device_var, ["auto", "cpu", "cuda"])
        row = self._entry(parent, row, "Seed", self.seed_var)

        row = self._section(parent, row, "Data")
        row = self._combobox(parent, row, "Dataset", self.dataset_var, ["CIFAR10", "MNIST"])
        row = self._combobox(parent, row, "Partition", self.partition_var, ["dirichlet", "pathological"])
        row = self._entry(parent, row, "Alpha", self.alpha_var)
        row = self._entry(parent, row, "Classes per client", self.classes_per_client_var)
        row = self._entry(parent, row, "Min partition size", self.min_partition_size_var)

        row = self._section(parent, row, "Federated")
        row = self._entry(parent, row, "Num clients", self.num_clients_var)
        row = self._entry(parent, row, "Sample rate", self.sample_rate_var)
        row = self._entry(parent, row, "Rounds", self.rounds_var)
        row = self._entry(parent, row, "Local epochs", self.local_epochs_var)
        row = self._entry(parent, row, "Batch size", self.batch_size_var)
        row = self._entry(parent, row, "Server learning rate", self.server_lr_var)

        row = self._section(parent, row, "Optimizer")
        row = self._entry(parent, row, "Learning rate", self.optimizer_lr_var)
        row = self._entry(parent, row, "Momentum", self.momentum_var)
        row = self._entry(parent, row, "Weight decay", self.weight_decay_var)

        row = self._section(parent, row, "Algorithm and DP")
        row = self._combobox(parent, row, "Algorithm", self.algorithm_var, list(SUPPORTED_ALGORITHMS) + ["all"])
        row = self._entry(parent, row, "FedProx mu", self.mu_var)
        row = self._checkbox(parent, row, "Enable DP", self.dp_enabled_var)
        row = self._entry(parent, row, "Max grad norm", self.max_grad_norm_var)
        row = self._entry(parent, row, "Noise multiplier", self.noise_var)
        row = self._entry(parent, row, "Target delta", self.delta_var)

        row = self._section(parent, row, "Evaluation")
        row = self._entry(parent, row, "Eval batch size", self.eval_batch_size_var)

        actions = ttk.Frame(parent)
        actions.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Run experiment", command=self.run_experiment).grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="Open results folder", command=self.open_results_folder).grid(row=0, column=1, padx=8)
        ttk.Button(actions, text="Clear log", command=self.clear_log).grid(row=0, column=2)

    def _build_output_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)
        parent.rowconfigure(5, weight=1)

        ttk.Label(parent, text="Run details", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.command_var = tk.StringVar(value="Command will appear here before each run.")
        ttk.Label(parent, textvariable=self.command_var, wraplength=560, justify="left").grid(
            row=1, column=0, sticky="ew", pady=(6, 12)
        )

        self.info_text = tk.Text(parent, height=10, wrap="word")
        self.info_text.grid(row=2, column=0, sticky="nsew")
        self.info_text.configure(state="disabled")

        ttk.Label(parent, text="Live logs", font=("Segoe UI", 11, "bold")).grid(row=3, column=0, sticky="sw", pady=(12, 6))
        self.log_text = tk.Text(parent, wrap="word")
        self.log_text.grid(row=4, column=0, sticky="nsew")

        controls = ttk.Frame(parent)
        controls.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        controls.columnconfigure(0, weight=1)
        ttk.Button(controls, text="Refresh summary", command=self.refresh_summary).grid(row=0, column=0, sticky="w")
        ttk.Button(controls, text="Stop run", command=self.stop_run).grid(row=0, column=1, padx=8)

        ttk.Label(parent, text="Summary markdown", font=("Segoe UI", 11, "bold")).grid(row=6, column=0, sticky="sw", pady=(12, 6))
        self.summary_text = tk.Text(parent, height=11, wrap="word")
        self.summary_text.grid(row=7, column=0, sticky="nsew")

    def _section(self, parent: ttk.Frame, row: int, title: str) -> int:
        ttk.Label(parent, text=title, font=("Segoe UI", 11, "bold")).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(12, 2)
        )
        return row + 1

    def _entry(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=3)
        return row + 1

    def _combobox(self, parent: ttk.Frame, row: int, label: str, variable: tk.StringVar, values: List[str]) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        box = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        box.grid(row=row, column=1, sticky="ew", pady=3)
        return row + 1

    def _checkbox(self, parent: ttk.Frame, row: int, label: str, variable: tk.BooleanVar) -> int:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3, padx=(0, 8))
        ttk.Checkbutton(parent, variable=variable).grid(row=row, column=1, sticky="w", pady=3)
        return row + 1

    def _browse_config(self) -> None:
        selected = filedialog.askopenfilename(
            title="Select config.yaml",
            filetypes=[("YAML files", "*.yaml *.yml"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.config_var.get()) or os.getcwd(),
        )
        if selected:
            self.config_var.set(selected)
            self.reload_from_config()

    def reload_from_config(self) -> None:
        config_path = self.config_var.get().strip() or self.config_path
        config = load_config(config_path)
        self.config_path = config_path

        self.results_dir_var.set(str(config["system"]["results_dir"]))
        self.device_var.set(str(config["system"]["device"]))
        self.seed_var.set(str(config["system"]["seed"]))
        self.dataset_var.set(str(config["data"]["dataset"]))
        self.partition_var.set(str(config["data"]["partition"]))
        self.alpha_var.set(str(config["data"]["alpha"]))
        self.classes_per_client_var.set(str(config["data"]["classes_per_client"]))
        self.min_partition_size_var.set(str(config["data"]["min_partition_size"]))
        self.num_clients_var.set(str(config["federated"]["num_clients"]))
        self.sample_rate_var.set(str(config["federated"]["sample_rate"]))
        self.rounds_var.set(str(config["federated"]["rounds"]))
        self.local_epochs_var.set(str(config["federated"]["local_epochs"]))
        self.batch_size_var.set(str(config["federated"]["batch_size"]))
        self.server_lr_var.set(str(config["federated"]["server_lr"]))
        self.optimizer_lr_var.set(str(config["optimizer"]["lr"]))
        self.momentum_var.set(str(config["optimizer"]["momentum"]))
        self.weight_decay_var.set(str(config["optimizer"]["weight_decay"]))
        self.algorithm_var.set(str(config["algorithm"]["name"]))
        self.mu_var.set(str(config["algorithm"]["mu"]))
        self.dp_enabled_var.set(bool(config["dp"]["enabled"]))
        self.max_grad_norm_var.set(str(config["dp"]["max_grad_norm"]))
        self.noise_var.set(str(config["dp"]["noise_multiplier"]))
        self.delta_var.set(str(config["dp"]["target_delta"]))
        self.eval_batch_size_var.set(str(config["evaluation"]["eval_batch_size"]))

        self.active_results_dir = self._resolve_results_dir()
        self._write_info(self._format_info())
        self.refresh_summary()
        self.status_var.set(f"Loaded configuration from {self.config_path}")

    def _format_info(self) -> str:
        dp_mode = "On" if self.dp_enabled_var.get() else "Off"
        return (
            f"Dataset: {self.dataset_var.get()}\n"
            f"Partition: {self.partition_var.get()} (alpha={self.alpha_var.get()}, "
            f"classes/client={self.classes_per_client_var.get()})\n"
            f"Clients: {self.num_clients_var.get()} total, sample rate {self.sample_rate_var.get()}, "
            f"rounds {self.rounds_var.get()}, local epochs {self.local_epochs_var.get()}\n"
            f"Algorithm: {self.algorithm_var.get()} | DP: {dp_mode} | Noise: {self.noise_var.get()} | "
            f"Delta: {self.delta_var.get()}\n"
            f"Device: {self.device_var.get()} | Seed: {self.seed_var.get()} | Results: {self._resolve_results_dir()}"
        )

    def _write_info(self, text: str) -> None:
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _resolve_results_dir(self) -> str:
        candidate = self.results_dir_var.get().strip() or "results"
        if os.path.isabs(candidate):
            return candidate
        return os.path.abspath(os.path.join(os.path.dirname(self.script_path), candidate))

    def _build_runtime_config(self) -> dict:
        return {
            "system": {
                "seed": int(self.seed_var.get()),
                "device": self.device_var.get(),
                "results_dir": self.results_dir_var.get().strip() or "results",
            },
            "data": {
                "dataset": self.dataset_var.get(),
                "data_root": load_config(self.config_path)["data"]["data_root"],
                "partition": self.partition_var.get(),
                "alpha": float(self.alpha_var.get()),
                "classes_per_client": int(self.classes_per_client_var.get()),
                "min_partition_size": int(self.min_partition_size_var.get()),
            },
            "federated": {
                "num_clients": int(self.num_clients_var.get()),
                "sample_rate": float(self.sample_rate_var.get()),
                "rounds": int(self.rounds_var.get()),
                "local_epochs": int(self.local_epochs_var.get()),
                "batch_size": int(self.batch_size_var.get()),
                "server_lr": float(self.server_lr_var.get()),
            },
            "optimizer": {
                "lr": float(self.optimizer_lr_var.get()),
                "momentum": float(self.momentum_var.get()),
                "weight_decay": float(self.weight_decay_var.get()),
            },
            "algorithm": {
                "name": self.algorithm_var.get(),
                "mu": float(self.mu_var.get()),
            },
            "dp": {
                "enabled": bool(self.dp_enabled_var.get()),
                "max_grad_norm": float(self.max_grad_norm_var.get()),
                "noise_multiplier": float(self.noise_var.get()),
                "target_delta": float(self.delta_var.get()),
            },
            "model": load_config(self.config_path)["model"],
            "evaluation": {
                "eval_batch_size": int(self.eval_batch_size_var.get()),
            },
        }

    def _write_runtime_config(self) -> str:
        config = self._build_runtime_config()
        os.makedirs(self._resolve_results_dir(), exist_ok=True)
        runtime_path = os.path.join(self._resolve_results_dir(), "_gui_runtime_config.yaml")
        with open(runtime_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(config, handle, sort_keys=False)
        self.runtime_config_path = runtime_path
        return runtime_path

    def _build_command(self) -> List[str]:
        runtime_path = self._write_runtime_config()
        return [sys.executable, self.script_path, "--cli", "--config", runtime_path]

    def _validate(self) -> None:
        required = {
            "alpha": self.alpha_var.get(),
            "noise": self.noise_var.get(),
            "rounds": self.rounds_var.get(),
            "seed": self.seed_var.get(),
            "num_clients": self.num_clients_var.get(),
            "sample_rate": self.sample_rate_var.get(),
            "local_epochs": self.local_epochs_var.get(),
            "batch_size": self.batch_size_var.get(),
            "server_lr": self.server_lr_var.get(),
            "optimizer_lr": self.optimizer_lr_var.get(),
            "momentum": self.momentum_var.get(),
            "weight_decay": self.weight_decay_var.get(),
            "mu": self.mu_var.get(),
            "max_grad_norm": self.max_grad_norm_var.get(),
            "delta": self.delta_var.get(),
            "eval_batch_size": self.eval_batch_size_var.get(),
        }
        for field, value in required.items():
            if not str(value).strip():
                raise ValueError(f"Missing value for {field}.")
        float(self.alpha_var.get())
        float(self.noise_var.get())
        int(self.rounds_var.get())
        int(self.seed_var.get())
        int(self.num_clients_var.get())
        float(self.sample_rate_var.get())
        int(self.local_epochs_var.get())
        int(self.batch_size_var.get())
        float(self.server_lr_var.get())
        float(self.optimizer_lr_var.get())
        float(self.momentum_var.get())
        float(self.weight_decay_var.get())
        float(self.mu_var.get())
        float(self.max_grad_norm_var.get())
        float(self.delta_var.get())
        int(self.eval_batch_size_var.get())

    def run_experiment(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Experiment running", "Please wait for the current run to finish.")
            return

        try:
            self._validate()
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc))
            return

        command = self._build_command()
        self.command_var.set(" ".join(command))
        self._write_info(self._format_info())
        self.active_results_dir = self._resolve_results_dir()
        self.clear_log()
        self.status_var.set("Experiment running...")

        def worker() -> None:
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            self.process = subprocess.Popen(
                command,
                cwd=os.path.dirname(self.script_path),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self.root.after(0, self._append_log, line)
            return_code = self.process.wait()
            self.process = None
            self.root.after(0, self._finish_run, return_code)

        threading.Thread(target=worker, daemon=True).start()

    def _append_log(self, text: str) -> None:
        self.log_text.insert("end", text)
        self.log_text.see("end")

    def _finish_run(self, return_code: int) -> None:
        self.refresh_summary()
        if return_code == 0:
            self.status_var.set("Experiment finished successfully.")
        else:
            self.status_var.set(f"Experiment failed with exit code {return_code}.")

    def clear_log(self) -> None:
        self._set_text(self.log_text, "")

    def refresh_summary(self) -> None:
        summary_path = os.path.join(self._resolve_results_dir(), "summary.md")
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as handle:
                self._set_text(self.summary_text, handle.read())
        else:
            self._set_text(
                self.summary_text,
                "No summary found yet. Run an experiment and summary.md will appear here.",
            )

    def open_results_folder(self) -> None:
        results_dir = self._resolve_results_dir()
        os.makedirs(results_dir, exist_ok=True)
        os.startfile(results_dir)

    def stop_run(self) -> None:
        if self.process is None:
            self.status_var.set("No active experiment to stop.")
            return
        self.process.terminate()
        self.status_var.set("Stopping experiment...")

    def _on_close(self) -> None:
        if self.process is not None:
            if not messagebox.askyesno("Exit", "An experiment is still running. Close the GUI anyway?"):
                return
            self.process.terminate()
        self.root.destroy()


def launch_gui(config_path: str) -> None:
    root = tk.Tk()
    root.option_add("*Font", "Segoe UI 10")
    ExperimentGUI(
        root=root,
        script_path=os.path.abspath(__file__),
        config_path=os.path.abspath(config_path),
    )
    root.mainloop()


def should_launch_gui(args: argparse.Namespace) -> bool:
    if args.cli:
        return False
    if args.gui:
        return True
    return len(sys.argv) == 1


# ---------------------------------------------------------------------- #
# Entry point                                                            #
# ---------------------------------------------------------------------- #
def main() -> None:
    args = parse_args()
    if should_launch_gui(args):
        launch_gui(args.config)
        return

    config = apply_overrides(load_config(args.config), args)

    seed = int(config["system"]["seed"])
    set_seed(seed)
    device = resolve_device(config["system"]["device"])
    results_dir = config["system"]["results_dir"]
    os.makedirs(results_dir, exist_ok=True)

    print(f"Device: {device} | Seed: {seed}")

    # --- Data ------------------------------------------------------------
    data_cfg = config["data"]
    train_set, test_set, num_classes, in_channels = get_dataset(
        data_cfg["dataset"], data_cfg["data_root"]
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
        client_dict, train_set, num_classes,
        save_path=os.path.join(results_dir, "distribution.png"),
    )
    print(f"Partition plot saved -> {dist_path}")

    # --- Run one algorithm or the full comparison matrix -----------------
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

    # --- Plots + Markdown summary ----------------------------------------
    run_csvs = {s["algorithm"]: s["csv_path"] for s in summaries}
    plots = generate_all_plots(
        results_dir, run_csvs=run_csvs, dp_enabled=bool(config["dp"]["enabled"])
    )
    for p in plots:
        print(f"Plot saved -> {p}")

    summary_md = build_summary_table(summaries, config)
    summary_path = os.path.join(results_dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary_md + "\n")

    print("\n" + summary_md)
    print(f"\nSummary written -> {summary_path}")


if __name__ == "__main__":
    main()
