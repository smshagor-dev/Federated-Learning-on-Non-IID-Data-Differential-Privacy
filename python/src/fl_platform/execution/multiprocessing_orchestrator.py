from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Protocol

from fl_platform.execution.modes import ExecutionMode, SchedulingConfig
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class ResultAggregator(Protocol):
    """Optional aggregation hook for accepted worker results."""

    def aggregate(self, results: list[TrainingResult]) -> object: ...


@dataclass(frozen=True)
class TaskAdmissionDecision:
    admitted: bool
    reason: str = ""


class TaskAdmissionPolicy(Protocol):
    """Optional pre-training policy for resource/availability admission."""

    def evaluate(self, task: TrainingTask) -> TaskAdmissionDecision: ...


@dataclass(slots=True)
class OrchestratorResult:
    accepted: list[TrainingResult] = field(default_factory=list)
    deferred: list[TrainingResult] = field(default_factory=list)
    rejected: list[TrainingResult] = field(default_factory=list)
    skipped_tasks: list[TrainingTask] = field(default_factory=list)
    skip_reasons: dict[str, str] = field(default_factory=dict)
    aggregation: object | None = None


class MultiprocessingOrchestrator:
    """Deterministic execution shell for process/distributed integration.

    Current scope:
    - preserves input ordering
    - supports optional pre-training admission policies
    - applies mode-specific result admission rules
    - supports explicit model-version staleness admission
    - can invoke an opt-in aggregation engine after admission
    - does not yet spawn child processes or remote async workers
    """

    def __init__(
        self,
        service: WorkerService,
        scheduling: SchedulingConfig,
        aggregator: ResultAggregator | None = None,
        admission_policy: TaskAdmissionPolicy | None = None,
    ) -> None:
        self._service = service
        self._scheduling = scheduling
        self._aggregator = aggregator
        self._admission_policy = admission_policy

    def run(self, tasks: Iterable[TrainingTask]) -> OrchestratorResult:
        task_list = list(tasks)
        admitted_tasks: list[TrainingTask] = []
        skipped_tasks: list[TrainingTask] = []
        skip_reasons: dict[str, str] = {}
        for task in task_list:
            if self._admission_policy is None:
                admitted_tasks.append(task)
                continue
            decision = self._admission_policy.evaluate(task)
            if decision.admitted:
                admitted_tasks.append(task)
            else:
                skipped_tasks.append(task)
                skip_reasons[task.client_id] = decision.reason or "admission_rejected"

        results = [self._service.handle_task(task) for task in admitted_tasks]
        classified = self._classify(results)
        classified.skipped_tasks = skipped_tasks
        classified.skip_reasons = skip_reasons
        if self._aggregator is not None and classified.accepted:
            classified.aggregation = self._aggregator.aggregate(classified.accepted)
        return classified

    def _classify(self, results: list[TrainingResult]) -> OrchestratorResult:
        if self._scheduling.mode == ExecutionMode.SYNCHRONOUS:
            return OrchestratorResult(accepted=results)

        if self._scheduling.mode == ExecutionMode.DEADLINE_BASED_SEMI_SYNCHRONOUS:
            accepted = results[: self._scheduling.target_clients]
            deferred: list[TrainingResult] = []
            if len(accepted) < self._scheduling.minimum_clients:
                return OrchestratorResult(rejected=results)
            if self._scheduling.carryover_late_results:
                deferred = results[self._scheduling.target_clients :]
            return OrchestratorResult(accepted=accepted, deferred=deferred)

        if self._scheduling.mode == ExecutionMode.BUFFERED_ASYNCHRONOUS:
            buffer_size = (
                self._scheduling.buffer_size or self._scheduling.target_clients
            )
            return OrchestratorResult(
                accepted=results[:buffer_size],
                deferred=results[buffer_size:],
            )

        if self._scheduling.mode == ExecutionMode.STALENESS_AWARE_ASYNCHRONOUS:
            return self._classify_staleness_aware(results)

        return OrchestratorResult(accepted=results)

    def _classify_staleness_aware(
        self,
        results: list[TrainingResult],
    ) -> OrchestratorResult:
        buffer_size = self._scheduling.buffer_size or self._scheduling.target_clients
        current_version = self._scheduling.current_model_version
        maximum_staleness = self._scheduling.maximum_staleness

        if current_version is None:
            accepted = results[:buffer_size]
            rejected: list[TrainingResult] = []
            for result in results[buffer_size:]:
                if maximum_staleness == 0:
                    rejected.append(result)
                else:
                    accepted.append(result)
            return OrchestratorResult(accepted=accepted, rejected=rejected)

        if maximum_staleness is None:
            return OrchestratorResult(rejected=results)

        eligible: list[TrainingResult] = []
        rejected = []
        for result in results:
            base_version = _result_base_version(result)
            if base_version is None:
                rejected.append(result)
                continue
            if base_version > current_version:
                rejected.append(result)
                continue
            if current_version - base_version > maximum_staleness:
                rejected.append(result)
                continue
            eligible.append(result)

        return OrchestratorResult(
            accepted=eligible[:buffer_size],
            deferred=eligible[buffer_size:],
            rejected=rejected,
        )


def _result_base_version(result: TrainingResult) -> int | None:
    if result.base_model_version is not None:
        return result.base_model_version if result.base_model_version >= 0 else None

    value = result.model_version.strip().lower()
    for prefix in ("model-v", "model-", "v"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    if not value.isdigit():
        return None
    return int(value)
