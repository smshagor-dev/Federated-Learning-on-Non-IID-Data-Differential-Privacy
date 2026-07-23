from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PerFedAvgConfig:
    inner_lr: float = 0.01
    meta_lr: float = 0.005
    first_order: bool = True


@dataclass(slots=True)
class PerFedAvgStep:
    adapted_weights: list[float]
    meta_weights: list[float]


def build_per_fedavg_step(
    weights: list[float],
    support_gradients: list[float],
    query_gradients: list[float],
    config: PerFedAvgConfig,
) -> PerFedAvgStep:
    """Build a first-order Per-FedAvg inner and meta update on flat weights."""
    if not (
        len(weights) == len(support_gradients) == len(query_gradients)
    ):
        raise ValueError("weight and gradient vectors must align")

    adapted_weights = [
        weight - config.inner_lr * support_gradient
        for weight, support_gradient in zip(weights, support_gradients)
    ]
    gradient_source = query_gradients if config.first_order else support_gradients
    meta_weights = [
        weight - config.meta_lr * gradient
        for weight, gradient in zip(adapted_weights, gradient_source)
    ]
    return PerFedAvgStep(
        adapted_weights=adapted_weights,
        meta_weights=meta_weights,
    )
