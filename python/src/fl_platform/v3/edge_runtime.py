"""Resource-bounded update transport primitives for edge workers.

The edge path keeps the canonical ``TrainingResult`` contract unchanged. A
worker result is wrapped into an int8+zlib payload for transport and restored
before it reaches the existing aggregation engine. The compressed envelope
contains no dataset examples or gradients beyond the already-produced model
update.
"""

from __future__ import annotations

import hashlib
import math
import struct
import zlib
from dataclasses import dataclass, replace

from fl_platform.workers import ModelUpdate, TrainingResult, TrainingTask, WorkerService

EDGE_CODEC = "qint8-zlib-v1"


class EdgeRuntimeError(RuntimeError):
    """Raised when an edge update violates codec or resource constraints."""


@dataclass(frozen=True)
class EdgeUpdatePayload:
    codec: str
    dimension: int
    scale: float
    payload: bytes
    quantized_sha256: str
    dense_float64_bytes: int

    @property
    def compressed_bytes(self) -> int:
        return len(self.payload)

    @property
    def compression_ratio(self) -> float:
        if self.compressed_bytes == 0:
            return math.inf
        return self.dense_float64_bytes / self.compressed_bytes

    @property
    def maximum_absolute_error(self) -> float:
        return self.scale / 2.0


@dataclass(frozen=True)
class EdgeRuntimeBudget:
    max_dimension: int = 1_000_000
    max_payload_bytes: int = 4 * 1024 * 1024

    def validate(self) -> None:
        if self.max_dimension <= 0:
            raise ValueError("max_dimension must be positive")
        if self.max_payload_bytes <= 0:
            raise ValueError("max_payload_bytes must be positive")


@dataclass(frozen=True)
class EdgeTrainingResult:
    """Training metadata plus a compressed model update transport payload."""

    metadata: TrainingResult
    update: EdgeUpdatePayload


class Int8UpdateCodec:
    """Symmetric per-update int8 quantization with zlib compression."""

    def encode(self, update: ModelUpdate) -> EdgeUpdatePayload:
        if not update:
            raise EdgeRuntimeError("model update must not be empty")
        vector = tuple(float(value) for value in update)
        if not all(math.isfinite(value) for value in vector):
            raise EdgeRuntimeError("model update contains non-finite values")

        maximum = max(abs(value) for value in vector)
        scale = maximum / 127.0 if maximum > 0.0 else 1.0
        quantized = tuple(
            max(-127, min(127, int(round(value / scale)))) for value in vector
        )
        packed = struct.pack(f"<{len(quantized)}b", *quantized)
        compressed = zlib.compress(packed, level=9)
        return EdgeUpdatePayload(
            codec=EDGE_CODEC,
            dimension=len(vector),
            scale=scale,
            payload=compressed,
            quantized_sha256=hashlib.sha256(packed).hexdigest(),
            dense_float64_bytes=len(vector) * 8,
        )

    def decode(self, encoded: EdgeUpdatePayload) -> ModelUpdate:
        if encoded.codec != EDGE_CODEC:
            raise EdgeRuntimeError(f"unsupported edge codec: {encoded.codec}")
        if encoded.dimension <= 0:
            raise EdgeRuntimeError("encoded dimension must be positive")
        if encoded.scale <= 0.0 or not math.isfinite(encoded.scale):
            raise EdgeRuntimeError("encoded scale must be finite and positive")
        if encoded.dense_float64_bytes != encoded.dimension * 8:
            raise EdgeRuntimeError("encoded dense-byte metadata is inconsistent")

        try:
            packed = zlib.decompress(encoded.payload)
        except zlib.error as exc:
            raise EdgeRuntimeError("edge payload decompression failed") from exc
        if len(packed) != encoded.dimension:
            raise EdgeRuntimeError("edge payload dimension does not match encoded data")
        digest = hashlib.sha256(packed).hexdigest()
        if digest != encoded.quantized_sha256:
            raise EdgeRuntimeError("edge payload integrity check failed")

        quantized = struct.unpack(f"<{encoded.dimension}b", packed)
        return tuple(float(value) * encoded.scale for value in quantized)


class EdgeWorkerRuntime:
    """Compress a canonical worker result for an edge transport boundary."""

    def __init__(
        self,
        service: WorkerService,
        *,
        codec: Int8UpdateCodec | None = None,
        budget: EdgeRuntimeBudget | None = None,
    ) -> None:
        resolved_budget = budget or EdgeRuntimeBudget()
        resolved_budget.validate()
        self._service = service
        self._codec = codec or Int8UpdateCodec()
        self._budget = resolved_budget

    def handle_task(self, task: TrainingTask) -> EdgeTrainingResult:
        result = self._service.handle_task(task)
        update = result.model_update
        if update is None:
            raise EdgeRuntimeError("edge worker result did not provide a model update")
        if len(update) > self._budget.max_dimension:
            raise EdgeRuntimeError("model update exceeds edge dimension budget")

        encoded = self._codec.encode(update)
        if encoded.compressed_bytes > self._budget.max_payload_bytes:
            raise EdgeRuntimeError(
                "compressed model update exceeds edge payload budget"
            )
        return EdgeTrainingResult(
            metadata=replace(result, model_update=None),
            update=encoded,
        )

    def restore(self, encoded: EdgeTrainingResult) -> TrainingResult:
        if encoded.update.dimension > self._budget.max_dimension:
            raise EdgeRuntimeError("encoded model update exceeds edge dimension budget")
        if encoded.update.compressed_bytes > self._budget.max_payload_bytes:
            raise EdgeRuntimeError("encoded payload exceeds edge payload budget")
        update = self._codec.decode(encoded.update)
        return replace(encoded.metadata, model_update=update)


__all__ = [
    "EDGE_CODEC",
    "EdgeRuntimeBudget",
    "EdgeRuntimeError",
    "EdgeTrainingResult",
    "EdgeUpdatePayload",
    "EdgeWorkerRuntime",
    "Int8UpdateCodec",
]
