from __future__ import annotations

from dataclasses import replace

import pytest

from fl_platform.v3.edge_runtime import (
    EDGE_CODEC,
    EdgeRuntimeBudget,
    EdgeRuntimeError,
    EdgeWorkerRuntime,
    Int8UpdateCodec,
)
from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class EdgeTrainer:
    def train(self, task: TrainingTask) -> TrainingResult:
        return TrainingResult(
            run_id=task.run_id,
            round_id=task.round_id,
            client_id=task.client_id,
            model_version=task.model_version,
            sample_count=32,
            local_step_count=4,
            model_update=tuple((index - 128) / 32.0 for index in range(256)),
        )


def _task() -> TrainingTask:
    return TrainingTask(
        run_id="run-edge",
        round_id=2,
        client_id="edge-client",
        model_version="model-v4",
        algorithm="fedavg",
    )


def test_int8_codec_round_trip_is_bounded_and_compressed() -> None:
    update = tuple((index - 128) / 32.0 for index in range(256))
    codec = Int8UpdateCodec()
    encoded = codec.encode(update)
    restored = codec.decode(encoded)

    assert encoded.codec == EDGE_CODEC
    assert encoded.dimension == len(update)
    assert encoded.compressed_bytes < encoded.dense_float64_bytes
    assert encoded.compression_ratio > 1.0
    maximum_error = max(
        abs(left - right)
        for left, right in zip(update, restored, strict=True)
    )
    assert maximum_error <= encoded.maximum_absolute_error + 1e-12


def test_zero_update_round_trip_preserves_zeros() -> None:
    codec = Int8UpdateCodec()
    encoded = codec.encode((0.0, 0.0, 0.0))
    assert codec.decode(encoded) == (0.0, 0.0, 0.0)


def test_codec_rejects_non_finite_and_corrupted_payloads() -> None:
    codec = Int8UpdateCodec()
    with pytest.raises(EdgeRuntimeError, match="non-finite"):
        codec.encode((1.0, float("nan")))

    encoded = codec.encode((1.0, 2.0, 3.0))
    with pytest.raises(EdgeRuntimeError, match="decompression"):
        codec.decode(replace(encoded, payload=b"not-zlib"))

    decoded_payload = bytearray(encoded.payload)
    decoded_payload[-1] ^= 1
    with pytest.raises(EdgeRuntimeError):
        codec.decode(replace(encoded, payload=bytes(decoded_payload)))


def test_edge_worker_strips_dense_update_from_transport_metadata_and_restores() -> None:
    runtime = EdgeWorkerRuntime(
        WorkerService(EdgeTrainer(), worker_id="edge-worker-1"),
        budget=EdgeRuntimeBudget(max_dimension=512, max_payload_bytes=2048),
    )
    encoded = runtime.handle_task(_task())

    assert encoded.metadata.model_update is None
    assert encoded.metadata.worker_id == "edge-worker-1"
    assert encoded.update.dimension == 256

    restored = runtime.restore(encoded)
    assert restored.model_update is not None
    assert restored.worker_id == "edge-worker-1"
    assert len(restored.model_update) == 256


def test_edge_worker_fails_closed_on_dimension_and_payload_budgets() -> None:
    service = WorkerService(EdgeTrainer())
    dimension_limited = EdgeWorkerRuntime(
        service,
        budget=EdgeRuntimeBudget(max_dimension=64, max_payload_bytes=2048),
    )
    with pytest.raises(EdgeRuntimeError, match="dimension budget"):
        dimension_limited.handle_task(_task())

    payload_limited = EdgeWorkerRuntime(
        service,
        budget=EdgeRuntimeBudget(max_dimension=512, max_payload_bytes=1),
    )
    with pytest.raises(EdgeRuntimeError, match="payload budget"):
        payload_limited.handle_task(_task())


def test_decode_rejects_inconsistent_metadata() -> None:
    codec = Int8UpdateCodec()
    encoded = codec.encode((1.0, -1.0, 0.5))
    with pytest.raises(EdgeRuntimeError, match="dense-byte metadata"):
        codec.decode(replace(encoded, dense_float64_bytes=99))
    with pytest.raises(EdgeRuntimeError, match="dimension"):
        codec.decode(replace(encoded, dimension=4, dense_float64_bytes=32))
