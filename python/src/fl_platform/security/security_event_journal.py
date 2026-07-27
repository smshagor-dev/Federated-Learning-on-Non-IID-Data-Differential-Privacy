"""Durable, append-only, cross-language-readable security-event journal
for the Python worker -- Security Events, Metrics, and Durable Audit
Journal slice, Work Package D/E. See docs/security-events.md.

Mirrors cpp/coordinator/include/fl_coordinator/security_event_journal.hpp's
design exactly: JSON Lines, one canonical record per line, size-based
rotation, skip-and-recover corruption policy (a malformed or checksum-
failing line is dropped and counted, not fatal -- an event journal is an
observability artifact, not a trust decision; see that header's comment
for the full rationale). Kept as its own small module (not sharing code
with worker/task_journal.py, which is a keyed state store with different
semantics, not an append-only log).

Scope note (see docs/known-limitations.md): this journal is local to the
Python worker process only. Worker-originated security events are
persisted here and exposed via Prometheus (security/metrics.py) but are
NOT shipped to the coordinator/Go this slice -- that would require a new
signed wire message type, out of scope here and explicitly disclosed
rather than silently expanded.
"""

from __future__ import annotations

import json
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fl_platform.security.security_event import (
    SecurityEvent,
    compute_security_event_checksum,
    validate_security_event,
)

_DEFAULT_MAX_BYTES_BEFORE_ROTATION = 10 * 1024 * 1024  # 10 MiB
_DEFAULT_MAX_RETAINED_FILES = 5


def _now_iso8601() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize_full_event(event: SecurityEvent) -> str:
    record = asdict(event)
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _parse_event_line(line: str) -> SecurityEvent | None:
    line = line.strip()
    if not line:
        return None
    try:
        raw = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        event = SecurityEvent(
            source_service=str(raw["source_service"]),
            event_type=str(raw["event_type"]),
            schema_version=int(raw["schema_version"]),
            event_id=str(raw["event_id"]),
            severity=str(raw["severity"]),
            timestamp=str(raw["timestamp"]),
            source_component=str(raw.get("source_component", "")),
            actor_type=str(raw.get("actor_type", "SYSTEM")),
            safe_actor_id=str(raw.get("safe_actor_id", "")),
            subject_type=str(raw.get("subject_type", "SECURITY_MUTATION")),
            safe_subject_id=str(raw.get("safe_subject_id", "")),
            worker_id=str(raw.get("worker_id", "")),
            run_id=str(raw.get("run_id", "")),
            round_id=int(raw.get("round_id", 0)),
            task_id=str(raw.get("task_id", "")),
            safe_signing_key_id=str(raw.get("safe_signing_key_id", "")),
            request_id=str(raw.get("request_id", "")),
            trace_id=str(raw.get("trace_id", "")),
            outcome=str(raw.get("outcome", "ACCEPTED")),
            reason_code=str(raw.get("reason_code", "")),
            safe_details={
                str(k): str(v) for k, v in dict(raw.get("safe_details", {})).items()
            },
            payload_checksum=str(raw.get("payload_checksum", "")),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if not event.event_id or not event.timestamp:
        return None
    if compute_security_event_checksum(event) != event.payload_checksum:
        return None
    return event


class SecurityEventJournal:
    def __init__(
        self,
        path: str | Path,
        *,
        max_bytes_before_rotation: int = _DEFAULT_MAX_BYTES_BEFORE_ROTATION,
        max_retained_files: int = _DEFAULT_MAX_RETAINED_FILES,
    ) -> None:
        self.path = Path(path)
        self._max_bytes_before_rotation = max_bytes_before_rotation
        self._max_retained_files = max_retained_files
        self._lock = threading.Lock()
        self._next_sequence = 1
        self._recovered_line_count = 0
        self._rotations = 0
        self._in_memory: list[SecurityEvent] = []
        self._load()

    def _load(self) -> None:
        self._in_memory = []
        self._recovered_line_count = 0
        self._next_sequence = 1
        # A rotated .1 file surviving a restart is itself evidence
        # rotation has happened at least once -- see the identical
        # rationale in go/internal/observability/security_event_journal.go's
        # HasRotated.
        rotated_marker = self.path.with_name(f"{self.path.name}.1")
        self._rotations = 1 if rotated_marker.exists() else 0
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                event = _parse_event_line(line)
                if event is None:
                    if line.strip():
                        self._recovered_line_count += 1
                    continue
                self._in_memory.append(event)
                try:
                    sequence = int(event.event_id)
                    if sequence + 1 > self._next_sequence:
                        self._next_sequence = sequence + 1
                except ValueError:
                    pass
        if self._recovered_line_count:
            print(
                f"security_event_journal: recovered from {self._recovered_line_count} "
                f"corrupt/unparseable line(s) in {self.path}",
                file=sys.stderr,
            )

    def _next_event_id(self) -> str:
        event_id = f"{self._next_sequence:020d}"
        self._next_sequence += 1
        return event_id

    def _maybe_rotate(self) -> None:
        if (
            not self.path.exists()
            or self.path.stat().st_size < self._max_bytes_before_rotation
        ):
            return
        for generation in range(self._max_retained_files, 0, -1):
            source = (
                self.path
                if generation == 1
                else self.path.with_name(f"{self.path.name}.{generation - 1}")
            )
            destination = self.path.with_name(f"{self.path.name}.{generation}")
            if generation == self._max_retained_files and destination.exists():
                destination.unlink()
            if source.exists():
                source.replace(destination)
        self._in_memory = []
        self._rotations += 1

    def emit(self, event: SecurityEvent) -> None:
        """Fills event_id/timestamp/payload_checksum if not already set,
        validates the event, and appends it. Never raises -- an invalid
        event or filesystem failure is printed to stderr and dropped,
        matching SecurityEventJournal::emit's C++ contract."""
        with self._lock:
            if not event.timestamp:
                event.timestamp = _now_iso8601()
            if not event.event_id:
                event.event_id = self._next_event_id()
            validation = validate_security_event(event)
            if not validation.valid:
                print(
                    f"security_event_journal: dropping invalid event "
                    f"({event.event_type}): {validation.reason}",
                    file=sys.stderr,
                )
                return
            event.payload_checksum = compute_security_event_checksum(event)
            try:
                self._maybe_rotate()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(_serialize_full_event(event) + "\n")
                self._in_memory.append(event)
            except OSError as error:
                print(
                    f"security_event_journal: failed to persist event: {error}",
                    file=sys.stderr,
                )

    def list(
        self,
        *,
        after_event_id: str = "",
        limit: int = 100,
        min_severity: str | None = None,
        subject_type: str | None = None,
        event_type: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            severity_order = {"INFO": 0, "WARNING": 1, "HIGH": 2, "CRITICAL": 3}
            min_rank = severity_order.get(min_severity, -1) if min_severity else -1
            past_cursor = not after_event_id
            events: list[SecurityEvent] = []
            next_cursor = ""
            for event in self._in_memory:
                if not past_cursor:
                    if event.event_id == after_event_id:
                        past_cursor = True
                    continue
                if min_rank >= 0 and severity_order.get(event.severity, -1) < min_rank:
                    continue
                if subject_type and event.subject_type != subject_type:
                    continue
                if event_type and event.event_type != event_type:
                    continue
                if len(events) >= limit:
                    next_cursor = events[-1].event_id
                    break
                events.append(event)
            return {"events": events, "next_cursor": next_cursor}

    def recovered_line_count(self) -> int:
        with self._lock:
            return self._recovered_line_count

    def size(self) -> int:
        with self._lock:
            return len(self._in_memory)

    def last_record_timestamp(self) -> str:
        """Most recently appended event's timestamp, or "" if empty --
        used for journal health/lag reporting."""
        with self._lock:
            if not self._in_memory:
                return ""
            return self._in_memory[-1].timestamp

    def has_rotated(self) -> bool:
        """Whether this journal has ever rotated -- see the identical
        rationale in go/internal/observability/security_event_journal.go's
        HasRotated."""
        with self._lock:
            return self._rotations > 0
