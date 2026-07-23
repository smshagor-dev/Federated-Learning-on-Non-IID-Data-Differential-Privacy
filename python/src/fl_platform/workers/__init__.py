"""Worker interfaces and service scaffolding."""

from .service import LocalTrainer, TrainingResult, TrainingTask, WorkerService

__all__ = ["LocalTrainer", "TrainingResult", "TrainingTask", "WorkerService"]
