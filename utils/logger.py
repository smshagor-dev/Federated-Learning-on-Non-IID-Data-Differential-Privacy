"""CSV experiment logging and plot generation for the root runtime."""

from __future__ import annotations

import csv
import glob
import math
import os
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="whitegrid", context="paper", font_scale=1.2)

FIELDNAMES = [
    "round",
    "algorithm",
    "cohort_size",
    "participation_rate",
    "test_acc",
    "test_loss",
    "epsilon",
    "weight_variance",
    "raw_client_drift",
    "clipped_client_drift",
    "mean_unclipped_update_norm",
    "mean_clipping_factor",
    "fraction_clients_clipped",
    "aggregate_noise_norm",
    "avg_client_loss",
]

_ALGO_COLORS = {
    "fedavg": "#1f77b4",
    "fedprox": "#d62728",
    "scaffold": "#2ca02c",
}
_ALGO_LABELS = {
    "fedavg": "FedAvg",
    "fedprox": "FedProx",
    "scaffold": "SCAFFOLD",
}


class CSVLogger:
    def __init__(self, csv_path: str) -> None:
        self.csv_path = csv_path
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        self._file = open(csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDNAMES)
        self._writer.writeheader()
        self._file.flush()

    def log(self, row: dict) -> None:
        clean = {key: row.get(key, "") for key in FIELDNAMES}
        self._writer.writerow(clean)
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "CSVLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


def read_run_csv(csv_path: str) -> Dict[str, List[float]]:
    columns: Dict[str, List] = {key: [] for key in FIELDNAMES}
    with open(csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            for key in FIELDNAMES:
                value = row.get(key, "")
                if key == "algorithm":
                    columns[key].append(value)
                    continue
                try:
                    columns[key].append(float(value))
                except (TypeError, ValueError):
                    columns[key].append(float("nan"))
    return columns


def discover_run_csvs(results_dir: str) -> Dict[str, str]:
    runs: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "run_*.csv"))):
        data = read_run_csv(path)
        if data["algorithm"]:
            runs[data["algorithm"][0].lower()] = path
    return runs


def _style(algorithm: str) -> tuple[str, str]:
    algo = algorithm.lower()
    return _ALGO_COLORS.get(algo, "#7f7f7f"), _ALGO_LABELS.get(algo, algo)


def plot_accuracy_vs_rounds(
    run_csvs: Dict[str, str], save_path: str, dp_enabled: bool = True
) -> str:
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=200)
    for algorithm, path in sorted(run_csvs.items()):
        data = read_run_csv(path)
        color, label = _style(algorithm)
        ax.plot(
            data["round"],
            [value * 100.0 for value in data["test_acc"]],
            color=color,
            label=label,
            linewidth=1.8,
        )
    suffix = "under central client-level DP" if dp_enabled else "(no DP)"
    ax.set_xlabel("Communication round")
    ax.set_ylabel("Global test accuracy (%)")
    ax.set_title(f"Convergence on Non-IID data {suffix}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return save_path


def plot_privacy_tradeoff(run_csvs: Dict[str, str], save_path: str) -> str:
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=200)
    plotted = False
    for algorithm, path in sorted(run_csvs.items()):
        data = read_run_csv(path)
        pairs = [
            (epsilon, acc * 100.0)
            for epsilon, acc in zip(data["epsilon"], data["test_acc"])
            if math.isfinite(epsilon) and epsilon > 0
        ]
        if not pairs:
            continue
        plotted = True
        eps, acc = zip(*pairs)
        color, label = _style(algorithm)
        ax.plot(eps, acc, color=color, label=label, linewidth=1.8)
    ax.set_xlabel(r"Privacy estimate $\varepsilon$")
    ax.set_ylabel("Global test accuracy (%)")
    ax.set_title(r"Privacy-utility trade-off")
    if plotted:
        ax.legend()
    else:
        ax.text(
            0.5,
            0.5,
            "DP disabled: no finite epsilon values to plot",
            ha="center",
            va="center",
            transform=ax.transAxes,
        )
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return save_path


def plot_weight_variance(run_csvs: Dict[str, str], save_path: str) -> str:
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), dpi=200)
    for algorithm, path in sorted(run_csvs.items()):
        data = read_run_csv(path)
        color, label = _style(algorithm)
        axes[0].plot(data["round"], data["weight_variance"], color=color, label=label, linewidth=1.8)
        axes[1].plot(data["round"], data["raw_client_drift"], color=color, label=label, linewidth=1.8)
        axes[2].plot(data["round"], data["clipped_client_drift"], color=color, label=label, linewidth=1.8)
    axes[0].set_xlabel("Communication round")
    axes[0].set_ylabel("Mean weight variance across clients")
    axes[0].set_title("Weight variance")
    axes[0].set_yscale("log")
    axes[0].legend()
    axes[1].set_xlabel("Communication round")
    axes[1].set_ylabel(r"Mean $\|\Delta_k - \bar{\Delta}\|_2$")
    axes[1].set_title("Raw client drift")
    axes[1].set_yscale("log")
    axes[1].legend()
    axes[2].set_xlabel("Communication round")
    axes[2].set_ylabel(r"Mean $\|\bar{\Delta}_k - \overline{\bar{\Delta}}\|_2$")
    axes[2].set_title("Clipped client drift")
    axes[2].set_yscale("log")
    axes[2].legend()
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return save_path


def generate_all_plots(
    results_dir: str,
    run_csvs: Optional[Dict[str, str]] = None,
    dp_enabled: bool = True,
) -> List[str]:
    if run_csvs is None:
        run_csvs = discover_run_csvs(results_dir)
    if not run_csvs:
        return []
    os.makedirs(results_dir, exist_ok=True)
    return [
        plot_accuracy_vs_rounds(
            run_csvs,
            os.path.join(results_dir, "accuracy_vs_rounds.png"),
            dp_enabled,
        ),
        plot_privacy_tradeoff(
            run_csvs,
            os.path.join(results_dir, "privacy_loss_tradeoff.png"),
        ),
        plot_weight_variance(
            run_csvs,
            os.path.join(results_dir, "weight_variance.png"),
        ),
    ]
