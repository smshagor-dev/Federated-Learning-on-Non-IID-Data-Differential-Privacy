from __future__ import annotations

import math
import os
from datetime import datetime

import pandas as pd

from utils.logger import discover_run_csvs, read_run_csv


class ResultsService:
    def __init__(self, results_dir: str) -> None:
        self.results_dir = results_dir

    def set_results_dir(self, results_dir: str) -> None:
        self.results_dir = results_dir

    def discover_artifacts(self) -> list[dict[str, str]]:
        os.makedirs(self.results_dir, exist_ok=True)
        artifacts: list[dict[str, str]] = []
        for name in sorted(os.listdir(self.results_dir)):
            path = os.path.join(self.results_dir, name)
            if os.path.isdir(path):
                continue
            artifacts.append(
                {
                    "name": name,
                    "path": path,
                    "type": self._artifact_type(name),
                    "modified": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S"),
                    "size_kb": f"{os.path.getsize(path) / 1024:.1f}",
                }
            )
        return artifacts

    def load_summary_text(self) -> str:
        summary_path = os.path.join(self.results_dir, "summary.md")
        if not os.path.exists(summary_path):
            return "No summary found yet. Run an experiment and summary.md will appear here."
        with open(summary_path, "r", encoding="utf-8") as handle:
            return handle.read()

    def load_metrics_snapshot(self) -> dict:
        run_csvs = discover_run_csvs(self.results_dir)
        snapshot = {
            "algorithm_count": len(run_csvs),
            "best_accuracy": None,
            "latest_epsilon": None,
            "latest_round": 0,
            "series": {},
        }
        for algorithm, path in run_csvs.items():
            data = read_run_csv(path)
            snapshot["series"][algorithm] = data
            if data["test_acc"]:
                snapshot["best_accuracy"] = max(
                    snapshot["best_accuracy"] or 0.0,
                    max(data["test_acc"]),
                )
            if data["round"]:
                snapshot["latest_round"] = max(snapshot["latest_round"], int(max(data["round"])))
            eps_values = [value for value in data["epsilon"] if math.isfinite(value)]
            if eps_values:
                peak = max(eps_values)
                snapshot["latest_epsilon"] = peak if snapshot["latest_epsilon"] is None else max(snapshot["latest_epsilon"], peak)
        return snapshot

    def load_client_distribution(self) -> pd.DataFrame:
        distribution_csv = os.path.join(self.results_dir, "client_distribution.csv")
        if os.path.exists(distribution_csv):
            return pd.read_csv(distribution_csv)
        return pd.DataFrame()

    def latest_summary_path(self) -> str | None:
        path = os.path.join(self.results_dir, "summary.md")
        return path if os.path.exists(path) else None

    @staticmethod
    def _artifact_type(name: str) -> str:
        ext = os.path.splitext(name)[1].lower()
        if ext == ".png":
            return "Plot"
        if ext == ".csv":
            return "CSV"
        if ext in {".yaml", ".yml"}:
            return "YAML"
        if ext == ".md":
            return "Markdown"
        return "File"
