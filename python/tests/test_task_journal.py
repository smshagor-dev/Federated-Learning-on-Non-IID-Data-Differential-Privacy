"""Tests for fl_platform.worker.task_journal -- Coordinator-Signed
Tasks slice, Work Packages N/O/P. See docs/accepted-task-journal.md
and docs/task-reissue-semantics.md.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fl_platform.worker.task_journal import (
    ACCEPTED,
    COMPLETED,
    FAILED,
    PREPARING,
    RESULT_SUBMITTED,
    TRAINING,
    AcceptedTaskJournal,
    DuplicateTaskExecutionError,
    TaskJournalError,
)


class AcceptedTaskJournalTests(unittest.TestCase):
    def test_record_accepted_creates_an_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = AcceptedTaskJournal(Path(tmp) / "journal.json")
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            entry = journal.get("task-1")
            assert entry is not None
            self.assertEqual(entry.status, ACCEPTED)
            self.assertEqual(entry.attempt, 1)

    def test_full_lifecycle_transitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = AcceptedTaskJournal(Path(tmp) / "journal.json")
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            for status in (PREPARING, TRAINING, RESULT_SUBMITTED, COMPLETED):
                journal.transition("task-1", status, now=101.0)
                entry = journal.get("task-1")
                assert entry is not None
                self.assertEqual(entry.status, status)

    def test_transition_on_unknown_task_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = AcceptedTaskJournal(Path(tmp) / "journal.json")
            with self.assertRaises(TaskJournalError):
                journal.transition("unknown-task", PREPARING, now=1.0)

    def test_reissue_with_higher_attempt_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = AcceptedTaskJournal(Path(tmp) / "journal.json")
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            journal.transition("task-1", RESULT_SUBMITTED, now=101.0)
            journal.transition("task-1", COMPLETED, now=102.0)
            # A reissue (new lease_id, attempt 2) for the same logical
            # task_id must be accepted -- this is the whole point of
            # keeping attempt in the comparison.
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-2",
                attempt=2,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=200.0,
            )
            entry = journal.get("task-1")
            assert entry is not None
            self.assertEqual(entry.attempt, 2)
            self.assertEqual(entry.status, ACCEPTED)

    def test_duplicate_execution_at_same_or_lower_attempt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = AcceptedTaskJournal(Path(tmp) / "journal.json")
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=2,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            journal.transition("task-1", RESULT_SUBMITTED, now=101.0)
            journal.transition("task-1", COMPLETED, now=102.0)
            with self.assertRaises(DuplicateTaskExecutionError):
                journal.record_accepted(
                    task_id="task-1",
                    lease_id="lease-replay",
                    attempt=2,
                    worker_id="worker-1",
                    coordinator_signing_key_id="coord-key-1",
                    now=200.0,
                )
            with self.assertRaises(DuplicateTaskExecutionError):
                journal.record_accepted(
                    task_id="task-1",
                    lease_id="lease-replay-2",
                    attempt=1,
                    worker_id="worker-1",
                    coordinator_signing_key_id="coord-key-1",
                    now=201.0,
                )

    def test_accepted_but_not_yet_submitted_does_not_block_reacceptance(self) -> None:
        # A task that only reached ACCEPTED/PREPARING (never
        # RESULT_SUBMITTED/COMPLETED) is not a "duplicate execution" --
        # it never actually executed. Re-accepting the same attempt
        # again (e.g. a retried acquire before any training happened)
        # must not raise.
        with tempfile.TemporaryDirectory() as tmp:
            journal = AcceptedTaskJournal(Path(tmp) / "journal.json")
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=101.0,
            )

    def test_recover_on_startup_marks_in_flight_tasks_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.json"
            journal = AcceptedTaskJournal(path)
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            journal.transition("task-1", PREPARING, now=101.0)
            journal.transition("task-1", TRAINING, now=102.0)
            # Simulate a crash: construct a fresh journal instance
            # against the same file (no clean shutdown transition ever
            # happened).
            restarted = AcceptedTaskJournal(path)
            recovered = restarted.recover_on_startup(now=200.0)
            self.assertEqual(recovered, ["task-1"])
            entry = restarted.get("task-1")
            assert entry is not None
            self.assertEqual(entry.status, FAILED)

    def test_recover_on_startup_leaves_completed_tasks_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.json"
            journal = AcceptedTaskJournal(path)
            journal.record_accepted(
                task_id="task-1",
                lease_id="lease-1",
                attempt=1,
                worker_id="worker-1",
                coordinator_signing_key_id="coord-key-1",
                now=100.0,
            )
            journal.transition("task-1", RESULT_SUBMITTED, now=101.0)
            journal.transition("task-1", COMPLETED, now=102.0)
            restarted = AcceptedTaskJournal(path)
            recovered = restarted.recover_on_startup(now=200.0)
            self.assertEqual(recovered, [])
            entry = restarted.get("task-1")
            assert entry is not None
            self.assertEqual(entry.status, COMPLETED)

    def test_corrupt_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(TaskJournalError):
                AcceptedTaskJournal(path)


if __name__ == "__main__":
    unittest.main()
