"""Deterministic adversarial transformations for v3 robustness validation."""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

FeatureVector = tuple[float, ...]
LabeledExample = tuple[FeatureVector, int]
Vector = tuple[float, ...]


class AttackKind(StrEnum):
    NONE = "none"
    LABEL_FLIP = "label_flip"
    BACKDOOR = "backdoor"
    MODEL_REPLACEMENT = "model_replacement"
    SIGN_FLIP = "sign_flip"


def _finite_vector(values: Sequence[float], *, name: str) -> Vector:
    vector = tuple(float(value) for value in values)
    if not vector or not all(math.isfinite(value) for value in vector):
        raise ValueError(f"{name} must be a non-empty finite vector")
    return vector


def apply_update_attack(
    update: Sequence[float],
    attack: AttackKind,
    *,
    scale: float = 12.0,
) -> Vector:
    """Apply a model-update poisoning transformation.

    Data-only attacks leave the update unchanged. Model replacement amplifies
    the submitted delta, while sign-flip reverses and amplifies it.
    """
    vector = _finite_vector(update, name="update")
    if not math.isfinite(scale) or scale <= 0.0:
        raise ValueError("scale must be finite and positive")
    if attack == AttackKind.MODEL_REPLACEMENT:
        return tuple(scale * value for value in vector)
    if attack == AttackKind.SIGN_FLIP:
        return tuple(-scale * value for value in vector)
    return vector


def apply_training_data_attack(
    examples: Sequence[LabeledExample],
    attack: AttackKind,
    *,
    target_label: int = 1,
    trigger_index: int = 2,
    trigger_value: float = 1.0,
) -> tuple[LabeledExample, ...]:
    """Apply deterministic binary label-flip or backdoor poisoning."""
    if not examples:
        raise ValueError("examples must not be empty")
    if target_label not in {0, 1}:
        raise ValueError("target_label must be 0 or 1")
    if trigger_index < 0:
        raise ValueError("trigger_index must be non-negative")
    if not math.isfinite(trigger_value):
        raise ValueError("trigger_value must be finite")

    normalized: list[LabeledExample] = []
    for features, label in examples:
        vector = _finite_vector(features, name="features")
        if label not in {0, 1}:
            raise ValueError("robustness benchmark expects binary labels")
        normalized.append((vector, int(label)))

    if attack == AttackKind.LABEL_FLIP:
        return tuple((features, 1 - label) for features, label in normalized)
    if attack != AttackKind.BACKDOOR:
        return tuple(normalized)

    poisoned: list[LabeledExample] = []
    source_label = 1 - target_label
    source_seen = 0
    for features, label in normalized:
        should_poison = label == source_label and source_seen % 2 == 0
        if label == source_label:
            source_seen += 1
        if not should_poison:
            poisoned.append((features, label))
            continue
        if trigger_index >= len(features):
            raise ValueError("trigger_index is outside the feature vector")
        triggered = list(features)
        triggered[trigger_index] = trigger_value
        poisoned.append((tuple(triggered), target_label))
    return tuple(poisoned)


__all__ = [
    "AttackKind",
    "FeatureVector",
    "LabeledExample",
    "Vector",
    "apply_training_data_attack",
    "apply_update_attack",
]
