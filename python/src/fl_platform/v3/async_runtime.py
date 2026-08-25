"""Asynchronous federated-learning primitives for the v3 platform."""

from __future__ import annotations

import math
from dataclasses import dataclass

Vector = tuple[float, ...]


@dataclass(frozen=True)
class AsyncUpdate:
    client_id: str
    base_version: int
    delta: Vector


@dataclass(frozen=True)
class AsyncApplyResult:
    accepted: bool
    version: int
    staleness: int
    weight: float
    reason: str | None = None


def staleness_weight(staleness: int, *, beta: float = 1.0) -> float:
    if staleness < 0:
        raise ValueError("staleness must be non-negative")
    if beta <= 0.0 or not math.isfinite(beta):
        raise ValueError("beta must be finite and positive")
    return 1.0 / (1.0 + beta * staleness)


class AsyncModelState:
    """Apply staleness-aware client deltas without a round barrier."""

    def __init__(
        self,
        initial: Vector,
        *,
        mixing_alpha: float = 0.5,
        max_staleness: int | None = None,
    ) -> None:
        if not initial or not all(math.isfinite(value) for value in initial):
            raise ValueError("initial model must be a non-empty finite vector")
        if not 0.0 < mixing_alpha <= 1.0:
            raise ValueError("mixing_alpha must be in (0, 1]")
        if max_staleness is not None and max_staleness < 0:
            raise ValueError("max_staleness must be non-negative")
        self._model = tuple(float(value) for value in initial)
        self._version = 0
        self._mixing_alpha = float(mixing_alpha)
        self._max_staleness = max_staleness

    @property
    def model(self) -> Vector:
        return self._model

    @property
    def version(self) -> int:
        return self._version

    def apply(self, update: AsyncUpdate) -> AsyncApplyResult:
        if update.base_version > self._version:
            return AsyncApplyResult(False, self._version, 0, 0.0, "future version")
        staleness = self._version - update.base_version
        if self._max_staleness is not None and staleness > self._max_staleness:
            return AsyncApplyResult(False, self._version, staleness, 0.0, "too stale")
        if len(update.delta) != len(self._model):
            return AsyncApplyResult(False, self._version, staleness, 0.0, "dimension mismatch")
        if not all(math.isfinite(value) for value in update.delta):
            return AsyncApplyResult(False, self._version, staleness, 0.0, "non-finite update")

        weight = self._mixing_alpha * staleness_weight(staleness)
        self._model = tuple(
            value + weight * delta
            for value, delta in zip(self._model, update.delta, strict=True)
        )
        self._version += 1
        return AsyncApplyResult(True, self._version, staleness, weight)


__all__ = ["AsyncApplyResult", "AsyncModelState", "AsyncUpdate", "staleness_weight"]
