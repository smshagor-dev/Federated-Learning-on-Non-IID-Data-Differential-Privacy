"""Common interface every federated local-training algorithm implements.

A registry (see ``registry.py``) maps an algorithm name string
("fedavg" | "fedprox" | "scaffold" | "fedsam" | "ditto" | "per_fedavg")
to a :class:`FederatedLocalAlgorithm` instance, so ``task_runner.py`` and
``service.py`` dispatch through one lookup rather than a growing
if/elif chain. See docs/algorithm-expansion-architecture.md.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import torch
import torch.nn as nn

from fl_platform.worker.cancellation import CancellationToken
from fl_platform.worker.dataset_loader import PartitionManifest


class TaskCancelled(RuntimeError):
    """Raised by any FederatedLocalAlgorithm.train() when the
    cancellation token was set mid-training."""


class TaskDeadlineExceeded(RuntimeError):
    """Raised by any FederatedLocalAlgorithm.train() when the wall-clock
    deadline passed mid-training."""


@dataclass(slots=True)
class LocalTrainingContext:
    """Everything one local-training call needs. See
    docs/algorithm-expansion-architecture.md for why this is a single dataclass
    rather than a long positional-argument list: every algorithm needs a
    different subset of these fields, and a dataclass makes "which fields
    does FedSAM actually read" a matter of reading fedsam.py, not
    threading a wide signature through every call site.
    """

    run_id: str
    round_id: int
    client_id: str
    task_id: str
    algorithm: str
    model_version: str
    global_model: nn.Module
    dataset_partition: PartitionManifest
    device: torch.device
    seed: int
    algorithm_config: dict[str, Any]
    optimizer_config: dict[str, Any]
    evaluation_config: dict[str, Any]
    trace_id: str = ""
    personalized_model: nn.Module | None = None
    cancellation_token: CancellationToken | None = None
    deadline_unix_s: float | None = None

    def is_cancelled(self) -> bool:
        return (
            self.cancellation_token is not None
            and self.cancellation_token.is_cancelled()
        )

    def deadline_exceeded(self) -> bool:
        return self.deadline_unix_s is not None and time.time() > self.deadline_unix_s


@dataclass(slots=True)
class LocalEvaluationContext:
    run_id: str
    round_id: int
    client_id: str
    algorithm: str
    global_model: nn.Module
    dataset_partition: PartitionManifest
    device: torch.device
    evaluation_config: dict[str, Any]
    personalized_model: nn.Module | None = None
    trace_id: str = ""


@dataclass(slots=True)
class LocalTrainingResult:
    global_update: dict[str, torch.Tensor]
    sample_count: int
    step_count: int
    training_loss: float
    is_non_finite: bool
    duration_seconds: float
    global_model_local_accuracy: float | None = None
    personalized_model_local_accuracy: float | None = None
    personalized_checkpoint: dict[str, torch.Tensor] | None = None
    algorithm_metrics: dict[str, float] = field(default_factory=dict)
    checksum: str = ""
    control_delta: dict[str, torch.Tensor] | None = None
    refreshed_client_control_variate: dict[str, torch.Tensor] | None = None


@dataclass(slots=True)
class LocalEvaluationResult:
    global_model_local_accuracy: float
    global_model_local_loss: float
    sample_count: int
    personalized_model_local_accuracy: float | None = None
    personalized_model_local_loss: float | None = None
    duration_seconds: float = 0.0


class FederatedLocalAlgorithm(Protocol):
    name: str

    def validate_task(self, context: LocalTrainingContext) -> None:
        """Raises ValueError for a task this algorithm cannot run
        (missing required config, insufficient samples, etc.) — checked
        before any training happens, not discovered mid-training."""
        ...

    def train(self, context: LocalTrainingContext) -> LocalTrainingResult: ...

    def evaluate(self, context: LocalEvaluationContext) -> LocalEvaluationResult: ...
