from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DittoConfig:
    regularization: float = 0.1
    personalized_learning_rate: float = 0.01


@dataclass(slots=True)
class DittoState:
    global_weights: list[float]
    local_weights: list[float]


def compute_ditto_regularized_weights(
    state: DittoState,
    gradients: list[float],
    config: DittoConfig,
) -> list[float]:
    """Single personalized Ditto-style update on a flat weight vector."""
    if not (
        len(state.global_weights) == len(state.local_weights) == len(gradients)
    ):
        raise ValueError("global, local, and gradient vectors must align")

    updated: list[float] = []
    for global_weight, local_weight, gradient in zip(
        state.global_weights, state.local_weights, gradients
    ):
        regularizer = config.regularization * (local_weight - global_weight)
        updated.append(
            local_weight
            - config.personalized_learning_rate * (gradient + regularizer)
        )
    return updated
