"""Strict, seed-paired, source-audited multi-dataset PFL analysis.

No value is synthesized from a benchmark plan, unit-test fixture or unrelated
paper. Missing cells abort the submission analysis instead of being imputed.
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
from pathlib import Path

import numpy as np

from tai_pfl.engine import METHODS, PERSONALIZED, summarize_client_rows
from tai_pfl.study import _digest, _partition_digest

DATASETS = ("MNIST", "FASHIONMNIST", "CIFAR10", "CIFAR100")
CONDITIONS = (
    "iid", "dirichlet:1.0", "dirichlet:0.3", "dirichlet:0.1",
    "dirichlet:0.05", "pathological:2", "quantity_skew:1.0",
)
SEEDS = (11, 23, 37, 53, 71, 83, 97, 109, 127, 149)
METRICS = (
    "micro_accuracy", "mean_client_accuracy", "median_client_accuracy",
    "p10_client_accuracy", "worst_client_accuracy", "client_accuracy_std",
    "jain_accuracy_index",
)


def _read_rows(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"empty client evidence: {path}")
    return [
        {"client_id": int(r["client_id"]), "n_test": int(r["n_test"]),
         "n_correct": int(r["n_correct"]),
         "accuracy": float(r["accuracy"]), "loss": float(r["loss"])}
        for r in rows
    ]


def _check_cell(path: Path) -> dict:
    item = json.loads(path.read_text(encoding="utf-8"))
    if item.get("status") != "completed" or item.get("evidence_class") != "official_torchvision_heldout":
        raise ValueError(f"not publishable official-dataset evidence: {path}")
    request = item.get("request", {})
    if item.get("run_id") != _digest(request):
        raise ValueError(f"request hash mismatch: {path}")
    if not request.get("source_commit_sha") or request.get("dp_enabled") is not False:
        raise ValueError(f"missing provenance or unexpected differential privacy: {path}")
    if request.get("official_torchvision") is not True or item.get("training_uses_test_split") is not False:
        raise ValueError(f"invalid official holdout identity: {path}")
    root = path.parent
    for name in ("request.json", "train_indices.npz", "test_indices.npz",
                 "clients_deployed.csv", "clients_global.csv", "global_model.pt",
                 "realized_heterogeneity.json"):
        if not (root / name).is_file():
            raise ValueError(f"required evidence artifact missing: {root / name}")
    if json.loads((root / "request.json").read_text(encoding="utf-8")) != request:
        raise ValueError(f"request manifest changed after run: {path}")
    # The request's claimed number of rounds is not sufficient evidence.
    # Verify the actual round journal has a complete, sequential trajectory.
    rounds_path = root / "rounds.csv"
    if not rounds_path.is_file():
        raise ValueError(f"missing round-level execution evidence: {path}")
    with rounds_path.open(newline="", encoding="utf-8") as handle:
        round_rows = list(csv.DictReader(handle))
    expected_rounds = int(request.get("rounds", 0))
    expected_clients = int(item["metrics"]["client_count"])
    if expected_rounds <= 0 or len(round_rows) != expected_rounds:
        raise ValueError(f"incomplete actual training round trace: {path}")
    previous_steps = 0
    for number, row in enumerate(round_rows, start=1):
        cohort = int(row["selected_client_count"])
        observed_steps = int(row["total_optimizer_steps"])
        if (
            int(row["round"]) != number
            or not 1 <= cohort <= expected_clients
            or observed_steps <= previous_steps
        ):
            raise ValueError(f"invalid or incomplete round evidence: {path}")
        previous_steps = observed_steps
    if (
        int(item["compute_observation"]["optimizer_steps_from_training"])
        != previous_steps
    ):
        raise ValueError(f"final optimizer step count contradicts journal: {path}")
    for split, manifest_key in (
        ("train", "train_partition_hash"), ("test", "test_partition_hash")
    ):
        with np.load(root / f"{split}_indices.npz", allow_pickle=False) as archive:
            indices = {int(k.removeprefix("client_")): archive[k]
                       for k in archive.files}
        if _partition_digest(indices) != request[manifest_key]:
            raise ValueError(f"{split} indices do not match persisted partition hash: {path}")
    recorded = json.loads((root / "realized_heterogeneity.json").read_text(encoding="utf-8"))
    recorded_summary = {k: v for k, v in recorded.items() if k != "clients"}
    if item.get("realized_heterogeneity") != recorded_summary:
        raise ValueError(f"realized heterogeneity summary does not match: {path}")
    rows = _read_rows(root / "clients_deployed.csv")
    ids = [r["client_id"] for r in rows]
    if ids != list(range(len(ids))):
        raise ValueError(f"missing/duplicated client IDs: {path}")
    with np.load(root / "test_indices.npz", allow_pickle=False) as archive:
        test_counts = {
            int(key.removeprefix("client_")): len(archive[key])
            for key in archive.files
        }
    if set(test_counts) != set(ids) or any(
        row["n_test"] != test_counts[row["client_id"]] for row in rows
    ):
        raise ValueError(f"reported test counts do not match partition indices: {path}")
    if item.get("primary_evaluation") != (
        "personalized_deployed"
        if request["method"] in PERSONALIZED else "shared_global"
    ):
        raise ValueError(f"incorrect deployed model identity: {path}")
    if any(r["n_correct"] > r["n_test"] or r["n_correct"] < 0 or
           not math.isfinite(r["accuracy"]) or
           abs(r["accuracy"] - r["n_correct"] / r["n_test"]) > 1e-10 for r in rows):
        raise ValueError(f"inconsistent held-out client predictions: {path}")
    calculated = summarize_client_rows(rows)
    for name in METRICS:
        observed = float(item["metrics"][name])
        if not math.isfinite(observed) or abs(observed - calculated[name]) > 1e-10:
            raise ValueError(f"unverified {name}: {path}")
    if item.get("minimum_heldout_client_samples") != min(r["n_test"] for r in rows):
        raise ValueError(f"held-out client minimum sample count mismatch: {path}")
    return item


def assert_primary_50round_protocol(item: dict) -> None:
    """Reject official one-round pilots or mismatched budgets as primary data."""
    request = item["request"]
    spec = request["training"]
    expected = {
        "rounds": 50,
        "batch_size": 64,
        "local_epochs": 2,
        "participation": 0.2,
        "adaptation_steps": 1,
    }
    observed = {
        "rounds": request["rounds"],
        "batch_size": spec["batch_size"],
        "local_epochs": spec["local_epochs"],
        "participation": spec["participation"],
        "adaptation_steps": spec["adaptation_steps"],
    }
    if observed != expected:
        raise ValueError(
            f"non-primary protocol (pilots/RQ4 budgets excluded): {observed}"
        )
    if item["metrics"]["client_count"] != 20:
        raise ValueError("primary study requires exactly twenty clients")
    if request["dataset"] not in DATASETS or request["partition"] not in CONDITIONS:
        raise ValueError("dataset or heterogeneity condition outside primary matrix")
    if request["method"] not in METHODS or request["seed"] not in SEEDS:
        raise ValueError("method or seed outside preregistered primary matrix")


def exact_paired_sign_flip(differences: np.ndarray) -> float:
    """Two-sided exhaustive randomization test on independent paired seeds."""
    d = np.asarray(differences, dtype=float)
    if len(d) < 2 or not np.isfinite(d).all():
        raise ValueError("paired differences require finite values and >=2 seeds")
    if len(d) > 18:
        raise ValueError("exact sign-flip restricted to <=18 seeds")
    observed = abs(float(d.mean()))
    extreme = 0
    for signs in itertools.product((-1.0, 1.0), repeat=len(d)):
        candidate = abs(float(np.mean(d * signs)))
        if candidate + 1e-12 >= observed:
            extreme += 1
    return extreme / 2**len(d)


def paired_ci(differences: np.ndarray, seed: int = 2026,
              samples: int = 10000) -> tuple[float, float]:
    d = np.asarray(differences, dtype=float)
    if len(d) < 2 or not np.isfinite(d).all():
        raise ValueError("CI needs finite paired seed differences")
    rng = np.random.default_rng(seed)
    resampled = rng.integers(0, len(d), size=(samples, len(d)))
    means = d[resampled].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    return float(low), float(high)


def holm_adjust(p_values: list[float]) -> list[float]:
    if not all(0 <= p <= 1 and math.isfinite(p) for p in p_values):
        raise ValueError("invalid p values")
    order = sorted(range(len(p_values)), key=lambda i: p_values[i])
    adjusted = [0.0] * len(p_values)
    previous = 0.0
    for rank, index in enumerate(order):
        previous = max(previous, min(1.0, (len(order) - rank) * p_values[index]))
        adjusted[index] = previous
    return adjusted


def analyze(root: Path, *, complete_required: bool = True,
            minimum_seeds: int = 10) -> tuple[list[dict], dict]:
    evidence = {}
    for path in sorted(root.rglob("result.json")):
        item = _check_cell(path)
        assert_primary_50round_protocol(item)
        req = item["request"]
        key = (req["dataset"], req["partition"], int(req["seed"]), req["method"])
        if key in evidence:
            raise ValueError(f"duplicated experimental cell: {key}")
        evidence[key] = item
    expected = [
        (dataset, condition, seed, method)
        for dataset in DATASETS for condition in CONDITIONS
        for seed in SEEDS for method in METHODS
    ]
    missing = [cell for cell in expected if cell not in evidence]
    coverage = {
        "expected_full_matrix": len(expected),
        "verified_cells": len(evidence),
        "missing_cells": len(missing),
        "complete": not missing,
        "missing_examples": [list(v) for v in missing[:25]],
    }
    if complete_required and missing:
        raise ValueError(
            f"submission evidence gate blocked: {len(missing)}/{len(expected)} "
            "cells missing (never treat plans or synthetic tests as measurements)"
        )
    # Each algorithm in one paired seed must use the identical train/test split.
    partitions: dict[tuple[str, str, int], tuple[str, str]] = {}
    for (dataset, condition, seed, _method), item in evidence.items():
        req = item["request"]
        k = (dataset, condition, seed)
        values = req["train_partition_hash"], req["test_partition_hash"]
        if k in partitions and partitions[k] != values:
            raise ValueError(f"unmatched client partition across methods: {k}")
        partitions[k] = values

    comparisons = []
    for dataset in DATASETS:
        for condition in CONDITIONS:
            for baseline in ("fedavg", "fedprox", "scaffold"):
                methods = tuple(m for m in METHODS if m != "fedavg") if baseline == "fedavg" else PERSONALIZED
                for method in methods:
                    seeds = [
                        seed for seed in SEEDS
                        if (dataset, condition, seed, method) in evidence
                        and (dataset, condition, seed, baseline) in evidence
                    ]
                    if len(seeds) < minimum_seeds:
                        if complete_required:
                            raise ValueError(f"insufficient paired seeds: {dataset}/{condition}/{method}/{baseline}")
                        continue
                    for metric in METRICS:
                        diffs = np.array([
                            evidence[(dataset, condition, seed, method)]["metrics"][metric]
                            - evidence[(dataset, condition, seed, baseline)]["metrics"][metric]
                            for seed in seeds
                        ], dtype=float)
                        lo, hi = paired_ci(diffs, seed=2026)
                        comparisons.append({
                            "dataset": dataset, "condition": condition,
                            "method": method, "baseline": baseline,
                            "metric": metric, "n_paired_seeds": len(seeds),
                            "mean_paired_difference": float(diffs.mean()),
                            "bootstrap_95ci_low": lo, "bootstrap_95ci_high": hi,
                            "two_sided_exact_p": exact_paired_sign_flip(diffs),
                        })
    # Holm within predeclared metric families across datasets, conditions, methods.
    for metric in METRICS:
        selected = [row for row in comparisons if row["metric"] == metric]
        adjusted = holm_adjust([row["two_sided_exact_p"] for row in selected])
        for row, p in zip(selected, adjusted, strict=True):
            row["holm_adjusted_p_within_metric_family"] = p
    # RQ3: paired difference-in-differences versus the IID condition.
    interactions = []
    for dataset in DATASETS:
        for condition in CONDITIONS:
            if condition == "iid":
                continue
            for method in PERSONALIZED:
                needed = lambda seed: all(
                    (dataset, cond, seed, alg) in evidence
                    for cond in (condition, "iid")
                    for alg in (method, "fedavg")
                )
                seeds = [seed for seed in SEEDS if needed(seed)]
                if len(seeds) < minimum_seeds:
                    if complete_required:
                        raise ValueError(f"RQ3 interaction missing paired seeds: {dataset}/{condition}/{method}")
                    continue
                for metric in ("mean_client_accuracy", "p10_client_accuracy", "jain_accuracy_index"):
                    deltas = np.array([
                        (
                            evidence[(dataset, condition, seed, method)]["metrics"][metric]
                            - evidence[(dataset, condition, seed, "fedavg")]["metrics"][metric]
                        ) - (
                            evidence[(dataset, "iid", seed, method)]["metrics"][metric]
                            - evidence[(dataset, "iid", seed, "fedavg")]["metrics"][metric]
                        )
                        for seed in seeds
                    ], dtype=float)
                    lo, hi = paired_ci(deltas)
                    interactions.append({
                        "dataset": dataset, "condition_minus": condition,
                        "reference_condition": "iid", "method": method,
                        "baseline": "fedavg", "metric": metric,
                        "paired_seeds": len(seeds),
                        "difference_in_differences": float(deltas.mean()),
                        "bootstrap_95ci_low": lo, "bootstrap_95ci_high": hi,
                        "two_sided_exact_p": exact_paired_sign_flip(deltas),
                    })
    for metric in ("mean_client_accuracy", "p10_client_accuracy", "jain_accuracy_index"):
        group = [row for row in interactions if row["metric"] == metric]
        for row, p in zip(
            group,
            holm_adjust([r["two_sided_exact_p"] for r in group]),
            strict=True,
        ):
            row["holm_adjusted_p_within_metric_family"] = p
    # A small, declared confirmatory family preserves attainable test power
    # with 10 seeds; the much larger matrix is otherwise exploratory.
    primary_tests = [
        row.copy()
        for row in comparisons
        if row["dataset"] in ("CIFAR10", "CIFAR100")
        and row["condition"] == "dirichlet:0.1"
        and row["baseline"] == "fedavg"
        and row["method"] in PERSONALIZED
        and row["metric"] == "p10_client_accuracy"
    ]
    for row, p in zip(
        primary_tests,
        holm_adjust([r["two_sided_exact_p"] for r in primary_tests]),
        strict=True,
    ):
        row["holm_adjusted_p_primary_family"] = p
    coverage["predeclared_primary_hypothesis_count"] = 4
    coverage["measured_primary_hypothesis_count"] = len(primary_tests)
    if complete_required and len(primary_tests) != 4:
        raise ValueError("primary severe non-IID p10 hypothesis family is incomplete")
    return comparisons, coverage, interactions, primary_tests


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify and analyze genuine IEEE TAI PFL evidence.")
    parser.add_argument("--results-root", default="benchmarks/tai-pfl")
    parser.add_argument("--output-dir", default="benchmarks/tai-pfl-analysis")
    parser.add_argument("--allow-partial-exploratory", action="store_true")
    args = parser.parse_args()
    root = Path(args.results_root)
    comparisons, coverage, interactions, primary_tests = analyze(
        root, complete_required=not args.allow_partial_exploratory
    )
    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "coverage.json").write_text(
        json.dumps(coverage, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "paired_comparisons.json").write_text(
        json.dumps(comparisons, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "rq3_interactions.json").write_text(
        json.dumps(interactions, indent=2) + "\n", encoding="utf-8"
    )
    (destination / "primary_tests.json").write_text(
        json.dumps(primary_tests, indent=2) + "\n", encoding="utf-8"
    )
    if comparisons:
        with (destination / "paired_comparisons.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=list(comparisons[0]))
            writer.writeheader()
            writer.writerows(comparisons)
    print(json.dumps(coverage, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
