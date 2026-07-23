from __future__ import annotations

from dataclasses import dataclass
from math import sqrt
from typing import Iterable


def _l2_norm(values: Iterable[float]) -> float:
    return sqrt(sum(value * value for value in values))


@dataclass(slots=True)
class FedSAMConfig:
    rho: float = 0.05
    adaptive: bool = False
    epsilon: float = 1e-12


@dataclass(slots=True)
class FedSAMStep:
    gradient_norm: float
    scale: float
    perturbation: list[float]


def build_fedsam_step(
    weights: list[float],
    gradients: list[float],
    config: FedSAMConfig,
) -> FedSAMStep:
    """Build the first SAM perturbation step for a flat parameter vector.

    This is a deterministic scalar-vector helper for Milestone 4 validation.
    It is not yet wired into the legacy trainer.
    """
    if len(weights) != len(gradients):
        raise ValueError("weights and gradients must have identical lengths")

    base = [
        abs(weight) * gradient if config.adaptive else gradient
        for weight, gradient in zip(weights, gradients)
    ]
    gradient_norm = _l2_norm(base)
    scale = config.rho / max(gradient_norm, config.epsilon)
    perturbation = [value * scale for value in base]
    return FedSAMStep(
        gradient_norm=gradient_norm,
        scale=scale,
        perturbation=perturbation,
    )
