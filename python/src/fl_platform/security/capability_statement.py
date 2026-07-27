"""Signed worker capability statements — Secure Transport and Worker
Identity Hardening slice, Work Package I. See
docs/signed-capabilities.md and docs/canonical-security-serialization.md.

Replaces the *unsigned*, self-reported ``WorkerPrivacyCapabilities``
wire message (see docs/worker-privacy-capabilities.md) with a
cryptographically signed statement — but this only authenticates that
the holder of ``signing_key_id`` made this specific claim at this
specific time; it does not prove the claim is true (a compromised or
misconfigured worker can still sign a false statement). Hardware or
software attestation, which would narrow that gap, remains deferred —
see docs/secure-aggregation-threat-model.md.

Canonical serialization (Work Package J's minimal slice actually
implemented this pass): every signed field is encoded via
``json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=True)``
— explicit lexicographic key ordering (never relying on dict insertion
order, which is deterministic *within* one Python process but not
something other languages' JSON encoders necessarily agree with),
compact separators (no incidental whitespace differences), and
``ensure_ascii=True`` (non-ASCII characters escaped identically
everywhere, avoiding UTF-8-normalization disagreements). This is not
yet cross-language-parity-tested against a C++ or Go implementation of
the same rule — see known-limitations.md; the C++/Go verification side
of this feature is deferred, so no such implementation exists yet to
test parity against.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import nacl.exceptions
import nacl.signing
from cryptography.hazmat.primitives import hashes as crypto_hashes

from fl_platform.security.signing_identity import WorkerSigningIdentity

SCHEMA_VERSION = 1

#: Fields that participate in the signature, in the exact set the
#: closure gate specifies (payload_hash and signature themselves are
#: computed *from* these, so are never included in the signed payload).
_PAYLOAD_FIELDS = (
    "schema_version",
    "worker_id",
    "software_version",
    "build_id",
    "supported_algorithms",
    "supported_privacy_modes",
    "opacus_version",
    "secure_random_available",
    "supported_accountants",
    "supported_clipping_modes",
    "supported_models",
    "supported_model_schema_hashes",
    "maximum_task_bytes",
    "maximum_private_batch_size",
    "cpu_count",
    "gpu_available",
    "gpu_count",
    "issued_at",
    "expires_at",
    "nonce",
    "signing_key_id",
)


class CapabilityStatementError(RuntimeError):
    """Raised on any construction, signing, or verification failure.
    Verification failures always carry a stable, specific reason (see
    :class:`VerificationResult`) — this exception is for construction
    errors (e.g. a required field missing), not signature rejection,
    which is reported via :func:`verify_capability_statement`'s return
    value rather than an exception, so a caller can distinguish
    "malformed request" from "this specific statement is untrustworthy"
    without parsing exception messages.
    """


@dataclass(slots=True, frozen=True)
class CapabilityStatementPayload:
    """The unsigned content of a capability statement — exactly the
    closure-gate's required field list."""

    worker_id: str
    software_version: str
    build_id: str
    supported_algorithms: tuple[str, ...]
    supported_privacy_modes: tuple[str, ...]
    opacus_version: str
    secure_random_available: bool
    supported_accountants: tuple[str, ...]
    supported_clipping_modes: tuple[str, ...]
    supported_models: tuple[str, ...]
    supported_model_schema_hashes: tuple[str, ...]
    maximum_task_bytes: int
    maximum_private_batch_size: int
    cpu_count: int
    gpu_available: bool
    gpu_count: int
    signing_key_id: str
    issued_at: float = field(default_factory=time.time)
    expires_at: float = 0.0  # 0 -- caller must set a real expiry before signing
    nonce: str = field(default_factory=lambda: uuid.uuid4().hex)


@dataclass(slots=True, frozen=True)
class SignedCapabilityStatement:
    payload: dict[str, Any]
    payload_hash: str
    signature: str  # hex-encoded

    def to_wire_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "payload_hash": self.payload_hash,
            "signature": self.signature,
        }


@dataclass(slots=True, frozen=True)
class VerificationResult:
    valid: bool
    reason: str


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    """The one canonicalization rule this pass actually implements —
    see this module's docstring. Every caller (signing and verification
    both) must go through this function; two independently-written
    encoders for the same data is exactly how signature verification
    silently breaks."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _payload_dict(payload: CapabilityStatementPayload) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "worker_id": payload.worker_id,
        "software_version": payload.software_version,
        "build_id": payload.build_id,
        "supported_algorithms": list(payload.supported_algorithms),
        "supported_privacy_modes": list(payload.supported_privacy_modes),
        "opacus_version": payload.opacus_version,
        "secure_random_available": payload.secure_random_available,
        "supported_accountants": list(payload.supported_accountants),
        "supported_clipping_modes": list(payload.supported_clipping_modes),
        "supported_models": list(payload.supported_models),
        "supported_model_schema_hashes": list(payload.supported_model_schema_hashes),
        "maximum_task_bytes": payload.maximum_task_bytes,
        "maximum_private_batch_size": payload.maximum_private_batch_size,
        "cpu_count": payload.cpu_count,
        "gpu_available": payload.gpu_available,
        "gpu_count": payload.gpu_count,
        "issued_at": payload.issued_at,
        "expires_at": payload.expires_at,
        "nonce": payload.nonce,
        "signing_key_id": payload.signing_key_id,
    }


def sign_capability_statement(
    payload: CapabilityStatementPayload, identity: WorkerSigningIdentity
) -> SignedCapabilityStatement:
    """Signs ``payload`` with ``identity``'s Ed25519 key. Raises
    :class:`CapabilityStatementError` if ``expires_at`` was left unset
    (0) or is not strictly after ``issued_at`` — a statement with no
    real expiry would defeat the entire point of the "capability
    expiry" requirement.
    """
    if payload.expires_at <= payload.issued_at:
        raise CapabilityStatementError(
            "expires_at must be set to a real time strictly after issued_at"
        )
    if payload.signing_key_id != identity.key_id:
        raise CapabilityStatementError(
            f"payload.signing_key_id ({payload.signing_key_id}) does not match the "
            f"signing identity's own key_id ({identity.key_id})"
        )

    payload_dict = _payload_dict(payload)
    canonical = _canonical_bytes(payload_dict)
    payload_hash = crypto_hashes.Hash(crypto_hashes.SHA256())
    payload_hash.update(canonical)
    digest = payload_hash.finalize().hex()

    signature = identity.sign(canonical).hex()
    return SignedCapabilityStatement(
        payload=payload_dict, payload_hash=digest, signature=signature
    )


def verify_capability_statement(
    statement: SignedCapabilityStatement,
    verify_key: nacl.signing.VerifyKey,
    *,
    now: float | None = None,
) -> VerificationResult:
    """Verifies signature validity and expiry — the two checks this
    pass actually implements. Nonce replay protection, signing-key
    revocation checks, and worker-identity/certificate binding are
    listed in the closure-gate's full requirement set but require the
    coordinator-side identity registry (Work Package G, deferred this
    pass) to check against — this function alone cannot honestly claim
    to do them, so it does not.
    """
    if now is None:
        now = time.time()

    canonical = _canonical_bytes(statement.payload)
    recomputed_hash = crypto_hashes.Hash(crypto_hashes.SHA256())
    recomputed_hash.update(canonical)
    if recomputed_hash.finalize().hex() != statement.payload_hash:
        return VerificationResult(False, "payload_hash does not match the payload")

    try:
        signature_bytes = bytes.fromhex(statement.signature)
        verify_key.verify(canonical, signature_bytes)
    except (nacl.exceptions.BadSignatureError, ValueError):
        return VerificationResult(False, "invalid signature")

    expires_at = statement.payload.get("expires_at", 0)
    if not isinstance(expires_at, (int, float)) or now >= expires_at:
        return VerificationResult(False, "capability statement has expired")

    return VerificationResult(True, "ok")
