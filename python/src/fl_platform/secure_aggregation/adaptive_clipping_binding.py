"""Signed adaptive clipping binding -- Secure Adaptive Clipping with
Private Indicator Aggregation slice, Work Area H. The Python
(worker-side) counterpart to
cpp/coordinator/src/signed_envelope_verifier.cpp's
verify_adaptive_clipping_binding: this module builds and signs the
same self-contained structure that file independently verifies.

Self-contained, not SignedWorkerEnvelope-wrapped -- mirrors
user_level_attestation.py's identical self-signed shape exactly, not
the envelope-wrapping pattern masked_update.py uses. See
docs/secure-adaptive-clipping-semantics.md section 15 for the full
mechanism this binding is evidence for -- and its limits (it is
evidence of configuration consistency and message integrity, never
cryptographic proof that the indicator value is truthful).

Deliberately excludes: the clear indicator value, the unclipped norm,
the clipped norm, the clipping factor, whether clipping occurred for
this specific worker. See this module's own field list -- none of
those fields exist here, by design, not oversight.
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

ADAPTIVE_CLIPPING_BINDING_SIGNING_PREFIX = (
    b"FL_PLATFORM_SECURE_ADAPTIVE_CLIPPING_ATTESTATION_V1\x00"
)


class AdaptiveClippingBindingError(RuntimeError):
    """Raised on a hashing failure (NaN/Inf field) while building a
    binding -- never on a verification rejection (this module never
    verifies; the coordinator does, in C++)."""


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_non_finite(*values: float) -> None:
    for value in values:
        if not math.isfinite(value):
            raise AdaptiveClippingBindingError(
                "cannot hash/sign an adaptive clipping binding containing a NaN or "
                "infinite value"
            )


def sha256_hex(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


@dataclass(slots=True, frozen=True)
class AdaptiveClippingBindingFields:
    """Mirrors fl.worker.v1.SignedAdaptiveClippingBinding field-for-
    field. ``signing_key_id``/``payload_hash``/``signature`` are filled
    in by build_signed_adaptive_clipping_binding -- never set directly
    by a caller."""

    worker_id: str
    client_id: str
    run_id: str
    round_id: int
    task_id: str
    session_id: str
    model_version: str
    adaptive_configuration_hash: str
    clip_state_step_count: int
    current_clip_bound: float
    provider: int
    operation_completed: bool
    issued_at: float
    expires_at: float
    signing_key_id: str = ""
    payload_hash: str = ""
    signature: str = ""
    schema_version: int = SCHEMA_VERSION


def adaptive_clipping_binding_payload_hash_input(
    fields: AdaptiveClippingBindingFields,
) -> str:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    adaptive_clipping_binding_payload_hash_input. Covers every content
    field EXCEPT signing_key_id/payload_hash/signature themselves."""
    _reject_non_finite(fields.current_clip_bound, fields.issued_at, fields.expires_at)
    payload = {
        "adaptive_configuration_hash": fields.adaptive_configuration_hash,
        "client_id": fields.client_id,
        "clip_state_step_count": fields.clip_state_step_count,
        "current_clip_bound": fields.current_clip_bound,
        "expires_at": fields.expires_at,
        "issued_at": fields.issued_at,
        "model_version": fields.model_version,
        "operation_completed": fields.operation_completed,
        "provider": fields.provider,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "schema_version": fields.schema_version,
        "session_id": fields.session_id,
        "task_id": fields.task_id,
        "worker_id": fields.worker_id,
    }
    return _canonical_json(payload)


def adaptive_clipping_binding_signing_bytes(
    fields: AdaptiveClippingBindingFields,
) -> bytes:
    """Must byte-for-byte match signed_envelope_verifier.cpp's
    (anonymous-namespace) adaptive_clipping_binding_signing_bytes.
    Canonical JSON of every metadata field INCLUDING payload_hash and
    signing_key_id, EXCLUDING signature -- mirrors
    user_level_privacy_attestation_signing_bytes's identical "sign
    everything but the signature itself" convention."""
    payload = {
        "adaptive_configuration_hash": fields.adaptive_configuration_hash,
        "client_id": fields.client_id,
        "clip_state_step_count": fields.clip_state_step_count,
        "current_clip_bound": fields.current_clip_bound,
        "expires_at": fields.expires_at,
        "issued_at": fields.issued_at,
        "model_version": fields.model_version,
        "operation_completed": fields.operation_completed,
        "payload_hash": fields.payload_hash,
        "provider": fields.provider,
        "round_id": fields.round_id,
        "run_id": fields.run_id,
        "schema_version": fields.schema_version,
        "session_id": fields.session_id,
        "signing_key_id": fields.signing_key_id,
        "task_id": fields.task_id,
        "worker_id": fields.worker_id,
    }
    return ADAPTIVE_CLIPPING_BINDING_SIGNING_PREFIX + _canonical_json(payload).encode(
        "utf-8"
    )


def build_signed_adaptive_clipping_binding(
    *,
    worker_id: str,
    client_id: str,
    run_id: str,
    round_id: int,
    task_id: str,
    session_id: str,
    model_version: str,
    adaptive_configuration_hash: str,
    clip_state_step_count: int,
    current_clip_bound: float,
    provider: int,
    operation_completed: bool,
    signing_identity: WorkerSigningIdentity,
    issued_at: float | None = None,
    expires_at_seconds_from_now: float = 300.0,
) -> AdaptiveClippingBindingFields:
    """Work Area H: builds and signs a real
    SignedAdaptiveClippingBinding. Deliberately takes no clear indicator
    value, unclipped norm, clipped norm, or clipping factor as input --
    this function cannot leak what it is never given."""
    now = issued_at if issued_at is not None else _time.time()
    expires_at = now + expires_at_seconds_from_now
    unsigned = AdaptiveClippingBindingFields(
        worker_id=worker_id,
        client_id=client_id,
        run_id=run_id,
        round_id=round_id,
        task_id=task_id,
        session_id=session_id,
        model_version=model_version,
        adaptive_configuration_hash=adaptive_configuration_hash,
        clip_state_step_count=clip_state_step_count,
        current_clip_bound=current_clip_bound,
        provider=provider,
        operation_completed=operation_completed,
        issued_at=now,
        expires_at=expires_at,
        signing_key_id=signing_identity.key_id,
    )
    payload_hash = sha256_hex(adaptive_clipping_binding_payload_hash_input(unsigned))
    with_hash = replace(unsigned, payload_hash=payload_hash)
    signing_bytes = adaptive_clipping_binding_signing_bytes(with_hash)
    signature = signing_identity.sign(signing_bytes).hex()
    return replace(with_hash, signature=signature)


__all__ = [
    "ADAPTIVE_CLIPPING_BINDING_SIGNING_PREFIX",
    "AdaptiveClippingBindingError",
    "AdaptiveClippingBindingFields",
    "adaptive_clipping_binding_payload_hash_input",
    "adaptive_clipping_binding_signing_bytes",
    "build_signed_adaptive_clipping_binding",
    "sha256_hex",
]
