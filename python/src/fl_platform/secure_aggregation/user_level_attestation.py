"""Signed user-level privacy attestation -- Secure User-Level
Differential Privacy Runtime slice, Work Areas I/J. The Python
(worker-side) counterpart to
cpp/coordinator/src/signed_envelope_verifier.cpp's
verify_user_level_privacy_attestation: this module builds and signs
the same self-contained structure that file independently verifies.

Self-contained, not SignedWorkerEnvelope-wrapped (unlike
MaskedClientUpdate/SecureAggregationKeyAdvertisement): the attestation
carries its own signing_key_id/payload_hash/signature fields, mirroring
fl.coordinator.v1.SignedCoordinatorTask's self-signed shape rather than
the envelope-wrapping pattern masked_update.py uses. See
docs/secure-user-level-dp-semantics.md for the full mechanism this
attestation is evidence for -- and its limits (it is evidence of
configured worker behavior, never cryptographic proof of correct
clipping).

Deliberately excludes: the unclipped norm, the clipped norm, the
clipping factor, whether clipping actually occurred, any clear tensor
statistic, dataset sample count, raw privacy noise. See this module's
own field list -- none of those fields exist here, by design, not
oversight.
"""

from __future__ import annotations

import hashlib
import json
import math
import time as _time
from dataclasses import dataclass, replace
from typing import Any

from fl_platform.security.signing_identity import WorkerSigningIdentity

SCHEMA_VERSION = 1

ATTESTATION_SIGNING_PREFIX = b"FL_PLATFORM_SECURE_USER_LEVEL_DP_ATTESTATION_V1\x00"


class UserLevelPrivacyAttestationError(RuntimeError):
    """Raised on a hashing failure (NaN/Inf field) while building an
    attestation -- never on a verification rejection (this module never
    verifies; the coordinator does, in C++)."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_non_finite(*values: float) -> None:
    for value in values:
        if not math.isfinite(value):
            raise UserLevelPrivacyAttestationError(
                "cannot hash/sign an attestation containing a NaN or infinite value"
            )


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class UserLevelPrivacyAttestationFields:
    """Mirrors fl.worker.v1.SignedUserLevelPrivacyAttestation field-for-
    field. ``signing_key_id``/``payload_hash``/``signature`` are filled
    in by build_signed_user_level_privacy_attestation -- never set
    directly by a caller."""

    worker_id: str
    client_id: str
    run_id: str
    round_id: int
    task_id: str
    session_id: str
    model_version: str
    privacy_mode: int
    privacy_configuration_hash: str
    clip_norm: float
    effective_sensitivity: float
    clipping_strategy: str
    fixed_weight: int
    fixed_point_profile_hash: str
    tensor_manifest_hash: str
    provider: int
    operation_completed: bool
    issued_at: float
    expires_at: float
    signing_key_id: str = ""
    payload_hash: str = ""
    signature: str = ""
    schema_version: int = SCHEMA_VERSION


def user_level_privacy_attestation_payload_hash_input(
    fields: UserLevelPrivacyAttestationFields,
) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    user_level_privacy_attestation_payload_hash_input. Covers every
    content field EXCEPT signing_key_id/payload_hash/signature
    themselves."""
    _reject_non_finite(
        fields.clip_norm,
        fields.effective_sensitivity,
        fields.issued_at,
        fields.expires_at,
    )
    payload = {
        "clip_norm": fields.clip_norm,
        "client_id": fields.client_id,
        "clipping_strategy": fields.clipping_strategy,
        "effective_sensitivity": fields.effective_sensitivity,
        "expires_at": fields.expires_at,
        "fixed_point_profile_hash": fields.fixed_point_profile_hash,
        "fixed_weight": fields.fixed_weight,
        "issued_at": fields.issued_at,
        "model_version": fields.model_version,
        "operation_completed": fields.operation_completed,
        "privacy_configuration_hash": fields.privacy_configuration_hash,
        "privacy_mode": fields.privacy_mode,
        "provider": fields.provider,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "schema_version": fields.schema_version,
        "session_id": fields.session_id,
        "task_id": fields.task_id,
        "tensor_manifest_hash": fields.tensor_manifest_hash,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


def user_level_privacy_attestation_signing_bytes(
    fields: UserLevelPrivacyAttestationFields,
) -> bytes:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    (anonymous-namespace) user_level_privacy_attestation_signing_bytes.
    Canonical JSON of every metadata field INCLUDING payload_hash and
    signing_key_id, EXCLUDING signature -- mirrors
    coordinator_task_signing_bytes's identical "sign everything but the
    signature itself" convention for a self-contained signed
    structure."""
    payload = {
        "clip_norm": fields.clip_norm,
        "client_id": fields.client_id,
        "clipping_strategy": fields.clipping_strategy,
        "effective_sensitivity": fields.effective_sensitivity,
        "expires_at": fields.expires_at,
        "fixed_point_profile_hash": fields.fixed_point_profile_hash,
        "fixed_weight": fields.fixed_weight,
        "issued_at": fields.issued_at,
        "model_version": fields.model_version,
        "operation_completed": fields.operation_completed,
        "payload_hash": fields.payload_hash,
        "privacy_configuration_hash": fields.privacy_configuration_hash,
        "privacy_mode": fields.privacy_mode,
        "provider": fields.provider,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "schema_version": fields.schema_version,
        "session_id": fields.session_id,
        "signing_key_id": fields.signing_key_id,
        "task_id": fields.task_id,
        "tensor_manifest_hash": fields.tensor_manifest_hash,
        "worker_id": fields.worker_id,
    }
    return ATTESTATION_SIGNING_PREFIX + _canonical_json(payload).encode("utf-8")


def build_signed_user_level_privacy_attestation(
    *,
    worker_id: str,
    client_id: str,
    run_id: str,
    round_id: int,
    task_id: str,
    session_id: str,
    model_version: str,
    privacy_mode: int,
    privacy_configuration_hash: str,
    clip_norm: float,
    effective_sensitivity: float,
    fixed_point_profile_hash: str,
    tensor_manifest_hash: str,
    provider: int,
    operation_completed: bool,
    signing_identity: WorkerSigningIdentity,
    clipping_strategy: str = "global_l2",
    fixed_weight: int = 1,
    issued_at: float | None = None,
    expires_at_seconds_from_now: float = 300.0,
) -> UserLevelPrivacyAttestationFields:
    """Work Area I: builds and signs a real
    SignedUserLevelPrivacyAttestation. Deliberately takes no unclipped
    norm, clipped norm, or clipping factor as input -- this function
    cannot leak what it is never given."""
    now = issued_at if issued_at is not None else _time.time()
    expires_at = now + expires_at_seconds_from_now
    unsigned = UserLevelPrivacyAttestationFields(
        worker_id=worker_id,
        client_id=client_id,
        run_id=run_id,
        round_id=round_id,
        task_id=task_id,
        session_id=session_id,
        model_version=model_version,
        privacy_mode=privacy_mode,
        privacy_configuration_hash=privacy_configuration_hash,
        clip_norm=clip_norm,
        effective_sensitivity=effective_sensitivity,
        clipping_strategy=clipping_strategy,
        fixed_weight=fixed_weight,
        fixed_point_profile_hash=fixed_point_profile_hash,
        tensor_manifest_hash=tensor_manifest_hash,
        provider=provider,
        operation_completed=operation_completed,
        issued_at=now,
        expires_at=expires_at,
        signing_key_id=signing_identity.key_id,
    )
    payload_hash = sha256_hex(
        user_level_privacy_attestation_payload_hash_input(unsigned)
    )
    with_hash = replace(unsigned, payload_hash=payload_hash)
    signing_bytes = user_level_privacy_attestation_signing_bytes(with_hash)
    signature = signing_identity.sign(signing_bytes).hex()
    return replace(with_hash, signature=signature)


__all__ = [
    "ATTESTATION_SIGNING_PREFIX",
    "UserLevelPrivacyAttestationError",
    "UserLevelPrivacyAttestationFields",
    "build_signed_user_level_privacy_attestation",
    "sha256_hex",
    "user_level_privacy_attestation_payload_hash_input",
    "user_level_privacy_attestation_signing_bytes",
]
