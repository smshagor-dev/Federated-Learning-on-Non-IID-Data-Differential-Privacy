"""Tests for fl_platform.security.signing_identity -- Ed25519 worker
signing identities (Secure Transport and Worker Identity Hardening
slice, Work Package H). See docs/worker-identity.md.
"""

from __future__ import annotations

import stat
import sys
import unittest

from fl_platform.security.signing_identity import (
    SigningIdentityError,
    generate_signing_identity,
    load_signing_identity,
    save_signing_identity,
    verify_key_from_hex,
)


class GenerateSigningIdentityTests(unittest.TestCase):
    def test_generates_a_real_ed25519_keypair(self) -> None:
        identity = generate_signing_identity("worker-1")
        self.assertEqual(identity.worker_id, "worker-1")
        self.assertEqual(len(identity.public_key_hex()), 64)  # 32 bytes hex-encoded
        int(identity.public_key_hex(), 16)  # raises if not valid hex

    def test_rejects_empty_worker_id(self) -> None:
        with self.assertRaises(SigningIdentityError):
            generate_signing_identity("")

    def test_two_identities_never_share_a_key(self) -> None:
        a = generate_signing_identity("worker-1")
        b = generate_signing_identity("worker-2")
        self.assertNotEqual(a.public_key_hex(), b.public_key_hex())
        self.assertNotEqual(a.key_id, b.key_id)

    def test_key_id_is_derived_from_the_public_key_deterministically(self) -> None:
        identity = generate_signing_identity("worker-1")
        # Reconstructing a VerifyKey from the same public key bytes and
        # recomputing must give the identical key_id -- key_id is a pure
        # function of the public key, not random per-call state.
        reconstructed = verify_key_from_hex(identity.public_key_hex())
        from fl_platform.security.signing_identity import _key_id_for  # noqa: PLC0415

        self.assertEqual(_key_id_for(reconstructed), identity.key_id)


class SignAndVerifyTests(unittest.TestCase):
    def test_a_signature_verifies_against_the_matching_public_key(self) -> None:
        identity = generate_signing_identity("worker-1")
        signature = identity.sign(b"hello world")
        verify_key = verify_key_from_hex(identity.public_key_hex())
        verify_key.verify(b"hello world", signature)  # must not raise

    def test_a_signature_does_not_verify_against_a_different_key(self) -> None:
        import nacl.exceptions

        identity_a = generate_signing_identity("worker-1")
        identity_b = generate_signing_identity("worker-2")
        signature = identity_a.sign(b"hello world")
        wrong_verify_key = verify_key_from_hex(identity_b.public_key_hex())
        with self.assertRaises(nacl.exceptions.BadSignatureError):
            wrong_verify_key.verify(b"hello world", signature)

    def test_tampered_payload_fails_verification(self) -> None:
        import nacl.exceptions

        identity = generate_signing_identity("worker-1")
        signature = identity.sign(b"original payload")
        verify_key = verify_key_from_hex(identity.public_key_hex())
        with self.assertRaises(nacl.exceptions.BadSignatureError):
            verify_key.verify(b"tampered payload", signature)

    def test_verify_key_from_hex_rejects_malformed_input(self) -> None:
        with self.assertRaises(SigningIdentityError):
            verify_key_from_hex("not-valid-hex-!!!")


class PersistenceTests(unittest.TestCase):
    def test_save_and_load_round_trips_to_an_identical_key(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            original = generate_signing_identity("worker-1")
            save_signing_identity(original, tmp)
            restored = load_signing_identity("worker-1", tmp)
            self.assertEqual(original.public_key_hex(), restored.public_key_hex())
            self.assertEqual(original.key_id, restored.key_id)
            # A signature made after restore must verify against the
            # original identity's public key -- proves it's genuinely
            # the same private key material, not merely equal metadata.
            signature = restored.sign(b"payload")
            verify_key = verify_key_from_hex(original.public_key_hex())
            verify_key.verify(b"payload", signature)

    def test_private_key_file_has_restrictive_permissions_on_posix(self) -> None:
        if sys.platform == "win32":
            self.skipTest("POSIX file-permission semantics do not apply on Windows")
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            identity = generate_signing_identity("worker-1")
            path = save_signing_identity(identity, tmp)
            mode = path.stat().st_mode
            self.assertEqual(mode & stat.S_IRWXG, 0, "group must have no permissions")
            self.assertEqual(mode & stat.S_IRWXO, 0, "other must have no permissions")

    def test_public_key_file_is_written_separately_and_matches(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            identity = generate_signing_identity("worker-1")
            save_signing_identity(identity, tmp)
            public_path = Path(tmp) / "worker-1.signing-key.pub"
            self.assertTrue(public_path.exists())
            self.assertEqual(public_path.read_text().strip(), identity.public_key_hex())

    def test_load_raises_for_a_missing_identity(self) -> None:
        import tempfile

        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(SigningIdentityError),
        ):
            load_signing_identity("no-such-worker", tmp)

    def test_load_raises_for_a_corrupted_key_file(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            corrupt_path = Path(tmp) / "worker-1.signing-key.pem"
            corrupt_path.write_bytes(b"not a real key")
            with self.assertRaises(SigningIdentityError):
                load_signing_identity("worker-1", tmp)

    def test_private_key_is_never_written_to_the_public_key_file(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            identity = generate_signing_identity("worker-1")
            save_signing_identity(identity, tmp)
            public_content = (Path(tmp) / "worker-1.signing-key.pub").read_text()
            private_bytes = bytes(identity.signing_key)
            self.assertNotIn(private_bytes.hex(), public_content)


if __name__ == "__main__":
    unittest.main()
