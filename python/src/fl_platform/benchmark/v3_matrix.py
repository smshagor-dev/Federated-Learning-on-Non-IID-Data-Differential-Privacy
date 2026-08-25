"""Scientific benchmark-v3 planning and evidence completeness.

This additive layer keeps the benchmark-v2 schema stable while adding the v3
release dimensions: algorithm, workload, partition, privacy target, attack,
robust aggregation, system heterogeneity, and repeated seeds.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from fl_platform.benchmark.matrix import (
    SUPPORTED_PARTITION_STRATEGIES,
    BenchmarkPartition,
    standard_partition_conditions,
)
from fl_platform.benchmark.statistics import (
    DEFAULT_BOOTSTRAP_SAMPLES,
    DEFAULT_MINIMUM_REPLICATES,
    MetricSummary,
    summarize_metric,
)
from fl_platform.v3.attacks import AttackKind
from fl_platform.v3.capabilities import CapabilityRequest, validate_capability_request
from fl_platform.v3.workloads import get_workload

ROBUST_STRATEGIES = frozenset({"median", "trimmed_mean", "krum", "multi_krum"})
AGGREGATION_STRATEGIES = frozenset({"mean", *ROBUST_STRATEGIES})


@dataclass(frozen=True, slots=True)
class PrivacyCondition:
    name: str
    target_epsilon: float | None
    target_delta: float | None = None

    def validate(self) -> None:
        if not self.name.strip():
            raise ValueError("privacy condition name must not be empty")
        if self.target_epsilon is None:
            if self.target_delta is not None:
                raise ValueError("non-private condition must not set target_delta")
            return
        if self.target_epsilon <= 0.0 or not math.isfinite(self.target_epsilon):
            raise ValueError("target_epsilon must be finite and positive")
        if self.target_delta is None or not 0.0 < self.target_delta < 1.0:
            raise ValueError("private condition requires target_delta in (0, 1)")


@dataclass(frozen=True, slots=True)
class HeterogeneityCondition:
    name: str
    description: str

    def validate(self) -> None:
        if not self.name.strip() or not self.description.strip():
            raise ValueError("heterogeneity condition requires name and description")


@dataclass(frozen=True, slots=True)
class V3BenchmarkCell:
    benchmark_id: str
    condition_id: str
    cell_id: str
    dataset_id: str
    algorithm_id: str
    partition_name: str
    partition_strategy: str
    privacy_name: str
    target_epsilon: float | None
    target_delta: float | None
    attack: str
    aggregation_strategy: str
    heterogeneity: str
    seed: int
    rounds: int
    runtime_identity: str
    runnable: bool
    exclusion_reason: str = ""


@dataclass(frozen=True, slots=True)
class V3BenchmarkPlan:
    benchmark_id: str
    datasets: tuple[str, ...]
    algorithms: tuple[str, ...]
    partitions: tuple[BenchmarkPartition, ...]
    privacy_conditions: tuple[PrivacyCondition, ...]
    attacks: tuple[AttackKind, ...]
    aggregation_strategies: tuple[str, ...]
    heterogeneity_conditions: tuple[HeterogeneityCondition, ...]
    seeds: tuple[int, ...]
    rounds: int
    runtime_identity: str = "distributed-platform"
    require_validated_workloads: bool = False
    primary_metrics: tuple[str, ...] = (
        "global_accuracy",
        "global_loss",
        "mean_client_accuracy",
        "worst_client_accuracy",
        "final_epsilon",
        "attack_success_rate",
        "communication_bytes",
        "wall_clock_seconds",
    )
    schema_version: int = 3

    def validate(self, *, minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES) -> None:
        if not self.benchmark_id.strip():
            raise ValueError("benchmark_id must not be empty")
        if not self.datasets or len(set(self.datasets)) != len(self.datasets):
            raise ValueError("datasets must be non-empty and unique")
        if not self.algorithms or len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("algorithms must be non-empty and unique")
        if not self.partitions:
            raise ValueError("at least one partition condition is required")
        if not self.privacy_conditions:
            raise ValueError("at least one privacy condition is required")
        if not self.attacks:
            raise ValueError("at least one attack condition is required")
        if not self.aggregation_strategies:
            raise ValueError("at least one aggregation strategy is required")
        if not self.heterogeneity_conditions:
            raise ValueError("at least one heterogeneity condition is required")
        if len(self.seeds) < minimum_replicates or len(set(self.seeds)) != len(
            self.seeds
        ):
            raise ValueError(
                f"at least {minimum_replicates} unique benchmark seeds are required"
            )
        if self.rounds <= 0:
            raise ValueError("rounds must be positive")
        if self.runtime_identity not in {"root-simulator", "distributed-platform"}:
            raise ValueError("unknown benchmark runtime identity")
        if not self.primary_metrics or any(
            not metric.strip() for metric in self.primary_metrics
        ):
            raise ValueError("primary_metrics must contain non-empty metric names")
        if len(set(self.primary_metrics)) != len(self.primary_metrics):
            raise ValueError("primary_metrics must be unique")

        for dataset in self.datasets:
            get_workload(dataset, require_validated=self.require_validated_workloads)
        for partition in self.partitions:
            if not partition.name.strip():
                raise ValueError("partition name must not be empty")
            if partition.strategy not in SUPPORTED_PARTITION_STRATEGIES:
                raise ValueError(
                    f"unsupported partition strategy: {partition.strategy}"
                )
        for privacy in self.privacy_conditions:
            privacy.validate()
        for heterogeneity in self.heterogeneity_conditions:
            heterogeneity.validate()
        for strategy in self.aggregation_strategies:
            if strategy not in AGGREGATION_STRATEGIES:
                raise ValueError(f"unknown aggregation strategy: {strategy}")

    def plan_hash(self) -> str:
        return _payload_hash(
            {
                "schema_version": self.schema_version,
                "benchmark_id": self.benchmark_id,
                "datasets": self.datasets,
                "algorithms": self.algorithms,
                "partitions": [
                    partition.canonical_payload() for partition in self.partitions
                ],
                "privacy_conditions": [
                    asdict(item) for item in self.privacy_conditions
                ],
                "attacks": [attack.value for attack in self.attacks],
                "aggregation_strategies": self.aggregation_strategies,
                "heterogeneity_conditions": [
                    asdict(item) for item in self.heterogeneity_conditions
                ],
                "seeds": self.seeds,
                "rounds": self.rounds,
                "runtime_identity": self.runtime_identity,
                "require_validated_workloads": self.require_validated_workloads,
                "primary_metrics": self.primary_metrics,
            }
        )

    def expand(
        self, *, minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES
    ) -> tuple[V3BenchmarkCell, ...]:
        self.validate(minimum_replicates=minimum_replicates)
        cells: list[V3BenchmarkCell] = []
        axes = itertools.product(
            self.datasets,
            self.algorithms,
            self.partitions,
            self.privacy_conditions,
            self.attacks,
            self.aggregation_strategies,
            self.heterogeneity_conditions,
            self.seeds,
        )
        for (
            dataset_id,
            algorithm_id,
            partition,
            privacy,
            attack,
            strategy,
            heterogeneity,
            seed,
        ) in axes:
            runnable, reason = _capability_status(
                algorithm_id=algorithm_id,
                privacy=privacy,
                aggregation_strategy=strategy,
                runtime_identity=self.runtime_identity,
            )
            condition_payload = {
                "benchmark_id": self.benchmark_id,
                "dataset_id": dataset_id,
                "algorithm_id": algorithm_id,
                "partition": partition.canonical_payload(),
                "privacy": asdict(privacy),
                "attack": attack.value,
                "aggregation_strategy": strategy,
                "heterogeneity": heterogeneity.name,
                "rounds": self.rounds,
                "runtime_identity": self.runtime_identity,
            }
            condition_id = _payload_hash(condition_payload)[:20]
            cell_id = _payload_hash({**condition_payload, "seed": seed})[:24]
            cells.append(
                V3BenchmarkCell(
                    benchmark_id=self.benchmark_id,
                    condition_id=condition_id,
                    cell_id=cell_id,
                    dataset_id=dataset_id,
                    algorithm_id=algorithm_id,
                    partition_name=partition.name,
                    partition_strategy=partition.strategy,
                    privacy_name=privacy.name,
                    target_epsilon=privacy.target_epsilon,
                    target_delta=privacy.target_delta,
                    attack=attack.value,
                    aggregation_strategy=strategy,
                    heterogeneity=heterogeneity.name,
                    seed=seed,
                    rounds=self.rounds,
                    runtime_identity=self.runtime_identity,
                    runnable=runnable,
                    exclusion_reason=reason,
                )
            )
        if len({cell.cell_id for cell in cells}) != len(cells):
            raise RuntimeError("v3 benchmark cell hash collision detected")
        return tuple(cells)


@dataclass(frozen=True, slots=True)
class V3BenchmarkObservation:
    benchmark_id: str
    condition_id: str
    cell_id: str
    seed: int
    metric: str
    value: float
    commit_sha: str
    specification_hash: str


@dataclass(frozen=True, slots=True)
class V3EvidenceReport:
    expected_observations: int
    received_observations: int
    missing: tuple[tuple[str, str], ...]
    unexpected: tuple[tuple[str, str], ...]
    excluded_cells: int

    @property
    def complete(self) -> bool:
        return not self.missing and not self.unexpected

    def require_complete(self) -> None:
        if not self.complete:
            raise ValueError(
                "v3 benchmark evidence is incomplete: "
                f"missing={len(self.missing)}, unexpected={len(self.unexpected)}"
            )


@dataclass(frozen=True, slots=True)
class V3MetricSummary:
    condition_id: str
    metric: str
    summary: MetricSummary


def validate_v3_evidence(
    plan: V3BenchmarkPlan,
    observations: Iterable[V3BenchmarkObservation],
    *,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> V3EvidenceReport:
    cells = plan.expand(minimum_replicates=minimum_replicates)
    runnable = {cell.cell_id: cell for cell in cells if cell.runnable}
    expected = {
        (cell.cell_id, metric)
        for cell in runnable.values()
        for metric in plan.primary_metrics
    }

    seen: set[tuple[str, str]] = set()
    unexpected: set[tuple[str, str]] = set()
    specification_hash = plan.plan_hash()
    for observation in observations:
        if observation.benchmark_id != plan.benchmark_id:
            raise ValueError("benchmark observation belongs to another benchmark")
        if not observation.metric.strip() or not math.isfinite(observation.value):
            raise ValueError("benchmark observation metric/value is invalid")
        if not observation.commit_sha.strip():
            raise ValueError("benchmark observation must record commit_sha")
        if observation.specification_hash != specification_hash:
            raise ValueError("benchmark observation specification hash mismatch")
        key = (observation.cell_id, observation.metric)
        if key in seen:
            raise ValueError(f"duplicate v3 benchmark observation: {key!r}")
        seen.add(key)

        cell = runnable.get(observation.cell_id)
        if cell is None:
            unexpected.add(key)
            continue
        if (
            observation.condition_id != cell.condition_id
            or observation.seed != cell.seed
        ):
            raise ValueError("benchmark observation cell provenance mismatch")
        if observation.metric not in plan.primary_metrics:
            unexpected.add(key)

    return V3EvidenceReport(
        expected_observations=len(expected),
        received_observations=len(seen),
        missing=tuple(sorted(expected - seen)),
        unexpected=tuple(sorted(unexpected | (seen - expected))),
        excluded_cells=sum(not cell.runnable for cell in cells),
    )


def summarize_v3_evidence(
    plan: V3BenchmarkPlan,
    observations: Iterable[V3BenchmarkObservation],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    bootstrap_seed: int = 0,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> tuple[V3MetricSummary, ...]:
    normalized = tuple(observations)
    report = validate_v3_evidence(
        plan,
        normalized,
        minimum_replicates=minimum_replicates,
    )
    report.require_complete()

    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    for observation in normalized:
        values[(observation.condition_id, observation.metric)].append(observation.value)

    summaries: list[V3MetricSummary] = []
    for (condition_id, metric), metric_values in sorted(values.items()):
        summaries.append(
            V3MetricSummary(
                condition_id=condition_id,
                metric=metric,
                summary=summarize_metric(
                    metric_values,
                    confidence=confidence,
                    bootstrap_samples=bootstrap_samples,
                    seed=bootstrap_seed,
                    minimum_replicates=minimum_replicates,
                ),
            )
        )
    return tuple(summaries)


def standard_privacy_conditions() -> tuple[PrivacyCondition, ...]:
    return (
        PrivacyCondition("non-private", None, None),
        PrivacyCondition("epsilon-1", 1.0, 1e-5),
        PrivacyCondition("epsilon-4", 4.0, 1e-5),
        PrivacyCondition("epsilon-8", 8.0, 1e-5),
    )


def standard_heterogeneity_conditions() -> tuple[HeterogeneityCondition, ...]:
    return (
        HeterogeneityCondition("nominal", "uniformly available baseline clients"),
        HeterogeneityCondition("compute-skew", "heterogeneous local compute speed"),
        HeterogeneityCondition("network-skew", "bandwidth and latency heterogeneity"),
        HeterogeneityCondition(
            "availability-dropout", "client availability and dropout"
        ),
        HeterogeneityCondition(
            "edge-constrained",
            "resource and payload constrained edge clients",
        ),
    )


def build_v3_benchmark_plan(
    *,
    benchmark_id: str,
    datasets: Iterable[str],
    algorithms: Iterable[str],
    seeds: Iterable[int] = (11, 23, 37, 53, 71),
    partitions: Iterable[BenchmarkPartition] | None = None,
    privacy_conditions: Iterable[PrivacyCondition] | None = None,
    attacks: Iterable[AttackKind] = tuple(AttackKind),
    aggregation_strategies: Iterable[str] = ("mean", "median", "trimmed_mean"),
    heterogeneity_conditions: Iterable[HeterogeneityCondition] | None = None,
    rounds: int = 100,
    runtime_identity: str = "distributed-platform",
    require_validated_workloads: bool = False,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> V3BenchmarkPlan:
    plan = V3BenchmarkPlan(
        benchmark_id=benchmark_id,
        datasets=tuple(datasets),
        algorithms=tuple(algorithms),
        partitions=tuple(partitions or standard_partition_conditions()),
        privacy_conditions=tuple(privacy_conditions or standard_privacy_conditions()),
        attacks=tuple(attacks),
        aggregation_strategies=tuple(aggregation_strategies),
        heterogeneity_conditions=tuple(
            heterogeneity_conditions or standard_heterogeneity_conditions()
        ),
        seeds=tuple(int(seed) for seed in seeds),
        rounds=rounds,
        runtime_identity=runtime_identity,
        require_validated_workloads=require_validated_workloads,
    )
    plan.validate(minimum_replicates=minimum_replicates)
    return plan


def _capability_status(
    *,
    algorithm_id: str,
    privacy: PrivacyCondition,
    aggregation_strategy: str,
    runtime_identity: str,
) -> tuple[bool, str]:
    from fl_platform.algorithms.registry import registered_algorithm_names

    if algorithm_id not in registered_algorithm_names():
        return False, "algorithm is not registered in canonical worker runtime"
    if runtime_identity == "root-simulator" and algorithm_id not in {
        "fedavg",
        "fedprox",
        "scaffold",
    }:
        return False, "algorithm is not implemented by root-simulator"
    try:
        validate_capability_request(
            CapabilityRequest(
                algorithm=algorithm_id,
                differential_privacy=privacy.target_epsilon is not None,
                robust_aggregation=aggregation_strategy in ROBUST_STRATEGIES,
            )
        )
    except ValueError as exc:
        return False, str(exc)
    return True, ""


def _payload_hash(payload: object) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "AGGREGATION_STRATEGIES",
    "ROBUST_STRATEGIES",
    "HeterogeneityCondition",
    "PrivacyCondition",
    "V3BenchmarkCell",
    "V3BenchmarkObservation",
    "V3BenchmarkPlan",
    "V3EvidenceReport",
    "V3MetricSummary",
    "build_v3_benchmark_plan",
    "standard_heterogeneity_conditions",
    "standard_privacy_conditions",
    "summarize_v3_evidence",
    "validate_v3_evidence",
]
