"""Tests for fl_platform.security.coordinator_task_verifier -- the full
coordinator-signed-task verification pipeline, Coordinator-Signed Tasks
slice, Work Package L. See docs/signed-coordinator-tasks.md.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import nacl.signing
from fl_platform.security.coordinator_task_replay import CoordinatorTaskReplayStore
from fl_platform.security.coordinator_task_signing import (
    AggregationManifestFields,
    SignedCoordinatorTaskFields,
    TaskConfigurationFields,
    TensorDescriptor,
    coordinator_task_signing_bytes,
    dataset_partition_hash,
    model_configuration_hash,
    personalization_configuration_hash,
    privacy_configuration_hash,
    secure_adaptive_clipping_configuration_hash,
    secure_aggregation_configuration_hash,
    secure_user_level_dp_configuration_hash,
    task_payload_hash,
    training_configuration_hash,
)
from fl_platform.security.coordinator_task_verifier import (
    CoordinatorTaskRejectedError,
    CoordinatorTaskRejectionReason,
    CoordinatorTaskVerificationParams,
    verify_coordinator_task,
)
from fl_platform.security.coordinator_trust_bundle import TrustedCoordinatorKey

Reason = CoordinatorTaskRejectionReason


def _task_fields() -> TaskConfigurationFields:
    return TaskConfigurationFields(
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
        sample_level_dp_active=False,
    )


class _Harness:
    """Builds a real, correctly-signed task and lets individual tests
    tamper with exactly one thing before verifying."""

    def __init__(self) -> None:
        self.signing_key = nacl.signing.SigningKey.generate()
        self.public_key_hex = bytes(self.signing_key.verify_key).hex()
        self.task_fields = _task_fields()
        self.trusted_keys = {
            "coord-key-1": TrustedCoordinatorKey(
                signing_key_id="coord-key-1",
                public_key_hex=self.public_key_hex,
                status="active",
            )
        }

    def build_signed(
        self, **overrides: object
    ) -> tuple[SignedCoordinatorTaskFields, str]:
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
            training_configuration_hash=training_configuration_hash(self.task_fields),
            model_configuration_hash=model_configuration_hash(self.task_fields),
            dataset_partition_hash=dataset_partition_hash(self.task_fields),
            privacy_configuration_hash=privacy_configuration_hash(self.task_fields),
            personalization_configuration_hash=personalization_configuration_hash(
                self.task_fields
            ),
            secure_aggregation_configuration_hash=secure_aggregation_configuration_hash(
                self.task_fields
            ),
            secure_user_level_dp_configuration_hash=secure_user_level_dp_configuration_hash(
                self.task_fields
            ),
            secure_adaptive_clipping_configuration_hash=secure_adaptive_clipping_configuration_hash(
                self.task_fields
            ),
            task_payload_hash=task_payload_hash(self.task_fields),
        )
        base.update(overrides)
        fields = SignedCoordinatorTaskFields(**base)  # type: ignore[arg-type]
        bytes_to_sign = coordinator_task_signing_bytes(fields)
        signature = self.signing_key.sign(bytes_to_sign).signature
        return fields, signature.hex()


class CoordinatorTaskVerifierTests(unittest.TestCase):
    def _verify(
        self,
        harness: _Harness,
        signed_fields: SignedCoordinatorTaskFields,
        signature_hex: str,
        *,
        now: float = 1100.0,
        task_fields: TaskConfigurationFields | None = None,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            replay_store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            verify_coordinator_task(
                CoordinatorTaskVerificationParams(
                    signed_fields=signed_fields,
                    signature_hex=signature_hex,
                    task_fields=task_fields or harness.task_fields,
                    worker_id="worker-1",
                    now=now,
                ),
                harness.trusted_keys,
                replay_store,
            )

    def test_valid_task_passes(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed()
        self._verify(harness, fields, signature_hex)  # must not raise

    def test_wrong_worker_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(worker_id="other-worker")
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.WRONG_WORKER)

    def test_unknown_signing_key_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(
            coordinator_signing_key_id="unknown-key"
        )
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.UNKNOWN_SIGNING_KEY)

    def test_revoked_signing_key_rejected(self) -> None:
        harness = _Harness()
        harness.trusted_keys["coord-key-1"] = TrustedCoordinatorKey(
            signing_key_id="coord-key-1",
            public_key_hex=harness.public_key_hex,
            status="revoked",
        )
        fields, signature_hex = harness.build_signed()
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.REVOKED_SIGNING_KEY)

    def test_expired_signing_key_rejected(self) -> None:
        harness = _Harness()
        harness.trusted_keys["coord-key-1"] = TrustedCoordinatorKey(
            signing_key_id="coord-key-1",
            public_key_hex=harness.public_key_hex,
            status="expired",
        )
        fields, signature_hex = harness.build_signed()
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.EXPIRED_SIGNING_KEY)

    def test_training_config_hash_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(
            training_configuration_hash="0" * 64
        )
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.TRAINING_CONFIG_HASH_MISMATCH)

    def test_model_config_hash_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(model_configuration_hash="0" * 64)
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.MODEL_CONFIG_HASH_MISMATCH)

    def test_dataset_partition_hash_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(dataset_partition_hash="0" * 64)
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.DATASET_PARTITION_HASH_MISMATCH)

    def test_privacy_config_hash_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(
            privacy_configuration_hash="0" * 64
        )
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.PRIVACY_CONFIG_HASH_MISMATCH)

    def test_personalization_config_hash_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(
            personalization_configuration_hash="0" * 64
        )
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(
            ctx.exception.reason, Reason.PERSONALIZATION_CONFIG_HASH_MISMATCH
        )

    def test_secure_aggregation_binding_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(
            secure_aggregation_configuration_hash="0" * 64
        )
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(
            ctx.exception.reason, Reason.SECURE_AGGREGATION_BINDING_MISMATCH
        )

    def test_payload_hash_mismatch_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(task_payload_hash="0" * 64)
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex)
        self.assertEqual(ctx.exception.reason, Reason.PAYLOAD_HASH_MISMATCH)

    def test_invalid_signature_rejected(self) -> None:
        harness = _Harness()
        fields, _signature_hex = harness.build_signed()
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, "00" * 64)
        self.assertEqual(ctx.exception.reason, Reason.INVALID_SIGNATURE)

    def test_expired_task_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed()
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex, now=2000.0)
        self.assertEqual(ctx.exception.reason, Reason.TASK_EXPIRED)

    def test_future_issued_task_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed(
            issued_at=100000.0, expires_at=100300.0
        )
        with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
            self._verify(harness, fields, signature_hex, now=1100.0)
        self.assertEqual(ctx.exception.reason, Reason.TASK_ISSUED_IN_FUTURE)

    def test_replay_duplicate_sequence_rejected(self) -> None:
        harness = _Harness()
        fields, signature_hex = harness.build_signed()
        with tempfile.TemporaryDirectory() as tmp:
            replay_store = CoordinatorTaskReplayStore(Path(tmp) / "replay.json")
            params = CoordinatorTaskVerificationParams(
                signed_fields=fields,
                signature_hex=signature_hex,
                task_fields=harness.task_fields,
                worker_id="worker-1",
                now=1100.0,
            )
            verify_coordinator_task(params, harness.trusted_keys, replay_store)
            # A byte-identical reissue of the exact same signed task
            # (same sequence_number/nonce) must be rejected the second
            # time -- proves commit() actually persisted.
            with self.assertRaises(CoordinatorTaskRejectedError) as ctx:
                verify_coordinator_task(params, harness.trusted_keys, replay_store)
            self.assertIn(
                ctx.exception.reason,
                (Reason.DUPLICATE_OR_LOWER_SEQUENCE, Reason.DUPLICATE_NONCE),
            )


if __name__ == "__main__":
    unittest.main()
