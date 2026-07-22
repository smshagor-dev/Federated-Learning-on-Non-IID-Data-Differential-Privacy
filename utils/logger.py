"""CSV experiment logging and publication-quality plot generation.

CSV schema (one row per communication round):
    round, algorithm, test_acc, test_loss, epsilon,
    weight_variance, client_drift, avg_client_loss

Plot outputs (written to the results directory):
    accuracy_vs_rounds.png    -- algorithm comparison of test accuracy
    privacy_loss_tradeoff.png -- accuracy as a function of spent epsilon
    weight_variance.png       -- client drift / weight variance per round
"""

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
    "test_acc",
    "test_loss",
    "epsilon",
    "weight_variance",
    "client_drift",
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
    """Append-only per-round CSV logger for a single experiment run."""

    def __init__(self, csv_path: str) -> None:
        self.csv_path = csv_path
        os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
        self._file = open(csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=FIELDNAMES)
        self._writer.writeheader()
        self._file.flush()

    def log(self, row: dict) -> None:
        clean = {k: row.get(k, "") for k in FIELDNAMES}
        self._writer.writerow(clean)
        self._file.flush()

    def close(self) -> None:
        if not self._file.closed:
            self._file.close()

    def __enter__(self) -> "CSVLogger":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


# ---------------------------------------------------------------------- #
# CSV reading helpers                                                    #
# ---------------------------------------------------------------------- #
def read_run_csv(csv_path: str) -> Dict[str, List[float]]:
    """Load a run CSV into column lists (floats where possible)."""
    columns: Dict[str, List] = {k: [] for k in FIELDNAMES}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
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
    """Map algorithm name -> newest run CSV found in ``results_dir``."""
    runs: Dict[str, str] = {}
    for path in sorted(glob.glob(os.path.join(results_dir, "run_*.csv"))):
        data = read_run_csv(path)
        if data["algorithm"]:
            runs[data["algorithm"][0].lower()] = path
    return runs


def _style(algo: str):
    algo = algo.lower()
    return (
        _ALGO_COLORS.get(algo, "#7f7f7f"),
        _ALGO_LABELS.get(algo, algo),
    )


# ---------------------------------------------------------------------- #
# Plot generators                                                        #
# ---------------------------------------------------------------------- #
def plot_accuracy_vs_rounds(
    run_csvs: Dict[str, str], save_path: str, dp_enabled: bool = True
) -> str:
    fig, ax = plt.subplots(figsize=(7, 4.5), dpi=200)
    for algo, path in sorted(run_csvs.items()):
        data = read_run_csv(path)
        color, label = _style(algo)
        ax.plot(
            data["round"],
            [a * 100.0 for a in data["test_acc"]],
            color=color,
            label=label,
            linewidth=1.8,
        )
    suffix = "under DP" if dp_enabled else "(no DP)"
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
    for algo, path in sorted(run_csvs.items()):
        data = read_run_csv(path)
        pairs = [
            (e, a * 100.0)
            for e, a in zip(data["epsilon"], data["test_acc"])
            if math.isfinite(e) and e > 0
        ]
        if not pairs:
            continue
        plotted = True
        eps, acc = zip(*pairs)
        color, label = _style(algo)
        ax.plot(eps, acc, color=color, label=label, linewidth=1.8)
    ax.set_xlabel(r"Privacy budget spent  $\varepsilon$")
    ax.set_ylabel("Global test accuracy (%)")
    ax.set_title(r"Privacy-utility trade-off (accuracy vs. $\varepsilon$)")
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
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), dpi=200)
    for algo, path in sorted(run_csvs.items()):
        data = read_run_csv(path)
        color, label = _style(algo)
        axes[0].plot(
            data["round"], data["weight_variance"], color=color, label=label,
            linewidth=1.8,
        )
        axes[1].plot(
            data["round"], data["client_drift"], color=color, label=label,
            linewidth=1.8,
        )
    axes[0].set_xlabel("Communication round")
    axes[0].set_ylabel("Mean weight variance across clients")
    axes[0].set_title("Weight variance (divergence of local models)")
    axes[0].set_yscale("log")
    axes[0].legend()
    axes[1].set_xlabel("Communication round")
    axes[1].set_ylabel(r"Mean $\|\Delta_i - \bar{\Delta}\|_2$")
    axes[1].set_title("Client drift (update disagreement)")
    axes[1].set_yscale("log")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(save_path)
    plt.close(fig)
    return save_path


def generate_all_plots(
    results_dir: str,
    run_csvs: Optional[Dict[str, str]] = None,
    dp_enabled: bool = True,
) -> List[str]:
    """Regenerate every comparison figure from the run CSVs in results_dir."""
    if run_csvs is None:
        run_csvs = discover_run_csvs(results_dir)
    if not run_csvs:
        return []
    os.makedirs(results_dir, exist_ok=True)
    return [
        plot_accuracy_vs_rounds(
            run_csvs, os.path.join(results_dir, "accuracy_vs_rounds.png"), dp_enabled
        ),
        plot_privacy_tradeoff(
            run_csvs, os.path.join(results_dir, "privacy_loss_tradeoff.png")
        ),
        plot_weight_variance(
            run_csvs, os.path.join(results_dir, "weight_variance.png")
        ),
    ]
