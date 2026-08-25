"""Federated local-training algorithms.

The package namespace stays lightweight so metadata-only consumers such as
benchmark planning can inspect the canonical registry without importing Torch.
Implementation classes are loaded on first attribute access.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from .registry import get_algorithm, register_algorithm, registered_algorithm_names

if TYPE_CHECKING:
    from .base import (
        FederatedLocalAlgorithm,
        LocalEvaluationContext,
        LocalEvaluationResult,
        LocalTrainingContext,
        LocalTrainingResult,
        TaskCancelled,
        TaskDeadlineExceeded,
    )
    from .ditto import DittoAlgorithm, DittoConfig
    from .fedsam import FedSamAlgorithm, FedSamConfig
    from .legacy_adapter import LegacyAlgorithmAdapter
    from .per_fedavg import (
        InsufficientSamplesError,
        PerFedAvgAlgorithm,
        PerFedAvgConfig,
    )

_LAZY_EXPORTS = {
    "DittoAlgorithm": ("ditto", "DittoAlgorithm"),
    "DittoConfig": ("ditto", "DittoConfig"),
    "FederatedLocalAlgorithm": ("base", "FederatedLocalAlgorithm"),
    "FedSamAlgorithm": ("fedsam", "FedSamAlgorithm"),
    "FedSamConfig": ("fedsam", "FedSamConfig"),
    "InsufficientSamplesError": ("per_fedavg", "InsufficientSamplesError"),
    "LegacyAlgorithmAdapter": ("legacy_adapter", "LegacyAlgorithmAdapter"),
    "LocalEvaluationContext": ("base", "LocalEvaluationContext"),
    "LocalEvaluationResult": ("base", "LocalEvaluationResult"),
    "LocalTrainingContext": ("base", "LocalTrainingContext"),
    "LocalTrainingResult": ("base", "LocalTrainingResult"),
    "PerFedAvgAlgorithm": ("per_fedavg", "PerFedAvgAlgorithm"),
    "PerFedAvgConfig": ("per_fedavg", "PerFedAvgConfig"),
    "TaskCancelled": ("base", "TaskCancelled"),
    "TaskDeadlineExceeded": ("base", "TaskDeadlineExceeded"),
}


def __getattr__(name: str) -> object:
    try:
        module_name, symbol_name = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    value = getattr(import_module(f"{__name__}.{module_name}"), symbol_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()).union(_LAZY_EXPORTS))


__all__ = [
    "DittoAlgorithm",
    "DittoConfig",
    "FederatedLocalAlgorithm",
    "FedSamAlgorithm",
    "FedSamConfig",
    "InsufficientSamplesError",
    "LegacyAlgorithmAdapter",
    "LocalEvaluationContext",
    "LocalEvaluationResult",
    "LocalTrainingContext",
    "LocalTrainingResult",
    "PerFedAvgAlgorithm",
    "PerFedAvgConfig",
    "TaskCancelled",
    "TaskDeadlineExceeded",
    "get_algorithm",
    "register_algorithm",
    "registered_algorithm_names",
]
