"""Tests for fl_platform.security.security_event_journal -- Security
Events, Metrics, and Durable Audit Journal slice."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fl_platform.security.security_event import (
    EVENT_COORDINATOR_TASK_VERIFIED,
    EVENT_HEARTBEAT_ACCEPTED,
    EVENT_WORKER_REGISTERED,
    SEVERITY_CRITICAL,
    SecurityEvent,
)
from fl_platform.security.security_event_journal import SecurityEventJournal


class SecurityEventJournalTests(unittest.TestCase):
    def test_fresh_journal_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            self.assertEqual(journal.size(), 0)

    def test_emit_and_list_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            event = SecurityEvent(
                source_service="python-worker",
                event_type=EVENT_HEARTBEAT_ACCEPTED,
                worker_id="worker-1",
            )
            journal.emit(event)
            self.assertEqual(journal.size(), 1)
            listed = journal.list()
            self.assertEqual(len(listed["events"]), 1)
            self.assertTrue(listed["events"][0].event_id)
            self.assertTrue(listed["events"][0].timestamp)
            self.assertTrue(listed["events"][0].payload_checksum)
            self.assertEqual(listed["events"][0].worker_id, "worker-1")

    def test_restart_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            journal = SecurityEventJournal(path)
            journal.emit(
                SecurityEvent(
                    source_service="python-worker", event_type=EVENT_WORKER_REGISTERED
                )
            )
            restarted = SecurityEventJournal(path)
            self.assertEqual(restarted.size(), 1)
            self.assertEqual(restarted.recovered_line_count(), 0)

    def test_cursor_pagination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            journal.emit(
                SecurityEvent(
                    source_service="python-worker", event_type=EVENT_WORKER_REGISTERED
                )
            )
            journal.emit(
                SecurityEvent(
                    source_service="python-worker",
                    event_type=EVENT_COORDINATOR_TASK_VERIFIED,
                )
            )
            first_page = journal.list(limit=1)
            self.assertEqual(len(first_page["events"]), 1)
            self.assertTrue(first_page["next_cursor"])
            second_page = journal.list(after_event_id=first_page["next_cursor"])
            self.assertEqual(len(second_page["events"]), 1)
            self.assertEqual(
                second_page["events"][0].event_type, EVENT_COORDINATOR_TASK_VERIFIED
            )

    def test_severity_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            journal.emit(
                SecurityEvent(
                    source_service="python-worker", event_type=EVENT_WORKER_REGISTERED
                )
            )
            filtered = journal.list(min_severity=SEVERITY_CRITICAL)
            self.assertEqual(len(filtered["events"]), 0)

    def test_invalid_event_is_dropped_not_raised(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            invalid = SecurityEvent(
                source_service="", event_type=EVENT_WORKER_REGISTERED
            )
            journal.emit(invalid)  # must not raise
            self.assertEqual(journal.size(), 0)

    def test_corruption_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            journal = SecurityEventJournal(path)
            journal.emit(
                SecurityEvent(
                    source_service="python-worker", event_type=EVENT_WORKER_REGISTERED
                )
            )
            with path.open("a", encoding="utf-8") as handle:
                handle.write("not valid json at all\n")
                handle.write(
                    '{"schema_version":1,"event_id":"x"}\n'
                )  # valid JSON, bad checksum
            reloaded = SecurityEventJournal(path)
            self.assertEqual(reloaded.size(), 1)
            self.assertEqual(reloaded.recovered_line_count(), 2)

    def test_rotation_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            journal = SecurityEventJournal(
                path, max_bytes_before_rotation=200, max_retained_files=2
            )
            for i in range(20):
                journal.emit(
                    SecurityEvent(
                        source_service="python-worker",
                        event_type=EVENT_HEARTBEAT_ACCEPTED,
                        worker_id=f"worker-{i}",
                    )
                )
            self.assertTrue((Path(tmp) / "events.jsonl.1").exists())
            self.assertFalse((Path(tmp) / "events.jsonl.3").exists())
            self.assertLess(journal.size(), 20)


if __name__ == "__main__":
    unittest.main()
