"""Tests for fl_platform.secure_aggregation.key_advertisement -- Secure
Cohort Handshake and Signed Roster Runtime slice
(docs/secure-cohort-handshake-foundation.md).

Uses a plain SimpleNamespace stand-in for the real generated
fl.coordinator.v1.FrozenCohortRoster message (frozen_cohort_roster_signing_bytes/
verify_frozen_cohort_roster are deliberately duck-typed, `roster: object`,
so this module has no import-time dependency on grpc/protobuf, which
are Docker/CI-only dependencies in this project -- see requirements.txt's
own documented policy). Real cross-language byte-for-byte parity
against the C++ side is validated by the live 3-worker Docker handshake,
not here.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import nacl.signing

from fl_platform.secure_aggregation.key_advertisement import (
    SecureCohortHandshakeError,
    build_signed_key_advertisement,
    frozen_cohort_roster_signing_bytes,
    generate_ephemeral_keypair,
    public_key_fingerprint,
    verify_frozen_cohort_roster,
)
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_SECURE_AGGREGATION,
    MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT,
    secure_aggregation_key_advertisement_payload_hash_input,
    sha256_hex,
)
from fl_platform.security.signing_identity import generate_signing_identity


def _make_participant(
    index: int, worker_id: str, client_id: str, public_key_hex: str
) -> SimpleNamespace:
    return SimpleNamespace(
        participant_index=index,
        worker_id=worker_id,
        client_id=client_id,
        ephemeral_public_key_x25519=public_key_hex,
        public_key_fingerprint=public_key_hex[:16],
    )


def _make_roster(**overrides: object) -> SimpleNamespace:
    defaults: dict[str, object] = {
        "schema_version": 1,
        "protocol_version": 1,
        "provider": 2,
        "session_id": "session-1",
        "run_id": "run-1",
        "round_id": 7,
        "model_version": "v1",
        "participants": [],
        "tensor_manifest_hash": "manifest-hash",
        "fixed_point_profile_hash": "fp-hash",
        "cryptographic_profile_hash": "crypto-hash",
        "cohort_commitment": "commitment-hash",
        "freeze_timestamp": 1000.0,
        "expiry": 1300.0,
        "coordinator_signing_key_id": "",
        "signature": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class EphemeralKeyGenerationTests(unittest.TestCase):
    def test_generates_distinct_32_byte_keypairs(self) -> None:
        first = generate_ephemeral_keypair()
        second = generate_ephemeral_keypair()
        self.assertEqual(len(first.private_key_raw), 32)
        self.assertEqual(len(first.public_key_raw), 32)
        self.assertNotEqual(first.private_key_raw, second.private_key_raw)
        self.assertNotEqual(first.public_key_raw, second.public_key_raw)

    def test_public_key_fingerprint_is_first_8_bytes_hex(self) -> None:
        keypair = generate_ephemeral_keypair()
        expected = keypair.public_key_raw[:8].hex()
        self.assertEqual(public_key_fingerprint(keypair.public_key_raw), expected)
        self.assertEqual(len(public_key_fingerprint(keypair.public_key_raw)), 16)


class BuildSignedKeyAdvertisementTests(unittest.TestCase):
    def test_builds_a_real_verifiable_signed_envelope(self) -> None:
        identity = generate_signing_identity("worker-1")
        keypair = generate_ephemeral_keypair()

        fields, envelope = build_signed_key_advertisement(
            session_id="session-1",
            run_id="run-1",
            round_id=7,
            model_version="v1",
            worker_id="worker-1",
            client_id="client-1",
            ephemeral_public_key=keypair.public_key_raw,
            signing_identity=identity,
            sequence_number=1,
            nonce="nonce-1",
            issued_at=1000.0,
        )

        self.assertEqual(
            fields.ephemeral_public_key_x25519, keypair.public_key_raw.hex()
        )
        self.assertEqual(
            envelope.fields.message_type,
            MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT,
        )
        self.assertEqual(
            envelope.fields.message_stream, MESSAGE_STREAM_SECURE_AGGREGATION
        )
        self.assertEqual(envelope.fields.signing_key_id, identity.key_id)

        expected_payload_hash = sha256_hex(
            secure_aggregation_key_advertisement_payload_hash_input(fields)
        )
        self.assertEqual(envelope.fields.payload_hash, expected_payload_hash)

        # Real, independent Ed25519 verification -- proves the signature
        # is genuinely valid, not merely well-formed.
        from fl_platform.security.signed_envelope import envelope_signing_bytes

        identity.verify_key.verify(
            envelope_signing_bytes(envelope.fields),
            bytes.fromhex(envelope.signature_hex),
        )

    def test_deterministic_hash_changes_with_any_field(self) -> None:
        identity = generate_signing_identity("worker-1")
        keypair = generate_ephemeral_keypair()
        _fields1, envelope1 = build_signed_key_advertisement(
            session_id="session-1",
            run_id="run-1",
            round_id=7,
            model_version="v1",
            worker_id="worker-1",
            client_id="client-1",
            ephemeral_public_key=keypair.public_key_raw,
            signing_identity=identity,
            sequence_number=1,
            nonce="nonce-1",
            issued_at=1000.0,
        )
        _fields2, envelope2 = build_signed_key_advertisement(
            session_id="session-1",
            run_id="run-1",
            round_id=8,  # different round
            model_version="v1",
            worker_id="worker-1",
            client_id="client-1",
            ephemeral_public_key=keypair.public_key_raw,
            signing_identity=identity,
            sequence_number=2,
            nonce="nonce-2",
            issued_at=1000.0,
        )
        self.assertNotEqual(
            envelope1.fields.payload_hash, envelope2.fields.payload_hash
        )
        self.assertNotEqual(envelope1.signature_hex, envelope2.signature_hex)


class FrozenRosterVerificationTests(unittest.TestCase):
    def test_accepts_a_real_signed_roster(self) -> None:
        coordinator_signing_key = nacl.signing.SigningKey.generate()
        own_keypair = generate_ephemeral_keypair()
        roster = _make_roster(
            participants=[
                _make_participant(
                    0, "worker-1", "client-1", own_keypair.public_key_raw.hex()
                )
            ],
        )
        signing_bytes = frozen_cohort_roster_signing_bytes(roster)
        roster.coordinator_signing_key_id = "coordinator-key-1"
        roster.signature = coordinator_signing_key.sign(signing_bytes).signature.hex()

        # Re-derive the signing bytes AFTER setting coordinator_signing_key_id
        # -- that field is deliberately excluded from the canonical bytes
        # (see frozen_cohort_roster_signing_bytes's own doc comment), so
        # this must still verify.
        verify_frozen_cohort_roster(
            roster,
            own_worker_id="worker-1",
            own_client_id="client-1",
            own_public_key_raw=own_keypair.public_key_raw,
            expected_session_id="session-1",
            expected_run_id="run-1",
            expected_round_id=7,
            expected_model_version="v1",
            trusted_coordinator_public_key_hex=bytes(
                coordinator_signing_key.verify_key
            ).hex(),
        )

    def test_rejects_an_unsigned_roster(self) -> None:
        roster = _make_roster(
            participants=[_make_participant(0, "worker-1", "client-1", "aa" * 32)]
        )
        with self.assertRaises(SecureCohortHandshakeError):
            verify_frozen_cohort_roster(
                roster,
                own_worker_id="worker-1",
                own_client_id="client-1",
                own_public_key_raw=bytes.fromhex("aa" * 32),
                expected_session_id="session-1",
                expected_run_id="run-1",
                expected_round_id=7,
                expected_model_version="v1",
                trusted_coordinator_public_key_hex="bb" * 32,
            )

    def test_rejects_a_tampered_roster(self) -> None:
        coordinator_signing_key = nacl.signing.SigningKey.generate()
        own_keypair = generate_ephemeral_keypair()
        roster = _make_roster(
            participants=[
                _make_participant(
                    0, "worker-1", "client-1", own_keypair.public_key_raw.hex()
                )
            ],
        )
        signing_bytes = frozen_cohort_roster_signing_bytes(roster)
        roster.coordinator_signing_key_id = "coordinator-key-1"
        roster.signature = coordinator_signing_key.sign(signing_bytes).signature.hex()

        # Tamper with the cohort commitment after signing -- the
        # signature was computed over the original bytes, so this must
        # now fail verification.
        roster.cohort_commitment = "tampered-commitment"
        with self.assertRaises(SecureCohortHandshakeError):
            verify_frozen_cohort_roster(
                roster,
                own_worker_id="worker-1",
                own_client_id="client-1",
                own_public_key_raw=own_keypair.public_key_raw,
                expected_session_id="session-1",
                expected_run_id="run-1",
                expected_round_id=7,
                expected_model_version="v1",
                trusted_coordinator_public_key_hex=bytes(
                    coordinator_signing_key.verify_key
                ).hex(),
            )

    def test_rejects_when_own_worker_is_not_a_participant(self) -> None:
        coordinator_signing_key = nacl.signing.SigningKey.generate()
        roster = _make_roster(
            participants=[_make_participant(0, "worker-2", "client-2", "bb" * 32)]
        )
        signing_bytes = frozen_cohort_roster_signing_bytes(roster)
        roster.coordinator_signing_key_id = "coordinator-key-1"
        roster.signature = coordinator_signing_key.sign(signing_bytes).signature.hex()

        with self.assertRaises(SecureCohortHandshakeError):
            verify_frozen_cohort_roster(
                roster,
                own_worker_id="worker-1",
                own_client_id="client-1",
                own_public_key_raw=bytes.fromhex("aa" * 32),
                expected_session_id="session-1",
                expected_run_id="run-1",
                expected_round_id=7,
                expected_model_version="v1",
                trusted_coordinator_public_key_hex=bytes(
                    coordinator_signing_key.verify_key
                ).hex(),
            )

    def test_rejects_an_all_zero_participant_public_key(self) -> None:
        coordinator_signing_key = nacl.signing.SigningKey.generate()
        own_keypair = generate_ephemeral_keypair()
        roster = _make_roster(
            participants=[
                _make_participant(
                    0, "worker-1", "client-1", own_keypair.public_key_raw.hex()
                ),
                _make_participant(1, "worker-2", "client-2", "00" * 32),
            ],
        )
        signing_bytes = frozen_cohort_roster_signing_bytes(roster)
        roster.coordinator_signing_key_id = "coordinator-key-1"
        roster.signature = coordinator_signing_key.sign(signing_bytes).signature.hex()

        with self.assertRaises(SecureCohortHandshakeError):
            verify_frozen_cohort_roster(
                roster,
                own_worker_id="worker-1",
                own_client_id="client-1",
                own_public_key_raw=own_keypair.public_key_raw,
                expected_session_id="session-1",
                expected_run_id="run-1",
                expected_round_id=7,
                expected_model_version="v1",
                trusted_coordinator_public_key_hex=bytes(
                    coordinator_signing_key.verify_key
                ).hex(),
            )


if __name__ == "__main__":
    unittest.main()
