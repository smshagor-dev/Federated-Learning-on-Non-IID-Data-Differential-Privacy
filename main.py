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
import math
import os
import random
import subprocess
import sys
import time
import threading
from typing import Dict, List

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np
import torch
import yaml
from torch.utils.data import DataLoader
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

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
from utils.logger import CSVLogger, discover_run_csvs, generate_all_plots, read_run_csv
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
    """Research dashboard for configuring and monitoring FL experiments."""

    def __init__(self, root: tk.Tk, script_path: str, config_path: str) -> None:
        self.root = root
        self.script_path = script_path
        self.config_path = config_path
        self.base_config = load_config(config_path)
        self.process: subprocess.Popen[str] | None = None
        self.active_results_dir = "results"
        self.runtime_config_path: str | None = None
        self.live_refresh_job: str | None = None

        self.root.title("Federated DP Research Studio")
        self.root.geometry("1460x920")
        self.root.minsize(1180, 760)

        self._configure_theme()
        self._init_state()
        self._build_layout()
        self.reload_from_config()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_theme(self) -> None:
        self.bg = "#f4f7fb"
        self.surface = "#ffffff"
        self.surface_alt = "#eef3f8"
        self.ink = "#16202b"
        self.muted = "#607284"
        self.accent = "#0f766e"
        self.accent_2 = "#d97706"
        self.border = "#d7e0ea"
        self.success = "#0b8f55"
        self.danger = "#c2410c"
        self.shadow = "#e9eef5"

        style = ttk.Style()
        style.theme_use("clam")
        self.root.configure(bg=self.bg)

        default_font = tkfont.nametofont("TkDefaultFont")
        default_font.configure(family="Segoe UI", size=10)
        text_font = tkfont.nametofont("TkTextFont")
        text_font.configure(family="Consolas", size=10)

        self.title_font = tkfont.Font(family="Segoe UI Semibold", size=20)
        self.hero_font = tkfont.Font(family="Georgia", size=11)
        self.section_font = tkfont.Font(family="Segoe UI Semibold", size=11)
        self.metric_value_font = tkfont.Font(family="Segoe UI Semibold", size=18)
        self.metric_label_font = tkfont.Font(family="Segoe UI", size=9)

        style.configure(".", background=self.bg, foreground=self.ink)
        style.configure("App.TFrame", background=self.bg)
        style.configure("Card.TFrame", background=self.surface, relief="flat")
        style.configure("Subtle.TFrame", background=self.surface_alt, relief="flat")
        style.configure("HeroTitle.TLabel", background=self.bg, foreground=self.ink, font=self.title_font)
        style.configure("HeroBody.TLabel", background=self.bg, foreground=self.muted, font=self.hero_font)
        style.configure("Section.TLabel", background=self.surface, foreground=self.ink, font=self.section_font)
        style.configure("Muted.TLabel", background=self.surface, foreground=self.muted)
        style.configure("MetricLabel.TLabel", background=self.surface, foreground=self.muted, font=self.metric_label_font)
        style.configure("MetricValue.TLabel", background=self.surface, foreground=self.ink, font=self.metric_value_font)
        style.configure("Pill.TLabel", background=self.surface_alt, foreground=self.accent, padding=(10, 5))
        style.configure("Primary.TButton", background=self.accent, foreground="white", borderwidth=0, padding=(12, 8))
        style.map("Primary.TButton", background=[("active", "#0b5f5a")])
        style.configure("Secondary.TButton", background=self.surface_alt, foreground=self.ink, bordercolor=self.border, padding=(12, 8))
        style.map("Secondary.TButton", background=[("active", "#dbe7f1")])
        style.configure("App.TNotebook", background=self.bg, borderwidth=0)
        style.configure("App.TNotebook.Tab", padding=(16, 8), font=("Segoe UI", 10, "bold"))
        style.map("App.TNotebook.Tab", background=[("selected", self.surface)], foreground=[("selected", self.ink)])
        style.configure("App.Horizontal.TProgressbar", troughcolor=self.surface_alt, background=self.accent, bordercolor=self.surface_alt, lightcolor=self.accent, darkcolor=self.accent)
        style.configure("App.Treeview", rowheight=28, fieldbackground=self.surface, background=self.surface, foreground=self.ink, bordercolor=self.border)
        style.configure("App.Treeview.Heading", background=self.surface_alt, foreground=self.ink, font=("Segoe UI", 10, "bold"))

    def _init_state(self) -> None:
        self.status_var = tk.StringVar(value="Studio ready")
        self.command_var = tk.StringVar(value="Command preview will appear before each run.")
        self.run_state_var = tk.StringVar(value="Idle")

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

        self.metric_vars = {
            "algorithms": tk.StringVar(value="0"),
            "best_acc": tk.StringVar(value="--"),
            "epsilon": tk.StringVar(value="--"),
            "last_round": tk.StringVar(value="--"),
        }

        self.summary_cards = {
            "dataset": tk.StringVar(value="--"),
            "federated": tk.StringVar(value="--"),
            "privacy": tk.StringVar(value="--"),
            "results": tk.StringVar(value="--"),
        }

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        hero = ttk.Frame(self.root, style="App.TFrame", padding=(26, 20, 26, 12))
        hero.grid(row=0, column=0, sticky="ew")
        hero.columnconfigure(0, weight=1)
        hero.columnconfigure(1, weight=0)

        hero_left = ttk.Frame(hero, style="App.TFrame")
        hero_left.grid(row=0, column=0, sticky="w")
        ttk.Label(hero_left, text="Federated DP Research Studio", style="HeroTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            hero_left,
            text="A richer desktop dashboard for non-IID federated learning experiments, privacy diagnostics, and artifact review.",
            style="HeroBody.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 0))

        hero_right = ttk.Frame(hero, style="App.TFrame")
        hero_right.grid(row=0, column=1, sticky="e")
        ttk.Label(hero_right, textvariable=self.run_state_var, style="Pill.TLabel").grid(row=0, column=0, sticky="e")
        ttk.Label(hero_right, textvariable=self.status_var, style="HeroBody.TLabel").grid(row=1, column=0, sticky="e", pady=(8, 0))

        body = ttk.Panedwindow(self.root, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 10))

        left_shell = ttk.Frame(body, style="App.TFrame", padding=(0, 0, 10, 0))
        right_shell = ttk.Frame(body, style="App.TFrame", padding=(10, 0, 0, 0))
        body.add(left_shell, weight=34)
        body.add(right_shell, weight=66)

        self._build_control_panel(left_shell)
        self._build_dashboard(right_shell)

        footer = ttk.Frame(self.root, style="App.TFrame", padding=(26, 0, 26, 18))
        footer.grid(row=2, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", style="App.Horizontal.TProgressbar")
        self.progress.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(footer, textvariable=self.command_var, style="HeroBody.TLabel").grid(row=1, column=0, sticky="w")

    def _build_control_panel(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        card = ttk.Frame(parent, style="Card.TFrame", padding=0)
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        header = ttk.Frame(card, style="Card.TFrame", padding=(18, 18, 18, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Experiment Controls", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Scrollable configuration panel with grouped settings and safer runtime validation.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 0))

        scroller = ttk.Frame(card, style="Card.TFrame")
        scroller.grid(row=1, column=0, sticky="nsew")
        scroller.columnconfigure(0, weight=1)
        scroller.rowconfigure(0, weight=1)

        self.form_canvas = tk.Canvas(scroller, bg=self.surface, highlightthickness=0, bd=0)
        self.form_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(scroller, orient="vertical", command=self.form_canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.form_canvas.configure(yscrollcommand=scrollbar.set)

        self.form_inner = ttk.Frame(self.form_canvas, style="Card.TFrame", padding=(18, 8, 18, 18))
        self.form_window = self.form_canvas.create_window((0, 0), window=self.form_inner, anchor="nw")
        self.form_inner.bind("<Configure>", self._sync_form_canvas)
        self.form_canvas.bind("<Configure>", self._resize_form_canvas)
        self.form_canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self._build_form_fields(self.form_inner)

    def _build_form_fields(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)

        self._build_config_card(parent).pack(fill="x", pady=(0, 14))
        self._build_summary_strip(parent).pack(fill="x", pady=(0, 14))
        self._build_section_card(parent, "System", [
            ("Results directory", self.results_dir_var, "entry"),
            ("Device", self.device_var, "combo", ["auto", "cpu", "cuda"]),
            ("Seed", self.seed_var, "entry"),
        ]).pack(fill="x", pady=(0, 14))
        self._build_section_card(parent, "Data Topology", [
            ("Dataset", self.dataset_var, "combo", ["CIFAR10", "MNIST"]),
            ("Partition", self.partition_var, "combo", ["dirichlet", "pathological"]),
            ("Dirichlet alpha", self.alpha_var, "entry"),
            ("Classes per client", self.classes_per_client_var, "entry"),
            ("Minimum partition size", self.min_partition_size_var, "entry"),
        ]).pack(fill="x", pady=(0, 14))
        self._build_section_card(parent, "Federated Loop", [
            ("Number of clients", self.num_clients_var, "entry"),
            ("Client sample rate", self.sample_rate_var, "entry"),
            ("Communication rounds", self.rounds_var, "entry"),
            ("Local epochs", self.local_epochs_var, "entry"),
            ("Batch size", self.batch_size_var, "entry"),
            ("Server learning rate", self.server_lr_var, "entry"),
        ]).pack(fill="x", pady=(0, 14))
        self._build_section_card(parent, "Optimizer", [
            ("Learning rate", self.optimizer_lr_var, "entry"),
            ("Momentum", self.momentum_var, "entry"),
            ("Weight decay", self.weight_decay_var, "entry"),
        ]).pack(fill="x", pady=(0, 14))
        self._build_section_card(parent, "Algorithm and Privacy", [
            ("Algorithm", self.algorithm_var, "combo", list(SUPPORTED_ALGORITHMS) + ["all"]),
            ("FedProx mu", self.mu_var, "entry"),
            ("Enable DP", self.dp_enabled_var, "check"),
            ("Max grad norm", self.max_grad_norm_var, "entry"),
            ("Noise multiplier", self.noise_var, "entry"),
            ("Target delta", self.delta_var, "entry"),
        ]).pack(fill="x", pady=(0, 14))
        self._build_section_card(parent, "Evaluation", [
            ("Eval batch size", self.eval_batch_size_var, "entry"),
        ]).pack(fill="x", pady=(0, 14))
        self._build_action_card(parent).pack(fill="x")

    def _build_config_card(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Subtle.TFrame", padding=14)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Config source", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Switch configs, reload defaults, and preserve a GUI-generated runtime YAML per run.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 10))
        row = ttk.Frame(frame, style="Subtle.TFrame")
        row.grid(row=2, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self.config_var).grid(row=0, column=0, sticky="ew")
        ttk.Button(row, text="Browse", style="Secondary.TButton", command=self._browse_config).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(row, text="Reload", style="Secondary.TButton", command=self.reload_from_config).grid(row=0, column=2, padx=(8, 0))
        return frame

    def _build_summary_strip(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame")
        for column in range(2):
            frame.columnconfigure(column, weight=1)
        cards = [
            ("Dataset", self.summary_cards["dataset"]),
            ("Federated", self.summary_cards["federated"]),
            ("Privacy", self.summary_cards["privacy"]),
            ("Results", self.summary_cards["results"]),
        ]
        for idx, (label, variable) in enumerate(cards):
            card = ttk.Frame(frame, style="Subtle.TFrame", padding=12)
            card.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=(0 if idx % 2 == 0 else 8, 0), pady=(0, 8))
            ttk.Label(card, text=label, style="MetricLabel.TLabel").grid(row=0, column=0, sticky="w")
            ttk.Label(card, textvariable=variable, style="Muted.TLabel", wraplength=220, justify="left").grid(row=1, column=0, sticky="w", pady=(6, 0))
        return frame

    def _build_section_card(self, parent: ttk.Frame, title: str, fields: list[tuple]) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Card.TFrame", padding=14)
        frame.columnconfigure(1, weight=1)
        ttk.Label(frame, text=title, style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Separator(frame, orient="horizontal").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 12))

        row = 2
        for field in fields:
            label = field[0]
            variable = field[1]
            kind = field[2]
            values = field[3] if len(field) > 3 else None
            ttk.Label(frame, text=label, style="Muted.TLabel").grid(row=row, column=0, sticky="w", pady=6, padx=(0, 12))
            if kind == "combo":
                widget = ttk.Combobox(frame, textvariable=variable, values=values, state="readonly")
                widget.grid(row=row, column=1, sticky="ew", pady=6)
            elif kind == "check":
                ttk.Checkbutton(frame, variable=variable).grid(row=row, column=1, sticky="w", pady=6)
            else:
                ttk.Entry(frame, textvariable=variable).grid(row=row, column=1, sticky="ew", pady=6)
            row += 1
        return frame

    def _build_action_card(self, parent: ttk.Frame) -> ttk.Frame:
        frame = ttk.Frame(parent, style="Subtle.TFrame", padding=14)
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="Run Actions", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(frame, text="Launch, stop, refresh, and inspect generated artifacts without leaving the studio.", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 12))

        buttons = ttk.Frame(frame, style="Subtle.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")
        for column in range(2):
            buttons.columnconfigure(column, weight=1)
        ttk.Button(buttons, text="Run Experiment", style="Primary.TButton", command=self.run_experiment).grid(row=0, column=0, sticky="ew", padx=(0, 6), pady=(0, 8))
        ttk.Button(buttons, text="Stop Run", style="Secondary.TButton", command=self.stop_run).grid(row=0, column=1, sticky="ew", padx=(6, 0), pady=(0, 8))
        ttk.Button(buttons, text="Open Results Folder", style="Secondary.TButton", command=self.open_results_folder).grid(row=1, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(buttons, text="Refresh Dashboard", style="Secondary.TButton", command=self.refresh_outputs).grid(row=1, column=1, sticky="ew", padx=(6, 0))
        return frame

    def _build_dashboard(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        metrics_frame = ttk.Frame(parent, style="App.TFrame")
        metrics_frame.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        for column in range(4):
            metrics_frame.columnconfigure(column, weight=1)
        self._build_metric_card(metrics_frame, 0, "Algorithms Logged", self.metric_vars["algorithms"], "Detected from result CSV files")
        self._build_metric_card(metrics_frame, 1, "Best Accuracy", self.metric_vars["best_acc"], "Highest test accuracy observed")
        self._build_metric_card(metrics_frame, 2, "Latest Epsilon", self.metric_vars["epsilon"], "Finite privacy budget if DP is enabled")
        self._build_metric_card(metrics_frame, 3, "Latest Round", self.metric_vars["last_round"], "Most recent communication round")

        notebook = ttk.Notebook(parent, style="App.TNotebook")
        notebook.grid(row=1, column=0, sticky="nsew")

        dashboard_tab = ttk.Frame(notebook, style="App.TFrame", padding=(0, 8, 0, 0))
        logs_tab = ttk.Frame(notebook, style="App.TFrame", padding=(0, 8, 0, 0))
        summary_tab = ttk.Frame(notebook, style="App.TFrame", padding=(0, 8, 0, 0))
        notebook.add(dashboard_tab, text="Dashboard")
        notebook.add(logs_tab, text="Logs")
        notebook.add(summary_tab, text="Summary")

        self._build_dashboard_tab(dashboard_tab)
        self._build_logs_tab(logs_tab)
        self._build_summary_tab(summary_tab)

    def _build_metric_card(self, parent: ttk.Frame, column: int, title: str, variable: tk.StringVar, caption: str) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
        ttk.Label(card, text=title, style="MetricLabel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, textvariable=variable, style="MetricValue.TLabel").grid(row=1, column=0, sticky="w", pady=(8, 4))
        ttk.Label(card, text=caption, style="Muted.TLabel", wraplength=220, justify="left").grid(row=2, column=0, sticky="w")

    def _build_dashboard_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=2)
        parent.rowconfigure(0, weight=1)

        chart_card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        chart_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        chart_card.columnconfigure(0, weight=1)
        chart_card.rowconfigure(1, weight=1)
        ttk.Label(chart_card, text="Experiment Analytics", style="Section.TLabel").grid(row=0, column=0, sticky="w")

        self.chart_figure = Figure(figsize=(9, 7), dpi=100, facecolor=self.surface)
        self.chart_axes = [
            self.chart_figure.add_subplot(221),
            self.chart_figure.add_subplot(222),
            self.chart_figure.add_subplot(223),
            self.chart_figure.add_subplot(224),
        ]
        self.chart_canvas = FigureCanvasTkAgg(self.chart_figure, master=chart_card)
        self.chart_canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", pady=(10, 0))

        side = ttk.Frame(parent, style="App.TFrame")
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)

        insight = ttk.Frame(side, style="Card.TFrame", padding=16)
        insight.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(insight, text="Run Snapshot", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.info_text = tk.Text(insight, height=9, wrap="word", relief="flat", bg=self.surface, fg=self.ink)
        self.info_text.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self.info_text.configure(state="disabled")

        artifact_card = ttk.Frame(side, style="Card.TFrame", padding=16)
        artifact_card.grid(row=1, column=0, sticky="nsew")
        artifact_card.columnconfigure(0, weight=1)
        artifact_card.rowconfigure(1, weight=1)
        ttk.Label(artifact_card, text="Artifacts", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        self.artifact_tree = ttk.Treeview(
            artifact_card,
            style="App.Treeview",
            columns=("type", "name"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        self.artifact_tree.heading("type", text="Type")
        self.artifact_tree.heading("name", text="File")
        self.artifact_tree.column("type", width=90, stretch=False)
        self.artifact_tree.column("name", width=260, stretch=True)
        self.artifact_tree.grid(row=1, column=0, sticky="nsew", pady=(10, 10))
        self.artifact_tree.bind("<Double-1>", lambda _event: self.open_selected_artifact())

        actions = ttk.Frame(artifact_card, style="Card.TFrame")
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(0, weight=1)
        ttk.Button(actions, text="Open Selected", style="Secondary.TButton", command=self.open_selected_artifact).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        ttk.Button(actions, text="Open Results Folder", style="Secondary.TButton", command=self.open_results_folder).grid(row=0, column=1, sticky="ew")

    def _build_logs_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        header = ttk.Frame(card, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Live Console", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Clear Log", style="Secondary.TButton", command=self.clear_log).grid(row=0, column=1, sticky="e")

        holder = ttk.Frame(card, style="Card.TFrame")
        holder.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.log_text = tk.Text(holder, wrap="word", bg="#101820", fg="#e6eef7", insertbackground="#e6eef7", relief="flat")
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=scroll.set)

    def _build_summary_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        card = ttk.Frame(parent, style="Card.TFrame", padding=16)
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)
        header = ttk.Frame(card, style="Card.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Markdown Summary", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(header, text="Refresh Summary", style="Secondary.TButton", command=self.refresh_summary).grid(row=0, column=1, sticky="e")

        holder = ttk.Frame(card, style="Card.TFrame")
        holder.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        self.summary_text = tk.Text(holder, wrap="word", bg=self.surface, fg=self.ink, relief="flat")
        self.summary_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(holder, orient="vertical", command=self.summary_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.summary_text.configure(yscrollcommand=scroll.set)

    def _sync_form_canvas(self, _event: tk.Event) -> None:
        self.form_canvas.configure(scrollregion=self.form_canvas.bbox("all"))

    def _resize_form_canvas(self, event: tk.Event) -> None:
        self.form_canvas.itemconfigure(self.form_window, width=event.width)

    def _on_mousewheel(self, event: tk.Event) -> None:
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        if widget is None:
            return
        if str(widget).startswith(str(self.form_canvas)) or str(widget).startswith(str(self.form_inner)):
            self.form_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

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
        self.base_config = load_config(config_path)
        self.config_path = config_path

        self.results_dir_var.set(str(self.base_config["system"]["results_dir"]))
        self.device_var.set(str(self.base_config["system"]["device"]))
        self.seed_var.set(str(self.base_config["system"]["seed"]))
        self.dataset_var.set(str(self.base_config["data"]["dataset"]))
        self.partition_var.set(str(self.base_config["data"]["partition"]))
        self.alpha_var.set(str(self.base_config["data"]["alpha"]))
        self.classes_per_client_var.set(str(self.base_config["data"]["classes_per_client"]))
        self.min_partition_size_var.set(str(self.base_config["data"]["min_partition_size"]))
        self.num_clients_var.set(str(self.base_config["federated"]["num_clients"]))
        self.sample_rate_var.set(str(self.base_config["federated"]["sample_rate"]))
        self.rounds_var.set(str(self.base_config["federated"]["rounds"]))
        self.local_epochs_var.set(str(self.base_config["federated"]["local_epochs"]))
        self.batch_size_var.set(str(self.base_config["federated"]["batch_size"]))
        self.server_lr_var.set(str(self.base_config["federated"]["server_lr"]))
        self.optimizer_lr_var.set(str(self.base_config["optimizer"]["lr"]))
        self.momentum_var.set(str(self.base_config["optimizer"]["momentum"]))
        self.weight_decay_var.set(str(self.base_config["optimizer"]["weight_decay"]))
        self.algorithm_var.set(str(self.base_config["algorithm"]["name"]))
        self.mu_var.set(str(self.base_config["algorithm"]["mu"]))
        self.dp_enabled_var.set(bool(self.base_config["dp"]["enabled"]))
        self.max_grad_norm_var.set(str(self.base_config["dp"]["max_grad_norm"]))
        self.noise_var.set(str(self.base_config["dp"]["noise_multiplier"]))
        self.delta_var.set(str(self.base_config["dp"]["target_delta"]))
        self.eval_batch_size_var.set(str(self.base_config["evaluation"]["eval_batch_size"]))

        self.active_results_dir = self._resolve_results_dir()
        self._update_summary_cards()
        self._write_info(self._format_info())
        self.refresh_outputs()
        self.status_var.set(f"Loaded configuration from {self.config_path}")

    def _update_summary_cards(self) -> None:
        self.summary_cards["dataset"].set(
            f"{self.dataset_var.get()} | {self.partition_var.get()} | alpha {self.alpha_var.get()}"
        )
        self.summary_cards["federated"].set(
            f"{self.num_clients_var.get()} clients | q={self.sample_rate_var.get()} | {self.rounds_var.get()} rounds"
        )
        self.summary_cards["privacy"].set(
            f"DP {'on' if self.dp_enabled_var.get() else 'off'} | sigma {self.noise_var.get()} | delta {self.delta_var.get()}"
        )
        self.summary_cards["results"].set(self._resolve_results_dir())

    def _format_info(self) -> str:
        dp_mode = "Enabled" if self.dp_enabled_var.get() else "Disabled"
        return (
            f"Dataset: {self.dataset_var.get()}\n"
            f"Partitioning: {self.partition_var.get()} | alpha={self.alpha_var.get()} | classes/client={self.classes_per_client_var.get()}\n"
            f"Federated loop: {self.num_clients_var.get()} clients, sample rate {self.sample_rate_var.get()}, "
            f"{self.rounds_var.get()} rounds, {self.local_epochs_var.get()} local epochs\n"
            f"Optimization: lr={self.optimizer_lr_var.get()}, momentum={self.momentum_var.get()}, "
            f"server lr={self.server_lr_var.get()}, batch={self.batch_size_var.get()}\n"
            f"Privacy: {dp_mode}, C={self.max_grad_norm_var.get()}, sigma={self.noise_var.get()}, delta={self.delta_var.get()}\n"
            f"Execution: device={self.device_var.get()}, seed={self.seed_var.get()}\n"
            f"Artifacts: {self._resolve_results_dir()}"
        )

    def _write_info(self, text: str) -> None:
        self.info_text.configure(state="normal")
        self.info_text.delete("1.0", "end")
        self.info_text.insert("1.0", text)
        self.info_text.configure(state="disabled")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="normal")

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
                "data_root": self.base_config["data"]["data_root"],
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
            "model": self.base_config["model"],
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
            "min_partition_size": self.min_partition_size_var.get(),
            "classes_per_client": self.classes_per_client_var.get(),
        }
        for field, value in required.items():
            if not str(value).strip():
                raise ValueError(f"Missing value for {field}.")
        if float(self.sample_rate_var.get()) <= 0 or float(self.sample_rate_var.get()) > 1:
            raise ValueError("Sample rate must be between 0 and 1.")
        if int(self.rounds_var.get()) <= 0:
            raise ValueError("Rounds must be greater than zero.")
        if int(self.num_clients_var.get()) <= 0:
            raise ValueError("Number of clients must be greater than zero.")
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
        int(self.min_partition_size_var.get())
        int(self.classes_per_client_var.get())

    def run_experiment(self) -> None:
        if self.process is not None:
            messagebox.showinfo("Experiment running", "Please wait for the current run to finish.")
            return

        try:
            self._validate()
        except Exception as exc:
            messagebox.showerror("Invalid input", str(exc))
            return

        self._update_summary_cards()
        self._write_info(self._format_info())
        self.active_results_dir = self._resolve_results_dir()
        command = self._build_command()
        self.command_var.set(" ".join(command))
        self.clear_log()
        self.run_state_var.set("Running")
        self.status_var.set("Experiment running...")
        self.progress.start(10)
        self._schedule_live_refresh()

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
        self.progress.stop()
        self.run_state_var.set("Completed" if return_code == 0 else "Failed")
        self.refresh_outputs()
        if return_code == 0:
            self.status_var.set("Experiment finished successfully.")
        else:
            self.status_var.set(f"Experiment failed with exit code {return_code}.")

    def _schedule_live_refresh(self) -> None:
        if self.live_refresh_job is not None:
            self.root.after_cancel(self.live_refresh_job)
        self.live_refresh_job = self.root.after(1800, self._refresh_live_outputs)

    def _refresh_live_outputs(self) -> None:
        self.refresh_outputs()
        if self.process is not None:
            self._schedule_live_refresh()
        else:
            self.live_refresh_job = None

    def refresh_outputs(self) -> None:
        self.refresh_summary()
        self._refresh_artifacts()
        self._refresh_metrics_and_charts()

    def clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    def refresh_summary(self) -> None:
        summary_path = os.path.join(self._resolve_results_dir(), "summary.md")
        if os.path.exists(summary_path):
            with open(summary_path, "r", encoding="utf-8") as handle:
                self._set_text(self.summary_text, handle.read())
        else:
            self._set_text(self.summary_text, "No summary found yet. Run an experiment and summary.md will appear here.")

    def _refresh_artifacts(self) -> None:
        for item in self.artifact_tree.get_children():
            self.artifact_tree.delete(item)
        results_dir = self._resolve_results_dir()
        os.makedirs(results_dir, exist_ok=True)
        for name in sorted(os.listdir(results_dir)):
            path = os.path.join(results_dir, name)
            if os.path.isdir(path):
                continue
            ext = os.path.splitext(name)[1].lower()
            kind = "Plot" if ext == ".png" else "CSV" if ext == ".csv" else "Markdown" if ext == ".md" else "YAML" if ext in {".yml", ".yaml"} else "File"
            self.artifact_tree.insert("", "end", values=(kind, name), tags=(path,))

    def open_selected_artifact(self) -> None:
        selected = self.artifact_tree.selection()
        if not selected:
            self.status_var.set("Select an artifact to open.")
            return
        values = self.artifact_tree.item(selected[0], "values")
        path = os.path.join(self._resolve_results_dir(), values[1])
        if os.path.exists(path):
            os.startfile(path)

    def _refresh_metrics_and_charts(self) -> None:
        run_csvs = discover_run_csvs(self._resolve_results_dir())
        self.metric_vars["algorithms"].set(str(len(run_csvs)))
        if not run_csvs:
            self.metric_vars["best_acc"].set("--")
            self.metric_vars["epsilon"].set("--")
            self.metric_vars["last_round"].set("--")
            self._draw_empty_chart_state()
            return

        best_acc = 0.0
        latest_epsilon = None
        latest_round = 0
        data_map = {}
        for algo, path in run_csvs.items():
            data = read_run_csv(path)
            data_map[algo] = data
            if data["test_acc"]:
                best_acc = max(best_acc, max(data["test_acc"]))
            if data["round"]:
                latest_round = max(latest_round, int(max(data["round"])))
            eps_values = [e for e in data["epsilon"] if math.isfinite(e)]
            if eps_values:
                latest_epsilon = max(eps_values) if latest_epsilon is None else max(latest_epsilon, max(eps_values))

        self.metric_vars["best_acc"].set(f"{best_acc * 100:.2f}%")
        self.metric_vars["epsilon"].set("--" if latest_epsilon is None else f"{latest_epsilon:.2f}")
        self.metric_vars["last_round"].set(str(latest_round))
        self._draw_charts(data_map)

    def _draw_empty_chart_state(self) -> None:
        for ax in self.chart_axes:
            ax.clear()
            ax.set_facecolor(self.surface)
            ax.text(0.5, 0.5, "Run an experiment to populate charts", ha="center", va="center", transform=ax.transAxes, color=self.muted)
            ax.set_xticks([])
            ax.set_yticks([])
        self.chart_figure.tight_layout(pad=2.0)
        self.chart_canvas.draw_idle()

    def _draw_charts(self, data_map: Dict[str, dict]) -> None:
        palette = {
            "fedavg": "#2563eb",
            "fedprox": "#dc2626",
            "scaffold": "#0891b2",
        }
        titles = [
            ("Accuracy vs Rounds", "round", "test_acc", "Accuracy (%)"),
            ("Loss vs Rounds", "round", "test_loss", "Loss"),
            ("Privacy Budget", "round", "epsilon", "Epsilon"),
            ("Client Drift", "round", "client_drift", "Drift"),
        ]

        for ax, (title, x_key, y_key, y_label) in zip(self.chart_axes, titles):
            ax.clear()
            ax.set_facecolor(self.surface)
            for algo, data in sorted(data_map.items()):
                xs = data[x_key]
                ys = data[y_key]
                if y_key == "test_acc":
                    ys = [v * 100.0 for v in ys]
                if y_key == "epsilon":
                    filtered = [(x, y) for x, y in zip(xs, ys) if math.isfinite(y)]
                    if not filtered:
                        continue
                    xs, ys = zip(*filtered)
                color = palette.get(algo, "#475569")
                ax.plot(xs, ys, linewidth=2.1, color=color, label=algo.upper())
            ax.set_title(title, color=self.ink, fontsize=11, pad=10)
            ax.set_xlabel("Round", color=self.muted)
            ax.set_ylabel(y_label, color=self.muted)
            ax.grid(alpha=0.22)
            ax.tick_params(colors=self.muted, labelsize=8)
            for spine in ax.spines.values():
                spine.set_color(self.border)
        self.chart_axes[0].legend(frameon=False, fontsize=8, loc="best")
        self.chart_figure.tight_layout(pad=2.0)
        self.chart_canvas.draw_idle()

    def open_results_folder(self) -> None:
        results_dir = self._resolve_results_dir()
        os.makedirs(results_dir, exist_ok=True)
        os.startfile(results_dir)

    def stop_run(self) -> None:
        if self.process is None:
            self.status_var.set("No active experiment to stop.")
            return
        self.process.terminate()
        self.progress.stop()
        self.run_state_var.set("Stopping")
        self.status_var.set("Stopping experiment...")

    def _on_close(self) -> None:
        if self.live_refresh_job is not None:
            self.root.after_cancel(self.live_refresh_job)
        if self.process is not None:
            if not messagebox.askyesno("Exit", "An experiment is still running. Close the GUI anyway?"):
                return
            self.process.terminate()
        self.root.destroy()


def launch_gui(config_path: str) -> None:
    root = tk.Tk()
    default_font = tkfont.nametofont("TkDefaultFont")
    default_font.configure(family="Segoe UI", size=10)
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
