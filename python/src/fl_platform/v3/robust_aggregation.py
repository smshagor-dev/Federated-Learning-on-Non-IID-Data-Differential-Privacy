"""Byzantine-robust aggregation primitives for the v3 federated platform.

The functions in this module operate on flattened update vectors so they can be
used by local simulation, distributed adapters, and benchmark tooling without
coupling the correctness tests to one tensor framework.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence

Vector = tuple[float, ...]


def _validate_updates(updates: Sequence[Sequence[float]]) -> list[Vector]:
    if not updates:
        raise ValueError("at least one client update is required")
    width = len(updates[0])
    if width == 0:
        raise ValueError("client updates must not be empty")
    normalized: list[Vector] = []
    for index, update in enumerate(updates):
        if len(update) != width:
            raise ValueError(
                f"client update {index} has dimension {len(update)}; expected {width}"
            )
        values = tuple(float(value) for value in update)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"client update {index} contains a non-finite value")
        normalized.append(values)
    return normalized


def coordinate_median(updates: Sequence[Sequence[float]]) -> Vector:
    """Return the coordinate-wise median of finite, shape-compatible updates."""
    vectors = _validate_updates(updates)
    return tuple(
        float(statistics.median(vector[column] for vector in vectors))
        for column in range(len(vectors[0]))
    )


def trimmed_mean(updates: Sequence[Sequence[float]], *, trim_ratio: float) -> Vector:
    """Return a coordinate-wise symmetric trimmed mean.

    ``trim_ratio`` is the fraction removed from each tail.  The function fails
    closed when trimming would remove every value from a coordinate.
    """
    vectors = _validate_updates(updates)
    if not 0.0 <= trim_ratio < 0.5:
        raise ValueError("trim_ratio must be in [0, 0.5)")
    trim = int(math.floor(len(vectors) * trim_ratio))
    if 2 * trim >= len(vectors):
        raise ValueError("trim_ratio removes all client updates")

    result: list[float] = []
    for column in range(len(vectors[0])):
        ordered = sorted(vector[column] for vector in vectors)
        kept = ordered[trim : len(ordered) - trim if trim else None]
        result.append(float(sum(kept) / len(kept)))
    return tuple(result)


def _squared_distance(left: Vector, right: Vector) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True))


def krum_scores(
    updates: Sequence[Sequence[float]], *, byzantine_clients: int
) -> tuple[float, ...]:
    """Compute Krum scores using the closest ``n-f-2`` peer updates."""
    vectors = _validate_updates(updates)
    f = int(byzantine_clients)
    if f < 0:
        raise ValueError("byzantine_clients must be non-negative")
    if len(vectors) < 2 * f + 3:
        raise ValueError("Krum requires n >= 2f + 3")
    neighbor_count = len(vectors) - f - 2
    scores: list[float] = []
    for index, vector in enumerate(vectors):
        distances = sorted(
            _squared_distance(vector, other)
            for other_index, other in enumerate(vectors)
            if other_index != index
        )
        scores.append(float(sum(distances[:neighbor_count])))
    return tuple(scores)


def krum(updates: Sequence[Sequence[float]], *, byzantine_clients: int) -> Vector:
    """Select the update with the minimum Krum score."""
    vectors = _validate_updates(updates)
    scores = krum_scores(vectors, byzantine_clients=byzantine_clients)
    selected = min(range(len(vectors)), key=lambda index: (scores[index], index))
    return vectors[selected]


def multi_krum(
    updates: Sequence[Sequence[float]], *, byzantine_clients: int, select: int
) -> Vector:
    """Average the ``select`` client updates with the lowest Krum scores."""
    vectors = _validate_updates(updates)
    scores = krum_scores(vectors, byzantine_clients=byzantine_clients)
    max_select = len(vectors) - int(byzantine_clients) - 2
    if not 1 <= int(select) <= max_select:
        raise ValueError(f"select must be in [1, {max_select}]")
    selected = sorted(range(len(vectors)), key=lambda index: (scores[index], index))[
        : int(select)
    ]
    return tuple(
        sum(vectors[index][column] for index in selected) / len(selected)
        for column in range(len(vectors[0]))
    )


__all__ = [
    "Vector",
    "coordinate_median",
    "krum",
    "krum_scores",
    "multi_krum",
    "trimmed_mean",
]
