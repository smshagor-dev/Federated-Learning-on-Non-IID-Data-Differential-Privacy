"""Worker interfaces and service scaffolding."""

from .service import (
    LocalTrainer,
    ModelUpdate,
    TrainingResult,
    TrainingTask,
    WorkerService,
)

__all__ = [
    "LocalTrainer",
    "ModelUpdate",
    "TrainingResult",
    "TrainingTask",
    "WorkerService",
]
