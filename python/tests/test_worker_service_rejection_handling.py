"""Tests for fl_platform.worker.service.WorkerService.run()'s
acquire_task rejection handling -- Masked Update Runtime and No-Dropout
Secure FedAvg Finalization slice, Work Area B.

Before this slice, run()'s acquire_task try/except caught
CoordinatorUnavailableError and CoordinatorTaskRejectedError but not
the more general CoordinatorRejectedError (raised by
GrpcCoordinatorClient._grpc_call for any non-OK gRPC status not
otherwise mapped, e.g. AcquireTask returning FAILED_PRECONDITION
"unknown run_id"). That gap crashed the whole worker process instead of
retrying on the next poll -- confirmed live by the prior slice's own
Docker validation (docs/known-limitations.md, "Secure Cohort Handshake
and Signed Roster Runtime slice"). This file proves the fix without
requiring grpc/protobuf (unavailable in this project's local dev venv,
Docker/CI-only) -- WorkerService only depends on the CoordinatorClient
Protocol (duck-typed), so a minimal fake exercises the real run() loop
directly.
"""

from __future__ import annotations

import unittest

import torch

from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    CoordinatorRejectedError,
    CoordinatorTaskRejectedError,
    CoordinatorUnavailableError,
    PersonalizationMetricRecord,
    RunSnapshot,
    RunSpec,
    SubmitOutcome,
)
from fl_platform.worker.service import WorkerLoopOptions, WorkerService


class _FakeClient:
    """Minimal CoordinatorClient stand-in: only the methods
    WorkerService.run() actually calls are meaningful; the rest exist
    only to satisfy the Protocol shape."""

    def __init__(
        self, acquire_task_side_effects: list[Exception | ClientTrainingTask]
    ) -> None:
        self._acquire_task_side_effects = list(acquire_task_side_effects)
        self.acquire_task_calls = 0

    def create_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        raise NotImplementedError

    def start_run(self, spec: RunSpec, now: float, trace_id: str = "") -> RunSnapshot:
        raise NotImplementedError

    def pause_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        raise NotImplementedError

    def resume_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        raise NotImplementedError

    def cancel_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        raise NotImplementedError

    def get_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        raise NotImplementedError

    def register_worker(self, spec: RunSpec, worker_id: str, now: float) -> None:
        return None

    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask:
        self.acquire_task_calls += 1
        outcome = self._acquire_task_side_effects.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def submit_result(self, *args: object, **kwargs: object) -> SubmitOutcome:
        raise NotImplementedError

    def get_personalization_summary(
        self, spec: RunSpec, now: float
    ) -> list[PersonalizationMetricRecord]:
        return []


def _spec() -> RunSpec:
    return RunSpec(run_id="run-1", algorithm="fedavg")


def _options() -> WorkerLoopOptions:
    # max_iterations=None: the loop's own iteration-count guard must not
    # be what stops it here -- each test's fake acquire_task side-effect
    # list ends with a has_task=False response, which is what actually
    # terminates run() ("no task available"). With max_iterations set to
    # a finite number, a has_task=False response does NOT break the loop
    # (it only breaks when max_iterations is None -- see service.py's
    # own branch), so a finite max_iterations would keep polling past
    # the end of the fake's side-effect list.
    return WorkerLoopOptions(
        worker_id="worker-1",
        max_iterations=None,
        poll_interval_seconds=0.0,
        device=torch.device("cpu"),
    )


class AcquireTaskRejectionHandlingTests(unittest.TestCase):
    def test_coordinator_rejected_error_is_caught_and_retried(self) -> None:
        # Previously: this exact exception, raised from acquire_task,
        # propagated straight out of run() uncaught -- the fix adds a
        # catch, it does not touch CoordinatorTaskRejectedError's own
        # stricter clause (tested separately below).
        client = _FakeClient(
            [
                CoordinatorRejectedError("unknown run_id: run-1"),
                CoordinatorRejectedError("unknown run_id: run-1"),
                ClientTrainingTask(has_task=False),
            ]
        )
        service = WorkerService(client, _spec(), _options())

        result = service.run()

        self.assertEqual(client.acquire_task_calls, 3)
        self.assertEqual(result.tasks_failed, 2)
        self.assertEqual(result.stopped_reason, "no task available")

    def test_coordinator_unavailable_error_still_retried(self) -> None:
        # Regression guard: adding the new except clause must not
        # change CoordinatorUnavailableError's existing, already-correct
        # behavior.
        client = _FakeClient(
            [
                CoordinatorUnavailableError("connection refused"),
                ClientTrainingTask(has_task=False),
            ]
        )
        service = WorkerService(client, _spec(), _options())

        result = service.run()

        self.assertEqual(result.heartbeat_failures, 1)
        self.assertEqual(result.stopped_reason, "no task available")

    def test_coordinator_task_rejected_error_still_fails_closed(self) -> None:
        # Regression guard: a signed-task verification failure must
        # still be treated as a real rejection (tasks_failed
        # incremented, never silently retried as if nothing happened,
        # never executed) -- the new CoordinatorRejectedError clause
        # must never intercept this more specific exception.
        from fl_platform.security.coordinator_task_verifier import (
            CoordinatorTaskRejectionReason,
        )

        client = _FakeClient(
            [
                CoordinatorTaskRejectedError(
                    CoordinatorTaskRejectionReason.INVALID_SIGNATURE, "bad signature"
                ),
                ClientTrainingTask(has_task=False),
            ]
        )
        service = WorkerService(client, _spec(), _options())

        result = service.run()

        self.assertEqual(result.tasks_failed, 1)
        self.assertEqual(result.stopped_reason, "no task available")

    def test_unrelated_exception_still_propagates(self) -> None:
        # The new clause must be narrowly scoped to CoordinatorRejectedError
        # -- it must never become a catch-all that swallows unrelated
        # failures.
        client = _FakeClient([RuntimeError("something else entirely")])
        service = WorkerService(client, _spec(), _options())

        with self.assertRaises(RuntimeError):
            service.run()


if __name__ == "__main__":
    unittest.main()
