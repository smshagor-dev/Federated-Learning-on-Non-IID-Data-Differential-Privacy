"""Executable primitives for the v3 federated algorithm expansion.

These helpers are intentionally backend-neutral and operate on flat vectors or
named scalar state. They make the algorithms testable without pretending that
all training backends, privacy modes, or secure-aggregation combinations are
release validated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vector = tuple[float, ...]


@dataclass(frozen=True)
class ParameterPartition:
    shared: dict[str, float]
    personalized: dict[str, float]


def _validate_vectors(vectors: tuple[Vector, ...]) -> int:
    if not vectors:
        raise ValueError("at least one vector is required")
    dimension = len(vectors[0])
    if dimension == 0:
        raise ValueError("vectors must not be empty")
    for vector in vectors:
        if len(vector) != dimension:
            raise ValueError("all vectors must have the same dimension")
        if not all(math.isfinite(value) for value in vector):
            raise ValueError("vectors must contain only finite values")
    return dimension


def _normalized_weights(weights: tuple[float, ...], count: int) -> tuple[float, ...]:
    if len(weights) != count:
        raise ValueError("weights length must match vector count")
    if any(weight < 0.0 or not math.isfinite(weight) for weight in weights):
        raise ValueError("weights must be finite and non-negative")
    total = sum(weights)
    if total <= 0.0:
        raise ValueError("at least one weight must be positive")
    return tuple(weight / total for weight in weights)


def fednova_aggregate(
    updates: tuple[Vector, ...],
    *,
    local_steps: tuple[int, ...],
    weights: tuple[float, ...],
) -> Vector:
    """Aggregate heterogeneous local work using FedNova normalization.

    Each client update is normalized by its local step count, normalized
    directions are averaged, then scaled by the weighted effective local-step
    count. Equal local step counts reduce exactly to a weighted mean update.
    """
    dimension = _validate_vectors(updates)
    if len(local_steps) != len(updates):
        raise ValueError("local_steps length must match update count")
    if any(step <= 0 for step in local_steps):
        raise ValueError("FedNova local_steps must be positive")
    normalized = _normalized_weights(weights, len(updates))
    effective_steps = sum(
        weight * step for weight, step in zip(normalized, local_steps, strict=True)
    )
    return tuple(
        effective_steps
        * sum(
            weight * update[index] / step
            for weight, update, step in zip(
                normalized,
                updates,
                local_steps,
                strict=True,
            )
        )
        for index in range(dimension)
    )


def fedbn_partition(
    state: dict[str, float],
    *,
    batch_norm_names: frozenset[str],
) -> ParameterPartition:
    """Keep BatchNorm parameters local while exposing other parameters as shared."""
    unknown = batch_norm_names.difference(state)
    if unknown:
        raise ValueError(f"unknown BatchNorm parameter names: {sorted(unknown)}")
    if any(not math.isfinite(value) for value in state.values()):
        raise ValueError("state values must be finite")
    personalized = {name: state[name] for name in state if name in batch_norm_names}
    shared = {name: state[name] for name in state if name not in batch_norm_names}
    return ParameterPartition(shared=shared, personalized=personalized)


def fedrep_partition(
    state: dict[str, float],
    *,
    representation_names: frozenset[str],
    head_names: frozenset[str],
) -> ParameterPartition:
    """Split FedRep representation parameters from the client-local head."""
    if not representation_names or not head_names:
        raise ValueError("FedRep representation and head sets must both be non-empty")
    overlap = representation_names.intersection(head_names)
    if overlap:
        raise ValueError(f"FedRep parameter sets overlap: {sorted(overlap)}")
    expected = representation_names.union(head_names)
    actual = frozenset(state)
    if expected != actual:
        missing = sorted(actual.difference(expected))
        unknown = sorted(expected.difference(actual))
        raise ValueError(
            f"FedRep partition must cover state exactly; unassigned={missing}, "
            f"unknown={unknown}"
        )
    if any(not math.isfinite(value) for value in state.values()):
        raise ValueError("state values must be finite")
    return ParameterPartition(
        shared={name: state[name] for name in representation_names},
        personalized={name: state[name] for name in head_names},
    )


def _cosine_similarity(left: Vector, right: Vector) -> float:
    _validate_vectors((left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("MOON representation vectors must have non-zero norm")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )


def _softplus(value: float) -> float:
    if value > 0.0:
        return value + math.log1p(math.exp(-value))
    return math.log1p(math.exp(value))


def moon_contrastive_loss(
    current_representation: Vector,
    global_representation: Vector,
    previous_local_representation: Vector,
    *,
    temperature: float = 0.5,
) -> float:
    """Return MOON's two-way model-contrastive loss for one representation."""
    if temperature <= 0.0 or not math.isfinite(temperature):
        raise ValueError("MOON temperature must be finite and positive")
    positive = _cosine_similarity(current_representation, global_representation)
    negative = _cosine_similarity(
        current_representation,
        previous_local_representation,
    )
    return _softplus((negative - positive) / temperature)


def pfedme_personalized_step(
    personalized: Vector,
    global_model: Vector,
    gradient: Vector,
    *,
    learning_rate: float,
    proximal_lambda: float,
) -> Vector:
    """Apply one pFedMe personalized proximal-gradient step."""
    dimension = _validate_vectors((personalized, global_model, gradient))
    if learning_rate <= 0.0 or not math.isfinite(learning_rate):
        raise ValueError("pFedMe learning_rate must be finite and positive")
    if proximal_lambda <= 0.0 or not math.isfinite(proximal_lambda):
        raise ValueError("pFedMe proximal_lambda must be finite and positive")
    return tuple(
        personalized[index]
        - learning_rate
        * (
            gradient[index]
            + proximal_lambda * (personalized[index] - global_model[index])
        )
        for index in range(dimension)
    )


__all__ = [
    "ParameterPartition",
    "fedbn_partition",
    "fednova_aggregate",
    "fedrep_partition",
    "moon_contrastive_loss",
    "pfedme_personalized_step",
]
