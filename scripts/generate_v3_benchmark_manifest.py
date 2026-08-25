#!/usr/bin/env python3
"""Generate an auditable v3 benchmark specification manifest.

The manifest describes planned/runnable/excluded cells only. It is deliberately
not benchmark evidence and never marks the benchmark release gate complete.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from fl_platform.benchmark.matrix import (
    BenchmarkPartition,
    standard_partition_conditions,
)
from fl_platform.benchmark.v3_matrix import (
    HeterogeneityCondition,
    PrivacyCondition,
    build_v3_benchmark_plan,
    standard_heterogeneity_conditions,
    standard_privacy_conditions,
)
from fl_platform.v3.attacks import AttackKind

ALL_ALGORITHMS = (
    "fedavg",
    "fedprox",
    "scaffold",
    "fedsam",
    "ditto",
    "per_fedavg",
    "fedadam",
    "fedyogi",
    "fedadagrad",
    "fednova",
    "fedbn",
    "fedrep",
    "moon",
    "pfedme",
)


def _ci_plan():
    return build_v3_benchmark_plan(
        benchmark_id="v3-benchmark-ci",
        datasets=("mnist",),
        algorithms=("fedavg", "scaffold", "fednova"),
        seeds=(11, 23, 37, 53, 71),
        partitions=(
            BenchmarkPartition("iid", "iid", {}),
            BenchmarkPartition("dirichlet-0.5", "dirichlet", {"alpha": 0.5}),
        ),
        privacy_conditions=(
            PrivacyCondition("non-private", None),
            PrivacyCondition("epsilon-4", 4.0, 1e-5),
        ),
        attacks=(AttackKind.NONE, AttackKind.SIGN_FLIP),
        aggregation_strategies=("mean", "median"),
        heterogeneity_conditions=(
            HeterogeneityCondition("nominal", "baseline clients"),
            HeterogeneityCondition("edge-constrained", "resource constrained"),
        ),
        rounds=5,
    )


def _release_plan():
    return build_v3_benchmark_plan(
        benchmark_id="v3-release-matrix",
        datasets=("mnist", "fashion_mnist", "cifar10", "cifar100"),
        algorithms=ALL_ALGORITHMS,
        seeds=(11, 23, 37, 53, 71),
        partitions=standard_partition_conditions(),
        privacy_conditions=standard_privacy_conditions(),
        attacks=tuple(AttackKind),
        aggregation_strategies=("mean", "median", "trimmed_mean"),
        heterogeneity_conditions=standard_heterogeneity_conditions(),
        rounds=100,
        require_validated_workloads=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("ci", "release"), default="ci")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    plan = _ci_plan() if args.profile == "ci" else _release_plan()
    cells = plan.expand()
    exclusions = Counter(cell.exclusion_reason for cell in cells if not cell.runnable)
    runnable = sum(cell.runnable for cell in cells)
    payload = {
        "schema_version": 1,
        "kind": "v3-benchmark-specification-manifest",
        "profile": args.profile,
        "benchmark_id": plan.benchmark_id,
        "plan_hash": plan.plan_hash(),
        "total_cells": len(cells),
        "runnable_cells": runnable,
        "excluded_cells": len(cells) - runnable,
        "expected_primary_metric_observations": runnable * len(plan.primary_metrics),
        "exclusion_reasons": dict(sorted(exclusions.items())),
        "seeds": list(plan.seeds),
        "primary_metrics": list(plan.primary_metrics),
        "evidence_complete": False,
        "note": (
            "This artifact specifies the matrix only. Release evidence requires "
            "observations for every runnable cell and primary metric."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
