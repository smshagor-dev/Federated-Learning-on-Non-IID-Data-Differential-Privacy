"""Tests for fl_platform.worker.security_event_queue.WorkerSecurityEventQueue
-- Web Security Center, Event Centralization, and Security CI slice,
Work Package L. See docs/security-event-centralization.md.

Covers: batch selection, acknowledgment advancing the cursor, at-least-
once redelivery when acknowledgment never happens (simulating a crash or
a rejected submission between select_pending() and mark_acknowledged()),
and cursor persistence across a simulated worker restart (a fresh
SecurityEventJournal + WorkerSecurityEventQueue pointed at the same
on-disk path).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fl_platform.security.security_event import (
    EVENT_HEARTBEAT_ACCEPTED,
    EVENT_WORKER_ACTIVATED,
    EVENT_WORKER_KEY_ROTATION_ACCEPTED,
    EVENT_WORKER_REGISTERED,
    SecurityEvent,
)
from fl_platform.security.security_event_journal import SecurityEventJournal
from fl_platform.worker.security_event_queue import (
    SecurityEventQueueError,
    WorkerSecurityEventQueue,
)


def _event(event_type: str = EVENT_HEARTBEAT_ACCEPTED) -> SecurityEvent:
    return SecurityEvent(
        source_service="python-worker", event_type=event_type, worker_id="worker-1"
    )


class WorkerSecurityEventQueueTests(unittest.TestCase):
    def test_fresh_queue_selects_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            queue = WorkerSecurityEventQueue(journal)
            self.assertEqual(queue.select_pending(100), [])
            self.assertEqual(queue.pending_count_hint(), 0)

    def test_enqueue_then_select_pending_returns_the_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event("HEARTBEAT_ACCEPTED"))
            queue.enqueue(_event("WORKER_KEY_ROTATION_ACCEPTED"))
            pending = queue.select_pending(100)
            self.assertEqual(len(pending), 2)
            self.assertEqual(pending[0].event_type, "HEARTBEAT_ACCEPTED")
            self.assertEqual(pending[1].event_type, "WORKER_KEY_ROTATION_ACCEPTED")
            self.assertEqual(queue.pending_count_hint(), 2)

    def test_mark_acknowledged_advances_the_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event())
            queue.enqueue(_event())
            pending = queue.select_pending(100)
            queue.mark_acknowledged(pending[-1].event_id)
            self.assertEqual(queue.select_pending(100), [])
            self.assertEqual(queue.pending_count_hint(), 0)

    def test_partial_acknowledgment_leaves_the_remainder_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event(EVENT_HEARTBEAT_ACCEPTED))
            queue.enqueue(_event(EVENT_WORKER_REGISTERED))
            queue.enqueue(_event(EVENT_WORKER_ACTIVATED))
            first_two = queue.select_pending(2)
            queue.mark_acknowledged(first_two[-1].event_id)
            remaining = queue.select_pending(100)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(remaining[0].event_type, EVENT_WORKER_ACTIVATED)

    def test_no_acknowledgment_means_at_least_once_redelivery(self) -> None:
        # Simulates a crash, or a rejected batch, between select_pending()
        # and mark_acknowledged() -- the same events must be selectable
        # again on the next attempt, never silently dropped.
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event())
            first_attempt = queue.select_pending(100)
            second_attempt = queue.select_pending(100)
            self.assertEqual(
                [event.event_id for event in first_attempt],
                [event.event_id for event in second_attempt],
            )

    def test_cursor_persists_across_a_simulated_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            journal = SecurityEventJournal(path)
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event(EVENT_HEARTBEAT_ACCEPTED))
            queue.enqueue(_event(EVENT_WORKER_KEY_ROTATION_ACCEPTED))
            acknowledged = queue.select_pending(1)
            queue.mark_acknowledged(acknowledged[-1].event_id)

            # A brand-new journal + queue instance over the same on-disk
            # path, as if the worker process had restarted.
            restarted_journal = SecurityEventJournal(path)
            restarted_queue = WorkerSecurityEventQueue(restarted_journal)
            remaining = restarted_queue.select_pending(100)
            self.assertEqual(len(remaining), 1)
            self.assertEqual(
                remaining[0].event_type, EVENT_WORKER_KEY_ROTATION_ACCEPTED
            )

    def test_mark_acknowledged_with_empty_id_is_a_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = SecurityEventJournal(Path(tmp) / "events.jsonl")
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event())
            queue.mark_acknowledged("")
            self.assertEqual(len(queue.select_pending(100)), 1)

    def test_malformed_cursor_file_raises_rather_than_silently_resetting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            journal = SecurityEventJournal(path)
            queue = WorkerSecurityEventQueue(journal)
            queue.enqueue(_event())
            acknowledged = queue.select_pending(100)
            queue.mark_acknowledged(acknowledged[-1].event_id)

            cursor_path = Path(tmp) / "events.jsonl.cursor"
            cursor_path.write_text("not valid json", encoding="utf-8")

            with self.assertRaises(SecurityEventQueueError):
                WorkerSecurityEventQueue(SecurityEventJournal(path))


if __name__ == "__main__":
    unittest.main()
