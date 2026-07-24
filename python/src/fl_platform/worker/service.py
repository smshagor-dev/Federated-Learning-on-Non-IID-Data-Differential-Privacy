"""Worker execution loop: register -> heartbeat -> acquire task -> train ->
submit -> repeat, with graceful handling of the failure modes listed in
the Milestone 3 task (coordinator unavailable, registration failure,
cancellation, invalid manifest, missing dataset, training exceptions,
submission retry, shutdown signal).
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, field

import torch

from fl_platform.worker.cancellation import CancellationToken
from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    CoordinatorClient,
    CoordinatorRejectedError,
    CoordinatorUnavailableError,
    RunSpec,
)
from fl_platform.worker.task_runner import (
    TaskCancelled,
    TaskDeadlineExceeded,
    build_bridge_compatible_model,
    run_local_training,
)

logger = logging.getLogger("fl_platform.worker")


@dataclass(slots=True)
class WorkerRunResult:
    tasks_completed: int = 0
    tasks_failed: int = 0
    heartbeat_failures: int = 0
    stopped_reason: str = ""


@dataclass(slots=True)
class WorkerLoopOptions:
    worker_id: str
    max_iterations: int | None = None  # None: run until shutdown/no-task; set for tests
    poll_interval_seconds: float = 0.0  # tests pass 0 to avoid real sleeping
    submission_retry_attempts: int = 3
    submission_retry_backoff_seconds: float = 0.5
    device: torch.device = field(default_factory=lambda: torch.device("cpu"))
    num_classes: int = 2
    in_channels: int = 1
    image_size: int = 4


class WorkerService:
    """Drives one worker's full lifecycle against a CoordinatorClient.

    Deliberately takes the CoordinatorClient and RunSpec as constructor
    arguments rather than owning transport selection itself — main.py (or
    a test) decides whether that client is the CLI bridge or a real gRPC
    client (see coordinator_client.py's module docstring for why both
    exist in this milestone).
    """

    def __init__(
        self, client: CoordinatorClient, spec: RunSpec, options: WorkerLoopOptions
    ) -> None:
        self._client = client
        self._spec = spec
        self._options = options
        self._cancellation = CancellationToken()
        self._shutdown_requested = False

    def request_shutdown(self, *_args: object) -> None:
        self._shutdown_requested = True

    def cancel_current_task(self) -> None:
        self._cancellation.cancel()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self.request_shutdown)
        signal.signal(signal.SIGTERM, self.request_shutdown)

    def register(self, now: float) -> None:
        try:
            self._client.register_worker(self._spec, self._options.worker_id, now)
        except CoordinatorUnavailableError:
            logger.error(
                "coordinator unavailable during registration; will retry next loop"
            )
            raise
        except CoordinatorRejectedError as error:
            logger.error("registration rejected by coordinator: %s", error)
            raise

    def _submit_with_retry(
        self,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None,
    ) -> bool:
        last_error: Exception | None = None
        for attempt in range(1, self._options.submission_retry_attempts + 1):
            try:
                outcome = self._client.submit_result(
                    self._spec,
                    worker_id,
                    task,
                    delta,
                    sample_count,
                    update_id,
                    nonce,
                    now,
                    control_delta=control_delta,
                    refreshed_client_control_variate=refreshed_client_control_variate,
                )
                if not outcome.accepted:
                    logger.warning(
                        "result for client '%s' rejected: %s",
                        task.client_id,
                        outcome.reason,
                    )
                return outcome.accepted
            except CoordinatorUnavailableError as error:
                last_error = error
                logger.warning(
                    "submission attempt %d/%d failed (coordinator unavailable): %s",
                    attempt,
                    self._options.submission_retry_attempts,
                    error,
                )
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.submission_retry_backoff_seconds)
        if last_error is not None:
            logger.error(
                "submission for client '%s' failed after %d attempts",
                task.client_id,
                self._options.submission_retry_attempts,
            )
        return False

    def run(self) -> WorkerRunResult:
        result = WorkerRunResult()
        now = 0.0
        iteration = 0

        try:
            self.register(now)
        except (CoordinatorUnavailableError, CoordinatorRejectedError) as error:
            result.stopped_reason = f"registration failed: {error}"
            return result

        while not self._shutdown_requested:
            if (
                self._options.max_iterations is not None
                and iteration >= self._options.max_iterations
            ):
                result.stopped_reason = "max_iterations reached"
                break
            iteration += 1

            try:
                task = self._client.acquire_task(
                    self._spec, self._options.worker_id, now
                )
            except CoordinatorUnavailableError as error:
                logger.warning(
                    "coordinator unavailable while acquiring a task: %s", error
                )
                result.heartbeat_failures += 1
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue

            if not task.has_task:
                if self._options.max_iterations is None:
                    result.stopped_reason = "no task available"
                    break
                if self._options.poll_interval_seconds > 0:
                    time.sleep(self._options.poll_interval_seconds)
                continue

            self._cancellation.reset()
            model = build_bridge_compatible_model(
                num_classes=self._options.num_classes,
                in_channels=self._options.in_channels,
                image_size=self._options.image_size,
            )
            global_state = {
                name: tensor.clone() for name, tensor in model.state_dict().items()
            }

            try:
                outcome = run_local_training(
                    task,
                    global_state,
                    model,
                    device=self._options.device,
                    seed=hash(task.client_id) & 0xFFFF,
                    num_classes=self._options.num_classes,
                    in_channels=self._options.in_channels,
                    image_size=self._options.image_size,
                    is_cancelled=self._cancellation.is_cancelled,
                )
            except TaskCancelled as error:
                logger.info("task for '%s' cancelled: %s", task.client_id, error)
                result.tasks_failed += 1
                continue
            except TaskDeadlineExceeded as error:
                logger.warning(
                    "task for '%s' missed its deadline: %s", task.client_id, error
                )
                result.tasks_failed += 1
                continue
            except RuntimeError as error:
                # Covers CUDA-unavailable / out-of-memory / other torch
                # runtime failures: log and move on rather than crash the
                # whole worker process over one bad task.
                logger.exception(
                    "training exception for client '%s': %s", task.client_id, error
                )
                result.tasks_failed += 1
                continue

            accepted = self._submit_with_retry(
                self._options.worker_id,
                task,
                outcome.delta,
                outcome.sample_count,
                update_id=f"update-{task.client_id}-{task.round_id}",
                nonce=f"nonce-{task.client_id}-{task.round_id}",
                now=now,
                control_delta=outcome.control_delta,
                refreshed_client_control_variate=outcome.refreshed_client_control_variate,
            )
            if accepted:
                result.tasks_completed += 1
            else:
                result.tasks_failed += 1

        if not result.stopped_reason:
            result.stopped_reason = (
                "shutdown requested" if self._shutdown_requested else "unknown"
            )
        return result
