from __future__ import annotations

from dataclasses import replace

import pytest

from fl_platform.benchmark.matrix import BenchmarkPartition
from fl_platform.benchmark.v3_matrix import (
    HeterogeneityCondition,
    PrivacyCondition,
    V3BenchmarkObservation,
    build_v3_benchmark_plan,
    summarize_v3_evidence,
    validate_v3_evidence,
)
from fl_platform.v3.attacks import AttackKind


def _small_plan():
    return build_v3_benchmark_plan(
        benchmark_id="v3-ci-matrix",
        datasets=("mnist",),
        algorithms=("fedavg",),
        seeds=(11, 23, 37),
        partitions=(BenchmarkPartition("iid", "iid", {}),),
        privacy_conditions=(PrivacyCondition("non-private", None),),
        attacks=(AttackKind.NONE,),
        aggregation_strategies=("mean",),
        heterogeneity_conditions=(
            HeterogeneityCondition("nominal", "baseline clients"),
        ),
        rounds=3,
        minimum_replicates=3,
    )


def _observations(plan, *, omit_last: bool = False):
    plan = replace(plan, primary_metrics=("global_accuracy", "wall_clock_seconds"))
    cells = tuple(cell for cell in plan.expand(minimum_replicates=3) if cell.runnable)
    observations: list[V3BenchmarkObservation] = []
    for cell in cells:
        for metric in plan.primary_metrics:
            value = 0.8 + cell.seed / 1000.0 if metric == "global_accuracy" else 2.0
            observations.append(
                V3BenchmarkObservation(
                    benchmark_id=plan.benchmark_id,
                    condition_id=cell.condition_id,
                    cell_id=cell.cell_id,
                    seed=cell.seed,
                    metric=metric,
                    value=value,
                    commit_sha="a" * 40,
                    specification_hash=plan.plan_hash(),
                )
            )
    if omit_last:
        observations.pop()
    return plan, tuple(observations)


def test_v3_matrix_expands_attack_privacy_robustness_and_heterogeneity_axes() -> None:
    plan = build_v3_benchmark_plan(
        benchmark_id="matrix-axes",
        datasets=("mnist",),
        algorithms=("fedavg", "scaffold"),
        seeds=(1, 2, 3),
        partitions=(BenchmarkPartition("iid", "iid", {}),),
        privacy_conditions=(
            PrivacyCondition("non-private", None),
            PrivacyCondition("epsilon-1", 1.0, 1e-5),
        ),
        attacks=(AttackKind.NONE, AttackKind.SIGN_FLIP),
        aggregation_strategies=("mean", "median"),
        heterogeneity_conditions=(
            HeterogeneityCondition("nominal", "baseline"),
            HeterogeneityCondition("edge", "resource constrained"),
        ),
        rounds=2,
        minimum_replicates=3,
    )
    cells = plan.expand(minimum_replicates=3)

    assert len(cells) == 2 * 3 * 2 * 2 * 2 * 2
    assert any(cell.runnable for cell in cells)
    assert any(not cell.runnable for cell in cells)
    assert any(
        "robust aggregation + DP" in cell.exclusion_reason
        for cell in cells
        if not cell.runnable
    )
    assert any(
        "DP-enabled scaffold" in cell.exclusion_reason
        for cell in cells
        if not cell.runnable
    )


def test_release_candidate_plan_rejects_unvalidated_federated_workload() -> None:
    with pytest.raises(ValueError, match="not release-validated"):
        build_v3_benchmark_plan(
            benchmark_id="release-workload",
            datasets=("femnist",),
            algorithms=("fedavg",),
            seeds=(1, 2, 3),
            partitions=(BenchmarkPartition("iid", "iid", {}),),
            privacy_conditions=(PrivacyCondition("non-private", None),),
            attacks=(AttackKind.NONE,),
            aggregation_strategies=("mean",),
            heterogeneity_conditions=(HeterogeneityCondition("nominal", "baseline"),),
            require_validated_workloads=True,
            minimum_replicates=3,
        )


def test_evidence_report_blocks_missing_cell_metric() -> None:
    plan, observations = _observations(_small_plan(), omit_last=True)
    report = validate_v3_evidence(plan, observations, minimum_replicates=3)
    assert not report.complete
    assert len(report.missing) == 1
    with pytest.raises(ValueError, match="incomplete"):
        report.require_complete()


def test_complete_evidence_produces_multi_seed_confidence_intervals() -> None:
    plan, observations = _observations(_small_plan())
    report = validate_v3_evidence(plan, observations, minimum_replicates=3)
    assert report.complete
    assert report.expected_observations == 6
    assert report.received_observations == 6

    summaries = summarize_v3_evidence(
        plan,
        observations,
        bootstrap_samples=200,
        minimum_replicates=3,
    )
    assert len(summaries) == 2
    accuracy = next(item for item in summaries if item.metric == "global_accuracy")
    assert accuracy.summary.n == 3
    assert accuracy.summary.ci_low <= accuracy.summary.mean <= accuracy.summary.ci_high


def test_evidence_rejects_wrong_specification_hash_and_duplicate_observations() -> None:
    plan, observations = _observations(_small_plan())
    wrong = replace(observations[0], specification_hash="wrong")
    with pytest.raises(ValueError, match="specification hash"):
        validate_v3_evidence(
            plan,
            (wrong, *observations[1:]),
            minimum_replicates=3,
        )

    with pytest.raises(ValueError, match="duplicate"):
        validate_v3_evidence(
            plan,
            (*observations, observations[0]),
            minimum_replicates=3,
        )
