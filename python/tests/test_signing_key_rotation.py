"""Tests for fl_platform.security.signing_key_rotation -- worker-side
local signing-key state during rotation (Signing-Key Lifecycle slice,
Work Package F). See docs/key-rotation.md.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fl_platform.security.signing_key_rotation import (
    KeyRotationStateError,
    WorkerKeyRotationState,
    generate_rotated_signing_identity,
    load_keyed_signing_identity,
    load_rotation_state,
    save_keyed_signing_identity,
    save_rotation_state,
)


class KeyedSigningIdentityTests(unittest.TestCase):
    def test_two_keys_for_the_same_worker_coexist_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = generate_rotated_signing_identity("worker-1")
            second = generate_rotated_signing_identity("worker-1")
            save_keyed_signing_identity(first, tmp)
            save_keyed_signing_identity(second, tmp)

            reloaded_first = load_keyed_signing_identity("worker-1", first.key_id, tmp)
            reloaded_second = load_keyed_signing_identity(
                "worker-1", second.key_id, tmp
            )
            self.assertEqual(reloaded_first.public_key_hex(), first.public_key_hex())
            self.assertEqual(reloaded_second.public_key_hex(), second.public_key_hex())
            self.assertNotEqual(
                reloaded_first.public_key_hex(), reloaded_second.public_key_hex()
            )

    def test_loading_an_unknown_key_raises(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(KeyRotationStateError),
        ):
            load_keyed_signing_identity("worker-1", "no-such-key", tmp)

    def test_loading_with_a_mismatched_key_id_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = generate_rotated_signing_identity("worker-1")
            saved_path = save_keyed_signing_identity(identity, tmp)
            # Simulate a corrupted/mislabeled file: the bytes on disk
            # derive to `identity.key_id`, but the path claims a
            # different key_id -- must be rejected, not silently trusted.
            mismatched_path = Path(tmp) / "worker-1.wrong-key-id.signing-key.pem"
            mismatched_path.write_bytes(saved_path.read_bytes())
            with self.assertRaises(KeyRotationStateError):
                load_keyed_signing_identity("worker-1", "wrong-key-id", tmp)


class RotationStateTests(unittest.TestCase):
    def test_no_state_file_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotation_state.json"
            self.assertIsNone(load_rotation_state(path))

    def test_save_and_reload_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotation_state.json"
            state = WorkerKeyRotationState(
                worker_id="worker-1",
                current_key_id="key-2",
                previous_key_id="key-1",
                grace_period_end_unix_s=1000.0,
            )
            save_rotation_state(state, path)
            reloaded = load_rotation_state(path)
            self.assertEqual(reloaded, state)

    def test_malformed_state_file_raises_not_silently_discarded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotation_state.json"
            path.write_text("not valid json", encoding="utf-8")
            with self.assertRaises(KeyRotationStateError):
                load_rotation_state(path)

    def test_missing_required_field_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotation_state.json"
            path.write_text('{"worker_id": "worker-1"}', encoding="utf-8")
            with self.assertRaises(KeyRotationStateError):
                load_rotation_state(path)

    def test_save_is_atomic_no_partial_file_left_on_disk(self) -> None:
        # A basic sanity check: after save_rotation_state returns, the
        # target file exists and is valid (no .tmp artifact left behind).
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rotation_state.json"
            state = WorkerKeyRotationState(worker_id="worker-1", current_key_id="key-1")
            save_rotation_state(state, path)
            self.assertTrue(path.exists())
            leftover_tmp_files = list(Path(tmp).glob("*.tmp"))
            self.assertEqual(leftover_tmp_files, [])


if __name__ == "__main__":
    unittest.main()
