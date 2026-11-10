"""RQ4 sensitivity: paired effect of Per-FedAvg held-out adaptation steps.

Uses independently archived, *official* held-out experiments generated under
separate output roots. Never adapts on the official test split. This script
does not synthesize results for missing budgets.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np

from tai_pfl.analysis import (
    CONDITIONS,
    DATASETS,
    METRICS,
    SEEDS,
    _check_cell,
    exact_paired_sign_flip,
    holm_adjust,
    paired_ci,
)


def _index(root: Path) -> dict[tuple[str, str, int], dict]:
    items: dict[tuple[str, str, int], dict] = {}
    for path in sorted(root.rglob("result.json")):
        result = _check_cell(path)
        request = result["request"]
        if request["method"] != "per_fedavg":
            continue
        key = (request["dataset"], request["partition"], int(request["seed"]))
        if key in items:
            raise ValueError(f"duplicate sensitivity experiment {key} in {root}")
        items[key] = result
    return items


def analyze_sensitivity(roots: dict[int, Path], *, complete_required: bool = True,
                        minimum_seeds: int = 10) -> tuple[list[dict], dict]:
    if 1 not in roots or len(roots) < 2:
        raise ValueError("reference adaptation_steps=1 and one alternative required")
    if any(step <= 0 for step in roots):
        raise ValueError("adaptation steps must be positive")
    indexed = {step: _index(root) for step, root in sorted(roots.items())}
    required = [
        (dataset, condition, seed)
        for dataset in DATASETS for condition in CONDITIONS for seed in SEEDS
    ]
    missing = [
        (step, key) for step, cells in indexed.items()
        for key in required if key not in cells
    ]
    coverage = {
        "required_cells": len(required) * len(roots),
        "verified_cells": sum(len(c) for c in indexed.values()),
        "missing_cells": len(missing),
        "complete": not missing,
        "steps": sorted(roots),
        "missing_examples": [[step, *key] for step, key in missing[:20]],
    }
    if complete_required and missing:
        raise ValueError(f"RQ4 blocked: {len(missing)} adaptation cells missing")

    results: list[dict] = []
    for dataset in DATASETS:
        for condition in CONDITIONS:
            for proposed_steps in sorted(set(roots) - {1}):
                paired = []
                for seed in SEEDS:
                    key = (dataset, condition, seed)
                    if key not in indexed[1] or key not in indexed[proposed_steps]:
                        continue
                    before, after = indexed[1][key], indexed[proposed_steps][key]
                    a, b = before["request"], after["request"]
                    before_spec, after_spec = a["training"].copy(), b["training"].copy()
                    before_spec.pop("adaptation_steps", None)
                    after_spec.pop("adaptation_steps", None)
                    if before_spec != after_spec:
                        raise ValueError(f"training hyperparameters differ beyond adaptation: {key}")
                    if (a["train_partition_hash"] != b["train_partition_hash"] or
                        a["test_partition_hash"] != b["test_partition_hash"]):
                        raise ValueError(f"adaptation comparison has mismatched data: {key}")
                    if before["model_architecture"] != after["model_architecture"]:
                        raise ValueError(f"model architecture changed across sensitivity: {key}")
                    if (a["training"]["adaptation_steps"] != 1 or
                        b["training"]["adaptation_steps"] != proposed_steps):
                        raise ValueError(f"mislabelled adaptation steps: {key}")
                    paired.append((before, after))
                if len(paired) < minimum_seeds:
                    if complete_required:
                        raise ValueError(f"insufficient paired RQ4 seeds for {dataset}/{condition}")
                    continue
                for metric in METRICS:
                    deltas = np.array([
                        after["metrics"][metric] - before["metrics"][metric]
                        for before, after in paired
                    ], dtype=float)
                    lo, hi = paired_ci(deltas)
                    results.append({
                        "dataset": dataset,
                        "condition": condition,
                        "method": "per_fedavg",
                        "reference_adaptation_steps": 1,
                        "alternative_adaptation_steps": proposed_steps,
                        "metric": metric,
                        "paired_seeds": len(paired),
                        "paired_mean_difference": float(deltas.mean()),
                        "bootstrap_95ci_low": lo,
                        "bootstrap_95ci_high": hi,
                        "exact_signflip_p": exact_paired_sign_flip(deltas),
                    })
    for metric in METRICS:
        family = [v for v in results if v["metric"] == metric]
        adjusted = holm_adjust([v["exact_signflip_p"] for v in family])
        for result, adjusted_p in zip(family, adjusted, strict=True):
            result["holm_adjusted_p_within_metric_family"] = adjusted_p
    return results, coverage


def main() -> int:
    parser = argparse.ArgumentParser(description="RQ4 official adaptation-budget sensitivity")
    parser.add_argument("--root-step-1", required=True)
    parser.add_argument("--root-step-3", required=True)
    parser.add_argument("--root-step-5", required=True)
    parser.add_argument("--output-dir", default="benchmarks/tai-pfl-rq4")
    parser.add_argument("--allow-partial-exploratory", action="store_true")
    args = parser.parse_args()
    roots = {1: Path(args.root_step_1), 3: Path(args.root_step_3),
             5: Path(args.root_step_5)}
    comparisons, coverage = analyze_sensitivity(
        roots, complete_required=not args.allow_partial_exploratory
    )
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "coverage.json").write_text(
        json.dumps(coverage, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "adaptation_comparisons.json").write_text(
        json.dumps(comparisons, indent=2) + "\n", encoding="utf-8"
    )
    if comparisons:
        with (destination / "adaptation_comparisons.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
            writer.writeheader()
            writer.writerows(comparisons)
    print(json.dumps(coverage, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
