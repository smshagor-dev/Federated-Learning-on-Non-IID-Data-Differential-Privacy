#!/usr/bin/env python3
"""Run repeatable multi-seed benchmarks against the real root FL runtime.

Each benchmark cell launches ``main.py --cli`` in a fresh process and writes its
own effective config, logs, exact client partition, round metrics, held-out
client metrics, and machine summary. Completed cells can be resumed without
rerunning them.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PYTHON_SRC = _REPO_ROOT / "python" / "src"
sys.path.insert(0, str(_PYTHON_SRC))
sys.path.insert(0, str(_REPO_ROOT))

from fl_platform.benchmark.matrix import (  # noqa: E402
    BenchmarkCell,
    BenchmarkPartition,
    BenchmarkPlan,
)
from fl_platform.benchmark.results import (  # noqa: E402
    BenchmarkObservation,
    compare_algorithms,
    comparison_row_dict,
    summarize_observations,
    summary_row_dict,
)


def _csv_values(value: str) -> tuple[str, ...]:
    values = tuple(part.strip() for part in value.split(",") if part.strip())
    if not values:
        raise argparse.ArgumentTypeError("at least one value is required")
    return values


def _integer_values(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(item) for item in _csv_values(value))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seeds must be comma-separated integers") from exc


def _epsilon_values(value: str) -> tuple[float | None, ...]:
    result: list[float | None] = []
    for item in _csv_values(value):
        if item.lower() in {"none", "off", "nonprivate", "non-private"}:
            result.append(None)
        else:
            try:
                result.append(float(item))
            except ValueError as exc:
                raise argparse.ArgumentTypeError(
                    "epsilons must be numbers or 'none'"
                ) from exc
    return tuple(result)


def _partition_values(value: str) -> tuple[BenchmarkPartition, ...]:
    partitions: list[BenchmarkPartition] = []
    for item in _csv_values(value):
        lower = item.lower()
        if lower == "iid":
            partitions.append(BenchmarkPartition("iid", "iid", {}))
            continue
        if lower.startswith("dirichlet:"):
            alpha = float(lower.split(":", 1)[1])
            partitions.append(
                BenchmarkPartition(
                    f"dirichlet-{alpha:g}", "dirichlet", {"alpha": alpha}
                )
            )
            continue
        if lower.startswith("pathological:"):
            classes = int(lower.split(":", 1)[1])
            partitions.append(
                BenchmarkPartition(
                    f"pathological-{classes}-classes",
                    "pathological",
                    {"classes_per_client": classes},
                )
            )
            continue
        if lower.startswith("quantity_skew:"):
            sigma = float(lower.split(":", 1)[1])
            partitions.append(
                BenchmarkPartition(
                    f"quantity-skew-{sigma:g}",
                    "quantity_skew",
                    {"quantity_skew_sigma": sigma},
                )
            )
            continue
        raise argparse.ArgumentTypeError(
            "partition values must be iid, dirichlet:<alpha>, "
            "pathological:<classes>, or quantity_skew:<sigma>"
        )
    return tuple(partitions)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run real multi-seed federated-learning benchmark matrices."
    )
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--benchmark-id", default="fl-benchmark")
    parser.add_argument("--datasets", type=_csv_values, default=("MNIST",))
    parser.add_argument(
        "--algorithms", type=_csv_values, default=("fedavg", "fedprox")
    )
    parser.add_argument(
        "--partitions",
        type=_partition_values,
        default=(
            BenchmarkPartition("iid", "iid", {}),
            BenchmarkPartition("dirichlet-0.1", "dirichlet", {"alpha": 0.1}),
        ),
    )
    parser.add_argument(
        "--epsilons",
        type=_epsilon_values,
        default=(None, 4.0),
        help="Comma-separated target epsilons; use 'none' for non-private runs.",
    )
    parser.add_argument("--delta", type=float, default=1e-5)
    parser.add_argument(
        "--seeds", type=_integer_values, default=(11, 23, 37, 53, 71)
    )
    parser.add_argument("--rounds", type=int, default=20)
    parser.add_argument("--output-dir", default="benchmarks/runs")
    parser.add_argument("--baseline", default="fedavg")
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--analysis-seed", type=int, default=2026)
    parser.add_argument(
        "--minimum-replicates",
        type=int,
        default=5,
        help="Minimum unique seeds required per aggregated benchmark condition.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-going", action="store_true")
    parser.add_argument("--max-cells", type=int, default=None)
    return parser.parse_args()


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"configuration root must be a mapping: {path}")
    return loaded


def _commit_sha() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        value = completed.stdout.strip()
        return value or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _config_hash(config: dict[str, Any]) -> str:
    payload = json.dumps(config, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cell_config(
    base: dict[str, Any], cell: BenchmarkCell, results_dir: Path
) -> dict[str, Any]:
    config = json.loads(json.dumps(base))
    config.setdefault("system", {})
    config.setdefault("data", {})
    config.setdefault("algorithm", {})
    config.setdefault("federated", {})
    config.setdefault("dp", {})

    config["system"]["seed"] = cell.seed
    config["system"]["results_dir"] = str(results_dir)
    config["data"]["dataset"] = cell.dataset_id.upper()
    config["data"]["partition"] = cell.partition_strategy
    config["algorithm"]["name"] = cell.algorithm_id
    config["federated"]["rounds"] = cell.rounds

    if cell.partition_strategy == "dirichlet":
        config["data"]["alpha"] = float(cell.partition_parameters["alpha"])
    elif cell.partition_strategy == "pathological":
        config["data"]["classes_per_client"] = int(
            cell.partition_parameters["classes_per_client"]
        )
    elif cell.partition_strategy == "quantity_skew":
        config["data"]["quantity_skew_sigma"] = float(
            cell.partition_parameters["quantity_skew_sigma"]
        )

    if cell.target_epsilon is None:
        config["dp"]["enabled"] = False
        config["dp"]["target_epsilon"] = None
    else:
        config["dp"]["enabled"] = True
        config["dp"]["target_epsilon"] = float(cell.target_epsilon)
        config["dp"]["target_delta"] = float(cell.target_delta)
    return config


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _run_cell(
    *,
    cell: BenchmarkCell,
    base_config: dict[str, Any],
    benchmark_root: Path,
    resume: bool,
    dry_run: bool,
) -> tuple[dict[str, Any] | None, str]:
    cell_root = benchmark_root / "cells" / cell.cell_id
    results_dir = cell_root / "results"
    summary_path = results_dir / "summary.json"
    cell_root.mkdir(parents=True, exist_ok=True)

    config = _cell_config(base_config, cell, results_dir)
    config_path = cell_root / "config.yaml"
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    _write_json(cell_root / "cell.json", cell.canonical_payload())

    if resume and summary_path.is_file():
        return json.loads(summary_path.read_text(encoding="utf-8")), "resumed"
    if dry_run:
        return None, "planned"

    log_path = cell_root / "run.log"
    command = [
        sys.executable,
        str(_REPO_ROOT / "main.py"),
        "--cli",
        "--config",
        str(config_path),
    ]
    with log_path.open("w", encoding="utf-8") as log_handle:
        completed = subprocess.run(
            command,
            cwd=_REPO_ROOT,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            check=False,
        )
    if completed.returncode != 0:
        _write_json(
            cell_root / "failure.json",
            {
                "cell_id": cell.cell_id,
                "returncode": completed.returncode,
                "command": command,
                "log": str(log_path),
            },
        )
        raise RuntimeError(
            f"benchmark cell {cell.cell_id} failed with return code "
            f"{completed.returncode}; see {log_path}"
        )
    if not summary_path.is_file():
        raise RuntimeError(
            f"benchmark cell {cell.cell_id} completed without {summary_path}"
        )
    return json.loads(summary_path.read_text(encoding="utf-8")), "completed"


def _observations_from_summary(
    *,
    cell: BenchmarkCell,
    summary: dict[str, Any],
    commit_sha: str,
    specification_hash: str,
) -> list[BenchmarkObservation]:
    partition = summary.get("partition") or {}
    partition_hash = str(partition.get("partition_hash") or "")
    if not partition_hash:
        raise ValueError(f"cell {cell.cell_id} summary is missing partition_hash")
    runs = summary.get("runs") or []
    if len(runs) != 1:
        raise ValueError(
            f"cell {cell.cell_id} expected exactly one algorithm run, got {len(runs)}"
        )
    run = runs[0]
    if run.get("algorithm") != cell.algorithm_id:
        raise ValueError(
            f"cell {cell.cell_id} algorithm mismatch: "
            f"{run.get('algorithm')!r} != {cell.algorithm_id!r}"
        )

    metrics = {
        "global_accuracy": run.get("final_acc"),
        "global_loss": run.get("final_loss"),
        "wall_clock_seconds": run.get("elapsed_sec"),
        "raw_client_drift": run.get("mean_raw_drift"),
        "clipped_client_drift": run.get("mean_clipped_drift"),
        "fraction_clients_clipped": run.get("mean_fraction_clipped"),
        "aggregate_noise_norm": run.get("mean_aggregate_noise_norm"),
    }
    client_evaluation = run.get("client_evaluation") or {}
    metrics.update(
        {
            "mean_client_accuracy": client_evaluation.get("mean_client_accuracy"),
            "weighted_client_accuracy": client_evaluation.get(
                "weighted_client_accuracy"
            ),
            "median_client_accuracy": client_evaluation.get(
                "median_client_accuracy"
            ),
            "p10_client_accuracy": client_evaluation.get("p10_client_accuracy"),
            "worst_client_accuracy": client_evaluation.get("worst_client_accuracy"),
            "best_client_accuracy": client_evaluation.get("best_client_accuracy"),
            "client_accuracy_std": client_evaluation.get("client_accuracy_std"),
            "client_accuracy_range": client_evaluation.get("client_accuracy_range"),
            "jain_accuracy_index": client_evaluation.get("jain_accuracy_index"),
            "mean_client_loss": client_evaluation.get("mean_client_loss"),
            "weighted_client_loss": client_evaluation.get("weighted_client_loss"),
            "p90_client_loss": client_evaluation.get("p90_client_loss"),
            "worst_client_loss": client_evaluation.get("worst_client_loss"),
        }
    )
    if cell.target_epsilon is not None:
        metrics["final_epsilon"] = run.get("final_epsilon")

    observations: list[BenchmarkObservation] = []
    for metric, raw_value in metrics.items():
        if raw_value is None:
            continue
        observations.append(
            BenchmarkObservation(
                benchmark_id=cell.benchmark_id,
                dataset_id=cell.dataset_id,
                partition_id=cell.partition_name,
                partition_hash=partition_hash,
                algorithm_id=cell.algorithm_id,
                target_epsilon=cell.target_epsilon,
                target_delta=cell.target_delta,
                seed=cell.seed,
                metric=metric,
                value=float(raw_value),
                runtime_identity=cell.runtime_identity,
                commit_sha=commit_sha,
                specification_hash=specification_hash,
            )
        )
    return observations


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    if args.minimum_replicates < 1:
        raise ValueError("minimum_replicates must be >= 1")
    config_path = (_REPO_ROOT / args.config).resolve()
    base_config = _load_yaml(config_path)
    plan = BenchmarkPlan(
        benchmark_id=args.benchmark_id,
        datasets=tuple(args.datasets),
        algorithms=tuple(value.lower() for value in args.algorithms),
        partitions=tuple(args.partitions),
        target_epsilons=tuple(args.epsilons),
        target_delta=float(args.delta),
        seeds=tuple(args.seeds),
        rounds=int(args.rounds),
        runtime_identity="root-simulator",
    )
    plan.validate(minimum_replicates=int(args.minimum_replicates))

    benchmark_root = (_REPO_ROOT / args.output_dir / plan.benchmark_id).resolve()
    benchmark_root.mkdir(parents=True, exist_ok=True)
    _write_json(
        benchmark_root / "plan.json",
        {
            "plan_hash": plan.plan_hash(),
            "minimum_replicates": int(args.minimum_replicates),
            **plan.canonical_payload(),
        },
    )

    cells = list(plan.expand(minimum_replicates=int(args.minimum_replicates)))
    if args.max_cells is not None:
        if args.max_cells <= 0:
            raise ValueError("max_cells must be > 0")
        cells = cells[: args.max_cells]

    commit_sha = _commit_sha()
    observations: list[BenchmarkObservation] = []
    statuses: list[dict[str, object]] = []
    failures = 0

    for index, cell in enumerate(cells, start=1):
        cell_root = benchmark_root / "cells" / cell.cell_id
        cell_config = _cell_config(base_config, cell, cell_root / "results")
        specification_hash = _config_hash(cell_config)
        print(
            f"[{index}/{len(cells)}] {cell.dataset_id} {cell.algorithm_id} "
            f"{cell.partition_name} epsilon={cell.target_epsilon} seed={cell.seed}"
        )
        try:
            summary, status = _run_cell(
                cell=cell,
                base_config=base_config,
                benchmark_root=benchmark_root,
                resume=bool(args.resume),
                dry_run=bool(args.dry_run),
            )
            statuses.append({"cell_id": cell.cell_id, "status": status})
            if summary is not None:
                observations.extend(
                    _observations_from_summary(
                        cell=cell,
                        summary=summary,
                        commit_sha=commit_sha,
                        specification_hash=specification_hash,
                    )
                )
        except Exception as exc:
            failures += 1
            statuses.append(
                {"cell_id": cell.cell_id, "status": "failed", "error": str(exc)}
            )
            print(f"ERROR: {exc}", file=sys.stderr)
            if not args.keep_going:
                _write_json(benchmark_root / "status.json", statuses)
                return 1

    _write_json(benchmark_root / "status.json", statuses)
    if args.dry_run:
        print(f"Planned {len(cells)} cells -> {benchmark_root}")
        return 0
    if failures:
        print(f"Completed with {failures} failed cells", file=sys.stderr)
        return 1

    _write_json(
        benchmark_root / "observations.json",
        [asdict(observation) for observation in observations],
    )

    summaries = summarize_observations(
        observations,
        bootstrap_samples=int(args.bootstrap_samples),
        bootstrap_seed=int(args.analysis_seed),
        minimum_replicates=int(args.minimum_replicates),
    )
    summary_rows = [summary_row_dict(row) for row in summaries]
    _write_json(benchmark_root / "summary.json", summary_rows)
    _write_csv(benchmark_root / "summary.csv", summary_rows)

    algorithms = set(plan.algorithms)
    if args.baseline in algorithms and len(algorithms) > 1:
        comparisons = compare_algorithms(
            observations,
            baseline_algorithm=args.baseline,
            bootstrap_samples=int(args.bootstrap_samples),
            analysis_seed=int(args.analysis_seed),
            minimum_replicates=int(args.minimum_replicates),
        )
        comparison_rows = [comparison_row_dict(row) for row in comparisons]
        _write_json(benchmark_root / "comparisons.json", comparison_rows)
        _write_csv(benchmark_root / "comparisons.csv", comparison_rows)

    print(f"Benchmark complete -> {benchmark_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
