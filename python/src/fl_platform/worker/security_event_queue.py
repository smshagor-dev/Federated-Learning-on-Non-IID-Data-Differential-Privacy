"""Persistent worker-local security-event queue -- Web Security Center,
Event Centralization, and Security CI slice, Work Package L. See
docs/security-event-centralization.md.

Reuses security.security_event_journal.SecurityEventJournal as the
durable storage engine (it already has atomic append, restart
persistence, rotation, corruption recovery) rather than building a
second persistence mechanism from scratch. Adds only what the journal
doesn't already provide: acknowledgment of what has been successfully
relayed to the coordinator, persisted in a small sidecar cursor file so
a worker restart resumes from where it left off rather than re-sending
(or silently dropping) already-acknowledged events.

Known limitation (see docs/known-limitations.md, matching
SecurityEventJournal.list's own documented scope): the journal only
serves its currently-active, not-yet-rotated file. If a worker falls so
far behind that rotation happens before select_pending() has picked up
and acknowledged everything, the un-acknowledged records in the rotated-
away file become unreachable to this queue (they remain on disk in the
rotated file for out-of-band inspection, just not queryable here). At
the default 10 MiB/5-generation rotation policy this requires a very
large local backlog; not eliminated, but disclosed rather than silently
assumed away.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fl_platform.security.security_event import SecurityEvent
from fl_platform.security.security_event_journal import SecurityEventJournal

__all__ = [
    "SecurityEventQueueError",
    "WorkerSecurityEventQueue",
]


class SecurityEventQueueError(RuntimeError):
    """Raised on a cursor-file load failure. Never silently discarded --
    a silent discard could make the queue re-send (harmless, the
    coordinator's replay protection dedupes) or, worse, mask a real
    filesystem problem the caller should know about."""


def _cursor_path(journal_path: str | Path) -> Path:
    journal_path = Path(journal_path)
    return journal_path.with_name(f"{journal_path.name}.cursor")


def _load_cursor(journal_path: str | Path) -> str:
    path = _cursor_path(journal_path)
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SecurityEventQueueError(
            f"failed to load security-event queue cursor from {path}: {error}"
        ) from error
    if not isinstance(raw, dict):
        raise SecurityEventQueueError(
            f"malformed security-event queue cursor at {path}"
        )
    return str(raw.get("acknowledged_up_to_event_id", ""))


def _save_cursor(journal_path: str | Path, event_id: str) -> None:
    path = _cursor_path(journal_path)
    payload = {"acknowledged_up_to_event_id": event_id}
    # Atomic temp-file-then-replace, matching every other persistence
    # class in this codebase's convention (signing_key_rotation.py's
    # save_rotation_state, etc).
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload), encoding="utf-8")
    tmp_path.replace(path)


class WorkerSecurityEventQueue:
    """Thin batch-selection/acknowledgment wrapper around a
    SecurityEventJournal instance. Every worker-local security event a
    producer wants relayed to the coordinator is first enqueue()-d here
    (durable, survives a crash), then select_pending() picks up
    everything after the last acknowledged cursor for the next signed
    batch submission, and mark_acknowledged() persists the new cursor
    only after the coordinator has confirmed acceptance -- so a crash
    between select_pending() and mark_acknowledged() re-sends the same
    events on the next attempt (at-least-once delivery, never silent
    loss) rather than advancing the cursor before delivery is confirmed.
    """

    def __init__(self, journal: SecurityEventJournal) -> None:
        self._journal = journal
        self._lock = threading.Lock()
        self._acknowledged_up_to_event_id = _load_cursor(journal.path)

    def enqueue(self, event: SecurityEvent) -> None:
        self._journal.emit(event)

    def select_pending(self, max_batch_size: int) -> list[SecurityEvent]:
        with self._lock:
            result = self._journal.list(
                after_event_id=self._acknowledged_up_to_event_id,
                limit=max_batch_size,
            )
            return list(result["events"])

    def mark_acknowledged(self, up_to_event_id: str) -> None:
        if not up_to_event_id:
            return
        with self._lock:
            self._acknowledged_up_to_event_id = up_to_event_id
            _save_cursor(self._journal.path, up_to_event_id)

    def pending_count_hint(self) -> int:
        """A bounded, approximate count of unacknowledged events -- used
        only to populate the outbound batch's own queue_depth_hint field,
        which the coordinator treats as an explicitly untrusted,
        self-reported signal, never ground truth (see
        GetSecurityEventSourceHealth's proto comment)."""
        with self._lock:
            result = self._journal.list(
                after_event_id=self._acknowledged_up_to_event_id,
                limit=10_000,
            )
            return len(result["events"])
