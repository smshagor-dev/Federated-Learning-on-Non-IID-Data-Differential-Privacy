"""Signed worker envelopes -- Signed Client Results and Worker Lifecycle
Enforcement slice. The Python counterpart to
cpp/coordinator/src/signed_envelope_verifier.cpp: that file verifies
what this module signs. See docs/signed-client-results.md and
docs/payload-hashing.md.

Canonicalization contract (must byte-for-byte match the C++ side --
see docs/canonical-security-serialization.md): every signed structure
is encoded via ``json.dumps(payload, sort_keys=True,
separators=(",", ":"), ensure_ascii=True)``, exactly as
capability_statement.py and every prior signed structure in this
project already do. Domain separation for the envelope signature
itself uses the fixed prefix ``b"fl.worker.v1.SignedWorkerEnvelope\\x00"``
-- see ``ENVELOPE_DOMAIN_SEPARATION_PREFIX`` below, which must match
signed_envelope_verifier.cpp's ``kDomainSeparationPrefix`` exactly.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from dataclasses import dataclass
from typing import Any

from fl_platform.security.signing_identity import WorkerSigningIdentity

SCHEMA_VERSION = 1

ENVELOPE_DOMAIN_SEPARATION_PREFIX = b"fl.worker.v1.SignedWorkerEnvelope\x00"

# Mirrors fl.worker.v1.SignedWorkerEnvelope.MessageType's wire integer
# values exactly (proto/worker/worker.proto) -- these are NOT re-derived
# from the generated protobuf enum at import time on purpose: this
# module must remain importable (for hashing/testing) even in a context
# where the generated bindings are not on sys.path yet (see
# fl_platform.rpc.ensure_generated_on_path).
MESSAGE_TYPE_WORKER_HEARTBEAT = 1
MESSAGE_TYPE_CLIENT_RESULT = 5
MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD = 7
MESSAGE_TYPE_KEY_ROTATION_REQUEST = 11
MESSAGE_TYPE_SECURITY_EVENT_BATCH = 12
# Secure Cohort Handshake and Signed Roster Runtime slice
# (docs/secure-cohort-handshake-foundation.md).
MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT = 13
# Masked Update Runtime and No-Dropout Secure FedAvg Finalization slice
# (docs/secure-aggregation-masked-update.md).
MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE = 14

MESSAGE_STREAM_HEARTBEAT = 2
MESSAGE_STREAM_CLIENT_RESULT = 4
MESSAGE_STREAM_PRIVACY_RECORD = 5
MESSAGE_STREAM_KEY_MANAGEMENT = 7
MESSAGE_STREAM_SECURITY_EVENTS = 8
MESSAGE_STREAM_SECURE_AGGREGATION = 9


class SignedEnvelopeError(RuntimeError):
    """Raised on any construction/signing failure -- never on a
    verification rejection (this module never verifies; only the C++
    coordinator does)."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_non_finite(*values: float) -> None:
    for value in values:
        if not math.isfinite(value):
            raise SignedEnvelopeError(
                "cannot sign a payload containing a NaN or infinite value"
            )


def _reject_negative(**named_values: float) -> None:
    for name, value in named_values.items():
        if value < 0.0:
            raise SignedEnvelopeError(f"'{name}' cannot be negative (got {value})")


@dataclass(slots=True, frozen=True)
class EnvelopeFields:
    """The 16 fields of fl.worker.v1.SignedWorkerEnvelope, in their
    canonical (pre-signature) form. Optional identifiers not used by a
    given message_type must be left at their canonical empty value
    ("" / 0) -- see docs/signed-worker-envelopes.md."""

    message_type: int
    worker_id: str
    message_stream: int
    sequence_number: int
    signing_key_id: str
    payload_hash: str
    run_id: str = ""
    round_id: int = 0
    task_id: str = ""
    client_id: str = ""
    model_version: str = ""
    issued_at: float = 0.0
    expires_at: float = 0.0
    nonce: str = ""
    schema_version: int = SCHEMA_VERSION


def canonical_envelope_metadata_json(fields: EnvelopeFields) -> str:
    """Must byte-for-byte match
    signed_envelope_verifier.cpp's canonical_envelope_metadata_json."""
    payload = {
        "client_id": fields.client_id,
        "expires_at": fields.expires_at,
        "issued_at": fields.issued_at,
        "message_stream": fields.message_stream,
        "message_type": fields.message_type,
        "model_version": fields.model_version,
        "nonce": fields.nonce,
        "payload_hash": fields.payload_hash,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "schema_version": fields.schema_version,
        "sequence_number": fields.sequence_number,
        "signing_key_id": fields.signing_key_id,
        "task_id": fields.task_id,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


def envelope_signing_bytes(fields: EnvelopeFields) -> bytes:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    envelope_signing_bytes."""
    return ENVELOPE_DOMAIN_SEPARATION_PREFIX + canonical_envelope_metadata_json(
        fields
    ).encode("utf-8")


@dataclass(slots=True, frozen=True)
class SignedEnvelope:
    fields: EnvelopeFields
    signature_hex: str


def sign_envelope(
    fields: EnvelopeFields, identity: WorkerSigningIdentity
) -> SignedEnvelope:
    if fields.expires_at <= fields.issued_at:
        raise SignedEnvelopeError(
            "expires_at must be set to a real time strictly after issued_at"
        )
    if fields.signing_key_id != identity.key_id:
        raise SignedEnvelopeError(
            f"fields.signing_key_id ({fields.signing_key_id}) does not match "
            f"the signing identity's own key_id ({identity.key_id})"
        )
    signature = identity.sign(envelope_signing_bytes(fields))
    return SignedEnvelope(fields=fields, signature_hex=signature.hex())


def heartbeat_payload_hash_input(
    worker_id: str, status: int, current_task_id: str, issued_at: float
) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    heartbeat_payload_hash_input."""
    return _canonical_json(
        {
            "current_task_id": current_task_id,
            "status": status,
            "timestamp": issued_at,
            "worker_id": worker_id,
        }
    )


def client_result_payload_hash_input(
    *,
    run_id: str,
    round_id: int,
    task_id: str,
    client_id: str,
    worker_id: str,
    model_version: str,
    algorithm: str,
    sample_count: int,
    step_count: int,
    update_norm: float,
    completion_timestamp: str,
    nonce: str,
    tensor_manifest: list[dict[str, Any]],
    training_metrics: list[dict[str, Any]] | None = None,
    personalization_metrics: dict[str, Any] | None = None,
    privacy_record: dict[str, Any] | None = None,
    privacy_record_payload_hash: str = "",
) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    client_result_payload_hash_input.

    `tensor_manifest` entries must each have exactly the keys
    {name, shape, dtype, byte_length, checksum} -- see
    docs/payload-hashing.md. Canonically sorted by name here (matching
    the C++ side's sort) so the caller does not need to pre-sort.
    Rejects (raises SignedEnvelopeError) if update_norm or any metric/
    privacy-record value is NaN/infinite, or if a tensor descriptor has
    an empty name -- the same checks the C++ verifier makes
    independently; failing fast here means a worker never even attempts
    to sign and submit an unhashable result.

    `privacy_record_payload_hash` (Privacy Record Authenticity slice):
    the SHA-256 payload_hash of this submission's independently-signed
    SignedSamplePrivacyRecord envelope, if any -- binds the outer
    client-result signature to a second, independent signature, so a
    tampered or missing signed privacy record is detectable from the
    outer signature alone. Empty string when no privacy record envelope
    accompanies this submission (the canonical-empty convention every
    other optional field here already uses).
    """
    training_metrics = training_metrics or []

    _reject_non_finite(update_norm)
    for metric in training_metrics:
        _reject_non_finite(float(metric["value"]))
    for tensor in tensor_manifest:
        if not tensor.get("name"):
            raise SignedEnvelopeError(
                "a tensor descriptor with an empty name cannot be hashed"
            )

    def personalization_json() -> dict[str, Any]:
        if personalization_metrics is None:
            return {}
        _reject_non_finite(
            float(personalization_metrics.get("global_local_accuracy", 0.0)),
            float(personalization_metrics.get("personalized_local_accuracy", 0.0)),
            float(personalization_metrics.get("global_local_loss", 0.0)),
            float(personalization_metrics.get("personalized_local_loss", 0.0)),
            float(personalization_metrics.get("personalized_improvement", 0.0)),
        )
        return {
            "algorithm": personalization_metrics.get("algorithm", ""),
            "global_local_accuracy": float(
                personalization_metrics.get("global_local_accuracy", 0.0)
            ),
            "global_local_loss": float(
                personalization_metrics.get("global_local_loss", 0.0)
            ),
            "has_personalized_model": bool(
                personalization_metrics.get("has_personalized_model", False)
            ),
            "personalized_improvement": float(
                personalization_metrics.get("personalized_improvement", 0.0)
            ),
            "personalized_local_accuracy": float(
                personalization_metrics.get("personalized_local_accuracy", 0.0)
            ),
            "personalized_local_loss": float(
                personalization_metrics.get("personalized_local_loss", 0.0)
            ),
            "personalized_model_version": int(
                personalization_metrics.get("personalized_model_version", 0)
            ),
            "sample_count": int(personalization_metrics.get("sample_count", 0)),
        }

    def privacy_json() -> dict[str, Any]:
        if privacy_record is None:
            return {}
        _reject_non_finite(
            float(privacy_record.get("epsilon", 0.0)),
            float(privacy_record.get("delta", 0.0)),
            float(privacy_record.get("noise_multiplier", 0.0)),
            float(privacy_record.get("sample_rate", 0.0)),
        )
        return {
            "accountant": int(privacy_record.get("accountant", 0)),
            "client_id": privacy_record.get("client_id", ""),
            "delta": float(privacy_record.get("delta", 0.0)),
            "entry_id": privacy_record.get("entry_id", ""),
            "epsilon": float(privacy_record.get("epsilon", 0.0)),
            "noise_multiplier": float(privacy_record.get("noise_multiplier", 0.0)),
            "privacy_record_payload_hash": privacy_record_payload_hash,
            "round_id": int(privacy_record.get("round_id", 0)),
            "run_id": privacy_record.get("run_id", ""),
            "sample_rate": float(privacy_record.get("sample_rate", 0.0)),
            "steps": int(privacy_record.get("steps", 0)),
        }

    sorted_tensors = sorted(tensor_manifest, key=lambda tensor: tensor["name"])
    sorted_metrics = sorted(training_metrics, key=lambda metric: metric["name"])

    payload = {
        "algorithm": algorithm,
        "client_id": client_id,
        "completion_timestamp": completion_timestamp,
        "model_version": model_version,
        "nonce": nonce,
        "personalization_metrics": personalization_json(),
        "privacy_record": privacy_json(),
        "round_id": round_id,
        "run_id": run_id,
        "sample_count": sample_count,
        "schema_version": SCHEMA_VERSION,
        "step_count": step_count,
        "task_id": task_id,
        "tensor_manifest": [
            {
                "byte_length": int(tensor["byte_length"]),
                "checksum": tensor["checksum"],
                "dtype": tensor["dtype"],
                "name": tensor["name"],
                "shape": [int(dim) for dim in tensor["shape"]],
            }
            for tensor in sorted_tensors
        ],
        "training_metrics": [
            {"name": metric["name"], "value": float(metric["value"])}
            for metric in sorted_metrics
        ],
        "update_norm": update_norm,
        "worker_id": worker_id,
    }
    return _canonical_json(payload)


@dataclass(slots=True, frozen=True)
class SamplePrivacyRecordFields:
    """The 27 domain fields of fl.privacy.v1.SignedSamplePrivacyRecord --
    see docs/signed-privacy-records.md. Deliberately does NOT include
    nonce/sequence_number/signing_key_id/payload_hash/signature: those
    cryptographic-envelope fields are carried by the SignedWorkerEnvelope
    that wraps this record's payload_hash (message_type =
    MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD), the same reuse decision already
    made for client results -- see this module's client_result_payload_hash_input.
    """

    worker_id: str
    run_id: str
    round_id: int
    task_id: str
    client_id: str
    model_version: str
    algorithm: str
    privacy_mode: int
    accountant_type: int
    accountant_step: int
    epsilon: float
    delta: float
    noise_multiplier: float
    max_grad_norm: float
    sample_rate: float
    expected_batch_size: int
    local_epochs: int
    configuration_hash: str
    accountant_state_hash: str
    budget_target_epsilon: float
    budget_target_delta: float
    budget_policy: int
    budget_decision: str
    secure_random_required: bool
    secure_random_available: bool
    secure_random_provider: str
    schema_version: int = SCHEMA_VERSION


def sample_privacy_record_payload_hash_input(fields: SamplePrivacyRecordFields) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    sample_privacy_record_payload_hash_input. Rejects (raises
    SignedEnvelopeError) if epsilon/delta/noise_multiplier/max_grad_norm/
    sample_rate/budget_target_epsilon/budget_target_delta is NaN/
    infinite or negative -- the same checks the C++ verifier makes
    independently.
    """
    _reject_non_finite(
        fields.epsilon,
        fields.delta,
        fields.noise_multiplier,
        fields.max_grad_norm,
        fields.sample_rate,
        fields.budget_target_epsilon,
        fields.budget_target_delta,
    )
    _reject_negative(
        epsilon=fields.epsilon,
        delta=fields.delta,
        noise_multiplier=fields.noise_multiplier,
        max_grad_norm=fields.max_grad_norm,
        sample_rate=fields.sample_rate,
    )
    payload = {
        "accountant_state_hash": fields.accountant_state_hash,
        "accountant_step": fields.accountant_step,
        "accountant_type": fields.accountant_type,
        "algorithm": fields.algorithm,
        "budget_decision": fields.budget_decision,
        "budget_policy": fields.budget_policy,
        "budget_target_delta": fields.budget_target_delta,
        "budget_target_epsilon": fields.budget_target_epsilon,
        "client_id": fields.client_id,
        "configuration_hash": fields.configuration_hash,
        "delta": fields.delta,
        "epsilon": fields.epsilon,
        "expected_batch_size": fields.expected_batch_size,
        "local_epochs": fields.local_epochs,
        "max_grad_norm": fields.max_grad_norm,
        "model_version": fields.model_version,
        "noise_multiplier": fields.noise_multiplier,
        "privacy_mode": fields.privacy_mode,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "sample_rate": fields.sample_rate,
        "schema_version": fields.schema_version,
        "secure_random_available": fields.secure_random_available,
        "secure_random_provider": fields.secure_random_provider,
        "secure_random_required": fields.secure_random_required,
        "task_id": fields.task_id,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


def sample_privacy_configuration_hash(
    *,
    noise_multiplier: float,
    max_grad_norm: float,
    target_delta: float,
    epsilon_budget: float,
    sample_budget_policy: int,
    poisson_sampling: bool,
) -> str:
    """SHA-256 hex digest binding the SampleLevelDPConfig fields a
    signed privacy record's `configuration_hash` field asserts --
    detects a worker signing a record under a privacy configuration
    different from the one the coordinator actually assigned for this
    task. Deliberately narrow (only the fields SampleLevelDPConfig
    actually carries -- see proto/privacy/privacy.proto)."""
    payload = {
        "epsilon_budget": epsilon_budget,
        "max_grad_norm": max_grad_norm,
        "noise_multiplier": noise_multiplier,
        "poisson_sampling": poisson_sampling,
        "sample_budget_policy": sample_budget_policy,
        "target_delta": target_delta,
    }
    return sha256_hex(_canonical_json(payload))


@dataclass(slots=True, frozen=True)
class WorkerKeyRotationFields:
    """The 7 domain fields of fl.worker.v1.WorkerKeyRotationPayload --
    see docs/key-rotation.md. Like SamplePrivacyRecordFields, does NOT
    carry nonce/sequence_number/signing_key_id/payload_hash/signature:
    those cryptographic-envelope fields are carried by the
    SignedWorkerEnvelope wrapping this payload's hash (message_type =
    MESSAGE_TYPE_KEY_ROTATION_REQUEST, message_stream =
    MESSAGE_STREAM_KEY_MANAGEMENT), signed by the CURRENT key named in
    current_signing_key_id -- the same envelope-reuse decision already
    made twice (client results, privacy records)."""

    worker_id: str
    current_signing_key_id: str
    new_signing_key_id: str
    new_public_key_hex: str
    new_key_expires_at_unix_s: float = 0.0
    requested_grace_period_seconds: float = 0.0
    schema_version: int = SCHEMA_VERSION


def rotation_payload_hash_input(fields: WorkerKeyRotationFields) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    rotation_payload_hash_input. Rejects (raises SignedEnvelopeError) if
    new_key_expires_at_unix_s/requested_grace_period_seconds is NaN/
    infinite, or if requested_grace_period_seconds is negative -- the
    same checks the C++ verifier makes independently."""
    _reject_non_finite(
        fields.new_key_expires_at_unix_s, fields.requested_grace_period_seconds
    )
    if fields.requested_grace_period_seconds < 0.0:
        raise SignedEnvelopeError("requested_grace_period_seconds cannot be negative")
    payload = {
        "current_signing_key_id": fields.current_signing_key_id,
        "new_key_expires_at_unix_s": fields.new_key_expires_at_unix_s,
        "new_public_key_hex": fields.new_public_key_hex,
        "new_signing_key_id": fields.new_signing_key_id,
        "requested_grace_period_seconds": fields.requested_grace_period_seconds,
        "schema_version": fields.schema_version,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


@dataclass(slots=True, frozen=True)
class SecureAggregationKeyAdvertisementFields:
    """The 10 domain fields of
    fl.worker.v1.SecureAggregationKeyAdvertisement -- Secure Cohort
    Handshake and Signed Roster Runtime slice
    (docs/secure-cohort-handshake-foundation.md). Like
    WorkerKeyRotationFields, does NOT carry nonce/sequence_number/
    signing_key_id/payload_hash/signature: those cryptographic-envelope
    fields are carried by the SignedWorkerEnvelope wrapping this
    payload's hash (message_type =
    MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT, message_stream =
    MESSAGE_STREAM_SECURE_AGGREGATION)."""

    session_id: str
    run_id: str
    round_id: int
    model_version: str
    worker_id: str
    client_id: str
    ephemeral_public_key_x25519: str
    public_key_fingerprint: str
    issued_at: float = 0.0
    expires_at: float = 0.0
    schema_version: int = SCHEMA_VERSION


def secure_aggregation_key_advertisement_payload_hash_input(
    fields: SecureAggregationKeyAdvertisementFields,
) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    secure_aggregation_key_advertisement_payload_hash_input. Rejects
    (raises SignedEnvelopeError) if issued_at/expires_at is NaN/
    infinite -- the same check the C++ verifier makes independently."""
    _reject_non_finite(fields.issued_at, fields.expires_at)
    payload = {
        "client_id": fields.client_id,
        "ephemeral_public_key_x25519": fields.ephemeral_public_key_x25519,
        "expires_at": fields.expires_at,
        "issued_at": fields.issued_at,
        "model_version": fields.model_version,
        "public_key_fingerprint": fields.public_key_fingerprint,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "schema_version": fields.schema_version,
        "session_id": fields.session_id,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


@dataclass(slots=True, frozen=True)
class MaskedTensorFields:
    """Mirrors fl.worker.v1.SecureAggregationMaskedTensor -- one
    already-masked tensor, ring values only, never a clear value."""

    tensor_name: str
    masked_values: tuple[int, ...]
    checksum: str


@dataclass(slots=True, frozen=True)
class EncodingStatisticsFields:
    """Mirrors fl.worker.v1.SecureAggregationEncodingStatistics."""

    total_elements: int = 0
    max_quantization_error: float = 0.0
    mean_quantization_error: float = 0.0


@dataclass(slots=True, frozen=True)
class MaskedClientUpdateFields:
    """The domain fields of fl.worker.v1.MaskedClientUpdate -- Masked
    Update Runtime and No-Dropout Secure FedAvg Finalization slice
    (docs/secure-aggregation-masked-update.md). Like
    SecureAggregationKeyAdvertisementFields, does NOT carry nonce/
    sequence_number/signing_key_id/payload_hash/signature: those
    cryptographic-envelope fields are carried by the SignedWorkerEnvelope
    wrapping this payload's hash (message_type =
    MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE, message_stream =
    MESSAGE_STREAM_SECURE_AGGREGATION). Deliberately carries no clear
    tensor values, clear model delta, individual clear weight, ephemeral
    private key, shared secret, derived mask key, or mask stream --
    masked_tensors/masked_weight are the only update-shaped fields, both
    already-masked ring values (Work Area P's cleartext-prohibition
    requirement, enforced structurally by this shape, not merely by
    handler logic)."""

    provider: int
    protocol_version: int
    session_id: str
    run_id: str
    round_id: int
    task_id: str
    lease_id: str
    attempt: int
    worker_id: str
    client_id: str
    model_version: str
    cohort_commitment: str
    tensor_manifest_hash: str
    fixed_point_profile_hash: str
    frozen_roster_payload_hash: str
    cryptographic_profile_hash: str
    masked_tensors: tuple[MaskedTensorFields, ...]
    masked_weight: int
    masked_weight_checksum: str
    encoding_statistics: EncodingStatisticsFields
    sample_privacy_record_hash: str = ""
    issued_at: float = 0.0
    expires_at: float = 0.0
    schema_version: int = SCHEMA_VERSION
    # Secure Adaptive Clipping with Private Indicator Aggregation slice:
    # a single ring value (0 or 1 cast directly, never fixed-point
    # scaled) plus its own checksum, mirroring masked_weight/
    # masked_weight_checksum's exact shape. Zero-valued/empty on every
    # non-adaptive-clipping submission.
    masked_clipping_indicator: int = 0
    masked_clipping_indicator_checksum: str = ""


def masked_client_update_payload_hash_input(fields: MaskedClientUpdateFields) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    masked_client_update_payload_hash_input. Work Area L: fixed field
    ordering (alphabetical keys, matching every other payload-hash-input
    function in this module), canonical tensor ordering (masked_tensors
    sorted by tensor_name -- NOT insertion order, since a worker's own
    iteration order over its model's tensors is an implementation detail
    that must not affect the hash), canonical integer encoding (ring
    values are plain JSON integers -- exact, no float-serialization
    ambiguity), domain-separated implicitly by this function's own
    identity (never called for anything but this one payload shape).
    Rejects (raises SignedEnvelopeError) on NaN/infinite
    quantization-error statistics or timestamps."""
    _reject_non_finite(
        fields.issued_at,
        fields.expires_at,
        fields.encoding_statistics.max_quantization_error,
        fields.encoding_statistics.mean_quantization_error,
    )
    sorted_tensors = sorted(fields.masked_tensors, key=lambda t: t.tensor_name)
    payload = {
        "attempt": fields.attempt,
        "client_id": fields.client_id,
        "cohort_commitment": fields.cohort_commitment,
        "cryptographic_profile_hash": fields.cryptographic_profile_hash,
        "encoding_statistics": {
            "max_quantization_error": (
                fields.encoding_statistics.max_quantization_error
            ),
            "mean_quantization_error": (
                fields.encoding_statistics.mean_quantization_error
            ),
            "total_elements": fields.encoding_statistics.total_elements,
        },
        "expires_at": fields.expires_at,
        "fixed_point_profile_hash": fields.fixed_point_profile_hash,
        "frozen_roster_payload_hash": fields.frozen_roster_payload_hash,
        "issued_at": fields.issued_at,
        "lease_id": fields.lease_id,
        "masked_clipping_indicator": fields.masked_clipping_indicator,
        "masked_clipping_indicator_checksum": fields.masked_clipping_indicator_checksum,
        "masked_tensors": [
            {
                "checksum": tensor.checksum,
                "masked_values": list(tensor.masked_values),
                "tensor_name": tensor.tensor_name,
            }
            for tensor in sorted_tensors
        ],
        "masked_weight": fields.masked_weight,
        "masked_weight_checksum": fields.masked_weight_checksum,
        "model_version": fields.model_version,
        "protocol_version": fields.protocol_version,
        "provider": fields.provider,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "sample_privacy_record_hash": fields.sample_privacy_record_hash,
        "schema_version": fields.schema_version,
        "session_id": fields.session_id,
        "task_id": fields.task_id,
        "tensor_manifest_hash": fields.tensor_manifest_hash,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


@dataclass(slots=True, frozen=True)
class WorkerSecurityEventFields:
    """The 18 fields of fl.worker.v1.WorkerSecurityEventPayload -- see
    docs/security-event-centralization.md. `safe_details` must already
    be a plain ``dict[str, str]``; this module does not itself enforce
    the shared kSecurityEventMaxDetailKeys/Value bounds (the coordinator
    re-validates every event against those bounds on ingest and skips,
    rather than trusts, anything out of bounds)."""

    event_type: str
    severity: str
    timestamp: str
    actor_type: str
    safe_actor_id: str
    subject_type: str
    safe_subject_id: str
    outcome: str
    source_component: str = ""
    run_id: str = ""
    round_id: int = 0
    task_id: str = ""
    safe_signing_key_id: str = ""
    request_id: str = ""
    trace_id: str = ""
    reason_code: str = ""
    safe_details: dict[str, str] | None = None
    schema_version: int = SCHEMA_VERSION


def _worker_security_event_json(fields: WorkerSecurityEventFields) -> dict[str, Any]:
    return {
        "actor_type": fields.actor_type,
        "event_type": fields.event_type,
        "outcome": fields.outcome,
        "reason_code": fields.reason_code,
        "request_id": fields.request_id,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "safe_actor_id": fields.safe_actor_id,
        "safe_details": dict(fields.safe_details or {}),
        "safe_signing_key_id": fields.safe_signing_key_id,
        "safe_subject_id": fields.safe_subject_id,
        "schema_version": fields.schema_version,
        "severity": fields.severity,
        "source_component": fields.source_component,
        "subject_type": fields.subject_type,
        "task_id": fields.task_id,
        "timestamp": fields.timestamp,
        "trace_id": fields.trace_id,
    }


@dataclass(slots=True, frozen=True)
class WorkerSecurityEventBatchFields:
    """The 4 fields of fl.worker.v1.SignedWorkerSecurityEventBatch --
    see docs/security-event-centralization.md. Deliberately does NOT
    carry nonce/sequence_number/signing_key_id/payload_hash/signature:
    those cryptographic-envelope fields are carried by the
    SignedWorkerEnvelope wrapping this batch's hash (message_type =
    MESSAGE_TYPE_SECURITY_EVENT_BATCH, message_stream =
    MESSAGE_STREAM_SECURITY_EVENTS), the same envelope-reuse decision
    already made for client results/privacy records/key rotation."""

    worker_id: str
    events: tuple[WorkerSecurityEventFields, ...]
    queue_depth_hint: int = 0
    schema_version: int = SCHEMA_VERSION


def security_event_batch_payload_hash_input(
    fields: WorkerSecurityEventBatchFields,
) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    security_event_batch_payload_hash_input. Events are hashed in the
    exact order given -- NOT re-sorted, unlike client_result's tensor/
    metric lists -- submission order is part of what gets signed."""
    payload = {
        "events": [_worker_security_event_json(event) for event in fields.events],
        "queue_depth_hint": fields.queue_depth_hint,
        "schema_version": fields.schema_version,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def make_nonce() -> str:
    return uuid.uuid4().hex


def now() -> float:
    return time.time()
