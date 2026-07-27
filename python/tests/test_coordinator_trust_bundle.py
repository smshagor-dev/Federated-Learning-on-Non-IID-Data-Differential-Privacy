"""Tests for fl_platform.security.coordinator_trust_bundle --
Coordinator-Signed Tasks slice, Work Package K, strengthened in the
Security Administration slice, Work Packages E/F.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from fl_platform.security.coordinator_trust_bundle import (
    CoordinatorTrustBundleError,
    TrustedCoordinatorKeyBundleReloader,
    _fnv1a_hash,
    _hash_to_hex,
    load_trusted_coordinator_key_bundle,
    load_trusted_coordinator_keys,
)


def _write_valid_bundle(
    path: Path,
    *,
    bundle_version: int = 1,
    active_signing_key_id: str = "coord-key-1",
    keys: list[dict[str, Any]] | None = None,
) -> None:
    """Builds a bundle exactly the way trusted_key_bundle.cpp's
    write_trusted_key_bundle does: compute the checksum over the JSON
    body (including its own closing brace), then append the checksum
    field and a new closing brace -- so tests exercise the real
    verification path, not a bypass of it."""
    if keys is None:
        keys = [
            {
                "signing_key_id": "coord-key-1",
                "public_key_hex": "a" * 64,
                "public_key_fingerprint": "fp-a",
                "status": "active",
                "created_at_unix_s": 100.0,
                "expires_at_unix_s": 0.0,
                "grace_period_end_unix_s": 0.0,
                "revoked_at_unix_s": 0.0,
            }
        ]
    body = {
        "schema_version": 1,
        "coordinator_identity": "coordinator",
        "bundle_version": bundle_version,
        "generated_at_unix_s": 100.0,
        "active_signing_key_id": active_signing_key_id,
        "keys": keys,
    }
    body_str = json.dumps(body, separators=(",", ":"))
    checksum = _hash_to_hex(_fnv1a_hash(body_str.encode("utf-8")))
    full = body_str[:-1] + f',"checksum":"{checksum}"' + "}"
    path.write_text(full, encoding="utf-8")


class TrustBundleTests(unittest.TestCase):
    def test_missing_file_raises(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(CoordinatorTrustBundleError),
        ):
            load_trusted_coordinator_keys(Path(tmp) / "does-not-exist.json")

    def test_valid_bundle_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(
                path,
                keys=[
                    {
                        "signing_key_id": "coord-key-1",
                        "public_key_hex": "a" * 64,
                        "public_key_fingerprint": "fp-a",
                        "status": "active",
                        "created_at_unix_s": 100.0,
                        "expires_at_unix_s": 0.0,
                        "grace_period_end_unix_s": 0.0,
                        "revoked_at_unix_s": 0.0,
                    },
                    {
                        "signing_key_id": "coord-key-0",
                        "public_key_hex": "b" * 64,
                        "public_key_fingerprint": "fp-b",
                        "status": "grace_period",
                        "created_at_unix_s": 50.0,
                        "expires_at_unix_s": 0.0,
                        "grace_period_end_unix_s": 200.0,
                        "revoked_at_unix_s": 0.0,
                    },
                ],
            )
            keys = load_trusted_coordinator_keys(path)
            self.assertEqual(len(keys), 2)
            self.assertEqual(keys["coord-key-1"].status, "active")
            self.assertEqual(keys["coord-key-0"].status, "grace_period")
            self.assertEqual(keys["coord-key-0"].grace_period_end_unix_s, 200.0)

    def test_full_bundle_metadata_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=3)
            bundle = load_trusted_coordinator_key_bundle(path)
            self.assertEqual(bundle.bundle_version, 3)
            self.assertEqual(bundle.schema_version, 1)
            self.assertEqual(bundle.active_signing_key_id, "coord-key-1")

    def test_malformed_json_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text("not json", encoding="utf-8")
            with self.assertRaises(CoordinatorTrustBundleError):
                load_trusted_coordinator_keys(path)

    def test_wrong_shape_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(json.dumps({"not_keys": []}), encoding="utf-8")
            with self.assertRaises(CoordinatorTrustBundleError):
                load_trusted_coordinator_keys(path)

    def test_malformed_key_entry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            path.write_text(
                json.dumps({"keys": [{"signing_key_id": "coord-key-1"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(CoordinatorTrustBundleError):
                load_trusted_coordinator_keys(path)

    def test_checksum_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path)
            # Flip one character inside the checksummed body -- the
            # checksum no longer matches.
            original = path.read_text(encoding="utf-8")
            tampered = original.replace("coord-key-1", "coord-key-9", 1)
            path.write_text(tampered, encoding="utf-8")
            with self.assertRaises(CoordinatorTrustBundleError):
                load_trusted_coordinator_keys(path)

    def test_duplicate_active_keys_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(
                path,
                keys=[
                    {
                        "signing_key_id": "coord-key-1",
                        "public_key_hex": "a" * 64,
                        "status": "active",
                    },
                    {
                        "signing_key_id": "coord-key-2",
                        "public_key_hex": "b" * 64,
                        "status": "active",
                    },
                ],
            )
            with self.assertRaises(CoordinatorTrustBundleError):
                load_trusted_coordinator_keys(path)

    def test_unsupported_schema_version_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            body = {"schema_version": 99, "keys": []}
            body_str = json.dumps(body, separators=(",", ":"))
            checksum = _hash_to_hex(_fnv1a_hash(body_str.encode("utf-8")))
            full = body_str[:-1] + f',"checksum":"{checksum}"' + "}"
            path.write_text(full, encoding="utf-8")
            with self.assertRaises(CoordinatorTrustBundleError):
                load_trusted_coordinator_keys(path)


class TrustedCoordinatorKeyBundleReloaderTests(unittest.TestCase):
    def test_initial_load_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=1)
            reloader = TrustedCoordinatorKeyBundleReloader(path)
            self.assertEqual(reloader.current_bundle().bundle_version, 1)

    def test_initial_load_missing_file_raises(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(CoordinatorTrustBundleError),
        ):
            TrustedCoordinatorKeyBundleReloader(Path(tmp) / "does-not-exist.json")

    def test_reload_with_higher_version_is_accepted_and_changed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=1)
            reloader = TrustedCoordinatorKeyBundleReloader(path)
            _write_valid_bundle(path, bundle_version=2)
            result = reloader.reload()
            self.assertTrue(result.accepted)
            self.assertTrue(result.changed)
            self.assertEqual(reloader.current_bundle().bundle_version, 2)

    def test_reload_with_same_version_is_accepted_but_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=1)
            reloader = TrustedCoordinatorKeyBundleReloader(path)
            result = reloader.reload()
            self.assertTrue(result.accepted)
            self.assertFalse(result.changed)

    def test_reload_rollback_is_rejected_and_keeps_previous_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=5)
            reloader = TrustedCoordinatorKeyBundleReloader(path)
            _write_valid_bundle(path, bundle_version=2)
            result = reloader.reload()
            self.assertFalse(result.accepted)
            self.assertFalse(result.changed)
            self.assertIn("rollback", result.reason)
            # The previous (higher-version) bundle must still be the
            # one in effect.
            self.assertEqual(reloader.current_bundle().bundle_version, 5)

    def test_reload_corrupted_file_is_rejected_and_keeps_previous_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=1)
            reloader = TrustedCoordinatorKeyBundleReloader(path)
            path.write_text("not json at all", encoding="utf-8")
            result = reloader.reload()
            self.assertFalse(result.accepted)
            self.assertEqual(reloader.current_bundle().bundle_version, 1)

    def test_reload_duplicate_active_keys_rejected_keeps_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundle.json"
            _write_valid_bundle(path, bundle_version=1)
            reloader = TrustedCoordinatorKeyBundleReloader(path)
            duplicate_active_keys = [
                {
                    "signing_key_id": "coord-key-1",
                    "public_key_hex": "a" * 64,
                    "status": "active",
                },
                {
                    "signing_key_id": "coord-key-2",
                    "public_key_hex": "b" * 64,
                    "status": "active",
                },
            ]
            _write_valid_bundle(path, bundle_version=2, keys=duplicate_active_keys)
            result = reloader.reload()
            self.assertFalse(result.accepted)
            self.assertEqual(reloader.current_bundle().bundle_version, 1)


if __name__ == "__main__":
    unittest.main()
