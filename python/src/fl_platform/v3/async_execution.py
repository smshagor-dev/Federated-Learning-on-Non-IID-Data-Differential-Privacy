"""Durable per-result application path for asynchronous federated updates."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from dataclasses import dataclass

from fl_platform.v3.async_checkpoint import AsyncStateStore, AsyncStateStoreError
from fl_platform.v3.async_runtime import (
    AsyncApplyResult,
    AsyncModelState,
    AsyncUpdate,
    Vector,
)
from fl_platform.workers import TrainingResult


class AsyncExecutionError(RuntimeError):
    """Raised when async execution cannot preserve its durability contract."""


@dataclass(frozen=True, slots=True)
class AsyncResultOutcome:
    update_id: str | None
    payload_digest: str | None
    apply_result: AsyncApplyResult


def _logical_update_id(result: TrainingResult, base_version: int) -> str:
    if not result.run_id or not result.client_id:
        raise ValueError("async result requires run_id and client_id")
    payload = {
        "base_model_version": base_version,
        "client_id": result.client_id,
        "round_id": result.round_id,
        "run_id": result.run_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"fl-platform-async-update-id-v1\x00" + encoded).hexdigest()


def _model_update_digest(update: Vector) -> str:
    digest = hashlib.sha256(b"fl-platform-async-update-payload-v1\x00")
    digest.update(len(update).to_bytes(8, "big", signed=False))
    for value in update:
        digest.update(struct.pack("!d", float(value)))
    return digest.hexdigest()


class DurableAsyncResultProcessor:
    """Apply each worker result immediately and checkpoint every accepted update.

    This is a real per-result state transition rather than a round-buffer
    classifier. It remains process-local; distributed delivery and coordinator
    failover evidence are separate release gates.
    """

    def __init__(self, state: AsyncModelState, store: AsyncStateStore) -> None:
        self._state = state
        self._store = store

    @classmethod
    def load_or_create(
        cls,
        store: AsyncStateStore,
        initial_model: Vector,
        *,
        mixing_alpha: float = 0.5,
        max_staleness: int | None = None,
    ) -> DurableAsyncResultProcessor:
        snapshot = store.load()
        if snapshot is None:
            state = AsyncModelState(
                initial_model,
                mixing_alpha=mixing_alpha,
                max_staleness=max_staleness,
            )
        else:
            state = AsyncModelState.from_snapshot(snapshot)
            if len(state.model) != len(initial_model):
                raise AsyncExecutionError(
                    "checkpoint model dimension does not match configured initial model"
                )
            if not math.isclose(state.mixing_alpha, mixing_alpha):
                raise AsyncExecutionError(
                    "checkpoint mixing_alpha does not match configured async runtime"
                )
            if state.max_staleness != max_staleness:
                raise AsyncExecutionError(
                    "checkpoint max_staleness does not match configured async runtime"
                )
        return cls(state, store)

    @property
    def state(self) -> AsyncModelState:
        return self._state

    def apply_result(self, result: TrainingResult) -> AsyncResultOutcome:
        if not result.accepted:
            return AsyncResultOutcome(
                None,
                None,
                AsyncApplyResult(
                    False,
                    self._state.version,
                    0,
                    0.0,
                    "worker result not accepted",
                ),
            )
        if result.model_update is None:
            return AsyncResultOutcome(
                None,
                None,
                AsyncApplyResult(
                    False,
                    self._state.version,
                    0,
                    0.0,
                    "missing model update",
                ),
            )
        base_version = result.base_model_version
        if base_version is None or base_version < 0:
            return AsyncResultOutcome(
                None,
                None,
                AsyncApplyResult(
                    False,
                    self._state.version,
                    0,
                    0.0,
                    "async result requires a non-negative base_model_version",
                ),
            )
        try:
            update_id = _logical_update_id(result, base_version)
        except ValueError as exc:
            return AsyncResultOutcome(
                None,
                None,
                AsyncApplyResult(
                    False,
                    self._state.version,
                    0,
                    0.0,
                    str(exc),
                ),
            )
        delta = tuple(float(value) for value in result.model_update)
        payload_digest = _model_update_digest(delta)
        update = AsyncUpdate(
            client_id=result.client_id,
            base_version=base_version,
            delta=delta,
            update_id=update_id,
            payload_digest=payload_digest,
        )
        before = self._state.snapshot()
        applied = self._state.apply(update)
        if not applied.accepted:
            return AsyncResultOutcome(update_id, payload_digest, applied)
        try:
            self._store.save(self._state.snapshot())
        except AsyncStateStoreError as exc:
            self._state = AsyncModelState.from_snapshot(before)
            raise AsyncExecutionError(
                "accepted async update was rolled back because checkpoint commit failed"
            ) from exc
        return AsyncResultOutcome(update_id, payload_digest, applied)


__all__ = [
    "AsyncExecutionError",
    "AsyncResultOutcome",
    "DurableAsyncResultProcessor",
]
