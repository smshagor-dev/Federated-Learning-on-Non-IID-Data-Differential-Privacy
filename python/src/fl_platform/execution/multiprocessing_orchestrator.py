from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from fl_platform.execution.modes import ExecutionMode, SchedulingConfig
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


@dataclass(slots=True)
class OrchestratorResult:
    accepted: list[TrainingResult] = field(default_factory=list)
    deferred: list[TrainingResult] = field(default_factory=list)
    rejected: list[TrainingResult] = field(default_factory=list)


class MultiprocessingOrchestrator:
    """Deterministic execution shell for future process pool integration.

    Milestone scope:
    - preserves input ordering
    - applies mode-specific admission rules
    - does not yet spawn child processes or real async workers
    """

    def __init__(self, service: WorkerService, scheduling: SchedulingConfig) -> None:
        self._service = service
        self._scheduling = scheduling

    def run(self, tasks: Iterable[TrainingTask]) -> OrchestratorResult:
        tasks = list(tasks)
        results = [self._service.handle_task(task) for task in tasks]
        return self._classify(results)

    def _classify(self, results: list[TrainingResult]) -> OrchestratorResult:
        if self._scheduling.mode == ExecutionMode.SYNCHRONOUS:
            return OrchestratorResult(accepted=results)

        if self._scheduling.mode == ExecutionMode.DEADLINE_BASED_SEMI_SYNCHRONOUS:
            accepted = results[: self._scheduling.target_clients]
            deferred = []
            if len(accepted) < self._scheduling.minimum_clients:
                return OrchestratorResult(rejected=results)
            if self._scheduling.carryover_late_results:
                deferred = results[self._scheduling.target_clients :]
            return OrchestratorResult(accepted=accepted, deferred=deferred)

        if self._scheduling.mode == ExecutionMode.BUFFERED_ASYNCHRONOUS:
            buffer_size = self._scheduling.buffer_size or self._scheduling.target_clients
            return OrchestratorResult(
                accepted=results[:buffer_size],
                deferred=results[buffer_size:],
            )

        if self._scheduling.mode == ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS:
            buffer_size = self._scheduling.buffer_size or self._scheduling.target_clients
            accepted = results[:buffer_size]
            rejected = []
            for result in results[buffer_size:]:
                if self._scheduling.maximum_staleness == 0:
                    rejected.append(result)
                else:
                    accepted.append(result)
            return OrchestratorResult(accepted=accepted, rejected=rejected)

        return OrchestratorResult(accepted=results)
