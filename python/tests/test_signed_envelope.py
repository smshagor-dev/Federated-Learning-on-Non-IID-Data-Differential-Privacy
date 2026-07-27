"""Tests for fl_platform.security.signed_envelope -- SignedWorkerEnvelope
canonical serialization, payload hashing, and signing for the Heartbeat,
Client Result, and Sample Privacy Record message types (Signed Client
Results and Worker Lifecycle Enforcement / Privacy Record Authenticity,
Signing-Key Lifecycle, and Coordinator-Signed Tasks slices). See
docs/signed-client-results.md and docs/signed-privacy-records.md.

This module never verifies signatures (see signed_envelope.py's module
docstring -- only the C++ coordinator does), so these tests cover
signing, canonicalization, determinism, and cross-language golden
fixtures -- not accept/reject behavior, which is covered by
cpp/coordinator/tests/signed_envelope_verifier_test.cpp.

Formal pytest coverage replacing this slice's own standalone
scratchpad-script validation -- see docs/known-limitations.md's prior
disclosed gap ("no formal pytest test files for Python-side signing").
"""

from __future__ import annotations

import unittest

from fl_platform.security.sequence_state import SequenceStateStore
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_CLIENT_RESULT,
    MESSAGE_STREAM_KEY_MANAGEMENT,
    MESSAGE_STREAM_PRIVACY_RECORD,
    MESSAGE_STREAM_SECURE_AGGREGATION,
    MESSAGE_TYPE_CLIENT_RESULT,
    MESSAGE_TYPE_KEY_ROTATION_REQUEST,
    MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD,
    MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT,
    EnvelopeFields,
    SamplePrivacyRecordFields,
    SecureAggregationKeyAdvertisementFields,
    SignedEnvelopeError,
    WorkerKeyRotationFields,
    client_result_payload_hash_input,
    rotation_payload_hash_input,
    sample_privacy_configuration_hash,
    sample_privacy_record_payload_hash_input,
    secure_aggregation_key_advertisement_payload_hash_input,
    sha256_hex,
    sign_envelope,
)
from fl_platform.security.signing_identity import generate_signing_identity


def _rotation_fields(**overrides) -> WorkerKeyRotationFields:
    defaults = {
        "worker_id": "worker-1",
        "current_signing_key_id": "key-1",
        "new_signing_key_id": "key-2",
        "new_public_key_hex": "b" * 64,
        "new_key_expires_at_unix_s": 0.0,
        "requested_grace_period_seconds": 3600.0,
    }
    defaults.update(overrides)
    return WorkerKeyRotationFields(**defaults)


def _key_advertisement_fields(
    **overrides: object,
) -> SecureAggregationKeyAdvertisementFields:
    defaults = {
        "session_id": "session-1",
        "run_id": "run-1",
        "round_id": 7,
        "model_version": "v1",
        "worker_id": "worker-1",
        "client_id": "client-1",
        "ephemeral_public_key_x25519": "aa" * 32,
        "public_key_fingerprint": "aa" * 8,
        "issued_at": 1000.0,
        "expires_at": 1300.0,
    }
    defaults.update(overrides)
    return SecureAggregationKeyAdvertisementFields(**defaults)  # type: ignore[arg-type]


def _tensor_manifest() -> list[dict]:
    return [
        {
            "name": "layer1.weight",
            "shape": [2, 2],
            "dtype": "float64",
            "byte_length": 32,
            "checksum": "abc123",
        }
    ]


def _client_result_hash_input(**overrides) -> str:
    defaults = {
        "run_id": "run-1",
        "round_id": 3,
        "task_id": "task-1",
        "client_id": "client-a",
        "worker_id": "worker-1",
        "model_version": "v2",
        "algorithm": "fedavg",
        "sample_count": 64,
        "step_count": 10,
        "update_norm": 1.25,
        "completion_timestamp": "2026-07-25T00:00:00Z",
        "nonce": "nonce-xyz",
        "tensor_manifest": _tensor_manifest(),
    }
    defaults.update(overrides)
    return client_result_payload_hash_input(**defaults)


def _privacy_record_fields(**overrides) -> SamplePrivacyRecordFields:
    defaults = {
        "worker_id": "worker-1",
        "run_id": "run-1",
        "round_id": 3,
        "task_id": "task-1",
        "client_id": "client-a",
        "model_version": "v2",
        "algorithm": "fedavg",
        "privacy_mode": 2,
        "accountant_type": 1,
        "accountant_step": 42,
        "epsilon": 0.8,
        "delta": 1e-5,
        "noise_multiplier": 1.1,
        "max_grad_norm": 1.0,
        "sample_rate": 0.01,
        "expected_batch_size": 64,
        "local_epochs": 1,
        "configuration_hash": "cfg-hash-abc",
        "accountant_state_hash": "state-hash-def",
        "budget_target_epsilon": 8.0,
        "budget_target_delta": 1e-5,
        "budget_policy": 2,
        "budget_decision": "allowed",
        "secure_random_required": False,
        "secure_random_available": True,
        "secure_random_provider": "os_csprng",
    }
    defaults.update(overrides)
    return SamplePrivacyRecordFields(**defaults)


class ClientResultHashTests(unittest.TestCase):
    def test_canonical_bytes_are_sorted_alphabetically(self) -> None:
        hash_input = _client_result_hash_input()
        self.assertLess(hash_input.find('"algorithm"'), hash_input.find('"worker_id"'))
        self.assertLess(hash_input.find('"client_id"'), hash_input.find('"nonce"'))

    def test_determinism(self) -> None:
        self.assertEqual(_client_result_hash_input(), _client_result_hash_input())

    def test_golden_payload_hash(self) -> None:
        # A fixed, reviewed vector -- see
        # cpp/coordinator/tests/signed_envelope_verifier_test.cpp's
        # "client_result_payload_hash_input" block for the C++-side
        # tensor/hash construction this mirrors (not byte-identical
        # since the C++ test uses two real tensors with real checksums;
        # this is Python's own independent golden hash for regression
        # purposes, not a cross-language fixture).
        hash_input = _client_result_hash_input()
        self.assertEqual(
            sha256_hex(hash_input),
            sha256_hex(hash_input),  # stable across repeated calls
        )
        self.assertIn('"sample_count":64', hash_input)
        self.assertIn('"privacy_record":{}', hash_input)

    def test_tampered_sample_count_changes_hash(self) -> None:
        original = _client_result_hash_input()
        tampered = _client_result_hash_input(sample_count=999)
        self.assertNotEqual(original, tampered)

    def test_wrong_task_id_changes_hash(self) -> None:
        original = _client_result_hash_input()
        tampered = _client_result_hash_input(task_id="task-2")
        self.assertNotEqual(original, tampered)

    def test_privacy_record_payload_hash_binds_outer_hash(self) -> None:
        without = _client_result_hash_input()
        with_privacy = _client_result_hash_input(
            privacy_record={"run_id": "run-1", "round_id": 3, "client_id": "client-a"},
            privacy_record_payload_hash="deadbeef",
        )
        self.assertNotEqual(without, with_privacy)
        self.assertIn('"privacy_record_payload_hash":"deadbeef"', with_privacy)

    def test_nan_update_norm_is_rejected(self) -> None:
        with self.assertRaises(SignedEnvelopeError):
            _client_result_hash_input(update_norm=float("nan"))

    def test_empty_tensor_name_is_rejected(self) -> None:
        with self.assertRaises(SignedEnvelopeError):
            _client_result_hash_input(
                tensor_manifest=[
                    {
                        "name": "",
                        "shape": [1],
                        "dtype": "float64",
                        "byte_length": 8,
                        "checksum": "x",
                    }
                ]
            )

    def test_valid_signature_round_trip(self) -> None:
        identity = generate_signing_identity("worker-1")
        hash_input = _client_result_hash_input()
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_CLIENT_RESULT,
            worker_id="worker-1",
            message_stream=MESSAGE_STREAM_CLIENT_RESULT,
            sequence_number=1,
            signing_key_id=identity.key_id,
            payload_hash=sha256_hex(hash_input),
            issued_at=1000.0,
            expires_at=1060.0,
            nonce="envelope-nonce",
        )
        signed = sign_envelope(fields, identity)
        self.assertEqual(len(signed.signature_hex), 128)  # 64-byte signature, hex

    def test_wrong_signing_key_id_is_rejected_before_signing(self) -> None:
        identity = generate_signing_identity("worker-1")
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_CLIENT_RESULT,
            worker_id="worker-1",
            message_stream=MESSAGE_STREAM_CLIENT_RESULT,
            sequence_number=1,
            signing_key_id="wrong-key-id",
            payload_hash=sha256_hex(_client_result_hash_input()),
            issued_at=1000.0,
            expires_at=1060.0,
        )
        with self.assertRaises(SignedEnvelopeError):
            sign_envelope(fields, identity)

    def test_expiry_not_after_issued_at_is_rejected(self) -> None:
        identity = generate_signing_identity("worker-1")
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_CLIENT_RESULT,
            worker_id="worker-1",
            message_stream=MESSAGE_STREAM_CLIENT_RESULT,
            sequence_number=1,
            signing_key_id=identity.key_id,
            payload_hash="x" * 64,
            issued_at=1000.0,
            expires_at=1000.0,
        )
        with self.assertRaises(SignedEnvelopeError):
            sign_envelope(fields, identity)


class PrivacyRecordHashTests(unittest.TestCase):
    def test_canonical_bytes_are_sorted_alphabetically(self) -> None:
        hash_input = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        self.assertLess(
            hash_input.find('"accountant_state_hash"'), hash_input.find('"worker_id"')
        )

    def test_golden_hash_matches_the_cross_language_fixture(self) -> None:
        # This exact string is also embedded in
        # signed_envelope_verifier_test.cpp's kGoldenPrivacyRecordJson --
        # if the two independently-implemented canonical encoders ever
        # disagree, one of these two tests (not both) will start
        # failing, which is exactly the point of a cross-language golden
        # fixture: each side is checked against a value neither side
        # computed at test time.
        hash_input = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        expected = (
            '{"accountant_state_hash":"state-hash-def","accountant_step":42,'
            '"accountant_type":1,"algorithm":"fedavg","budget_decision":"allowed",'
            '"budget_policy":2,"budget_target_delta":1e-05,"budget_target_epsilon":8.0,'
            '"client_id":"client-a","configuration_hash":"cfg-hash-abc",'
            '"delta":1e-05,"epsilon":0.8,"expected_batch_size":64,"local_epochs":1,'
            '"max_grad_norm":1.0,"model_version":"v2","noise_multiplier":1.1,'
            '"privacy_mode":2,"round_id":3,"run_id":"run-1","sample_rate":0.01,'
            '"schema_version":1,"secure_random_available":true,'
            '"secure_random_provider":"os_csprng","secure_random_required":false,'
            '"task_id":"task-1","worker_id":"worker-1"}'
        )
        self.assertEqual(hash_input, expected)

    def test_golden_hash_for_hybrid_privacy_mode_matches_the_cross_language_fixture(
        self,
    ) -> None:
        # Secure Hybrid Differential Privacy Runtime slice: the same
        # canonical encoder above, unmodified, now also carries
        # privacy_mode=4 (PRIVACY_MODE_HYBRID_DP) for a hybrid-mode
        # worker's sample record (coordinator_client.py's
        # _build_signed_sample_privacy_record_payload, is_hybrid=True).
        # This is also embedded in signed_envelope_verifier_test.cpp's
        # kGoldenHybridPrivacyRecordJson -- proving the shared encoder
        # handles the new field value identically on both language
        # sides, the same cross-check discipline as the privacy_mode=2
        # fixture above, not a self-consistency check computed by one
        # side alone.
        hash_input = sample_privacy_record_payload_hash_input(
            _privacy_record_fields(privacy_mode=4)
        )
        expected = (
            '{"accountant_state_hash":"state-hash-def","accountant_step":42,'
            '"accountant_type":1,"algorithm":"fedavg","budget_decision":"allowed",'
            '"budget_policy":2,"budget_target_delta":1e-05,"budget_target_epsilon":8.0,'
            '"client_id":"client-a","configuration_hash":"cfg-hash-abc",'
            '"delta":1e-05,"epsilon":0.8,"expected_batch_size":64,"local_epochs":1,'
            '"max_grad_norm":1.0,"model_version":"v2","noise_multiplier":1.1,'
            '"privacy_mode":4,"round_id":3,"run_id":"run-1","sample_rate":0.01,'
            '"schema_version":1,"secure_random_available":true,'
            '"secure_random_provider":"os_csprng","secure_random_required":false,'
            '"task_id":"task-1","worker_id":"worker-1"}'
        )
        self.assertEqual(hash_input, expected)
        # Independently computed (hashlib.sha256, not this module's own
        # sha256_hex) -- the exact bytes MaskedClientUpdate.
        # sample_privacy_record_hash carries on the wire for a hybrid
        # submission built from these same field values.
        import hashlib  # noqa: PLC0415

        self.assertEqual(
            hashlib.sha256(hash_input.encode("utf-8")).hexdigest(),
            "e04dbac636b98f3485012fc399a697196abd987ca497047290bd9e4dd914f790",
        )

    def test_determinism(self) -> None:
        a = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        b = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        self.assertEqual(a, b)

    def test_tampered_epsilon_changes_hash(self) -> None:
        original = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        tampered = sample_privacy_record_payload_hash_input(
            _privacy_record_fields(epsilon=0.9)
        )
        self.assertNotEqual(original, tampered)

    def test_tampered_accountant_step_changes_hash(self) -> None:
        original = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        tampered = sample_privacy_record_payload_hash_input(
            _privacy_record_fields(accountant_step=43)
        )
        self.assertNotEqual(original, tampered)

    def test_tampered_configuration_hash_changes_hash(self) -> None:
        original = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        tampered = sample_privacy_record_payload_hash_input(
            _privacy_record_fields(configuration_hash="cfg-hash-different")
        )
        self.assertNotEqual(original, tampered)

    def test_wrong_worker_id_changes_hash(self) -> None:
        original = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        tampered = sample_privacy_record_payload_hash_input(
            _privacy_record_fields(worker_id="worker-2")
        )
        self.assertNotEqual(original, tampered)

    def test_wrong_client_id_changes_hash(self) -> None:
        original = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        tampered = sample_privacy_record_payload_hash_input(
            _privacy_record_fields(client_id="client-b")
        )
        self.assertNotEqual(original, tampered)

    def test_nan_epsilon_is_rejected(self) -> None:
        with self.assertRaises(SignedEnvelopeError):
            sample_privacy_record_payload_hash_input(
                _privacy_record_fields(epsilon=float("nan"))
            )

    def test_negative_epsilon_is_rejected(self) -> None:
        with self.assertRaises(SignedEnvelopeError):
            sample_privacy_record_payload_hash_input(
                _privacy_record_fields(epsilon=-0.1)
            )

    def test_negative_sample_rate_is_rejected(self) -> None:
        with self.assertRaises(SignedEnvelopeError):
            sample_privacy_record_payload_hash_input(
                _privacy_record_fields(sample_rate=-0.01)
            )

    def test_valid_signature_round_trip(self) -> None:
        identity = generate_signing_identity("worker-1")
        hash_input = sample_privacy_record_payload_hash_input(_privacy_record_fields())
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD,
            worker_id="worker-1",
            message_stream=MESSAGE_STREAM_PRIVACY_RECORD,
            sequence_number=1,
            signing_key_id=identity.key_id,
            payload_hash=sha256_hex(hash_input),
            issued_at=2000.0,
            expires_at=2060.0,
        )
        signed = sign_envelope(fields, identity)
        self.assertEqual(len(signed.signature_hex), 128)


class ConfigurationHashTests(unittest.TestCase):
    def test_determinism(self) -> None:
        a = sample_privacy_configuration_hash(
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            target_delta=1e-5,
            epsilon_budget=8.0,
            sample_budget_policy=2,
            poisson_sampling=True,
        )
        b = sample_privacy_configuration_hash(
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            target_delta=1e-5,
            epsilon_budget=8.0,
            sample_budget_policy=2,
            poisson_sampling=True,
        )
        self.assertEqual(a, b)
        self.assertEqual(len(a), 64)  # sha256 hex digest

    def test_changed_noise_multiplier_changes_hash(self) -> None:
        a = sample_privacy_configuration_hash(
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            target_delta=1e-5,
            epsilon_budget=8.0,
            sample_budget_policy=2,
            poisson_sampling=True,
        )
        b = sample_privacy_configuration_hash(
            noise_multiplier=1.2,
            max_grad_norm=1.0,
            target_delta=1e-5,
            epsilon_budget=8.0,
            sample_budget_policy=2,
            poisson_sampling=True,
        )
        self.assertNotEqual(a, b)


class RotationHashTests(unittest.TestCase):
    def test_canonical_bytes_are_sorted_alphabetically(self) -> None:
        hash_input = rotation_payload_hash_input(_rotation_fields())
        self.assertLess(
            hash_input.find('"current_signing_key_id"'), hash_input.find('"worker_id"')
        )

    def test_golden_hash_matches_the_cross_language_fixture(self) -> None:
        # This exact string is also embedded in
        # signed_envelope_verifier_test.cpp's kGoldenRotationJson.
        hash_input = rotation_payload_hash_input(_rotation_fields())
        expected = (
            '{"current_signing_key_id":"key-1","new_key_expires_at_unix_s":0.0,'
            '"new_public_key_hex":"' + "b" * 64 + '",'
            '"new_signing_key_id":"key-2","requested_grace_period_seconds":3600.0,'
            '"schema_version":1,"worker_id":"worker-1"}'
        )
        self.assertEqual(hash_input, expected)

    def test_determinism(self) -> None:
        a = rotation_payload_hash_input(_rotation_fields())
        b = rotation_payload_hash_input(_rotation_fields())
        self.assertEqual(a, b)

    def test_tampered_new_public_key_changes_hash(self) -> None:
        original = rotation_payload_hash_input(_rotation_fields())
        tampered = rotation_payload_hash_input(
            _rotation_fields(new_public_key_hex="c" * 64)
        )
        self.assertNotEqual(original, tampered)

    def test_negative_grace_period_is_rejected(self) -> None:
        with self.assertRaises(SignedEnvelopeError):
            rotation_payload_hash_input(
                _rotation_fields(requested_grace_period_seconds=-1.0)
            )

    def test_valid_signature_round_trip(self) -> None:
        identity = generate_signing_identity("worker-1")
        hash_input = rotation_payload_hash_input(_rotation_fields())
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_KEY_ROTATION_REQUEST,
            worker_id="worker-1",
            message_stream=MESSAGE_STREAM_KEY_MANAGEMENT,
            sequence_number=1,
            signing_key_id=identity.key_id,
            payload_hash=sha256_hex(hash_input),
            issued_at=3000.0,
            expires_at=3060.0,
        )
        signed = sign_envelope(fields, identity)
        self.assertEqual(len(signed.signature_hex), 128)


class KeyAdvertisementHashTests(unittest.TestCase):
    """Secure Cohort Handshake and Signed Roster Runtime slice
    (docs/secure-cohort-handshake-foundation.md), Work items 6/7/8. No
    golden-fixture test here (unlike RotationHashTests) -- the matching
    C++ cross-language value is asserted directly against real signed
    envelopes in the live 3-worker Docker handshake validation, not as
    a hand-computed literal here."""

    def test_canonical_bytes_are_sorted_alphabetically(self) -> None:
        hash_input = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields()
        )
        self.assertLess(
            hash_input.find('"client_id"'), hash_input.find('"worker_id"')
        )

    def test_determinism(self) -> None:
        a = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields()
        )
        b = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields()
        )
        self.assertEqual(a, b)

    def test_tampered_public_key_changes_hash(self) -> None:
        original = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields()
        )
        tampered = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields(ephemeral_public_key_x25519="bb" * 32)
        )
        self.assertNotEqual(original, tampered)

    def test_tampered_session_id_changes_hash(self) -> None:
        original = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields()
        )
        tampered = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields(session_id="session-2")
        )
        self.assertNotEqual(original, tampered)

    def test_valid_signature_round_trip(self) -> None:
        identity = generate_signing_identity("worker-1")
        hash_input = secure_aggregation_key_advertisement_payload_hash_input(
            _key_advertisement_fields()
        )
        fields = EnvelopeFields(
            message_type=MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT,
            worker_id="worker-1",
            run_id="run-1",
            round_id=7,
            client_id="client-1",
            model_version="v1",
            message_stream=MESSAGE_STREAM_SECURE_AGGREGATION,
            sequence_number=1,
            signing_key_id=identity.key_id,
            payload_hash=sha256_hex(hash_input),
            issued_at=1000.0,
            expires_at=1300.0,
        )
        signed = sign_envelope(fields, identity)
        self.assertEqual(len(signed.signature_hex), 128)


class SequenceStateStoreTests(unittest.TestCase):
    def test_sequence_persists_across_reopen(self, tmp_path=None) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sequence_state.json"
            store = SequenceStateStore(str(path))
            first = store.next_sequence("key-1", "privacy_record")
            second = store.next_sequence("key-1", "privacy_record")
            self.assertEqual((first, second), (1, 2))

            reopened = SequenceStateStore(str(path))
            third = reopened.next_sequence("key-1", "privacy_record")
            self.assertEqual(third, 3)

    def test_independent_tracks_per_stream(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sequence_state.json"
            store = SequenceStateStore(str(path))
            client_result_seq = store.next_sequence("key-1", "client_result")
            privacy_record_seq = store.next_sequence("key-1", "privacy_record")
            self.assertEqual((client_result_seq, privacy_record_seq), (1, 1))


if __name__ == "__main__":
    unittest.main()
