"""Execution bridge for v3 aggregation primitives.

The bridge consumes admitted worker results and produces one aggregate model
update. It deliberately operates on flattened numeric vectors so orchestration
can validate policy independently from the tensor/runtime backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from fl_platform.v3.algorithm_suite import fednova_aggregate
from fl_platform.v3.capabilities import (
    CapabilityRequest,
    validate_capability_request,
)
from fl_platform.v3.robust_aggregation import (
    coordinate_median,
    krum,
    multi_krum,
    trimmed_mean,
)
from fl_platform.v3.server_optimizers import (
    AdaptiveServerOptimizer,
    OptimizerConfig,
)
from fl_platform.workers import TrainingResult

Vector = tuple[float, ...]
ROBUST_STRATEGIES = frozenset({"median", "trimmed_mean", "krum", "multi_krum"})


@dataclass(frozen=True)
class AggregationConfig:
    algorithm: str = "fedavg"
    strategy: str = "mean"
    weighting: str = "sample_count"
    trim_ratio: float = 0.1
    byzantine_clients: int = 0
    multi_krum_select: int = 1
    differential_privacy: bool = False
    secure_aggregation: bool = False
    optimizer: OptimizerConfig | None = None


@dataclass(frozen=True)
class AggregationOutcome:
    update: Vector
    strategy: str
    client_count: int
    total_samples: int
    optimizer: str | None


class V3AggregationEngine:
    """Aggregate worker updates with optional robust/adaptive server logic."""

    def __init__(self, dimension: int, config: AggregationConfig) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        algorithm = config.algorithm.lower()
        strategy = config.strategy.lower()
        if strategy not in {"mean", *ROBUST_STRATEGIES}:
            raise ValueError(f"unknown aggregation strategy: {config.strategy}")
        weighting = config.weighting.lower()
        if weighting not in {"uniform", "sample_count"}:
            raise ValueError("weighting must be uniform or sample_count")
        if strategy in ROBUST_STRATEGIES and weighting != "uniform":
            raise ValueError("robust aggregation requires uniform weighting")
        if config.byzantine_clients < 0:
            raise ValueError("byzantine_clients must be non-negative")
        if algorithm == "fednova" and strategy != "mean":
            raise ValueError("FedNova cannot be combined with robust aggregation")
        if algorithm == "fednova" and config.optimizer is not None:
            raise ValueError("FedNova + adaptive server optimizer is not validated")
        if algorithm == "fednova" and config.secure_aggregation:
            raise ValueError("FedNova secure aggregation is not release-validated")

        validate_capability_request(
            CapabilityRequest(
                algorithm=algorithm,
                differential_privacy=config.differential_privacy,
                secure_aggregation=config.secure_aggregation,
                robust_aggregation=strategy in ROBUST_STRATEGIES,
            )
        )

        self._dimension = dimension
        self._config = config
        self._algorithm = algorithm
        self._strategy = strategy
        self._weighting = weighting
        self._optimizer = (
            AdaptiveServerOptimizer(dimension, config.optimizer)
            if config.optimizer is not None
            else None
        )

    def aggregate(self, results: list[TrainingResult]) -> AggregationOutcome:
        vectors = [self._result_update(result) for result in results]
        if not vectors:
            raise ValueError("at least one accepted worker result is required")

        aggregate = self._aggregate_vectors(vectors, results)
        optimizer_name: str | None = None
        if self._optimizer is not None:
            aggregate = self._optimizer.step(aggregate)
            optimizer = self._config.optimizer
            optimizer_name = optimizer.name.lower() if optimizer is not None else None

        return AggregationOutcome(
            update=aggregate,
            strategy=self._strategy,
            client_count=len(results),
            total_samples=sum(result.sample_count for result in results),
            optimizer=optimizer_name,
        )

    def _result_update(self, result: TrainingResult) -> Vector:
        update = result.model_update
        if update is None:
            raise ValueError(
                f"client {result.client_id} did not provide a model update"
            )
        if len(update) != self._dimension:
            raise ValueError(
                f"client {result.client_id} update dimension {len(update)}; "
                f"expected {self._dimension}"
            )
        vector = tuple(float(value) for value in update)
        if not all(math.isfinite(value) for value in vector):
            raise ValueError(
                f"client {result.client_id} update contains non-finite values"
            )
        if result.sample_count <= 0:
            raise ValueError(f"client {result.client_id} sample_count must be positive")
        return vector

    def _aggregate_vectors(
        self,
        vectors: list[Vector],
        results: list[TrainingResult],
    ) -> Vector:
        if self._algorithm == "fednova":
            return self._fednova(vectors, results)
        if self._strategy == "mean":
            return self._mean(vectors, results)
        if self._strategy == "median":
            return coordinate_median(vectors)
        if self._strategy == "trimmed_mean":
            return trimmed_mean(vectors, trim_ratio=self._config.trim_ratio)
        if self._strategy == "krum":
            return krum(
                vectors,
                byzantine_clients=self._config.byzantine_clients,
            )
        return multi_krum(
            vectors,
            byzantine_clients=self._config.byzantine_clients,
            select=self._config.multi_krum_select,
        )

    def _fednova(
        self,
        vectors: list[Vector],
        results: list[TrainingResult],
    ) -> Vector:
        if self._weighting == "uniform":
            weights = tuple(1.0 for _ in results)
        else:
            weights = tuple(float(result.sample_count) for result in results)
        return fednova_aggregate(
            tuple(vectors),
            local_steps=tuple(result.local_step_count for result in results),
            weights=weights,
        )

    def _mean(
        self,
        vectors: list[Vector],
        results: list[TrainingResult],
    ) -> Vector:
        if self._weighting == "uniform":
            weights = [1.0 / len(vectors)] * len(vectors)
        else:
            total_samples = sum(result.sample_count for result in results)
            weights = [result.sample_count / total_samples for result in results]
        return tuple(
            sum(
                weight * vector[index]
                for weight, vector in zip(weights, vectors, strict=True)
            )
            for index in range(self._dimension)
        )


__all__ = [
    "AggregationConfig",
    "AggregationOutcome",
    "ROBUST_STRATEGIES",
    "V3AggregationEngine",
]
