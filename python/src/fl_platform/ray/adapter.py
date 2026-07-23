from __future__ import annotations

from dataclasses import dataclass

from fl_platform.execution import SchedulingConfig
from fl_platform.workers import TrainingTask


@dataclass(slots=True)
class RayExecutionPlan:
    num_workers: int
    scheduling_mode: str
    task_count: int


class RayWorkerAdapter:
    """Planning-only Ray adapter scaffold for future distributed execution."""

    def build_plan(
        self,
        tasks: list[TrainingTask],
        scheduling: SchedulingConfig,
        num_workers: int,
    ) -> RayExecutionPlan:
        if num_workers <= 0:
            raise ValueError("num_workers must be positive")
        return RayExecutionPlan(
            num_workers=num_workers,
            scheduling_mode=scheduling.mode.value,
            task_count=len(tasks),
        )
