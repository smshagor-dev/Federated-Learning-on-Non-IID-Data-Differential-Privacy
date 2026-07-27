"""Worker-side replay protection for coordinator-signed tasks --
Coordinator-Signed Tasks slice, Work Package M. See
docs/coordinator-task-replay-protection.md.

Mirrors cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp's
validate/commit transaction split (validate() is read-only; commit()
is only called after every other check -- signature, hash, expiry,
worker binding -- has already accepted the task), but runs on the
worker, tracking the *coordinator's* issued sequence rather than a
worker's own. Track key is coordinator_signing_key_id alone: a worker
only ever receives tasks from the one coordinator it is configured
against, so there is no separate worker_id/message_stream dimension to
track (unlike the coordinator-side store, which must disambiguate many
workers).

Persistent, atomic writes -- same convention as
fl_platform.security.sequence_state.SequenceStateStore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any


class CoordinatorTaskReplayError(RuntimeError):
    """Raised if the persisted replay-state file exists but is
    corrupt/unreadable -- never silently reset (a silent reset would
    let an old sequence number / nonce be accepted again)."""


@dataclass(slots=True, frozen=True)
class CoordinatorTaskReplayCandidate:
    coordinator_signing_key_id: str
    sequence_number: int
    nonce: str


@dataclass(slots=True, frozen=True)
class CoordinatorTaskReplayDecision:
    accepted: bool
    # "duplicate_or_lower_sequence" | "duplicate_nonce"; "" when accepted
    reason: str = ""
    detail: str = "ok"


class CoordinatorTaskReplayStore:
    """Bounded: at most kMaxNonceEntriesPerTrack recent nonces are
    retained per track (oldest-first eviction) -- matching
    ReplayProtectionStore's bound. Unlike that store, no time-based
    nonce expiry is implemented here: a worker's per-coordinator-key
    track count is tiny (one coordinator, occasionally rotated keys),
    so the fixed per-track cap alone is a sufficient bound -- see
    docs/coordinator-task-replay-protection.md's "What is deferred"
    section.
    """

    kMaxNonceEntriesPerTrack = 256

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        if self.path.exists():
            self._load()  # raises on corruption; never silently starts empty

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CoordinatorTaskReplayError(
                f"failed to load coordinator task replay state from {self.path}: "
                f"{error}"
            ) from error
        if not isinstance(raw, dict):
            raise CoordinatorTaskReplayError(
                f"coordinator task replay state file {self.path} is not a JSON object"
            )
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)

    def validate(
        self, candidate: CoordinatorTaskReplayCandidate
    ) -> CoordinatorTaskReplayDecision:
        """Read-only. Callers must call commit() separately, and only
        after the candidate has passed every other check (signature,
        hash comparisons, expiry, worker binding) -- never persist
        replay state for a task that was otherwise rejected."""
        with self._lock:
            state = self._load() if self.path.exists() else {}
            track = state.get(candidate.coordinator_signing_key_id, {})
            last_sequence = int(track.get("last_sequence_number", 0))
            if candidate.sequence_number <= last_sequence:
                return CoordinatorTaskReplayDecision(
                    accepted=False,
                    reason="duplicate_or_lower_sequence",
                    detail=(
                        f"sequence_number {candidate.sequence_number} is not "
                        f"strictly greater than the last accepted sequence "
                        f"{last_sequence} for this coordinator signing key"
                    ),
                )
            recent_nonces = track.get("recent_nonces", [])
            if candidate.nonce in recent_nonces:
                return CoordinatorTaskReplayDecision(
                    accepted=False,
                    reason="duplicate_nonce",
                    detail=(
                        "this nonce has already been accepted for this coordinator "
                        "signing key"
                    ),
                )
            return CoordinatorTaskReplayDecision(accepted=True)

    def commit(self, candidate: CoordinatorTaskReplayCandidate) -> None:
        """Records `candidate` as accepted. Caller must have already
        called validate() on this exact candidate and checked
        `accepted`; commit() does not re-validate."""
        with self._lock:
            state = self._load() if self.path.exists() else {}
            track = state.get(candidate.coordinator_signing_key_id, {})
            recent_nonces = list(track.get("recent_nonces", []))
            recent_nonces.append(candidate.nonce)
            if len(recent_nonces) > self.kMaxNonceEntriesPerTrack:
                recent_nonces = recent_nonces[-self.kMaxNonceEntriesPerTrack :]
            state[candidate.coordinator_signing_key_id] = {
                "last_sequence_number": candidate.sequence_number,
                "recent_nonces": recent_nonces,
            }
            self._save(state)
