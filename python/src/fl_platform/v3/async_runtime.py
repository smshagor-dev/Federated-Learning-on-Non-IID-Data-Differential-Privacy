"""Asynchronous federated-learning primitives for the v3 platform."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

Vector = tuple[float, ...]
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class AsyncUpdate:
    client_id: str
    base_version: int
    delta: Vector
    update_id: str | None = None
    payload_digest: str | None = None


@dataclass(frozen=True)
class AsyncApplyResult:
    accepted: bool
    version: int
    staleness: int
    weight: float
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class AsyncStateSnapshot:
    schema_version: int
    model: Vector
    version: int
    mixing_alpha: float
    max_staleness: int | None
    applied_updates: tuple[tuple[str, str], ...]

    def validate(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported async state snapshot schema version")
        if not self.model or not all(math.isfinite(value) for value in self.model):
            raise ValueError("snapshot model must be a non-empty finite vector")
        if self.version < 0:
            raise ValueError("snapshot version must be non-negative")
        if not 0.0 < self.mixing_alpha <= 1.0 or not math.isfinite(
            self.mixing_alpha
        ):
            raise ValueError("snapshot mixing_alpha must be finite and in (0, 1]")
        if self.max_staleness is not None and self.max_staleness < 0:
            raise ValueError("snapshot max_staleness must be non-negative")
        seen: set[str] = set()
        for update_id, digest in self.applied_updates:
            if not update_id or update_id in seen:
                raise ValueError("snapshot contains an invalid or duplicate update_id")
            if _SHA256_HEX.fullmatch(digest) is None:
                raise ValueError("snapshot contains an invalid update payload digest")
            seen.add(update_id)


def staleness_weight(staleness: int, *, beta: float = 1.0) -> float:
    if staleness < 0:
        raise ValueError("staleness must be non-negative")
    if beta <= 0.0 or not math.isfinite(beta):
        raise ValueError("beta must be finite and positive")
    return 1.0 / (1.0 + beta * staleness)


class AsyncModelState:
    """Apply staleness-aware client deltas without a round barrier.

    Optional ``update_id`` + ``payload_digest`` bindings provide replay/conflict
    protection. They are deliberately optional for backward compatibility with
    the v3 foundation API, but the durable result processor always supplies
    both and persists them in the state snapshot.
    """

    def __init__(
        self,
        initial: Vector,
        *,
        mixing_alpha: float = 0.5,
        max_staleness: int | None = None,
    ) -> None:
        if not initial or not all(math.isfinite(value) for value in initial):
            raise ValueError("initial model must be a non-empty finite vector")
        if not 0.0 < mixing_alpha <= 1.0 or not math.isfinite(mixing_alpha):
            raise ValueError("mixing_alpha must be finite and in (0, 1]")
        if max_staleness is not None and max_staleness < 0:
            raise ValueError("max_staleness must be non-negative")
        self._model = tuple(float(value) for value in initial)
        self._version = 0
        self._mixing_alpha = float(mixing_alpha)
        self._max_staleness = max_staleness
        self._applied_updates: dict[str, str] = {}

    @property
    def model(self) -> Vector:
        return self._model

    @property
    def version(self) -> int:
        return self._version

    @property
    def mixing_alpha(self) -> float:
        return self._mixing_alpha

    @property
    def max_staleness(self) -> int | None:
        return self._max_staleness

    def snapshot(self) -> AsyncStateSnapshot:
        snapshot = AsyncStateSnapshot(
            schema_version=1,
            model=self._model,
            version=self._version,
            mixing_alpha=self._mixing_alpha,
            max_staleness=self._max_staleness,
            applied_updates=tuple(sorted(self._applied_updates.items())),
        )
        snapshot.validate()
        return snapshot

    @classmethod
    def from_snapshot(cls, snapshot: AsyncStateSnapshot) -> AsyncModelState:
        snapshot.validate()
        state = cls(
            snapshot.model,
            mixing_alpha=snapshot.mixing_alpha,
            max_staleness=snapshot.max_staleness,
        )
        state._version = snapshot.version
        state._applied_updates = dict(snapshot.applied_updates)
        return state

    def apply(self, update: AsyncUpdate) -> AsyncApplyResult:
        replay = self._validate_replay_binding(update)
        if replay is not None:
            return replay
        if update.base_version > self._version:
            return AsyncApplyResult(False, self._version, 0, 0.0, "future version")
        if update.base_version < 0:
            return AsyncApplyResult(False, self._version, 0, 0.0, "invalid base version")
        staleness = self._version - update.base_version
        if self._max_staleness is not None and staleness > self._max_staleness:
            return AsyncApplyResult(
                False,
                self._version,
                staleness,
                0.0,
                "too stale",
            )
        if len(update.delta) != len(self._model):
            return AsyncApplyResult(
                False,
                self._version,
                staleness,
                0.0,
                "dimension mismatch",
            )
        if not all(math.isfinite(value) for value in update.delta):
            return AsyncApplyResult(
                False,
                self._version,
                staleness,
                0.0,
                "non-finite update",
            )

        weight = self._mixing_alpha * staleness_weight(staleness)
        self._model = tuple(
            value + weight * delta
            for value, delta in zip(self._model, update.delta, strict=True)
        )
        self._version += 1
        if update.update_id is not None and update.payload_digest is not None:
            self._applied_updates[update.update_id] = update.payload_digest
        return AsyncApplyResult(True, self._version, staleness, weight)

    def _validate_replay_binding(self, update: AsyncUpdate) -> AsyncApplyResult | None:
        if update.update_id is None and update.payload_digest is None:
            return None
        if (
            update.update_id is None
            or not update.update_id.strip()
            or update.payload_digest is None
            or _SHA256_HEX.fullmatch(update.payload_digest) is None
        ):
            return AsyncApplyResult(
                False,
                self._version,
                0,
                0.0,
                "invalid update identity",
            )
        existing = self._applied_updates.get(update.update_id)
        if existing is None:
            return None
        if existing == update.payload_digest:
            return AsyncApplyResult(
                False,
                self._version,
                0,
                0.0,
                "duplicate update",
            )
        return AsyncApplyResult(
            False,
            self._version,
            0,
            0.0,
            "conflicting replay",
        )


__all__ = [
    "AsyncApplyResult",
    "AsyncModelState",
    "AsyncStateSnapshot",
    "AsyncUpdate",
    "staleness_weight",
]
