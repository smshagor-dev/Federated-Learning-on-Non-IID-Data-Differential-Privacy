#!/usr/bin/env python3
"""Evidence-only collection gate for real 50-round, multi-run IEEE TAI experiments.

Inspect extracted GitHub Actions archives or local study roots. Never substitute
pilot runs, missing cells, unpaired sources, or synthetic scores for real data.
This emits a descriptive coverage ledger, NOT inferential statistical claims.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tai_pfl.analysis import (  # noqa: E402
    CONDITIONS,
    DATASETS,
    SEEDS,
    _check_cell,
    assert_primary_50round_protocol,
)
from tai_pfl.engine import METHODS  # noqa: E402


def _official_split_coverage(directory: Path, dataset: str) -> None:
    expected_train = 60000 if dataset in ("MNIST", "FASHIONMNIST") else 50000
    expected_test = 10000
    for name, size in (("train", expected_train), ("test", expected_test)):
        with np.load(directory / f"{name}_indices.npz", allow_pickle=False) as data:
            ids = list(data.files)
            if set(ids) != {f"client_{i}" for i in range(20)}:
                raise ValueError(f"client index keys mismatch at {directory}: {ids}")
            values = [data[f"client_{i}"] for i in range(20)]
        if sum(len(part) for part in values) != size:
            raise ValueError(f"official {name} size mismatch: {directory}")
        all_indices = np.concatenate(values)
        if not np.array_equal(np.sort(all_indices), np.arange(size)):
            raise ValueError(
                f"official {name} coverage has missing or repeated indices: "
                f"{directory}"
            )


def _primary_key(value: dict) -> tuple[str, str, int, str]:
    request = value["request"]
    return (
        request["dataset"],
        request["partition"],
        int(request["seed"]),
        request["method"],
    )


def collect(roots: list[Path]) -> tuple[list[dict], dict]:
    primary: dict[tuple[str, str, int, str], tuple[dict, Path]] = {}
    derived: dict[tuple[str, str, int, int], tuple[dict, Path]] = {}
    duplicate_archives = 0
    files_seen = 0
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"evidence root not found: {root}")
        paths = sorted(root.rglob("result.json"))
        if not paths:
            raise ValueError(f"no machine-readable result files at {root}")
        for path in paths:
            files_seen += 1
            result = _check_cell(path)
            request = result["request"]
            steps = int(request["training"]["adaptation_steps"])
            if steps == 1:
                assert_primary_50round_protocol(result)
                _official_split_coverage(path.parent, request["dataset"])
                key = _primary_key(result)
                group = primary
            else:
                if (
                    steps not in (3, 5)
                    or request["method"] != "per_fedavg"
                    or request["rounds"] != 50
                    or result.get("training_reused_from_primary") is not True
                    or result.get("source_primary_run_id")
                    != request.get("derived_from_primary_run_id")
                ):
                    raise ValueError(f"unverified/non-protocol sensitivity: {path}")
                if (
                    request["dataset"] not in DATASETS
                    or request["partition"] not in CONDITIONS
                    or request["seed"] not in SEEDS
                ):
                    raise ValueError(f"invalid RQ4 factor: {path}")
                _official_split_coverage(path.parent, request["dataset"])
                key = (
                    request["dataset"],
                    request["partition"],
                    int(request["seed"]),
                    steps,
                )
                group = derived
            if key in group:
                previous, _ = group[key]
                if previous["run_id"] != result["run_id"]:
                    raise ValueError(f"conflicting experiments for {key}")
                duplicate_archives += 1
                continue
            group[key] = (result, path)

    # The independent replicate is the paired training seed. All six methods
    # in a dataset/partition/seed must share both actual data and implementation.
    paired: dict[tuple[str, str, int], dict[str, tuple[str, str, str]]] = defaultdict(
        dict
    )
    for (dataset, condition, seed, method), (result, _) in primary.items():
        req = result["request"]
        paired[(dataset, condition, seed)][method] = (
            req["train_partition_hash"],
            req["test_partition_hash"],
            req["source_commit_sha"],
        )
    complete_six_method_seed_groups = Counter()
    paired_method_seed_groups = Counter()
    for (dataset, condition, _), methods in paired.items():
        versions = set(methods.values())
        if len(versions) != 1:
            raise ValueError(
                f"scientifically invalid unmatched code/partition hashes: "
                f"{dataset}/{condition}, methods={methods}"
            )
        if "fedavg" in methods and "per_fedavg" in methods:
            paired_method_seed_groups[(dataset, condition)] += 1
        if set(methods) == set(METHODS):
            complete_six_method_seed_groups[(dataset, condition)] += 1

    derived_verified: dict[tuple[str, str, int, int], tuple[dict, Path]] = {}
    for key, (value, path) in derived.items():
        dataset, condition, seed, _ = key
        base_pair = primary.get((dataset, condition, seed, "per_fedavg"))
        if base_pair is None:
            continue
        base = base_pair[0]
        a, b = base["request"], value["request"]
        if (
            value["source_primary_run_id"] != base["run_id"]
            or a["train_partition_hash"] != b["train_partition_hash"]
            or a["test_partition_hash"] != b["test_partition_hash"]
            or a["source_commit_sha"] != b["source_commit_sha"]
            or a["dataset"] != b["dataset"]
            or a["partition"] != b["partition"]
            or int(a["seed"]) != int(b["seed"])
        ):
            raise ValueError(f"RQ4 derived result is not linked to primary: {path}")
        one = a["training"].copy()
        other = b["training"].copy()
        one.pop("adaptation_steps")
        other.pop("adaptation_steps")
        if one != other:
            raise ValueError(f"RQ4 varied training budget beyond adaptation: {path}")
        derived_verified[key] = (value, path)

    expected_primary = {
        (d, c, s, m) for d in DATASETS for c in CONDITIONS
        for s in SEEDS for m in METHODS
    }
    expected_rq4 = {
        (d, c, s, step) for d in DATASETS for c in CONDITIONS
        for s in SEEDS for step in (3, 5)
    }
    if not set(primary).issubset(expected_primary):
        raise ValueError("primary collection contains unprespecified experiment")
    if not set(derived_verified).issubset(expected_rq4):
        raise ValueError("sensitivity collection contains unprespecified experiment")

    rows: list[dict] = []
    for key, (item, path) in sorted(primary.items()):
        req = item["request"]
        rows.append({
            "evidence_type": "50-round-primary",
            "dataset": key[0],
            "condition": key[1],
            "seed": key[2],
            "method": key[3],
            "adaptation_steps": 1,
            "source_commit_sha": req["source_commit_sha"],
            "run_id": item["run_id"],
            "micro_accuracy": item["metrics"]["micro_accuracy"],
            "p10_client_accuracy": item["metrics"]["p10_client_accuracy"],
            "worst_client_accuracy": item["metrics"]["worst_client_accuracy"],
            "jain_accuracy_index": item["metrics"]["jain_accuracy_index"],
            "client_count": item["metrics"]["client_count"],
            "evidence_path": str(path),
        })
    for key, (item, path) in sorted(derived_verified.items()):
        rows.append({
            "evidence_type": "50-round-trained-RQ4-evaluation",
            "dataset": key[0],
            "condition": key[1],
            "seed": key[2],
            "method": "per_fedavg",
            "adaptation_steps": key[3],
            "source_commit_sha": item["request"]["source_commit_sha"],
            "run_id": item["run_id"],
            "micro_accuracy": item["metrics"]["micro_accuracy"],
            "p10_client_accuracy": item["metrics"]["p10_client_accuracy"],
            "worst_client_accuracy": item["metrics"]["worst_client_accuracy"],
            "jain_accuracy_index": item["metrics"]["jain_accuracy_index"],
            "client_count": item["metrics"]["client_count"],
            "evidence_path": str(path),
        })
    report = {
        "status": "NOT_SUBMISSION_READY",
        "source": "verified official torchvision raw result artifacts only",
        "primary": {
            "expected": len(expected_primary),
            "verified": len(primary),
            "missing": len(expected_primary - set(primary)),
            "complete": set(primary) == expected_primary,
        },
        "rq4_derived": {
            "expected": len(expected_rq4),
            "verified": len(derived_verified),
            "missing": len(expected_rq4 - set(derived_verified)),
            "unpaired_derived_not_counted": len(derived) - len(derived_verified),
            "complete": set(derived_verified) == expected_rq4,
        },
        "method_replication": {
            "paired_fedavg_perfedavg": [
                {"dataset": d, "condition": c, "n_independent_seeds": n}
                for (d, c), n in sorted(paired_method_seed_groups.items())
            ],
            "complete_six_method": [
                {"dataset": d, "condition": c, "n_independent_seeds": n}
                for (d, c), n in sorted(complete_six_method_seed_groups.items())
            ],
        },
        "coverage": {
            "unique_primary_seed_groups": len(paired),
            "complete_six_method_seed_groups": sum(
                complete_six_method_seed_groups.values()
            ),
            "groups_with_ten_complete_seeds": [
                {"dataset": d, "condition": c}
                for (d, c), n in sorted(complete_six_method_seed_groups.items())
                if n == len(SEEDS)
            ],
        },
        "statistical_significance": "NOT_ESTABLISHED_BY_COVERAGE_LEDGER",
        "statistical_tests_executed": False,
        "raw_result_files_seen": files_seen,
        "duplicate_identical_archives_ignored": duplicate_archives,
        "notes": [
            "No synthetic/one-round pilot or incomplete experiment is counted.",
            "A client and an optimization round are not independent seed replicates.",
            "A new complete subset may justify a narrower predeclared research "
            "study, but not an automatic claim that the 1680-cell protocol finished.",
            "Statistical tests, reviewer-facing novelty and reporting audit are "
            "separate gates after matched multi-seed coverage.",
        ],
    }
    return rows, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail-closed real-data 50-round RQ1-RQ4 evidence ledger"
    )
    parser.add_argument("roots", type=Path, nargs="+")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    rows, report = collect(args.roots)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "coverage.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if rows:
        with (args.output_dir / "verified_primary_and_rq4.csv").open(
            "w", newline="", encoding="utf-8"
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
