"""Accepted-task execution journal -- Coordinator-Signed Tasks slice,
Work Packages N/O/P. See docs/accepted-task-journal.md and
docs/task-reissue-semantics.md.

Tracks every task this worker has accepted through
ACCEPTED -> PREPARING -> TRAINING -> RESULT_READY -> RESULT_SUBMITTED
-> COMPLETED (or FAILED/CANCELED at any point). Two jobs:

1. Crash recovery (Work Package O): on process startup, any entry left
   in PREPARING/TRAINING (the process died mid-execution) is marked
   FAILED with reason "worker_restarted_during_execution" -- this
   codebase has no training-state checkpointing to safely resume from,
   so the deliberately conservative policy is "never silently resume;
   require the coordinator to reissue" (a fresh AcquireTask call gets a
   new lease_id/attempt for the same logical task_id, per
   TaskDispatcher's existing requeue behavior).
2. Duplicate-execution prevention (Work Package P): TaskDispatcher
   keeps task_id stable and increments `attempt` on every reissue --
   record_accepted() rejects an acceptance whose attempt is not
   strictly greater than any attempt already RESULT_SUBMITTED/COMPLETED
   for that task_id, so a duplicate/replayed task can never be executed
   twice even if it independently passes every other check.

Persistent, atomic writes -- same convention as
fl_platform.security.sequence_state.SequenceStateStore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

ACCEPTED = "ACCEPTED"
PREPARING = "PREPARING"
TRAINING = "TRAINING"
RESULT_READY = "RESULT_READY"
RESULT_SUBMITTED = "RESULT_SUBMITTED"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
CANCELED = "CANCELED"

_TERMINAL_SUCCESS_STATUSES = frozenset({RESULT_SUBMITTED, COMPLETED})
_IN_FLIGHT_STATUSES = frozenset({PREPARING, TRAINING})


class TaskJournalError(RuntimeError):
    """Raised on a corrupt journal file -- never silently reset (a
    silent reset would lose the in-flight-execution history crash
    recovery depends on)."""


class DuplicateTaskExecutionError(RuntimeError):
    """Raised by record_accepted() when a task_id/attempt has already
    been executed (RESULT_SUBMITTED or COMPLETED) -- the caller must
    not execute it again."""

    def __init__(self, task_id: str, attempt: int, existing_attempt: int) -> None:
        super().__init__(
            f"task_id '{task_id}' attempt {attempt} was rejected: attempt "
            f"{existing_attempt} of this same task_id has already been "
            "submitted/completed -- refusing to execute a duplicate/replayed task"
        )
        self.task_id = task_id
        self.attempt = attempt
        self.existing_attempt = existing_attempt


@dataclass(slots=True, frozen=True)
class JournalEntry:
    task_id: str
    lease_id: str
    attempt: int
    worker_id: str
    coordinator_signing_key_id: str
    status: str
    updated_at: float


class AcceptedTaskJournal:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = Lock()
        if self.path.exists():
            self._load()  # raises on corruption; never silently starts empty

    def _load(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise TaskJournalError(
                f"failed to load accepted-task journal from {self.path}: {error}"
            ) from error
        if not isinstance(raw, dict):
            raise TaskJournalError(
                f"accepted-task journal file {self.path} is not a JSON object"
            )
        return raw

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(state, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)

    def get(self, task_id: str) -> JournalEntry | None:
        with self._lock:
            state = self._load() if self.path.exists() else {}
            raw_entry = state.get(task_id)
            if raw_entry is None:
                return None
            return JournalEntry(
                task_id=task_id,
                lease_id=str(raw_entry["lease_id"]),
                attempt=int(raw_entry["attempt"]),
                worker_id=str(raw_entry["worker_id"]),
                coordinator_signing_key_id=str(raw_entry["coordinator_signing_key_id"]),
                status=str(raw_entry["status"]),
                updated_at=float(raw_entry["updated_at"]),
            )

    def record_accepted(
        self,
        *,
        task_id: str,
        lease_id: str,
        attempt: int,
        worker_id: str,
        coordinator_signing_key_id: str,
        now: float,
    ) -> None:
        """Raises DuplicateTaskExecutionError if this exact task_id has
        already been submitted/completed at an attempt >= this one --
        must be called (and must succeed) before any model/dataset
        access, matching CoordinatorClient.acquire_task's verification
        pipeline ordering."""
        with self._lock:
            state = self._load() if self.path.exists() else {}
            existing = state.get(task_id)
            if existing is not None:
                existing_attempt = int(existing.get("attempt", 0))
                existing_status = str(existing.get("status", ""))
                already_done = existing_status in _TERMINAL_SUCCESS_STATUSES
                if already_done and existing_attempt >= attempt:
                    raise DuplicateTaskExecutionError(
                        task_id, attempt, existing_attempt
                    )
            state[task_id] = {
                "lease_id": lease_id,
                "attempt": attempt,
                "worker_id": worker_id,
                "coordinator_signing_key_id": coordinator_signing_key_id,
                "status": ACCEPTED,
                "updated_at": now,
            }
            self._save(state)

    def transition(self, task_id: str, status: str, now: float) -> None:
        with self._lock:
            state = self._load() if self.path.exists() else {}
            existing = state.get(task_id)
            if existing is None:
                raise TaskJournalError(
                    f"cannot transition task_id '{task_id}' to {status}: no "
                    "journal entry exists (record_accepted must be called first)"
                )
            existing["status"] = status
            existing["updated_at"] = now
            state[task_id] = existing
            self._save(state)

    def recover_on_startup(self, now: float) -> list[str]:
        """Marks every entry left PREPARING/TRAINING as FAILED (the
        process died mid-execution) -- see this module's docstring for
        why this is the safe policy rather than silently resuming.
        Returns the task_ids that were recovered, for the caller to log."""
        with self._lock:
            state = self._load() if self.path.exists() else {}
            recovered: list[str] = []
            for task_id, entry in state.items():
                if str(entry.get("status", "")) in _IN_FLIGHT_STATUSES:
                    entry["status"] = FAILED
                    entry["updated_at"] = now
                    entry["failure_reason"] = "worker_restarted_during_execution"
                    recovered.append(task_id)
            if recovered:
                self._save(state)
            return recovered
