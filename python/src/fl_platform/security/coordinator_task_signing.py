"""Coordinator-signed task verification -- Coordinator-Signed Tasks
slice. The Python (worker-side) counterpart to
cpp/coordinator/src/coordinator_task_signing.cpp: that file computes
and signs these same hashes on the coordinator; this module recomputes
them independently and verifies the Ed25519 signature before a worker
ever touches a model, dataset, or Opacus. See
docs/signed-coordinator-tasks.md and docs/task-configuration-hashes.md.

Canonicalization contract (must byte-for-byte match the C++ side --
see docs/canonical-security-serialization.md): every hash uses
``json.dumps(payload, sort_keys=True, separators=(",", ":"),
ensure_ascii=True)``, exactly as every other signed structure in this
project. Each of the five configuration hashes and the task payload
hash uses its own domain-separation prefix (never the shared task-
signing prefix) -- see docs/task-configuration-hashes.md.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import nacl.exceptions
import nacl.signing

SCHEMA_VERSION = 1

TASK_DOMAIN_SEPARATION_PREFIX = b"FL_PLATFORM_COORDINATOR_TASK_V1\x00"
TRAINING_CONFIG_DOMAIN_SEPARATION_PREFIX = b"FL_PLATFORM_TRAINING_CONFIG_V1\x00"
MODEL_CONFIG_DOMAIN_SEPARATION_PREFIX = b"FL_PLATFORM_MODEL_CONFIG_V1\x00"
DATASET_PARTITION_DOMAIN_SEPARATION_PREFIX = b"FL_PLATFORM_DATASET_PARTITION_V1\x00"
PRIVACY_CONFIG_DOMAIN_SEPARATION_PREFIX = b"FL_PLATFORM_PRIVACY_CONFIG_V1\x00"
PERSONALIZATION_CONFIG_DOMAIN_SEPARATION_PREFIX = (
    b"FL_PLATFORM_PERSONALIZATION_CONFIG_V1\x00"
)
TASK_PAYLOAD_DOMAIN_SEPARATION_PREFIX = b"FL_PLATFORM_COORDINATOR_TASK_PAYLOAD_V1\x00"
SECURE_AGGREGATION_TASK_BINDING_DOMAIN_SEPARATION_PREFIX = (
    b"FL_PLATFORM_SECURE_AGGREGATION_TASK_BINDING_V1\x00"
)
SECURE_USER_LEVEL_DP_CONFIG_DOMAIN_SEPARATION_PREFIX = (
    b"FL_PLATFORM_SECURE_USER_LEVEL_DP_CONFIG_V1\x00"
)


class CoordinatorTaskSigningError(RuntimeError):
    """Raised on a hashing failure (NaN/Inf field) -- never on a
    verification rejection; see CoordinatorTaskRejectionReason /
    CoordinatorTaskRejectedError for those (coordinator_task_verifier.py)."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_non_finite(*values: float) -> None:
    for value in values:
        if not math.isfinite(value):
            raise CoordinatorTaskSigningError(
                "cannot hash a payload containing a NaN or infinite value"
            )


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class TensorDescriptor:
    name: str
    shape: tuple[int, ...]
    dtype: str
    byte_length: int
    checksum: str


@dataclass(slots=True, frozen=True)
class AggregationManifestFields:
    shared_parameter_names: tuple[str, ...] = ()
    personalized_parameter_names: tuple[str, ...] = ()
    frozen_parameter_names: tuple[str, ...] = ()
    schema_hash: str = ""


@dataclass(slots=True, frozen=True)
class SampleLevelPrivacyFields:
    noise_multiplier: float
    max_grad_norm: float
    target_delta: float
    accountant: int
    poisson_sampling: bool
    epsilon_budget: float
    sample_budget_policy: int = 0


@dataclass(slots=True, frozen=True)
class SecureAggregationTaskBindingFields:
    """Mirrors fl.coordinator.v1.SecureAggregationTaskBinding -- Secure
    Cohort Handshake and Signed Roster Runtime slice
    (docs/secure-cohort-handshake-foundation.md), Work item 4/5;
    extended by the Masked Update Runtime and No-Dropout Secure FedAvg
    Finalization slice, Work Area C (see
    docs/secure-aggregation-masked-runtime-audit.md for why this
    binding does not also carry the frozen roster's own fields)."""

    secure_aggregation_active: bool = False
    provider: int = 0
    protocol_version: int = 0
    session_id: str = ""
    session_configuration_hash: str = ""
    key_advertisement_deadline_unix_s: float = 0.0
    minimum_cohort_size: int = 0
    masked_update_deadline_unix_s: float = 0.0
    session_expiry_unix_s: float = 0.0
    max_absolute_update_bound: float = 0.0
    max_client_weight: int = 0
    max_aggregate_bound: int = 0
    tensor_chunk_size: int = 0
    privacy_mode_compatible: bool = True
    privacy_incompatibility_reason: str = ""
    # Secure User-Level Differential Privacy Runtime slice, Work Area E
    # -- mirrors fl.coordinator.v1.SecureAggregationTaskBinding's fields
    # 16-26 field-for-field. adjacency_model/sampling_assumption are the
    # raw proto enum ints (SecureUserLevelAdjacencyModel/
    # SecureUserLevelSamplingAssumption), matching `provider: int`'s own
    # convention above.
    secure_user_level_dp_active: bool = False
    secure_user_level_adjacency_model: int = 0
    secure_user_level_clip_norm: float = 0.0
    secure_user_level_quantization_margin: float = 0.0
    secure_user_level_effective_sensitivity: float = 0.0
    secure_user_level_noise_multiplier: float = 0.0
    secure_user_level_target_delta: float = 0.0
    secure_user_level_max_epsilon: float = 0.0
    secure_user_level_fixed_weight: int = 0
    secure_user_level_sampling_assumption: int = 0


@dataclass(slots=True, frozen=True)
class TaskConfigurationFields:
    """Every field the five configuration hashes and the task payload
    hash are computed from -- mirrors exactly what
    fl.coordinator.v1.ClientTrainingTask carries on the wire today (see
    docs/task-configuration-hashes.md's field-list rationale)."""

    run_id: str
    round_id: int
    client_id: str
    model_version: str
    algorithm: str
    dataset_reference: str
    model_manifest: tuple[TensorDescriptor, ...]
    task_id: str
    lease_id: str
    lease_expires_at: str
    attempt: int
    local_epochs: int
    batch_size: int
    learning_rate: float
    momentum: float
    weight_decay: float
    fedprox_mu: float
    aggregation_manifest: AggregationManifestFields = field(
        default_factory=AggregationManifestFields
    )
    sample_level_dp_active: bool = False
    sample_level_privacy: SampleLevelPrivacyFields | None = None
    secure_aggregation: SecureAggregationTaskBindingFields = field(
        default_factory=SecureAggregationTaskBindingFields
    )


def _tensor_json(tensor: TensorDescriptor) -> dict[str, Any]:
    return {
        "byte_length": int(tensor.byte_length),
        "checksum": tensor.checksum,
        "dtype": tensor.dtype,
        "name": tensor.name,
        "shape": [int(dim) for dim in tensor.shape],
    }


def training_configuration_hash(fields: TaskConfigurationFields) -> str:
    """Must byte-for-byte match coordinator_task_signing.cpp's
    training_configuration_hash's hash_input (SHA-256 of it is what's
    actually compared)."""
    _reject_non_finite(
        fields.learning_rate, fields.momentum, fields.weight_decay, fields.fedprox_mu
    )
    payload = {
        "algorithm": fields.algorithm,
        "batch_size": fields.batch_size,
        "fedprox_mu": fields.fedprox_mu,
        "learning_rate": fields.learning_rate,
        "local_epochs": fields.local_epochs,
        "momentum": fields.momentum,
        "weight_decay": fields.weight_decay,
    }
    return _sha256_hex_prefixed(TRAINING_CONFIG_DOMAIN_SEPARATION_PREFIX, payload)


def _sha256_hex_prefixed(prefix: bytes, payload: dict[str, Any]) -> str:
    body = prefix + _canonical_json(payload).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def model_configuration_hash(fields: TaskConfigurationFields) -> str:
    manifest = fields.aggregation_manifest
    payload = {
        "aggregation_manifest": {
            "frozen_parameter_names": list(manifest.frozen_parameter_names),
            "personalized_parameter_names": list(manifest.personalized_parameter_names),
            "schema_hash": manifest.schema_hash,
            "shared_parameter_names": list(manifest.shared_parameter_names),
        },
        "model_manifest": [_tensor_json(tensor) for tensor in fields.model_manifest],
        "model_version": fields.model_version,
    }
    return _sha256_hex_prefixed(MODEL_CONFIG_DOMAIN_SEPARATION_PREFIX, payload)


def dataset_partition_hash(fields: TaskConfigurationFields) -> str:
    payload = {
        "client_id": fields.client_id,
        "dataset_reference": fields.dataset_reference,
        "run_id": fields.run_id,
    }
    return _sha256_hex_prefixed(DATASET_PARTITION_DOMAIN_SEPARATION_PREFIX, payload)


def privacy_configuration_hash(fields: TaskConfigurationFields) -> str:
    payload: dict[str, Any] = {"sample_level_dp_active": fields.sample_level_dp_active}
    if fields.sample_level_dp_active:
        privacy = fields.sample_level_privacy
        if privacy is None:
            raise CoordinatorTaskSigningError(
                "sample_level_dp_active is true but sample_level_privacy is absent"
            )
        _reject_non_finite(
            privacy.noise_multiplier,
            privacy.max_grad_norm,
            privacy.target_delta,
            privacy.epsilon_budget,
        )
        payload.update(
            {
                "accountant": privacy.accountant,
                "epsilon_budget": privacy.epsilon_budget,
                "max_grad_norm": privacy.max_grad_norm,
                "noise_multiplier": privacy.noise_multiplier,
                "poisson_sampling": privacy.poisson_sampling,
                "sample_budget_policy": privacy.sample_budget_policy,
                "target_delta": privacy.target_delta,
            }
        )
    return _sha256_hex_prefixed(PRIVACY_CONFIG_DOMAIN_SEPARATION_PREFIX, payload)


def personalization_configuration_hash(fields: TaskConfigurationFields) -> str:
    manifest = fields.aggregation_manifest
    payload = {
        "frozen_parameter_names": list(manifest.frozen_parameter_names),
        "personalized_parameter_names": list(manifest.personalized_parameter_names),
    }
    return _sha256_hex_prefixed(
        PERSONALIZATION_CONFIG_DOMAIN_SEPARATION_PREFIX, payload
    )


def secure_aggregation_configuration_hash(fields: TaskConfigurationFields) -> str:
    """Must byte-for-byte match coordinator_task_signing.cpp's
    secure_aggregation_configuration_hash. A sibling hash, like
    personalization_configuration_hash -- never folded into
    task_payload_hash."""
    binding = fields.secure_aggregation
    if not binding.secure_aggregation_active:
        payload: dict[str, Any] = {"secure_aggregation_active": False}
        return _sha256_hex_prefixed(
            SECURE_AGGREGATION_TASK_BINDING_DOMAIN_SEPARATION_PREFIX, payload
        )
    _reject_non_finite(
        binding.key_advertisement_deadline_unix_s,
        binding.masked_update_deadline_unix_s,
        binding.session_expiry_unix_s,
        binding.max_absolute_update_bound,
    )
    payload = {
        "key_advertisement_deadline_unix_s": binding.key_advertisement_deadline_unix_s,
        "masked_update_deadline_unix_s": binding.masked_update_deadline_unix_s,
        "max_absolute_update_bound": binding.max_absolute_update_bound,
        "max_aggregate_bound": binding.max_aggregate_bound,
        "max_client_weight": binding.max_client_weight,
        "minimum_cohort_size": binding.minimum_cohort_size,
        "privacy_incompatibility_reason": binding.privacy_incompatibility_reason,
        "privacy_mode_compatible": binding.privacy_mode_compatible,
        "protocol_version": binding.protocol_version,
        "provider": binding.provider,
        "secure_aggregation_active": True,
        "session_configuration_hash": binding.session_configuration_hash,
        "session_expiry_unix_s": binding.session_expiry_unix_s,
        "session_id": binding.session_id,
        "tensor_chunk_size": binding.tensor_chunk_size,
    }
    return _sha256_hex_prefixed(
        SECURE_AGGREGATION_TASK_BINDING_DOMAIN_SEPARATION_PREFIX, payload
    )


def secure_user_level_dp_configuration_hash(fields: TaskConfigurationFields) -> str:
    """Must byte-for-byte match coordinator_task_signing.cpp's
    secure_user_level_dp_configuration_hash. A second sibling hash,
    same convention as secure_aggregation_configuration_hash above."""
    binding = fields.secure_aggregation
    if not binding.secure_user_level_dp_active:
        payload: dict[str, Any] = {"secure_user_level_dp_active": False}
        return _sha256_hex_prefixed(
            SECURE_USER_LEVEL_DP_CONFIG_DOMAIN_SEPARATION_PREFIX, payload
        )
    _reject_non_finite(
        binding.secure_user_level_clip_norm,
        binding.secure_user_level_quantization_margin,
        binding.secure_user_level_effective_sensitivity,
        binding.secure_user_level_noise_multiplier,
        binding.secure_user_level_target_delta,
        binding.secure_user_level_max_epsilon,
    )
    payload = {
        "secure_user_level_adjacency_model": binding.secure_user_level_adjacency_model,
        "secure_user_level_clip_norm": binding.secure_user_level_clip_norm,
        "secure_user_level_dp_active": True,
        "secure_user_level_effective_sensitivity": (
            binding.secure_user_level_effective_sensitivity
        ),
        "secure_user_level_fixed_weight": binding.secure_user_level_fixed_weight,
        "secure_user_level_max_epsilon": binding.secure_user_level_max_epsilon,
        "secure_user_level_noise_multiplier": (
            binding.secure_user_level_noise_multiplier
        ),
        "secure_user_level_quantization_margin": (
            binding.secure_user_level_quantization_margin
        ),
        "secure_user_level_sampling_assumption": (
            binding.secure_user_level_sampling_assumption
        ),
        "secure_user_level_target_delta": binding.secure_user_level_target_delta,
    }
    return _sha256_hex_prefixed(
        SECURE_USER_LEVEL_DP_CONFIG_DOMAIN_SEPARATION_PREFIX, payload
    )


def task_payload_hash(fields: TaskConfigurationFields) -> str:
    _reject_non_finite(
        fields.learning_rate, fields.momentum, fields.weight_decay, fields.fedprox_mu
    )
    payload = {
        "attempt": fields.attempt,
        "dataset_partition_hash": dataset_partition_hash(fields),
        "lease_expires_at": fields.lease_expires_at,
        "lease_id": fields.lease_id,
        "model_configuration_hash": model_configuration_hash(fields),
        "privacy_configuration_hash": privacy_configuration_hash(fields),
        "round_id": fields.round_id,
        "task_available": True,
        "task_id": fields.task_id,
        "training_configuration_hash": training_configuration_hash(fields),
    }
    return _sha256_hex_prefixed(TASK_PAYLOAD_DOMAIN_SEPARATION_PREFIX, payload)


@dataclass(slots=True, frozen=True)
class SignedCoordinatorTaskFields:
    """The 17 metadata fields of fl.coordinator.v1.SignedCoordinatorTask
    (excluding `signature` itself) -- see docs/signed-coordinator-tasks.md."""

    coordinator_signing_key_id: str
    worker_id: str
    task_id: str
    lease_id: str
    attempt: int
    issued_at: float
    expires_at: float
    nonce: str
    sequence_number: int
    training_configuration_hash: str
    model_configuration_hash: str
    dataset_partition_hash: str
    privacy_configuration_hash: str
    personalization_configuration_hash: str
    task_payload_hash: str
    secure_aggregation_configuration_hash: str = ""
    secure_user_level_dp_configuration_hash: str = ""
    schema_version: int = SCHEMA_VERSION


def coordinator_task_signing_bytes(fields: SignedCoordinatorTaskFields) -> bytes:
    """Must byte-for-byte match coordinator_task_signing.cpp's
    coordinator_task_signing_bytes."""
    payload = {
        "attempt": fields.attempt,
        "coordinator_signing_key_id": fields.coordinator_signing_key_id,
        "dataset_partition_hash": fields.dataset_partition_hash,
        "expires_at": fields.expires_at,
        "issued_at": fields.issued_at,
        "lease_id": fields.lease_id,
        "model_configuration_hash": fields.model_configuration_hash,
        "nonce": fields.nonce,
        "personalization_configuration_hash": fields.personalization_configuration_hash,
        "privacy_configuration_hash": fields.privacy_configuration_hash,
        "schema_version": fields.schema_version,
        "secure_aggregation_configuration_hash": (
            fields.secure_aggregation_configuration_hash
        ),
        "secure_user_level_dp_configuration_hash": (
            fields.secure_user_level_dp_configuration_hash
        ),
        "sequence_number": fields.sequence_number,
        "task_id": fields.task_id,
        "task_payload_hash": fields.task_payload_hash,
        "training_configuration_hash": fields.training_configuration_hash,
        "worker_id": fields.worker_id,
    }
    return TASK_DOMAIN_SEPARATION_PREFIX + _canonical_json(payload).encode("utf-8")


def verify_coordinator_task_signature(
    fields: SignedCoordinatorTaskFields, signature_hex: str, public_key_hex: str
) -> bool:
    """Real Ed25519 verification (PyNaCl) -- never a stub. Returns
    False (never raises) for a malformed signature/key so callers can
    fold this into a single structured rejection reason."""
    try:
        verify_key = nacl.signing.VerifyKey(bytes.fromhex(public_key_hex))
        verify_key.verify(
            coordinator_task_signing_bytes(fields), bytes.fromhex(signature_hex)
        )
        return True
    except (nacl.exceptions.BadSignatureError, ValueError, nacl.exceptions.CryptoError):
        return False
