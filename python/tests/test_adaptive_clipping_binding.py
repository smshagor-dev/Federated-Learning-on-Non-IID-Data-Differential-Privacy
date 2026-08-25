"""Tests for fl_platform.secure_aggregation.adaptive_clipping_binding --
Secure Adaptive Clipping with Private Indicator Aggregation slice, Work
Area H.
"""

from __future__ import annotations

import dataclasses
import unittest

import nacl.exceptions
import nacl.signing

from fl_platform.secure_aggregation.adaptive_clipping_binding import (
    AdaptiveClippingBindingFields,
    adaptive_clipping_binding_payload_hash_input,
    adaptive_clipping_binding_signing_bytes,
    build_signed_adaptive_clipping_binding,
    sha256_hex,
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
        adaptive_configuration_hash="adaptive-config-hash-abc",
        clip_state_step_count=3,
        current_clip_bound=4.5,
        provider=2,
        operation_completed=True,
        signing_identity=identity,
    )
    base.update(overrides)
    return build_signed_adaptive_clipping_binding(**base), identity


class BindingFieldExclusionTests(unittest.TestCase):
    """Work Area H: the binding must never carry the clear indicator
    value, the unclipped norm, the clipped norm, or the clipping
    factor -- verified here by asserting those attribute names simply
    do not exist on the dataclass, not by trusting a docstring."""

    def test_excluded_fields_do_not_exist(self) -> None:
        fields, _identity = _build()
        field_names = {f.name for f in dataclasses.fields(fields)}
        for excluded in (
            "indicator",
            "clear_indicator",
            "unclipped_norm",
            "clipped_norm",
            "clipping_factor",
            "operation_clipped",
        ):
            self.assertNotIn(excluded, field_names)


class BindingCanonicalBytesTests(unittest.TestCase):
    def test_payload_hash_input_deterministic(self) -> None:
        fields, _identity = _build()
        first = adaptive_clipping_binding_payload_hash_input(fields)
        second = adaptive_clipping_binding_payload_hash_input(fields)
        self.assertEqual(first, second)

    def test_payload_hash_changes_when_current_clip_bound_changes(self) -> None:
        fields_a, identity = _build()
        fields_b, _ = _build(signing_identity=identity, current_clip_bound=999.0)
        self.assertNotEqual(
            adaptive_clipping_binding_payload_hash_input(fields_a),
            adaptive_clipping_binding_payload_hash_input(fields_b),
        )

    def test_payload_hash_changes_when_clip_state_step_count_changes(self) -> None:
        fields_a, identity = _build()
        fields_b, _ = _build(signing_identity=identity, clip_state_step_count=999)
        self.assertNotEqual(
            adaptive_clipping_binding_payload_hash_input(fields_a),
            adaptive_clipping_binding_payload_hash_input(fields_b),
        )

    def test_signing_bytes_include_payload_hash_and_signing_key_id(self) -> None:
        fields, _identity = _build()
        signing_bytes = adaptive_clipping_binding_signing_bytes(fields)
        self.assertIn(fields.payload_hash.encode("ascii"), signing_bytes)
        self.assertIn(fields.signing_key_id.encode("ascii"), signing_bytes)
        self.assertNotIn(fields.signature.encode("ascii"), signing_bytes)


class BindingSignatureTests(unittest.TestCase):
    def test_real_signature_verifies(self) -> None:
        fields, identity = _build()
        verify_key = identity.signing_key.verify_key
        signing_bytes = adaptive_clipping_binding_signing_bytes(fields)
        verify_key.verify(signing_bytes, bytes.fromhex(fields.signature))

    def test_tampered_field_breaks_verification(self) -> None:
        fields, identity = _build()
        tampered = dataclasses.replace(fields, current_clip_bound=999.0)
        verify_key = identity.signing_key.verify_key
        with self.assertRaises(nacl.exceptions.BadSignatureError):
            verify_key.verify(
                adaptive_clipping_binding_signing_bytes(tampered),
                bytes.fromhex(tampered.signature),
            )

    def test_wrong_key_fails_verification(self) -> None:
        fields, _identity = _build()
        wrong_key = nacl.signing.SigningKey.generate().verify_key
        with self.assertRaises(nacl.exceptions.BadSignatureError):
            wrong_key.verify(
                adaptive_clipping_binding_signing_bytes(fields),
                bytes.fromhex(fields.signature),
            )

    def test_signing_key_id_matches_identity(self) -> None:
        fields, identity = _build()
        self.assertEqual(fields.signing_key_id, identity.key_id)


class BindingContentTests(unittest.TestCase):
    def test_operation_completed_is_carried_through(self) -> None:
        fields, _identity = _build(operation_completed=True)
        self.assertTrue(fields.operation_completed)

    def test_expires_at_is_after_issued_at(self) -> None:
        fields, _identity = _build()
        self.assertGreater(fields.expires_at, fields.issued_at)

    def test_clip_state_step_count_is_carried_through(self) -> None:
        fields, _identity = _build(clip_state_step_count=42)
        self.assertEqual(fields.clip_state_step_count, 42)


class CrossLanguageGoldenFixtureTests(unittest.TestCase):
    """Work Area AF: a fixed set of field values whose expected
    payload_hash/signing_bytes SHA-256 are hardcoded here AND
    independently in
    cpp/coordinator/tests/signed_envelope_verifier_test.cpp -- neither
    side computes its own expected value from the implementation under
    test. If either implementation's canonicalization ever drifts from
    the other, one of these two hardcoded hex digests goes stale and
    this test fails."""

    def _fixture_fields(self) -> AdaptiveClippingBindingFields:
        return AdaptiveClippingBindingFields(
            worker_id="worker-1",
            client_id="client-1",
            run_id="run-1",
            round_id=7,
            task_id="task-1",
            session_id="session-1",
            model_version="v1",
            adaptive_configuration_hash="adaptive-config-hash-abc",
            clip_state_step_count=3,
            current_clip_bound=4.5,
            # 2 == fl.worker.v1.SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_
            # EXPERIMENTAL (proto/worker/worker.proto) -- the real enum
            # value, not a guess (see user_level_attestation's own
            # cautionary note in its identical fixture).
            provider=2,
            operation_completed=True,
            issued_at=1000.0,
            expires_at=1300.0,
            signing_key_id="worker-key-1",
        )

    def test_payload_hash_matches_the_cpp_golden_value(self) -> None:
        fields = self._fixture_fields()
        payload_hash = sha256_hex(adaptive_clipping_binding_payload_hash_input(fields))
        self.assertEqual(
            payload_hash,
            "e7146528bf87842d8dae057e40df1da3ab940f793d009be80dc3a0986fabeb7b",
        )

    def test_signing_bytes_sha256_matches_the_cpp_golden_value(self) -> None:
        fields = dataclasses.replace(
            self._fixture_fields(),
            payload_hash="e7146528bf87842d8dae057e40df1da3ab940f793d009be80dc3a0986fabeb7b",
        )
        signing_bytes = adaptive_clipping_binding_signing_bytes(fields)
        self.assertEqual(
            sha256_hex(signing_bytes.decode("utf-8")),
            "55dabb7cfca8ff06dace9fdb7b09e79ed65fe40874f6be7ccb0a7feab492b7d8",
        )


if __name__ == "__main__":
    unittest.main()
