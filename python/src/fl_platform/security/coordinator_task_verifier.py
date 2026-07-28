"""Full coordinator-signed-task verification pipeline -- Coordinator-
Signed Tasks slice, Work Package L. See
docs/signed-coordinator-tasks.md.

verify_coordinator_task() is the single entry point
fl_platform.worker.coordinator_client.GrpcCoordinatorClient.acquire_task
calls before returning a task to its caller -- every check below runs
before the caller (WorkerService.run) ever builds a model, touches a
dataset partition, loads a personalized checkpoint, allocates CUDA
memory, initializes PyTorch training, or initializes Opacus. A failure
raises CoordinatorTaskRejectedError with a structured, stable
`reason` -- never a silent skip and never a partial/best-effort
execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fl_platform.security.coordinator_task_replay import (
    CoordinatorTaskReplayCandidate,
    CoordinatorTaskReplayStore,
)
from fl_platform.security.coordinator_task_signing import (
    SignedCoordinatorTaskFields,
    TaskConfigurationFields,
    dataset_partition_hash,
    model_configuration_hash,
    personalization_configuration_hash,
    privacy_configuration_hash,
    secure_adaptive_clipping_configuration_hash,
    secure_aggregation_configuration_hash,
    secure_user_level_dp_configuration_hash,
    task_payload_hash,
    training_configuration_hash,
    verify_coordinator_task_signature,
)
from fl_platform.security.coordinator_trust_bundle import TrustedCoordinatorKey


class CoordinatorTaskRejectionReason(str, Enum):
    """17 structured rejection reasons -- Work Package Q, plus
    SECURE_AGGREGATION_BINDING_MISMATCH (Secure Cohort Handshake and
    Signed Roster Runtime slice). Stable, machine-readable strings
    (never renumbered/reworded silently, since operators and tests
    match on these)."""

    UNKNOWN_SIGNING_KEY = "unknown_signing_key"
    REVOKED_SIGNING_KEY = "revoked_signing_key"
    EXPIRED_SIGNING_KEY = "expired_signing_key"
    INVALID_SIGNATURE = "invalid_signature"
    PAYLOAD_HASH_MISMATCH = "payload_hash_mismatch"
    TRAINING_CONFIG_HASH_MISMATCH = "training_config_hash_mismatch"
    MODEL_CONFIG_HASH_MISMATCH = "model_config_hash_mismatch"
    DATASET_PARTITION_HASH_MISMATCH = "dataset_partition_hash_mismatch"
    PRIVACY_CONFIG_HASH_MISMATCH = "privacy_config_hash_mismatch"
    PERSONALIZATION_CONFIG_HASH_MISMATCH = "personalization_config_hash_mismatch"
    SECURE_AGGREGATION_BINDING_MISMATCH = "secure_aggregation_binding_mismatch"
    SECURE_USER_LEVEL_DP_CONFIG_HASH_MISMATCH = (
        "secure_user_level_dp_config_hash_mismatch"
    )
    SECURE_ADAPTIVE_CLIPPING_CONFIG_HASH_MISMATCH = (
        "secure_adaptive_clipping_config_hash_mismatch"
    )
    TASK_EXPIRED = "task_expired"
    TASK_ISSUED_IN_FUTURE = "task_issued_in_future"
    WRONG_WORKER = "wrong_worker"
    DUPLICATE_NONCE = "duplicate_nonce"
    DUPLICATE_OR_LOWER_SEQUENCE = "duplicate_or_lower_sequence"
    DUPLICATE_TASK_EXECUTION = "duplicate_task_execution"


class CoordinatorTaskRejectedError(RuntimeError):
    """Raised for any of the 16 CoordinatorTaskRejectionReason cases.
    Never caught and silently ignored by WorkerService -- a rejected
    task must not be executed."""

    def __init__(self, reason: CoordinatorTaskRejectionReason, detail: str) -> None:
        super().__init__(f"coordinator task rejected ({reason.value}): {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(slots=True, frozen=True)
class CoordinatorTaskVerificationParams:
    signed_fields: SignedCoordinatorTaskFields
    signature_hex: str
    task_fields: TaskConfigurationFields
    worker_id: str
    now: float
    future_issued_tolerance_seconds: float = 30.0


def verify_coordinator_task(
    params: CoordinatorTaskVerificationParams,
    trusted_keys: dict[str, TrustedCoordinatorKey],
    replay_store: CoordinatorTaskReplayStore,
) -> None:
    """Raises CoordinatorTaskRejectedError on any failure; returns
    None (and commits replay state) on success. Order matches
    docs/signed-coordinator-tasks.md's pipeline: worker binding, key
    resolution/status, configuration hashes, payload hash, signature,
    expiry/future-issuance, replay."""
    signed = params.signed_fields

    if signed.worker_id != params.worker_id:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.WRONG_WORKER,
            f"task is addressed to worker_id '{signed.worker_id}', not this worker "
            f"('{params.worker_id}')",
        )

    trusted_key = trusted_keys.get(signed.coordinator_signing_key_id)
    if trusted_key is None:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.UNKNOWN_SIGNING_KEY,
            f"coordinator_signing_key_id '{signed.coordinator_signing_key_id}' is not "
            "in the trusted-coordinator-key bundle",
        )
    if trusted_key.status == "revoked":
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.REVOKED_SIGNING_KEY,
            f"coordinator_signing_key_id '{signed.coordinator_signing_key_id}' has "
            "been revoked",
        )
    if trusted_key.status == "expired":
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.EXPIRED_SIGNING_KEY,
            f"coordinator_signing_key_id '{signed.coordinator_signing_key_id}' has "
            "expired",
        )

    recomputed_training = training_configuration_hash(params.task_fields)
    if recomputed_training != signed.training_configuration_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.TRAINING_CONFIG_HASH_MISMATCH,
            "recomputed training_configuration_hash does not match the signed value",
        )
    recomputed_model = model_configuration_hash(params.task_fields)
    if recomputed_model != signed.model_configuration_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.MODEL_CONFIG_HASH_MISMATCH,
            "recomputed model_configuration_hash does not match the signed value",
        )
    recomputed_dataset = dataset_partition_hash(params.task_fields)
    if recomputed_dataset != signed.dataset_partition_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.DATASET_PARTITION_HASH_MISMATCH,
            "recomputed dataset_partition_hash does not match the signed value",
        )
    recomputed_privacy = privacy_configuration_hash(params.task_fields)
    if recomputed_privacy != signed.privacy_configuration_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.PRIVACY_CONFIG_HASH_MISMATCH,
            "recomputed privacy_configuration_hash does not match the signed value",
        )
    recomputed_personalization = personalization_configuration_hash(params.task_fields)
    if recomputed_personalization != signed.personalization_configuration_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.PERSONALIZATION_CONFIG_HASH_MISMATCH,
            "recomputed personalization_configuration_hash does not match the "
            "signed value",
        )
    recomputed_secure_aggregation = secure_aggregation_configuration_hash(
        params.task_fields
    )
    if recomputed_secure_aggregation != signed.secure_aggregation_configuration_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.SECURE_AGGREGATION_BINDING_MISMATCH,
            "recomputed secure_aggregation_configuration_hash does not match the "
            "signed value",
        )
    recomputed_secure_user_level_dp = secure_user_level_dp_configuration_hash(
        params.task_fields
    )
    if (
        recomputed_secure_user_level_dp
        != signed.secure_user_level_dp_configuration_hash
    ):
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.SECURE_USER_LEVEL_DP_CONFIG_HASH_MISMATCH,
            "recomputed secure_user_level_dp_configuration_hash does not match the "
            "signed value",
        )
    recomputed_secure_adaptive_clipping = secure_adaptive_clipping_configuration_hash(
        params.task_fields
    )
    if (
        recomputed_secure_adaptive_clipping
        != signed.secure_adaptive_clipping_configuration_hash
    ):
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.SECURE_ADAPTIVE_CLIPPING_CONFIG_HASH_MISMATCH,
            "recomputed secure_adaptive_clipping_configuration_hash does not match the "
            "signed value",
        )
    recomputed_payload = task_payload_hash(params.task_fields)
    if recomputed_payload != signed.task_payload_hash:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.PAYLOAD_HASH_MISMATCH,
            "recomputed task_payload_hash does not match the signed value",
        )

    signature_valid = verify_coordinator_task_signature(
        signed, params.signature_hex, trusted_key.public_key_hex
    )
    if not signature_valid:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.INVALID_SIGNATURE,
            "Ed25519 signature verification failed against the trusted coordinator "
            "public key",
        )

    if signed.expires_at <= 0.0 or params.now >= signed.expires_at:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.TASK_EXPIRED,
            f"task expires_at ({signed.expires_at}) has passed (now={params.now})",
        )
    if signed.issued_at > params.now + params.future_issued_tolerance_seconds:
        raise CoordinatorTaskRejectedError(
            CoordinatorTaskRejectionReason.TASK_ISSUED_IN_FUTURE,
            f"task issued_at ({signed.issued_at}) is too far ahead of now "
            f"({params.now})",
        )

    replay_candidate = CoordinatorTaskReplayCandidate(
        coordinator_signing_key_id=signed.coordinator_signing_key_id,
        sequence_number=signed.sequence_number,
        nonce=signed.nonce,
    )
    replay_decision = replay_store.validate(replay_candidate)
    if not replay_decision.accepted:
        reason = (
            CoordinatorTaskRejectionReason.DUPLICATE_NONCE
            if replay_decision.reason == "duplicate_nonce"
            else CoordinatorTaskRejectionReason.DUPLICATE_OR_LOWER_SEQUENCE
        )
        raise CoordinatorTaskRejectedError(reason, replay_decision.detail)

    replay_store.commit(replay_candidate)
