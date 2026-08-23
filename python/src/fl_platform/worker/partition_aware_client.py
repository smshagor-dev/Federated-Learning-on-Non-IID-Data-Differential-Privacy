"""Partition-aware adapter for the production gRPC worker client.

The base :class:`GrpcCoordinatorClient` remains the sole implementation of
transport, coordinator-signature verification, replay protection, accepted-task
journaling, privacy binding checks, and secure-aggregation task verification.
This adapter only remembers the raw ``dataset_reference`` long enough to publish
it to the deterministic dataset loader after ``super().acquire_task`` has
successfully completed the existing task-acceptance pipeline.
"""

from __future__ import annotations

from typing import Any

from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    GrpcCoordinatorClient,
    RunSpec,
)
from fl_platform.worker.dataset_loader import register_verified_partition_reference


class PartitionAwareGrpcCoordinatorClient(GrpcCoordinatorClient):
    """GrpcCoordinatorClient with accepted dataset-partition handoff."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pending_partition_reference: tuple[str, str, str] | None = None

    def _grpc_call(self, rpc_callable: Any, request: Any) -> Any:
        response = super()._grpc_call(rpc_callable, request)
        if (
            hasattr(response, "task_available")
            and bool(response.task_available)
            and hasattr(response, "task")
            and getattr(response, "task_id", "")
        ):
            task = response.task
            self._pending_partition_reference = (
                str(response.task_id),
                str(task.client_id),
                str(task.dataset_reference),
            )
        return response

    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask:
        self._pending_partition_reference = None
        try:
            task = super().acquire_task(spec, worker_id, now)
        except Exception:
            # A rejected/unverifiable task must never influence later dataset
            # selection, even transiently.
            self._pending_partition_reference = None
            raise

        captured = self._pending_partition_reference
        self._pending_partition_reference = None
        if task.has_task and captured is not None:
            task_id, client_id, dataset_reference = captured
            if task_id == task.task_id and client_id == task.client_id:
                register_verified_partition_reference(client_id, dataset_reference)
        return task
