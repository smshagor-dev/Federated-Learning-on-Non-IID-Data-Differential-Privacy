"""Adaptive server optimizers used by the v3 federated algorithm expansion."""

from __future__ import annotations

import math
from dataclasses import dataclass

Vector = tuple[float, ...]


@dataclass(frozen=True)
class OptimizerConfig:
    name: str
    learning_rate: float = 0.01
    beta1: float = 0.9
    beta2: float = 0.99
    epsilon: float = 1e-8


class AdaptiveServerOptimizer:
    """Stateful FedAdam/FedYogi/FedAdagrad server-update primitive."""

    def __init__(self, dimension: int, config: OptimizerConfig) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        name = config.name.lower()
        if name not in {"fedadam", "fedyogi", "fedadagrad"}:
            raise ValueError("optimizer must be fedadam, fedyogi, or fedadagrad")
        if config.learning_rate <= 0.0 or config.epsilon <= 0.0:
            raise ValueError("learning_rate and epsilon must be positive")
        if not 0.0 <= config.beta1 < 1.0 or not 0.0 <= config.beta2 < 1.0:
            raise ValueError("beta1 and beta2 must be in [0, 1)")
        self.config = config
        self._name = name
        self._m: list[float] = [0.0] * dimension
        self._v: list[float] = [0.0] * dimension

    def step(self, aggregate_delta: Vector) -> Vector:
        if len(aggregate_delta) != len(self._m):
            raise ValueError("aggregate_delta dimension mismatch")
        if not all(math.isfinite(value) for value in aggregate_delta):
            raise ValueError("aggregate_delta contains a non-finite value")

        cfg = self.config
        result: list[float] = []
        for index, gradient in enumerate(aggregate_delta):
            self._m[index] = (
                cfg.beta1 * self._m[index] + (1.0 - cfg.beta1) * gradient
            )
            squared = gradient * gradient
            if self._name == "fedadam":
                self._v[index] = (
                    cfg.beta2 * self._v[index]
                    + (1.0 - cfg.beta2) * squared
                )
            elif self._name == "fedyogi":
                direction = 1.0 if self._v[index] - squared >= 0.0 else -1.0
                self._v[index] -= (1.0 - cfg.beta2) * squared * direction
            else:
                self._v[index] += squared
            denominator = math.sqrt(max(self._v[index], 0.0)) + cfg.epsilon
            result.append(cfg.learning_rate * self._m[index] / denominator)
        return tuple(result)


__all__ = ["AdaptiveServerOptimizer", "OptimizerConfig", "Vector"]
