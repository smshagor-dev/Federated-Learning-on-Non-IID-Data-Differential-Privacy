from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any

from .nonce import NonceReplayGuard


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(slots=True)
class SignedEnvelope:
    payload: dict[str, Any]
    nonce: str
    trace_id: str
    issued_at: str
    signature_hex: str
    algorithm: str = "hmac-sha256"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EnvelopeValidationResult:
    valid: bool
    reason: str


def sign_envelope(
    payload: dict[str, Any],
    nonce: str,
    trace_id: str,
    issued_at: str,
    secret: str,
    metadata: dict[str, Any] | None = None,
) -> SignedEnvelope:
    if not secret:
        raise ValueError("secret must not be empty")
    body = {
        "payload": payload,
        "nonce": nonce,
        "trace_id": trace_id,
        "issued_at": issued_at,
    }
    signature = hmac.new(
        secret.encode("utf-8"), _canonical_bytes(body), sha256
    ).hexdigest()
    return SignedEnvelope(
        payload=payload,
        nonce=nonce,
        trace_id=trace_id,
        issued_at=issued_at,
        signature_hex=signature,
        metadata=metadata or {},
    )


def verify_envelope(
    envelope: SignedEnvelope,
    secret: str,
    nonce_guard: NonceReplayGuard | None = None,
    scope: str = "default",
) -> EnvelopeValidationResult:
    if envelope.algorithm != "hmac-sha256":
        return EnvelopeValidationResult(False, "unsupported signature algorithm")
    body = {
        "payload": envelope.payload,
        "nonce": envelope.nonce,
        "trace_id": envelope.trace_id,
        "issued_at": envelope.issued_at,
    }
    expected = hmac.new(
        secret.encode("utf-8"), _canonical_bytes(body), sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, envelope.signature_hex):
        return EnvelopeValidationResult(False, "signature mismatch")
    if nonce_guard is not None and not nonce_guard.register(scope, envelope.nonce):
        return EnvelopeValidationResult(False, "replayed nonce")
    return EnvelopeValidationResult(True, "ok")
