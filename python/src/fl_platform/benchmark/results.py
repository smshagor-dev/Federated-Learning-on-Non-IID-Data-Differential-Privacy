"""Aggregation and comparison of repeated benchmark observations."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass

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
    partition_hashes_digest: str
    partition_hash_count: int
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
    partition_hashes_digest: str
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


def _summary_key(observation: BenchmarkObservation) -> SummaryKey:
    return (
        observation.benchmark_id,
        observation.dataset_id,
        observation.partition_id,
        observation.algorithm_id,
        observation.target_epsilon,
        observation.target_delta,
        observation.metric,
        observation.runtime_identity,
        observation.commit_sha,
    )


def _comparison_context(observation: BenchmarkObservation) -> ComparisonContext:
    return (
        observation.benchmark_id,
        observation.dataset_id,
        observation.partition_id,
        observation.target_epsilon,
        observation.target_delta,
        observation.metric,
        observation.runtime_identity,
        observation.commit_sha,
    )


def _partition_digest(seed_to_hash: dict[int, str]) -> str:
    payload = "\n".join(f"{seed}:{seed_to_hash[seed]}" for seed in sorted(seed_to_hash))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    values: dict[SummaryKey, list[float]] = defaultdict(list)
    hashes: dict[SummaryKey, dict[int, str]] = defaultdict(dict)
    for observation in normalized:
        key = _summary_key(observation)
        values[key].append(observation.value)
        hashes[key][observation.seed] = observation.partition_hash

    rows: list[BenchmarkSummaryRow] = []
    for key in sorted(values, key=str):
        summary = summarize_metric(
            values[key],
            confidence=confidence,
            bootstrap_samples=bootstrap_samples,
            seed=bootstrap_seed,
            minimum_replicates=minimum_replicates,
        )
        (
            benchmark_id,
            dataset_id,
            partition_id,
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
                partition_hashes_digest=_partition_digest(hashes[key]),
                partition_hash_count=len(hashes[key]),
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
    """Run matched-seed comparisons and verify exact partition parity."""
    if not baseline_algorithm.strip():
        raise ValueError("baseline_algorithm must be non-empty")
    normalized = validate_observations(
        observations, minimum_replicates=minimum_replicates
    )

    grouped: dict[ComparisonContext, dict[str, dict[int, BenchmarkObservation]]] = (
        defaultdict(lambda: defaultdict(dict))
    )
    for observation in normalized:
        grouped[_comparison_context(observation)][observation.algorithm_id][
            observation.seed
        ] = observation

    pending: list[tuple[ComparisonContext, str, PairedComparison, dict[int, str]]] = []
    raw_p_values: dict[str, float] = {}
    for context in sorted(grouped, key=str):
        algorithms = grouped[context]
        if baseline_algorithm not in algorithms:
            raise ValueError(
                f"baseline algorithm {baseline_algorithm!r} is missing "
                f"for context {context!r}"
            )
        baseline_rows = algorithms[baseline_algorithm]
        for candidate in sorted(algorithms):
            if candidate == baseline_algorithm:
                continue
            candidate_rows = algorithms[candidate]
            if set(baseline_rows) != set(candidate_rows):
                raise ValueError(
                    "paired algorithm comparison requires identical seed sets"
                )
            seed_hashes: dict[int, str] = {}
            for seed in sorted(baseline_rows):
                baseline_hash = baseline_rows[seed].partition_hash
                candidate_hash = candidate_rows[seed].partition_hash
                if baseline_hash != candidate_hash:
                    raise ValueError(
                        "matched algorithms used different exact partitions for "
                        f"seed={seed}: {baseline_hash} != {candidate_hash}"
                    )
                seed_hashes[seed] = baseline_hash

            comparison = compare_paired_metrics(
                {seed: row.value for seed, row in baseline_rows.items()},
                {seed: row.value for seed, row in candidate_rows.items()},
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
            pending.append((context, candidate, comparison, seed_hashes))

    adjusted = holm_adjust(raw_p_values)
    rows: list[AlgorithmComparisonRow] = []
    for context, candidate, comparison, seed_hashes in pending:
        (
            benchmark_id,
            dataset_id,
            partition_id,
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
                partition_hashes_digest=_partition_digest(seed_hashes),
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
