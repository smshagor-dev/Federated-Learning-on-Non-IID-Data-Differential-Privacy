"""Federated local-training algorithms: FedAvg/FedProx/SCAFFOLD (thin
adapters over the Coordinator Runtime phase's proven task_runner.py path) and the real
FedSAM/Ditto/Per-FedAvg implementations added this phase. See
docs/algorithm-expansion-architecture.md.

Use `fl_platform.algorithms.registry.get_algorithm(name)` to look one up
by the algorithm identifier a task carries, rather than importing a
specific class directly.
"""

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
from .per_fedavg import InsufficientSamplesError, PerFedAvgAlgorithm, PerFedAvgConfig
from .registry import get_algorithm, register_algorithm, registered_algorithm_names

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
