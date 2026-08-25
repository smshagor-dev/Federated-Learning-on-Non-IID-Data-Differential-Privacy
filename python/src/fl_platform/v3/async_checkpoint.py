"""Crash-safe checkpoint persistence for the v3 asynchronous model state."""

from __future__ import annotations

import hashlib
import json
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

from fl_platform.v3.async_runtime import AsyncStateSnapshot


class AsyncStateStoreError(RuntimeError):
    """Raised when durable async state cannot be loaded or committed safely."""


def _snapshot_payload(snapshot: AsyncStateSnapshot) -> dict[str, object]:
    snapshot.validate()
    return {
        "schema_version": snapshot.schema_version,
        "model": list(snapshot.model),
        "version": snapshot.version,
        "mixing_alpha": snapshot.mixing_alpha,
        "max_staleness": snapshot.max_staleness,
        "applied_updates": [list(item) for item in snapshot.applied_updates],
    }


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _snapshot_from_payload(payload: dict[str, Any]) -> AsyncStateSnapshot:
    try:
        applied_updates = tuple(
            (str(item[0]), str(item[1])) for item in payload["applied_updates"]
        )
        snapshot = AsyncStateSnapshot(
            schema_version=int(payload["schema_version"]),
            model=tuple(float(value) for value in payload["model"]),
            version=int(payload["version"]),
            mixing_alpha=float(payload["mixing_alpha"]),
            max_staleness=(
                None
                if payload["max_staleness"] is None
                else int(payload["max_staleness"])
            ),
            applied_updates=applied_updates,
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise AsyncStateStoreError(
            "async checkpoint payload has invalid structure"
        ) from exc
    try:
        snapshot.validate()
    except ValueError as exc:
        raise AsyncStateStoreError(str(exc)) from exc
    return snapshot


class AsyncStateStore:
    """Atomic JSON checkpoint with a SHA-256 integrity envelope."""

    def __init__(self, path: str | Path) -> None:
        if isinstance(path, str) and not path.strip():
            raise ValueError("async state checkpoint path must not be empty")
        self._path = Path(path)
        if self._path.exists() and self._path.is_dir():
            raise ValueError("async state checkpoint path must be a file path")

    @property
    def path(self) -> Path:
        return self._path

    def save(self, snapshot: AsyncStateSnapshot) -> None:
        payload = _snapshot_payload(snapshot)
        payload_json = _canonical_json(payload)
        envelope = {
            "schema_version": 1,
            "payload": payload,
            "payload_sha256": hashlib.sha256(payload_json.encode("utf-8")).hexdigest(),
        }
        encoded = (_canonical_json(envelope) + "\n").encode("utf-8")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(self._path.name + ".tmp")
        try:
            with temp_path.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)
        except OSError as exc:
            with suppress(OSError):
                temp_path.unlink(missing_ok=True)
            raise AsyncStateStoreError(
                "failed to persist async state checkpoint"
            ) from exc

    def load(self) -> AsyncStateSnapshot | None:
        if not self._path.exists():
            return None
        if not self._path.is_file():
            raise AsyncStateStoreError("async checkpoint path is not a regular file")
        try:
            raw = self._path.read_text(encoding="utf-8")
            envelope = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AsyncStateStoreError(
                "async checkpoint is unreadable or invalid JSON"
            ) from exc
        if not isinstance(envelope, dict) or envelope.get("schema_version") != 1:
            raise AsyncStateStoreError("unsupported async checkpoint envelope schema")
        payload = envelope.get("payload")
        digest = envelope.get("payload_sha256")
        if not isinstance(payload, dict) or not isinstance(digest, str):
            raise AsyncStateStoreError("async checkpoint envelope is incomplete")
        actual = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        if actual != digest:
            raise AsyncStateStoreError("async checkpoint checksum mismatch")
        return _snapshot_from_payload(payload)


__all__ = ["AsyncStateStore", "AsyncStateStoreError"]
