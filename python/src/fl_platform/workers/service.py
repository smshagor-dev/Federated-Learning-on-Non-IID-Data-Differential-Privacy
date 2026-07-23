from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class TrainingTask:
    run_id: str
    round_id: int
    client_id: str
    model_version: str
    algorithm: str
    training_config: dict[str, Any] = field(default_factory=dict)
    privacy_config: dict[str, Any] = field(default_factory=dict)
    deadline_unix_s: float | None = None
    trace_id: str = ""


@dataclass(slots=True)
class TrainingResult:
    run_id: str
    round_id: int
    client_id: str
    model_version: str
    sample_count: int
    local_step_count: int
    metrics: dict[str, float] = field(default_factory=dict)
    accepted: bool = True
    worker_id: str = ""
    trace_id: str = ""


class LocalTrainer(Protocol):
    def train(self, task: TrainingTask) -> TrainingResult:
        ...


class WorkerService:
    """Milestone 3 local worker shell. RPC transport will wrap this later."""

    def __init__(self, trainer: LocalTrainer, worker_id: str = "local-worker") -> None:
        self._trainer = trainer
        self._worker_id = worker_id

    def handle_task(self, task: TrainingTask) -> TrainingResult:
        result = self._trainer.train(task)
        result.worker_id = self._worker_id
        if not result.trace_id:
            result.trace_id = task.trace_id
        return result
