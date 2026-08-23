"""Deterministic benchmark-matrix planning for federated-learning runs."""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .statistics import DEFAULT_MINIMUM_REPLICATES

SUPPORTED_PARTITION_STRATEGIES = {
    "iid",
    "dirichlet",
    "pathological",
    "quantity_skew",
}
SUPPORTED_RUNTIME_IDENTITIES = {"root-simulator", "distributed-platform"}
ROOT_DATASETS = {"mnist", "fashionmnist", "cifar10", "cifar100"}


@dataclass(frozen=True, slots=True)
class BenchmarkPartition:
    name: str
    strategy: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "strategy": self.strategy,
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class BenchmarkCell:
    benchmark_id: str
    condition_id: str
    cell_id: str
    dataset_id: str
    algorithm_id: str
    partition_name: str
    partition_strategy: str
    partition_parameters: dict[str, Any]
    target_epsilon: float | None
    target_delta: float | None
    seed: int
    rounds: int
    runtime_identity: str

    def canonical_payload(self) -> dict[str, Any]:
        return dict(asdict(self))


@dataclass(frozen=True, slots=True)
class BenchmarkPlan:
    benchmark_id: str
    datasets: tuple[str, ...]
    algorithms: tuple[str, ...]
    partitions: tuple[BenchmarkPartition, ...]
    target_epsilons: tuple[float | None, ...]
    target_delta: float
    seeds: tuple[int, ...]
    rounds: int
    runtime_identity: str
    primary_metrics: tuple[str, ...] = (
        "global_accuracy",
        "global_loss",
        "mean_client_accuracy",
        "p10_client_accuracy",
        "worst_client_accuracy",
        "client_accuracy_std",
        "jain_accuracy_index",
        "mean_client_loss",
        "p90_client_loss",
        "worst_client_loss",
        "final_epsilon",
        "communication_rounds",
        "wall_clock_seconds",
    )
    schema_version: int = 2

    def validate(self, *, minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES) -> None:
        if minimum_replicates < 1:
            raise ValueError("minimum_replicates must be >= 1")
        if not self.benchmark_id.strip():
            raise ValueError("benchmark_id must be non-empty")
        if not self.datasets or any(not value.strip() for value in self.datasets):
            raise ValueError("at least one non-empty dataset id is required")
        if len(set(self.datasets)) != len(self.datasets):
            raise ValueError("dataset ids must be unique")
        if not self.algorithms or any(not value.strip() for value in self.algorithms):
            raise ValueError("at least one non-empty algorithm id is required")
        if len(set(self.algorithms)) != len(self.algorithms):
            raise ValueError("algorithm ids must be unique")
        if not self.partitions:
            raise ValueError("at least one partition condition is required")
        if len({partition.name for partition in self.partitions}) != len(self.partitions):
            raise ValueError("partition condition names must be unique")
        for partition in self.partitions:
            if not partition.name.strip():
                raise ValueError("partition condition names must be non-empty")
            if partition.strategy not in SUPPORTED_PARTITION_STRATEGIES:
                raise ValueError(
                    f"unsupported partition strategy {partition.strategy!r}; "
                    f"use one of {sorted(SUPPORTED_PARTITION_STRATEGIES)}"
                )
        if not self.target_epsilons:
            raise ValueError("target_epsilons must include at least one privacy condition")
        for epsilon in self.target_epsilons:
            if epsilon is not None and epsilon <= 0.0:
                raise ValueError("private target epsilons must be > 0")
        if not 0.0 < self.target_delta < 1.0:
            raise ValueError("target_delta must lie in (0, 1)")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be unique")
        if len(self.seeds) < minimum_replicates:
            raise ValueError(
                f"benchmark requires at least {minimum_replicates} unique seeds"
            )
        if self.rounds <= 0:
            raise ValueError("rounds must be > 0")
        if self.runtime_identity not in SUPPORTED_RUNTIME_IDENTITIES:
            raise ValueError(
                f"runtime_identity must be one of {sorted(SUPPORTED_RUNTIME_IDENTITIES)}"
            )
        if not self.primary_metrics or any(not metric.strip() for metric in self.primary_metrics):
            raise ValueError("primary_metrics must contain non-empty metric names")
        if self.runtime_identity == "root-simulator":
            normalized_datasets = {value.lower() for value in self.datasets}
            unsupported_datasets = normalized_datasets - ROOT_DATASETS
            if unsupported_datasets:
                raise ValueError(
                    "root-simulator dataset is unsupported; "
                    f"unsupported={sorted(unsupported_datasets)}, "
                    f"supported={sorted(ROOT_DATASETS)}"
                )
            unsupported_root = set(self.algorithms) - {"fedavg", "fedprox", "scaffold"}
            if unsupported_root:
                raise ValueError(
                    "root-simulator supports only fedavg/fedprox/scaffold; "
                    f"unsupported={sorted(unsupported_root)}"
                )
            if "scaffold" in self.algorithms and any(
                epsilon is not None for epsilon in self.target_epsilons
            ):
                raise ValueError(
                    "root-simulator does not permit DP-enabled SCAFFOLD; split private "
                    "and non-private SCAFFOLD into separate benchmark plans"
                )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "benchmark_id": self.benchmark_id,
            "datasets": list(self.datasets),
            "algorithms": list(self.algorithms),
            "partitions": [partition.canonical_payload() for partition in self.partitions],
            "target_epsilons": list(self.target_epsilons),
            "target_delta": self.target_delta,
            "seeds": list(self.seeds),
            "rounds": self.rounds,
            "runtime_identity": self.runtime_identity,
            "primary_metrics": list(self.primary_metrics),
        }

    def plan_hash(self) -> str:
        canonical = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def expand(
        self, *, minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES
    ) -> tuple[BenchmarkCell, ...]:
        self.validate(minimum_replicates=minimum_replicates)
        cells: list[BenchmarkCell] = []
        for dataset_id, algorithm_id, partition, epsilon, seed in itertools.product(
            self.datasets,
            self.algorithms,
            self.partitions,
            self.target_epsilons,
            self.seeds,
        ):
            condition_payload = {
                "benchmark_id": self.benchmark_id,
                "dataset_id": dataset_id,
                "algorithm_id": algorithm_id,
                "partition": partition.canonical_payload(),
                "target_epsilon": epsilon,
                "target_delta": self.target_delta if epsilon is not None else None,
                "rounds": self.rounds,
                "runtime_identity": self.runtime_identity,
            }
            condition_id = _payload_hash(condition_payload)[:20]
            cell_payload = {**condition_payload, "seed": seed}
            cell_id = _payload_hash(cell_payload)[:24]
            cells.append(
                BenchmarkCell(
                    benchmark_id=self.benchmark_id,
                    condition_id=condition_id,
                    cell_id=cell_id,
                    dataset_id=dataset_id,
                    algorithm_id=algorithm_id,
                    partition_name=partition.name,
                    partition_strategy=partition.strategy,
                    partition_parameters=dict(partition.parameters),
                    target_epsilon=epsilon,
                    target_delta=self.target_delta if epsilon is not None else None,
                    seed=seed,
                    rounds=self.rounds,
                    runtime_identity=self.runtime_identity,
                )
            )
        if len({cell.cell_id for cell in cells}) != len(cells):
            raise RuntimeError("benchmark cell hash collision detected")
        return tuple(cells)


def _payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def standard_partition_conditions() -> tuple[BenchmarkPartition, ...]:
    return (
        BenchmarkPartition("iid", "iid", {}),
        BenchmarkPartition("dirichlet-0.1", "dirichlet", {"alpha": 0.1}),
        BenchmarkPartition("dirichlet-0.5", "dirichlet", {"alpha": 0.5}),
        BenchmarkPartition("dirichlet-10", "dirichlet", {"alpha": 10.0}),
        BenchmarkPartition(
            "pathological-2-classes",
            "pathological",
            {"classes_per_client": 2},
        ),
        BenchmarkPartition(
            "quantity-skew-1.0",
            "quantity_skew",
            {"quantity_skew_sigma": 1.0},
        ),
    )


def build_benchmark_plan(
    *,
    benchmark_id: str,
    datasets: Iterable[str],
    algorithms: Iterable[str],
    seeds: Iterable[int] = (11, 23, 37, 53, 71),
    target_epsilons: Iterable[float | None] = (None, 1.0, 2.0, 4.0, 8.0),
    target_delta: float = 1e-5,
    rounds: int = 100,
    runtime_identity: str = "root-simulator",
    partitions: Iterable[BenchmarkPartition] | None = None,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> BenchmarkPlan:
    plan = BenchmarkPlan(
        benchmark_id=benchmark_id,
        datasets=tuple(datasets),
        algorithms=tuple(algorithms),
        partitions=tuple(partitions or standard_partition_conditions()),
        target_epsilons=tuple(target_epsilons),
        target_delta=target_delta,
        seeds=tuple(int(seed) for seed in seeds),
        rounds=rounds,
        runtime_identity=runtime_identity,
    )
    plan.validate(minimum_replicates=minimum_replicates)
    return plan
