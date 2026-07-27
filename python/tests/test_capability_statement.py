"""Tests for fl_platform.security.capability_statement -- signed worker
capability statements (Secure Transport and Worker Identity Hardening
slice, Work Package I). See docs/signed-capabilities.md.
"""

from __future__ import annotations

import copy
import time
import unittest

from fl_platform.security.capability_statement import (
    SCHEMA_VERSION,
    CapabilityStatementError,
    CapabilityStatementPayload,
    sign_capability_statement,
    verify_capability_statement,
)
from fl_platform.security.signing_identity import (
    generate_signing_identity,
    verify_key_from_hex,
)


def _payload(identity, **overrides) -> CapabilityStatementPayload:
    now = time.time()
    defaults = {
        "worker_id": identity.worker_id,
        "software_version": "0.1.0",
        "build_id": "test-build",
        "supported_algorithms": ("fedavg", "fedprox"),
        "supported_privacy_modes": ("sample_level_dp",),
        "opacus_version": "1.5.0",
        "secure_random_available": False,
        "supported_accountants": ("rdp",),
        "supported_clipping_modes": ("adaptive",),
        "supported_models": ("bridge_compatible",),
        "supported_model_schema_hashes": ("abc123",),
        "maximum_task_bytes": 1_000_000,
        "maximum_private_batch_size": 64,
        "cpu_count": 8,
        "gpu_available": False,
        "gpu_count": 0,
        "signing_key_id": identity.key_id,
        "issued_at": now,
        "expires_at": now + 3600,
    }
    defaults.update(overrides)
    return CapabilityStatementPayload(**defaults)


class SigningTests(unittest.TestCase):
    def test_signed_statement_includes_schema_version(self) -> None:
        identity = generate_signing_identity("worker-1")
        statement = sign_capability_statement(_payload(identity), identity)
        self.assertEqual(statement.payload["schema_version"], SCHEMA_VERSION)

    def test_rejects_unset_expiry(self) -> None:
        identity = generate_signing_identity("worker-1")
        payload = _payload(identity, expires_at=0.0)
        with self.assertRaises(CapabilityStatementError):
            sign_capability_statement(payload, identity)

    def test_rejects_expiry_not_after_issued_at(self) -> None:
        identity = generate_signing_identity("worker-1")
        now = time.time()
        payload = _payload(identity, issued_at=now, expires_at=now)
        with self.assertRaises(CapabilityStatementError):
            sign_capability_statement(payload, identity)

    def test_rejects_signing_key_id_mismatch(self) -> None:
        identity = generate_signing_identity("worker-1")
        payload = _payload(identity, signing_key_id="wrong-key-id")
        with self.assertRaises(CapabilityStatementError):
            sign_capability_statement(payload, identity)


class VerificationTests(unittest.TestCase):
    def test_a_freshly_signed_statement_verifies(self) -> None:
        identity = generate_signing_identity("worker-1")
        statement = sign_capability_statement(_payload(identity), identity)
        verify_key = verify_key_from_hex(identity.public_key_hex())
        result = verify_capability_statement(statement, verify_key)
        self.assertTrue(result.valid, result.reason)

    def test_tampered_field_fails_verification(self) -> None:
        identity = generate_signing_identity("worker-1")
        statement = sign_capability_statement(_payload(identity), identity)
        tampered_payload = copy.deepcopy(statement.payload)
        tampered_payload["maximum_task_bytes"] = 999_999_999
        tampered = type(statement)(
            payload=tampered_payload,
            payload_hash=statement.payload_hash,
            signature=statement.signature,
        )
        verify_key = verify_key_from_hex(identity.public_key_hex())
        result = verify_capability_statement(tampered, verify_key)
        self.assertFalse(result.valid)

    def test_wrong_verify_key_fails_verification(self) -> None:
        identity_a = generate_signing_identity("worker-1")
        identity_b = generate_signing_identity("worker-2")
        statement = sign_capability_statement(_payload(identity_a), identity_a)
        wrong_verify_key = verify_key_from_hex(identity_b.public_key_hex())
        result = verify_capability_statement(statement, wrong_verify_key)
        self.assertFalse(result.valid)
        self.assertIn("signature", result.reason)

    def test_expired_statement_fails_verification(self) -> None:
        identity = generate_signing_identity("worker-1")
        now = time.time()
        payload = _payload(identity, issued_at=now - 7200, expires_at=now - 3600)
        statement = sign_capability_statement(payload, identity)
        verify_key = verify_key_from_hex(identity.public_key_hex())
        result = verify_capability_statement(statement, verify_key, now=now)
        self.assertFalse(result.valid)
        self.assertIn("expired", result.reason)

    def test_not_yet_expired_statement_verifies_right_before_expiry(self) -> None:
        identity = generate_signing_identity("worker-1")
        now = time.time()
        payload = _payload(identity, issued_at=now, expires_at=now + 3600)
        statement = sign_capability_statement(payload, identity)
        verify_key = verify_key_from_hex(identity.public_key_hex())
        result = verify_capability_statement(statement, verify_key, now=now + 1000)
        self.assertTrue(result.valid, result.reason)

    def test_payload_hash_mismatch_is_detected_independent_of_signature(self) -> None:
        identity = generate_signing_identity("worker-1")
        statement = sign_capability_statement(_payload(identity), identity)
        corrupted = type(statement)(
            payload=statement.payload,
            payload_hash="0" * 64,
            signature=statement.signature,
        )
        verify_key = verify_key_from_hex(identity.public_key_hex())
        result = verify_capability_statement(corrupted, verify_key)
        self.assertFalse(result.valid)
        self.assertIn("payload_hash", result.reason)


class CanonicalSerializationTests(unittest.TestCase):
    """The one canonicalization rule this pass implements: explicit
    sorted-key, compact-separator JSON -- see the module's docstring for
    why this matters and what's still deferred (cross-language parity
    tests)."""

    def test_signing_is_independent_of_python_dict_construction_order(self) -> None:
        identity = generate_signing_identity("worker-1")
        payload = _payload(identity)
        statement_a = sign_capability_statement(payload, identity)

        # Same logical payload, but the underlying dict built in a
        # different key order (by round-tripping through a
        # reconstructed dataclass with kwargs supplied in reverse
        # field order) must still produce byte-identical canonical
        # encoding and therefore the identical hash.
        from dataclasses import fields

        reversed_kwargs = {
            f.name: getattr(payload, f.name) for f in reversed(fields(payload))
        }
        payload_b = CapabilityStatementPayload(**reversed_kwargs)
        statement_b = sign_capability_statement(payload_b, identity)

        self.assertEqual(statement_a.payload_hash, statement_b.payload_hash)

    def test_two_statements_with_different_nonces_have_different_hashes(self) -> None:
        identity = generate_signing_identity("worker-1")
        payload_a = _payload(identity, nonce="nonce-a")
        payload_b = _payload(identity, nonce="nonce-b")
        statement_a = sign_capability_statement(payload_a, identity)
        statement_b = sign_capability_statement(payload_b, identity)
        self.assertNotEqual(statement_a.payload_hash, statement_b.payload_hash)


if __name__ == "__main__":
    unittest.main()
