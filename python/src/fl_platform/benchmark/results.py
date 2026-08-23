"""Aggregation and comparison of repeated benchmark observations."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Iterable

from .statistics import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_MINIMUM_REPLICATES,
    DEFAULT_RANDOMIZATION_SAMPLES,
    PairedComparison,
    compare_paired_metrics,
    holm_adjust,
    summarize_metric,
)


@dataclass(frozen=True, slots=True)
class BenchmarkObservation:
    benchmark_id: str
    dataset_id: str
    partition_id: str
    partition_hash: str
    algorithm_id: str
    target_epsilon: float | None
    target_delta: float | None
    seed: int
    metric: str
    value: float
    runtime_identity: str
    commit_sha: str
    specification_hash: str


@dataclass(frozen=True, slots=True)
class BenchmarkSummaryRow:
    benchmark_id: str
    dataset_id: str
    partition_id: str
    partition_hash: str
    algorithm_id: str
    target_epsilon: float | None
    target_delta: float | None
    metric: str
    runtime_identity: str
    commit_sha: str
    n: int
    mean: float
    sample_std: float
    median: float
    minimum: float
    maximum: float
    confidence: float
    ci_low: float
    ci_high: float
    interval_method: str


@dataclass(frozen=True, slots=True)
class AlgorithmComparisonRow:
    benchmark_id: str
    dataset_id: str
    partition_id: str
    partition_hash: str
    target_epsilon: float | None
    target_delta: float | None
    metric: str
    runtime_identity: str
    commit_sha: str
    baseline_algorithm: str
    candidate_algorithm: str
    n: int
    mean_difference: float
    sample_std_difference: float
    cohen_dz: float
    win_rate: float
    confidence: float
    difference_ci_low: float
    difference_ci_high: float
    p_value: float
    p_value_holm: float
    p_value_method: str


SummaryKey = tuple[
    str,
    str,
    str,
    str,
    str,
    float | None,
    float | None,
    str,
    str,
    str,
]
ComparisonContext = tuple[
    str,
    str,
    str,
    str,
    float | None,
    float | None,
    str,
    str,
    str,
]


def _validate_observation(observation: BenchmarkObservation) -> None:
    required = {
        "benchmark_id": observation.benchmark_id,
        "dataset_id": observation.dataset_id,
        "partition_id": observation.partition_id,
        "partition_hash": observation.partition_hash,
        "algorithm_id": observation.algorithm_id,
        "metric": observation.metric,
        "runtime_identity": observation.runtime_identity,
        "commit_sha": observation.commit_sha,
        "specification_hash": observation.specification_hash,
    }
    empty = [name for name, value in required.items() if not value.strip()]
    if empty:
        raise ValueError(f"benchmark observation has empty provenance fields: {empty}")
    if observation.runtime_identity not in {"root-simulator", "distributed-platform"}:
        raise ValueError(f"unknown runtime identity {observation.runtime_identity!r}")
    if not math.isfinite(observation.value):
        raise ValueError("benchmark observation value must be finite")
    if observation.target_epsilon is None:
        if observation.target_delta is not None:
            raise ValueError("non-private observation must not report target_delta")
    else:
        if observation.target_epsilon <= 0.0:
            raise ValueError("target_epsilon must be > 0")
        if observation.target_delta is None or not 0.0 < observation.target_delta < 1.0:
            raise ValueError("private observation requires target_delta in (0, 1)")


def validate_observations(
    observations: Iterable[BenchmarkObservation],
    *,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> tuple[BenchmarkObservation, ...]:
    normalized = tuple(observations)
    if not normalized:
        raise ValueError("at least one benchmark observation is required")
    for observation in normalized:
        _validate_observation(observation)

    seen: set[tuple[object, ...]] = set()
    replicate_groups: dict[SummaryKey, set[int]] = defaultdict(set)
    for observation in normalized:
        unique_key = (
            observation.benchmark_id,
            observation.dataset_id,
            observation.partition_id,
            observation.partition_hash,
            observation.algorithm_id,
            observation.target_epsilon,
            observation.target_delta,
            observation.seed,
            observation.metric,
            observation.runtime_identity,
            observation.commit_sha,
        )
        if unique_key in seen:
            raise ValueError(f"duplicate benchmark observation for key {unique_key!r}")
        seen.add(unique_key)
        replicate_groups[_summary_key(observation)].add(observation.seed)

    insufficient = {
        key: len(seeds)
        for key, seeds in replicate_groups.items()
        if len(seeds) < minimum_replicates
    }
    if insufficient:
        raise ValueError(
            "benchmark observations do not meet the minimum replicate count: "
            f"{insufficient}"
        )
    return normalized


def _summary_key(observation: BenchmarkObservation) -> SummaryKey:
    return (
        observation.benchmark_id,
        observation.dataset_id,
        observation.partition_id,
        observation.partition_hash,
        observation.algorithm_id,
        observation.target_epsilon,
        observation.target_delta,
        observation.metric,
        observation.runtime_identity,
        observation.commit_sha,
    )


def summarize_observations(
    observations: Iterable[BenchmarkObservation],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = 0,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> tuple[BenchmarkSummaryRow, ...]:
    normalized = validate_observations(
        observations, minimum_replicates=minimum_replicates
    )
    grouped: dict[SummaryKey, list[float]] = defaultdict(list)
    for observation in normalized:
        grouped[_summary_key(observation)].append(observation.value)

    rows: list[BenchmarkSummaryRow] = []
    for key in sorted(grouped, key=str):
        summary = summarize_metric(
            grouped[key],
            confidence=confidence,
            bootstrap_samples=bootstrap_samples,
            seed=bootstrap_seed,
            minimum_replicates=minimum_replicates,
        )
        (
            benchmark_id,
            dataset_id,
            partition_id,
            partition_hash,
            algorithm_id,
            target_epsilon,
            target_delta,
            metric,
            runtime_identity,
            commit_sha,
        ) = key
        rows.append(
            BenchmarkSummaryRow(
                benchmark_id=benchmark_id,
                dataset_id=dataset_id,
                partition_id=partition_id,
                partition_hash=partition_hash,
                algorithm_id=algorithm_id,
                target_epsilon=target_epsilon,
                target_delta=target_delta,
                metric=metric,
                runtime_identity=runtime_identity,
                commit_sha=commit_sha,
                n=summary.n,
                mean=summary.mean,
                sample_std=summary.sample_std,
                median=summary.median,
                minimum=summary.minimum,
                maximum=summary.maximum,
                confidence=summary.confidence,
                ci_low=summary.ci_low,
                ci_high=summary.ci_high,
                interval_method=summary.interval_method,
            )
        )
    return tuple(rows)


def _comparison_context(observation: BenchmarkObservation) -> ComparisonContext:
    return (
        observation.benchmark_id,
        observation.dataset_id,
        observation.partition_id,
        observation.partition_hash,
        observation.target_epsilon,
        observation.target_delta,
        observation.metric,
        observation.runtime_identity,
        observation.commit_sha,
    )


def compare_algorithms(
    observations: Iterable[BenchmarkObservation],
    *,
    baseline_algorithm: str,
    confidence: float = 0.95,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    randomization_samples: int = DEFAULT_RANDOMIZATION_SAMPLES,
    analysis_seed: int = 0,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> tuple[AlgorithmComparisonRow, ...]:
    """Run matched-seed comparisons against one baseline algorithm."""
    if not baseline_algorithm.strip():
        raise ValueError("baseline_algorithm must be non-empty")
    normalized = validate_observations(
        observations, minimum_replicates=minimum_replicates
    )

    values: dict[ComparisonContext, dict[str, dict[int, float]]] = defaultdict(
        lambda: defaultdict(dict)
    )
    for observation in normalized:
        values[_comparison_context(observation)][observation.algorithm_id][
            observation.seed
        ] = observation.value

    pending: list[tuple[ComparisonContext, str, PairedComparison]] = []
    raw_p_values: dict[str, float] = {}
    for context in sorted(values, key=str):
        algorithms = values[context]
        if baseline_algorithm not in algorithms:
            raise ValueError(
                f"baseline algorithm {baseline_algorithm!r} is missing for context {context!r}"
            )
        for candidate in sorted(algorithms):
            if candidate == baseline_algorithm:
                continue
            comparison = compare_paired_metrics(
                algorithms[baseline_algorithm],
                algorithms[candidate],
                baseline_name=baseline_algorithm,
                candidate_name=candidate,
                confidence=confidence,
                bootstrap_samples=bootstrap_samples,
                randomization_samples=randomization_samples,
                seed=analysis_seed,
                minimum_replicates=minimum_replicates,
            )
            comparison_id = _comparison_id(context, candidate)
            raw_p_values[comparison_id] = comparison.p_value
            pending.append((context, candidate, comparison))

    adjusted = holm_adjust(raw_p_values)
    rows: list[AlgorithmComparisonRow] = []
    for context, candidate, comparison in pending:
        (
            benchmark_id,
            dataset_id,
            partition_id,
            partition_hash,
            target_epsilon,
            target_delta,
            metric,
            runtime_identity,
            commit_sha,
        ) = context
        rows.append(
            AlgorithmComparisonRow(
                benchmark_id=benchmark_id,
                dataset_id=dataset_id,
                partition_id=partition_id,
                partition_hash=partition_hash,
                target_epsilon=target_epsilon,
                target_delta=target_delta,
                metric=metric,
                runtime_identity=runtime_identity,
                commit_sha=commit_sha,
                baseline_algorithm=baseline_algorithm,
                candidate_algorithm=candidate,
                n=comparison.n,
                mean_difference=comparison.mean_difference,
                sample_std_difference=comparison.sample_std_difference,
                cohen_dz=comparison.cohen_dz,
                win_rate=comparison.win_rate,
                confidence=comparison.confidence,
                difference_ci_low=comparison.difference_ci_low,
                difference_ci_high=comparison.difference_ci_high,
                p_value=comparison.p_value,
                p_value_holm=adjusted[_comparison_id(context, candidate)],
                p_value_method=comparison.p_value_method,
            )
        )
    return tuple(rows)


def _comparison_id(context: ComparisonContext, candidate: str) -> str:
    return "|".join(str(value) for value in (*context, candidate))


def summary_row_dict(row: BenchmarkSummaryRow) -> dict[str, object]:
    return dict(asdict(row))


def comparison_row_dict(row: AlgorithmComparisonRow) -> dict[str, object]:
    return dict(asdict(row))
