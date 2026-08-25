#!/usr/bin/env python3
"""Validate the real five-seed benchmark used to qualify the v3.0.0 baseline."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

EXPECTED_SEEDS = (11, 23, 37, 53, 71)
REQUIRED_METRICS = ("global_accuracy", "global_loss", "wall_clock_seconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark-root", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def main() -> int:
    args = parse_args()
    root = Path(args.benchmark_root).resolve()
    commit_sha = str(args.commit_sha).strip().lower()
    output = Path(args.output).resolve()

    plan_path = root / "plan.json"
    status_path = root / "status.json"
    observations_path = root / "observations.json"
    summary_path = root / "summary.json"
    for path in (plan_path, status_path, observations_path, summary_path):
        _require(path.is_file(), f"missing benchmark evidence file: {path}")

    plan = _load_json(plan_path)
    statuses = _load_json(status_path)
    observations = _load_json(observations_path)
    summaries = _load_json(summary_path)

    _require(plan.get("benchmark_id") == "v3-release-baseline", "benchmark id mismatch")
    _require(plan.get("datasets") == ["MNIST"], "release benchmark must use MNIST only")
    _require(plan.get("algorithms") == ["fedavg"], "release benchmark must use FedAvg")
    _require(plan.get("target_epsilons") == [None], "release benchmark must be non-private")
    _require(plan.get("runtime_identity") == "root-simulator", "runtime identity mismatch")
    _require(plan.get("rounds") == 1, "release benchmark must run one qualification round")
    _require(tuple(plan.get("seeds", ())) == EXPECTED_SEEDS, "release seed set mismatch")

    partitions = plan.get("partitions")
    _require(isinstance(partitions, list) and len(partitions) == 1, "expected one partition")
    _require(partitions[0].get("name") == "iid", "release partition must be iid")
    _require(partitions[0].get("strategy") == "iid", "release partition strategy mismatch")

    _require(isinstance(statuses, list), "benchmark status must be a list")
    _require(len(statuses) == len(EXPECTED_SEEDS), "expected exactly five benchmark cells")
    _require(
        all(item.get("status") in {"completed", "resumed"} for item in statuses),
        "all release benchmark cells must complete",
    )
    _require(isinstance(observations, list) and observations, "benchmark has no observations")
    _require(isinstance(summaries, list) and summaries, "benchmark has no summaries")

    metrics_by_seed: dict[int, set[str]] = {seed: set() for seed in EXPECTED_SEEDS}
    for observation in observations:
        _require(observation.get("benchmark_id") == "v3-release-baseline", "observation id mismatch")
        _require(observation.get("dataset_id") == "MNIST", "observation dataset mismatch")
        _require(observation.get("algorithm_id") == "fedavg", "observation algorithm mismatch")
        _require(observation.get("partition_id") == "iid", "observation partition mismatch")
        _require(observation.get("target_epsilon") is None, "observation must be non-private")
        _require(observation.get("target_delta") is None, "non-private delta must be null")
        _require(observation.get("runtime_identity") == "root-simulator", "observation runtime mismatch")
        _require(str(observation.get("commit_sha", "")).lower() == commit_sha, "observation commit mismatch")

        seed = int(observation.get("seed"))
        _require(seed in metrics_by_seed, f"unexpected benchmark seed: {seed}")
        metric = str(observation.get("metric", ""))
        value = float(observation.get("value"))
        _require(metric != "", "observation metric is empty")
        _require(math.isfinite(value), f"non-finite observation value for {metric}")
        metrics_by_seed[seed].add(metric)

    for seed, metrics in metrics_by_seed.items():
        missing = sorted(set(REQUIRED_METRICS) - metrics)
        _require(not missing, f"seed {seed} is missing required metrics: {missing}")

    evidence = {
        "schema_version": 1,
        "release": "3.0.0",
        "evidence_complete": True,
        "scope": {
            "runtime_identity": "root-simulator",
            "dataset": "MNIST",
            "algorithm": "fedavg",
            "partition": "iid",
            "privacy": "non-private",
            "rounds": 1,
            "seeds": list(EXPECTED_SEEDS),
        },
        "required_metrics": list(REQUIRED_METRICS),
        "commit_sha": commit_sha,
        "plan_hash": plan.get("plan_hash"),
        "observation_count": len(observations),
        "summary_count": len(summaries),
        "observations_sha256": hashlib.sha256(observations_path.read_bytes()).hexdigest(),
        "qualification_note": (
            "This empirical baseline qualifies the stable v3.0.0 runtime baseline; "
            "it does not claim completion of the full research benchmark cross-product."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
