"""Tests for fl_platform.security.coordinator_task_signing --
Coordinator-Signed Tasks slice. See docs/signed-coordinator-tasks.md
and docs/task-configuration-hashes.md.

Cross-language golden fixture: GoldenFixtureTests below asserts the six
SHA-256 hex digests this module computes for a fixed input. The same
six literal hex strings are pasted verbatim into
cpp/coordinator/tests/coordinator_task_signing_test.cpp, computed there
independently from the identical field values -- the same "two
independent implementations, each computes its own output, then
compared" methodology already used for every prior golden fixture in
this project (see docs/canonical-security-serialization.md).
"""

from __future__ import annotations

import dataclasses
import unittest

import nacl.signing

from fl_platform.security.coordinator_task_signing import (
    AggregationManifestFields,
    CoordinatorTaskSigningError,
    SampleLevelPrivacyFields,
    SecureAggregationTaskBindingFields,
    SignedCoordinatorTaskFields,
    TaskConfigurationFields,
    TensorDescriptor,
    coordinator_task_signing_bytes,
    dataset_partition_hash,
    model_configuration_hash,
    personalization_configuration_hash,
    privacy_configuration_hash,
    secure_aggregation_configuration_hash,
    task_payload_hash,
    training_configuration_hash,
    verify_coordinator_task_signature,
)


def _fields(**overrides: object) -> TaskConfigurationFields:
    base = dict(  # noqa: C408 - kwarg style is clearer for this many fields
        run_id="run-1",
        round_id=2,
        client_id="client-a",
        model_version="v3",
        algorithm="fedavg",
        dataset_reference="partition-7",
        model_manifest=(
            TensorDescriptor(
                name="weight",
                shape=(32,),
                dtype="float32",
                byte_length=128,
                checksum="abc123",
            ),
        ),
        task_id="task-1",
        lease_id="lease-1",
        lease_expires_at="2026-01-01T00:05:00Z",
        attempt=1,
        local_epochs=3,
        batch_size=32,
        learning_rate=0.01,
        momentum=0.9,
        weight_decay=0.0001,
        fedprox_mu=0.0,
        aggregation_manifest=AggregationManifestFields(
            shared_parameter_names=("backbone",),
            personalized_parameter_names=("head",),
            schema_hash="schema-1",
        ),
        sample_level_dp_active=True,
        sample_level_privacy=SampleLevelPrivacyFields(
            noise_multiplier=1.1,
            max_grad_norm=1.0,
            target_delta=1e-5,
            accountant=1,
            poisson_sampling=True,
            epsilon_budget=8.0,
        ),
    )
    base.update(overrides)
    return TaskConfigurationFields(**base)  # type: ignore[arg-type]


class GoldenFixtureTests(unittest.TestCase):
    """Real values computed by actually running this module against the
    fixed input from _fields() -- pasted verbatim into
    cpp/coordinator/tests/coordinator_task_signing_test.cpp's
    make_task() (identical field values) and asserted there too. If the
    two encoders ever disagree on a single byte, these constants would
    no longer match a fresh run of either side -- this is what makes
    the fixture a real cross-language proof, not a tautology."""

    def test_golden_hashes_for_fixed_input(self) -> None:
        fields = _fields()
        self.assertEqual(
            training_configuration_hash(fields),
            "03522fd3f60e0f085ec4ac97a1bacecd0175bb6a40f4a46c33f4f78fec2e4886",
        )
        self.assertEqual(
            model_configuration_hash(fields),
            "03ff11f75cec5b6885b39f9fe967cadfa8576f83644aef3d23eac5e4410c4df2",
        )
        self.assertEqual(
            dataset_partition_hash(fields),
            "651e914d371ff5c90a30cef18dd34a87d0a46919a705a5305607e0ce83153c1b",
        )
        self.assertEqual(
            privacy_configuration_hash(fields),
            "39a3d2920122e9ad09d040b9301a45ce5595997773a483508c2a53f830c0c73a",
        )
        self.assertEqual(
            personalization_configuration_hash(fields),
            "107627fce65e62806c6ba2cc13fb2820d44342a25ad357977d85015bcaa6dd3b",
        )
        self.assertEqual(
            task_payload_hash(fields),
            "d40b3262deb80649f305676d30b46a2c251e919a9d93e8a0b5b65c7f7f89cfc2",
        )


class TrainingConfigurationHashTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        fields = _fields()
        self.assertEqual(
            training_configuration_hash(fields), training_configuration_hash(fields)
        )

    def test_tamper_detection(self) -> None:
        fields = _fields()
        tampered = _fields(learning_rate=0.5)
        self.assertNotEqual(
            training_configuration_hash(fields), training_configuration_hash(tampered)
        )

    def test_rejects_non_finite(self) -> None:
        fields = _fields(learning_rate=float("nan"))
        with self.assertRaises(CoordinatorTaskSigningError):
            training_configuration_hash(fields)


class ModelConfigurationHashTests(unittest.TestCase):
    def test_tamper_detection_schema_hash(self) -> None:
        fields = _fields()
        tampered = _fields(
            aggregation_manifest=AggregationManifestFields(
                shared_parameter_names=("backbone",),
                personalized_parameter_names=("head",),
                schema_hash="different-schema",
            )
        )
        self.assertNotEqual(
            model_configuration_hash(fields), model_configuration_hash(tampered)
        )

    def test_tamper_detection_tensor_manifest(self) -> None:
        fields = _fields()
        tampered = _fields(
            model_manifest=(
                TensorDescriptor(
                    name="weight",
                    shape=(64,),
                    dtype="float32",
                    byte_length=256,
                    checksum="different",
                ),
            )
        )
        self.assertNotEqual(
            model_configuration_hash(fields), model_configuration_hash(tampered)
        )


class DatasetPartitionHashTests(unittest.TestCase):
    def test_tamper_detection(self) -> None:
        fields = _fields()
        tampered = _fields(dataset_reference="different-partition")
        self.assertNotEqual(
            dataset_partition_hash(fields), dataset_partition_hash(tampered)
        )


class PrivacyConfigurationHashTests(unittest.TestCase):
    def test_tamper_detection(self) -> None:
        fields = _fields()
        tampered = _fields(
            sample_level_privacy=SampleLevelPrivacyFields(
                noise_multiplier=9.9,
                max_grad_norm=1.0,
                target_delta=1e-5,
                accountant=1,
                poisson_sampling=True,
                epsilon_budget=8.0,
            )
        )
        self.assertNotEqual(
            privacy_configuration_hash(fields), privacy_configuration_hash(tampered)
        )

    def test_inactive_hashes_differently_from_active(self) -> None:
        active = _fields()
        inactive = _fields(sample_level_dp_active=False, sample_level_privacy=None)
        self.assertNotEqual(
            privacy_configuration_hash(active), privacy_configuration_hash(inactive)
        )

    def test_inactive_does_not_require_privacy_fields(self) -> None:
        inactive = _fields(sample_level_dp_active=False, sample_level_privacy=None)
        # Must not raise even though sample_level_privacy is None.
        privacy_configuration_hash(inactive)


class PersonalizationConfigurationHashTests(unittest.TestCase):
    def test_tamper_detection(self) -> None:
        fields = _fields()
        tampered = _fields(
            aggregation_manifest=AggregationManifestFields(
                shared_parameter_names=("backbone",),
                personalized_parameter_names=("head",),
                frozen_parameter_names=("extra-frozen",),
                schema_hash="schema-1",
            )
        )
        self.assertNotEqual(
            personalization_configuration_hash(fields),
            personalization_configuration_hash(tampered),
        )


def _active_secure_aggregation_binding(
    **overrides: object,
) -> SecureAggregationTaskBindingFields:
    base = dict(  # noqa: C408 - kwarg style is clearer for this many fields
        secure_aggregation_active=True,
        provider=2,
        protocol_version=1,
        session_id="session-1",
        session_configuration_hash="a" * 64,
        key_advertisement_deadline_unix_s=1500.0,
        minimum_cohort_size=3,
    )
    base.update(overrides)
    return SecureAggregationTaskBindingFields(**base)  # type: ignore[arg-type]


class SecureAggregationConfigurationHashTests(unittest.TestCase):
    """Secure Cohort Handshake and Signed Roster Runtime slice
    (docs/secure-cohort-handshake-foundation.md), Work item 4."""

    def test_inactive_by_default_is_deterministic(self) -> None:
        fields = _fields()
        self.assertFalse(fields.secure_aggregation.secure_aggregation_active)
        self.assertEqual(
            secure_aggregation_configuration_hash(fields),
            secure_aggregation_configuration_hash(fields),
        )

    def test_active_binding_changes_the_hash(self) -> None:
        fields = _fields()
        active = _fields(secure_aggregation=_active_secure_aggregation_binding())
        self.assertNotEqual(
            secure_aggregation_configuration_hash(fields),
            secure_aggregation_configuration_hash(active),
        )

    def test_tamper_detection_within_an_active_binding(self) -> None:
        binding = _active_secure_aggregation_binding()
        fields = _fields(secure_aggregation=binding)
        tampered = _fields(
            secure_aggregation=dataclasses.replace(binding, session_id="session-2")
        )
        self.assertNotEqual(
            secure_aggregation_configuration_hash(fields),
            secure_aggregation_configuration_hash(tampered),
        )

    def test_does_not_affect_task_payload_hash(self) -> None:
        # A sibling hash, like personalization_configuration_hash --
        # never folded into task_payload_hash (see
        # docs/secure-cohort-handshake-foundation.md).
        fields = _fields()
        active = _fields(secure_aggregation=_active_secure_aggregation_binding())
        self.assertEqual(task_payload_hash(fields), task_payload_hash(active))

    def test_rejects_non_finite_deadline(self) -> None:
        fields = _fields(
            secure_aggregation=_active_secure_aggregation_binding(
                key_advertisement_deadline_unix_s=float("nan")
            )
        )
        with self.assertRaises(CoordinatorTaskSigningError):
            secure_aggregation_configuration_hash(fields)


class TaskPayloadHashTests(unittest.TestCase):
    def test_tamper_detection(self) -> None:
        fields = _fields()
        tampered = _fields(lease_id="different-lease")
        self.assertNotEqual(task_payload_hash(fields), task_payload_hash(tampered))

    def test_deterministic(self) -> None:
        fields = _fields()
        self.assertEqual(task_payload_hash(fields), task_payload_hash(fields))


def _signed_fields(**overrides: object) -> SignedCoordinatorTaskFields:
    base = dict(  # noqa: C408 - kwarg style is clearer for this many fields
        coordinator_signing_key_id="coord-key-1",
        worker_id="worker-1",
        task_id="task-1",
        lease_id="lease-1",
        attempt=1,
        issued_at=1000.0,
        expires_at=1300.0,
        nonce="nonce-abc",
        sequence_number=1,
        training_configuration_hash="a" * 64,
        model_configuration_hash="b" * 64,
        dataset_partition_hash="c" * 64,
        privacy_configuration_hash="d" * 64,
        personalization_configuration_hash="e" * 64,
        task_payload_hash="f" * 64,
    )
    base.update(overrides)
    return SignedCoordinatorTaskFields(**base)  # type: ignore[arg-type]


class SignatureRoundTripTests(unittest.TestCase):
    def test_valid_signature_verifies(self) -> None:
        signing_key = nacl.signing.SigningKey.generate()
        public_key_hex = bytes(signing_key.verify_key).hex()
        signed_fields = _signed_fields()
        bytes_to_sign = coordinator_task_signing_bytes(signed_fields)
        signature_hex = signing_key.sign(bytes_to_sign).signature.hex()
        self.assertTrue(
            verify_coordinator_task_signature(
                signed_fields, signature_hex, public_key_hex
            )
        )

    def test_tampered_field_invalidates_signature(self) -> None:
        signing_key = nacl.signing.SigningKey.generate()
        public_key_hex = bytes(signing_key.verify_key).hex()
        signed_fields = _signed_fields()
        bytes_to_sign = coordinator_task_signing_bytes(signed_fields)
        signature_hex = signing_key.sign(bytes_to_sign).signature.hex()
        tampered = dataclasses.replace(signed_fields, task_payload_hash="0" * 64)
        self.assertFalse(
            verify_coordinator_task_signature(tampered, signature_hex, public_key_hex)
        )

    def test_tampered_secure_aggregation_hash_invalidates_signature(self) -> None:
        # Locks in that secure_aggregation_configuration_hash is a real
        # part of the signed bytes (coordinator_task_signing_bytes),
        # even though it is never folded into task_payload_hash itself
        # -- see SecureAggregationConfigurationHashTests above.
        signing_key = nacl.signing.SigningKey.generate()
        public_key_hex = bytes(signing_key.verify_key).hex()
        signed_fields = _signed_fields()
        bytes_to_sign = coordinator_task_signing_bytes(signed_fields)
        signature_hex = signing_key.sign(bytes_to_sign).signature.hex()
        tampered = dataclasses.replace(
            signed_fields, secure_aggregation_configuration_hash="0" * 64
        )
        self.assertFalse(
            verify_coordinator_task_signature(tampered, signature_hex, public_key_hex)
        )

    def test_wrong_key_fails(self) -> None:
        signing_key = nacl.signing.SigningKey.generate()
        other_key = nacl.signing.SigningKey.generate()
        other_public_hex = bytes(other_key.verify_key).hex()
        signed_fields = _signed_fields()
        bytes_to_sign = coordinator_task_signing_bytes(signed_fields)
        signature_hex = signing_key.sign(bytes_to_sign).signature.hex()
        self.assertFalse(
            verify_coordinator_task_signature(
                signed_fields, signature_hex, other_public_hex
            )
        )

    def test_malformed_signature_returns_false_not_raise(self) -> None:
        signed_fields = _signed_fields()
        self.assertFalse(
            verify_coordinator_task_signature(signed_fields, "not-hex", "also-not-hex")
        )


if __name__ == "__main__":
    unittest.main()
