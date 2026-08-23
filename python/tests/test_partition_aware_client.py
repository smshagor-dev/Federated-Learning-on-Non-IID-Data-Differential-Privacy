from __future__ import annotations

import pytest

from fl_platform.worker.coordinator_client import (
    ClientTrainingTask,
    GrpcCoordinatorClient,
    RunSpec,
)
from fl_platform.worker.dataset_loader import (
    clear_verified_partition_references,
    manifest_for_client,
)
from fl_platform.worker.partition_aware_client import PartitionAwareGrpcCoordinatorClient


def test_verified_raw_reference_is_published_only_after_base_acquire_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_verified_partition_references()
    client = object.__new__(PartitionAwareGrpcCoordinatorClient)
    client._pending_partition_reference = None
    reference = (
        "fl-partition-v1://synthetic?dataset=CIFAR100&strategy=pathological"
        "&alpha=0&classes_per_client=2&quantity_skew_sigma=0"
        "&min_client_size=0&seed=7"
    )

    def _verified_acquire(
        self: GrpcCoordinatorClient,
        spec: RunSpec,
        worker_id: str,
        now: float,
    ) -> ClientTrainingTask:
        del spec, worker_id, now
        self._pending_partition_reference = (  # type: ignore[attr-defined]
            "task-1",
            "client-a",
            reference,
        )
        return ClientTrainingTask(
            has_task=True,
            task_id="task-1",
            client_id="client-a",
        )

    monkeypatch.setattr(GrpcCoordinatorClient, "acquire_task", _verified_acquire)
    task = client.acquire_task(
        RunSpec(run_id="run-1", algorithm="fedavg"),
        "worker-1",
        0.0,
    )
    assert task.has_task
    manifest = manifest_for_client("synthetic:client-a", "client-a", 999)
    assert manifest.partition_strategy == "pathological"
    assert manifest.classes_per_client == 2


def test_rejected_base_acquire_does_not_publish_pending_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_verified_partition_references()
    client = object.__new__(PartitionAwareGrpcCoordinatorClient)
    client._pending_partition_reference = None

    def _rejected_acquire(
        self: GrpcCoordinatorClient,
        spec: RunSpec,
        worker_id: str,
        now: float,
    ) -> ClientTrainingTask:
        del spec, worker_id, now
        self._pending_partition_reference = (  # type: ignore[attr-defined]
            "task-bad",
            "client-a",
            "fl-partition-v1://synthetic?dataset=X&strategy=pathological"
            "&alpha=0&classes_per_client=1&quantity_skew_sigma=0"
            "&min_client_size=0&seed=9",
        )
        raise RuntimeError("signature rejected")

    monkeypatch.setattr(GrpcCoordinatorClient, "acquire_task", _rejected_acquire)
    with pytest.raises(RuntimeError, match="signature rejected"):
        client.acquire_task(
            RunSpec(run_id="run-1", algorithm="fedavg"),
            "worker-1",
            0.0,
        )

    manifest = manifest_for_client("synthetic:client-a", "client-a", 5)
    assert manifest.partition_strategy == "iid"
