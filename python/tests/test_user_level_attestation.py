"""Tests for fl_platform.secure_aggregation.user_level_attestation --
Secure User-Level Differential Privacy Runtime slice, Work Areas I/J.
"""

from __future__ import annotations

import dataclasses
import unittest

import nacl.exceptions
import nacl.signing

from fl_platform.secure_aggregation.user_level_attestation import (
    UserLevelPrivacyAttestationFields,
    build_signed_user_level_privacy_attestation,
    sha256_hex,
    user_level_privacy_attestation_payload_hash_input,
    user_level_privacy_attestation_signing_bytes,
)
from fl_platform.security.signing_identity import (
    WorkerSigningIdentity,
    generate_signing_identity,
)


def _make_identity(worker_id: str = "worker-1") -> WorkerSigningIdentity:
    return generate_signing_identity(worker_id)


def _build(**overrides: object) -> object:
    identity = overrides.pop("signing_identity", None) or _make_identity()
    base = dict(  # noqa: C408 - kwarg style is clearer for this many fields
        worker_id="worker-1",
        client_id="client-1",
        run_id="run-1",
        round_id=7,
        task_id="task-1",
        session_id="session-1",
        model_version="v1",
        privacy_mode=3,
        privacy_configuration_hash="config-hash-abc",
        clip_norm=2.5,
        effective_sensitivity=2.500015,
        fixed_point_profile_hash="fp-profile-hash",
        tensor_manifest_hash="tensor-manifest-hash",
        provider=1,
        operation_completed=True,
        signing_identity=identity,
    )
    base.update(overrides)
    return build_signed_user_level_privacy_attestation(**base), identity


class AttestationFieldExclusionTests(unittest.TestCase):
    """Work Area I: the attestation must never carry an unclipped norm,
    clipped norm, clipping factor, or any clear tensor statistic --
    verified here by asserting those attribute names simply do not
    exist on the dataclass, not by trusting a docstring."""

    def test_excluded_fields_do_not_exist(self) -> None:
        fields, _identity = _build()
        field_names = {f.name for f in dataclasses.fields(fields)}
        for excluded in (
            "unclipped_norm",
            "clipped_norm",
            "clipping_factor",
            "clear_update_checksum",
            "dataset_sample_count",
            "noise",
        ):
            self.assertNotIn(excluded, field_names)


class AttestationCanonicalBytesTests(unittest.TestCase):
    def test_payload_hash_input_deterministic(self) -> None:
        fields, _identity = _build()
        first = user_level_privacy_attestation_payload_hash_input(fields)
        second = user_level_privacy_attestation_payload_hash_input(fields)
        self.assertEqual(first, second)

    def test_payload_hash_changes_when_clip_norm_changes(self) -> None:
        fields_a, identity = _build()
        fields_b, _ = _build(signing_identity=identity, clip_norm=999.0)
        self.assertNotEqual(
            user_level_privacy_attestation_payload_hash_input(fields_a),
            user_level_privacy_attestation_payload_hash_input(fields_b),
        )

    def test_signing_bytes_include_payload_hash_and_signing_key_id(self) -> None:
        fields, _identity = _build()
        signing_bytes = user_level_privacy_attestation_signing_bytes(fields)
        self.assertIn(fields.payload_hash.encode("ascii"), signing_bytes)
        self.assertIn(fields.signing_key_id.encode("ascii"), signing_bytes)
        self.assertNotIn(fields.signature.encode("ascii"), signing_bytes)


class AttestationSignatureTests(unittest.TestCase):
    def test_real_signature_verifies(self) -> None:
        fields, identity = _build()
        verify_key = identity.signing_key.verify_key
        signing_bytes = user_level_privacy_attestation_signing_bytes(fields)
        verify_key.verify(signing_bytes, bytes.fromhex(fields.signature))

    def test_tampered_field_breaks_verification(self) -> None:
        fields, identity = _build()
        tampered = dataclasses.replace(fields, clip_norm=999.0)
        verify_key = identity.signing_key.verify_key
        with self.assertRaises(nacl.exceptions.BadSignatureError):
            verify_key.verify(
                user_level_privacy_attestation_signing_bytes(tampered),
                bytes.fromhex(tampered.signature),
            )

    def test_wrong_key_fails_verification(self) -> None:
        fields, _identity = _build()
        wrong_key = nacl.signing.SigningKey.generate().verify_key
        with self.assertRaises(nacl.exceptions.BadSignatureError):
            wrong_key.verify(
                user_level_privacy_attestation_signing_bytes(fields),
                bytes.fromhex(fields.signature),
            )

    def test_signing_key_id_matches_identity(self) -> None:
        fields, identity = _build()
        self.assertEqual(fields.signing_key_id, identity.key_id)


class AttestationContentTests(unittest.TestCase):
    def test_fixed_weight_defaults_to_one(self) -> None:
        fields, _identity = _build()
        self.assertEqual(fields.fixed_weight, 1)

    def test_clipping_strategy_defaults_to_global_l2(self) -> None:
        fields, _identity = _build()
        self.assertEqual(fields.clipping_strategy, "global_l2")

    def test_operation_completed_is_carried_through(self) -> None:
        fields, _identity = _build(operation_completed=True)
        self.assertTrue(fields.operation_completed)

    def test_expires_at_is_after_issued_at(self) -> None:
        fields, _identity = _build()
        self.assertGreater(fields.expires_at, fields.issued_at)


class CrossLanguageGoldenFixtureTests(unittest.TestCase):
    """Work Area AC: a fixed set of field values whose expected
    payload_hash/signing_bytes SHA-256 are hardcoded here AND
    independently in
    cpp/coordinator/tests/signed_envelope_verifier_test.cpp -- neither
    side computes its own expected value from the implementation under
    test (that would just re-test self-consistency, exactly the gap
    that let a real cross-language field-ordering bug through this
    slice's own live Docker validation undetected by every prior unit
    test). If either implementation's canonicalization ever drifts
    from the other, one of these two hardcoded hex digests goes stale
    and this test fails."""

    def _fixture_fields(self) -> UserLevelPrivacyAttestationFields:
        return UserLevelPrivacyAttestationFields(
            worker_id="worker-1",
            client_id="client-1",
            run_id="run-1",
            round_id=7,
            task_id="task-1",
            session_id="session-1",
            model_version="v1",
            privacy_mode=3,
            privacy_configuration_hash="config-hash-abc",
            clip_norm=2.5,
            effective_sensitivity=2.500015,
            clipping_strategy="global_l2",
            fixed_weight=1,
            fixed_point_profile_hash="fp-profile-hash",
            tensor_manifest_hash="tensor-manifest-hash",
            # 2 == fl.worker.v1.SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_
            # EXPERIMENTAL (proto/worker/worker.proto) -- NOT 1 (..._PROVIDER_NONE).
            # An earlier draft of this fixture used 1 by mistake, which
            # made this exact test fail after the real client_id/
            # clip_norm ordering bug (below) was fixed -- both C++ and
            # Python must use the identical, real enum value 2.
            provider=2,
            operation_completed=True,
            issued_at=1000.0,
            expires_at=1300.0,
            signing_key_id="worker-key-1",
        )

    def test_payload_hash_matches_the_cpp_golden_value(self) -> None:
        fields = self._fixture_fields()
        payload_hash = sha256_hex(
            user_level_privacy_attestation_payload_hash_input(fields)
        )
        self.assertEqual(
            payload_hash,
            "dccb624cb56e6743ec4823e5bab7c71da234635b7fe9d3056a6878e59309b4c1",
        )

    def test_signing_bytes_sha256_matches_the_cpp_golden_value(self) -> None:
        fields = dataclasses.replace(
            self._fixture_fields(),
            payload_hash="dccb624cb56e6743ec4823e5bab7c71da234635b7fe9d3056a6878e59309b4c1",
        )
        signing_bytes = user_level_privacy_attestation_signing_bytes(fields)
        self.assertEqual(
            sha256_hex(signing_bytes.decode("utf-8")),
            "4fcab078d846cfce70d72a59046c9e0d0f385a8ca7961af2c87de56dbd6d9c1d",
        )


if __name__ == "__main__":
    unittest.main()
