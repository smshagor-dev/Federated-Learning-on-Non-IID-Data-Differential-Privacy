"""Tests for fl_platform.security.coordinator_task_replay --
Coordinator-Signed Tasks slice, Work Package M. See
docs/coordinator-task-replay-protection.md.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fl_platform.security.coordinator_task_replay import (
    CoordinatorTaskReplayCandidate,
    CoordinatorTaskReplayError,
    CoordinatorTaskReplayStore,
)


class CoordinatorTaskReplayStoreTests(unittest.TestCase):
    def test_first_candidate_for_a_fresh_track_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            candidate = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-1", sequence_number=1, nonce="n1"
            )
            decision = store.validate(candidate)
            self.assertTrue(decision.accepted)
            store.commit(candidate)

    def test_lower_or_equal_sequence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            first = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-1", sequence_number=5, nonce="n1"
            )
            store.commit(first)
            replay_same = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-1", sequence_number=5, nonce="n2"
            )
            decision = store.validate(replay_same)
            self.assertFalse(decision.accepted)
            self.assertEqual(decision.reason, "duplicate_or_lower_sequence")

            replay_lower = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-1", sequence_number=3, nonce="n3"
            )
            decision_lower = store.validate(replay_lower)
            self.assertFalse(decision_lower.accepted)
            self.assertEqual(decision_lower.reason, "duplicate_or_lower_sequence")

    def test_duplicate_nonce_is_rejected_even_with_higher_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            first = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-1",
                sequence_number=1,
                nonce="reused",
            )
            store.commit(first)
            replay = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-1",
                sequence_number=2,
                nonce="reused",
            )
            decision = store.validate(replay)
            self.assertFalse(decision.accepted)
            self.assertEqual(decision.reason, "duplicate_nonce")

    def test_different_coordinator_signing_keys_are_independent_tracks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            store.commit(
                CoordinatorTaskReplayCandidate(
                    coordinator_signing_key_id="coord-key-1",
                    sequence_number=5,
                    nonce="n1",
                )
            )
            other_key_candidate = CoordinatorTaskReplayCandidate(
                coordinator_signing_key_id="coord-key-2", sequence_number=1, nonce="n2"
            )
            decision = store.validate(other_key_candidate)
            self.assertTrue(decision.accepted)

    def test_restart_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            store = CoordinatorTaskReplayStore(path)
            store.commit(
                CoordinatorTaskReplayCandidate(
                    coordinator_signing_key_id="coord-key-1",
                    sequence_number=7,
                    nonce="n1",
                )
            )
            restarted = CoordinatorTaskReplayStore(path)
            decision = restarted.validate(
                CoordinatorTaskReplayCandidate(
                    coordinator_signing_key_id="coord-key-1",
                    sequence_number=7,
                    nonce="n2",
                )
            )
            self.assertFalse(decision.accepted)

    def test_nonce_cap_evicts_oldest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            for i in range(CoordinatorTaskReplayStore.kMaxNonceEntriesPerTrack + 10):
                store.commit(
                    CoordinatorTaskReplayCandidate(
                        coordinator_signing_key_id="coord-key-1",
                        sequence_number=i + 1,
                        nonce=f"n{i}",
                    )
                )
            # The very first nonce should have been evicted -- reusing it
            # (with a fresh, higher sequence) must be accepted again.
            decision = store.validate(
                CoordinatorTaskReplayCandidate(
                    coordinator_signing_key_id="coord-key-1",
                    sequence_number=10_000,
                    nonce="n0",
                )
            )
            self.assertTrue(decision.accepted)

    def test_corrupt_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "replay.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(CoordinatorTaskReplayError):
                CoordinatorTaskReplayStore(path)


if __name__ == "__main__":
    unittest.main()
