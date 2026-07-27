"""Shared-backbone / personalization-head model support (the Algorithm Expansion phase,
Work Package F). See docs/shared-backbone-local-head.md.

Personalization architecture is expressed purely as parameter-*name*
prefixes against an ordinary nn.Module's state_dict — no custom Module
subclassing is required, so any existing or future architecture works
as long as its shared and personalized parameters are cleanly
prefix-separated (true for models/networks.py's GroupNormCNN: "features."
is the shared backbone, "classifier." is the personalized head).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

import torch
import torch.nn as nn


@dataclass(slots=True)
class ModelMetadata:
    name: str
    architecture_version: str
    parameter_count: int
    trainable_parameter_count: int
    input_channels: int
    num_classes: int
    normalization_type: str
    shared_parameter_names: list[str] = field(default_factory=list)
    personalized_parameter_names: list[str] = field(default_factory=list)
    frozen_parameter_names: list[str] = field(default_factory=list)
    state_dict_schema_hash: str = ""


def compute_schema_hash(model: nn.Module) -> str:
    """Stable hash of (name, shape, dtype) tuples, sorted by name — used
    to detect a personalized checkpoint being loaded against an
    incompatible architecture (see docs/personalized-model-store.md).
    Deliberately not `hash()` (per-process-salted) or a raw tensor
    checksum (would change every training step); this only fingerprints
    *shape*, which changes only when the architecture itself changes.
    """
    entries = sorted(
        f"{name}:{tuple(tensor.shape)}:{tensor.dtype}"
        for name, tensor in model.state_dict().items()
    )
    digest = hashlib.sha256("|".join(entries).encode("utf-8")).hexdigest()
    return digest[:16]


def parameter_names_with_prefix(model: nn.Module, prefixes: list[str]) -> list[str]:
    if not prefixes:
        return []
    return [
        name
        for name in model.state_dict()
        if any(name.startswith(prefix) for prefix in prefixes)
    ]


def shared_state_dict(
    model: nn.Module, shared_prefixes: list[str]
) -> dict[str, torch.Tensor]:
    names = set(parameter_names_with_prefix(model, shared_prefixes))
    return {
        name: tensor.clone()
        for name, tensor in model.state_dict().items()
        if name in names
    }


def personalized_state_dict(
    model: nn.Module, personalized_prefixes: list[str]
) -> dict[str, torch.Tensor]:
    names = set(parameter_names_with_prefix(model, personalized_prefixes))
    return {
        name: tensor.clone()
        for name, tensor in model.state_dict().items()
        if name in names
    }


def apply_partial_state(
    model: nn.Module, partial_state: dict[str, torch.Tensor]
) -> None:
    """Loads only the given tensors, leaving every other parameter
    untouched — the shared-backbone/personalized-head equivalent of
    `load_state_dict(..., strict=False)`, but explicit about exactly
    which names are being written rather than tolerating typos-as-noops."""
    full_state = model.state_dict()
    unknown = set(partial_state) - set(full_state)
    if unknown:
        raise ValueError(f"unknown parameter names for this model: {sorted(unknown)}")
    for name, tensor in partial_state.items():
        full_state[name].copy_(tensor)


def describe_model(
    model: nn.Module,
    name: str,
    architecture_version: str,
    *,
    input_channels: int,
    num_classes: int,
    normalization_type: str,
    shared_parameter_prefixes: list[str] | None = None,
    personalized_parameter_prefixes: list[str] | None = None,
    frozen_parameter_prefixes: list[str] | None = None,
) -> ModelMetadata:
    parameter_count = sum(p.numel() for p in model.parameters())
    trainable_count = sum(p.numel() for p in model.parameters() if p.requires_grad)
    return ModelMetadata(
        name=name,
        architecture_version=architecture_version,
        parameter_count=parameter_count,
        trainable_parameter_count=trainable_count,
        input_channels=input_channels,
        num_classes=num_classes,
        normalization_type=normalization_type,
        shared_parameter_names=parameter_names_with_prefix(
            model, shared_parameter_prefixes or []
        ),
        personalized_parameter_names=parameter_names_with_prefix(
            model, personalized_parameter_prefixes or []
        ),
        frozen_parameter_names=parameter_names_with_prefix(
            model, frozen_parameter_prefixes or []
        ),
        state_dict_schema_hash=compute_schema_hash(model),
    )
