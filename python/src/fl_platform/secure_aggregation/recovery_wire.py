"""Authenticated wire-domain primitives for secure-aggregation recovery shares.

This module deliberately contains no gRPC/protobuf dependency. It defines the
canonical payload signed by the existing SignedWorkerEnvelope before a raw
Shamir share is admitted to the volatile threshold collector.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
from dataclasses import dataclass

from fl_platform.secure_aggregation.threshold_recovery import RecoveryShare
from fl_platform.security.signed_envelope import (
    EnvelopeFields,
    MESSAGE_STREAM_SECURE_AGGREGATION,
    SignedEnvelope,
    SignedEnvelopeError,
    sign_envelope,
)
from fl_platform.security.signing_identity import WorkerSigningIdentity

MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_SHARE = 15
RECOVERY_SHARE_SCHEMA_VERSION = 1
RECOVERY_FIELD_ID = "mersenne-521-v1"
MAX_RECOVERY_SHARE_HEX_LENGTH = 132
_RECOVERY_FIELD_PRIME = (1 << 521) - 1
_SAFE_BINDING = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,256}$")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_LOWER_HEX = re.compile(r"^[0-9a-f]+$")


class RecoveryWireError(RuntimeError):
    """Raised when a recovery-share payload is malformed or unbindable."""


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise RecoveryWireError("recovery timestamp must be finite")
    # C++ std::setprecision(17) + defaultfloat and Python .17g both emit
    # enough significant digits for round-trip binary64 representation.
    return format(value, ".17g")


def _validate_safe_binding(name: str, value: str) -> None:
    if not _SAFE_BINDING.fullmatch(value):
        raise RecoveryWireError(
            f"{name} must use the canonical printable ASCII identifier vocabulary"
        )


@dataclass(frozen=True, slots=True)
class RecoverySharePayload:
    session_id: str
    run_id: str
    round_id: int
    model_version: str
    cohort_commitment: str
    owner_worker_id: str
    holder_worker_id: str
    generation: int
    threshold: int
    total_shares: int
    share_index: int
    share_value_hex: str
    secret_digest: str
    secret_length: int
    field_id: str = RECOVERY_FIELD_ID
    issued_at: float = 0.0
    expires_at: float = 0.0
    schema_version: int = RECOVERY_SHARE_SCHEMA_VERSION

    def validate(self) -> None:
        for name, value in (
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("model_version", self.model_version),
            ("owner_worker_id", self.owner_worker_id),
            ("holder_worker_id", self.holder_worker_id),
        ):
            _validate_safe_binding(name, value)
        if self.owner_worker_id == self.holder_worker_id:
            raise RecoveryWireError("recovery share owner and holder must be different workers")
        if self.round_id < 0 or self.generation < 0:
            raise RecoveryWireError("round_id and generation must be non-negative")
        if not 2 <= self.threshold <= self.total_shares:
            raise RecoveryWireError("threshold must be in [2, total_shares]")
        if not 1 <= self.share_index <= self.total_shares:
            raise RecoveryWireError("share_index is outside the declared share set")
        if not 1 <= self.secret_length <= 64:
            raise RecoveryWireError("secret_length must be in [1, 64]")
        if self.field_id != RECOVERY_FIELD_ID:
            raise RecoveryWireError(f"unsupported recovery field {self.field_id!r}")
        if not _LOWER_HEX_64.fullmatch(self.cohort_commitment):
            raise RecoveryWireError("cohort_commitment must be lowercase SHA-256 hex")
        if not _LOWER_HEX_64.fullmatch(self.secret_digest):
            raise RecoveryWireError("secret_digest must be lowercase SHA-256 hex")
        if (
            not self.share_value_hex
            or len(self.share_value_hex) > MAX_RECOVERY_SHARE_HEX_LENGTH
            or not _LOWER_HEX.fullmatch(self.share_value_hex)
            or (len(self.share_value_hex) > 1 and self.share_value_hex.startswith("0"))
        ):
            raise RecoveryWireError("share_value_hex is not canonical lowercase field hex")
        if int(self.share_value_hex, 16) >= _RECOVERY_FIELD_PRIME:
            raise RecoveryWireError("share_value_hex is outside the recovery field")
        if not math.isfinite(self.issued_at) or not math.isfinite(self.expires_at):
            raise RecoveryWireError("issued_at/expires_at must be finite")
        if self.expires_at <= self.issued_at:
            raise RecoveryWireError("expires_at must be strictly after issued_at")
        if self.schema_version != RECOVERY_SHARE_SCHEMA_VERSION:
            raise RecoveryWireError("unsupported recovery share schema version")

    def to_recovery_share(self) -> RecoveryShare:
        self.validate()
        return RecoveryShare(
            session_id=self.session_id,
            owner_id=self.owner_worker_id,
            holder_id=self.holder_worker_id,
            generation=self.generation,
            threshold=self.threshold,
            total_shares=self.total_shares,
            index=self.share_index,
            value=int(self.share_value_hex, 16),
            secret_digest=self.secret_digest,
            secret_length=self.secret_length,
            field_id=self.field_id,
        )


def recovery_share_payload_hash_input(payload: RecoverySharePayload) -> str:
    """Cross-language canonical JSON hashed by SignedWorkerEnvelope."""
    payload.validate()
    # All string values are validated to a vocabulary that cannot contain JSON
    # quoting/backslash/control characters. Floats use explicit round-trip
    # binary64 formatting rather than language-specific JSON serializers.
    return (
        "{"
        f'"cohort_commitment":"{payload.cohort_commitment}",'
        f'"expires_at":{_canonical_float(payload.expires_at)},'
        f'"field_id":"{payload.field_id}",'
        f'"generation":{payload.generation},'
        f'"holder_worker_id":"{payload.holder_worker_id}",'
        f'"issued_at":{_canonical_float(payload.issued_at)},'
        f'"model_version":"{payload.model_version}",'
        f'"owner_worker_id":"{payload.owner_worker_id}",'
        f'"round_id":{payload.round_id},'
        f'"run_id":"{payload.run_id}",'
        f'"schema_version":{payload.schema_version},'
        f'"secret_digest":"{payload.secret_digest}",'
        f'"secret_length":{payload.secret_length},'
        f'"session_id":"{payload.session_id}",'
        f'"share_index":{payload.share_index},'
        f'"share_value_hex":"{payload.share_value_hex}",'
        f'"threshold":{payload.threshold},'
        f'"total_shares":{payload.total_shares}'
        "}"
    )


def payload_from_recovery_share(
    share: RecoveryShare,
    *,
    run_id: str,
    round_id: int,
    model_version: str,
    cohort_commitment: str,
    issued_at: float | None = None,
    expires_after_seconds: float = 300.0,
) -> RecoverySharePayload:
    if expires_after_seconds <= 0.0 or not math.isfinite(expires_after_seconds):
        raise RecoveryWireError("expires_after_seconds must be finite and positive")
    now = time.time() if issued_at is None else float(issued_at)
    payload = RecoverySharePayload(
        session_id=share.session_id,
        run_id=run_id,
        round_id=round_id,
        model_version=model_version,
        cohort_commitment=cohort_commitment,
        owner_worker_id=share.owner_id,
        holder_worker_id=share.holder_id,
        generation=share.generation,
        threshold=share.threshold,
        total_shares=share.total_shares,
        share_index=share.index,
        share_value_hex=format(share.value, "x"),
        secret_digest=share.secret_digest,
        secret_length=share.secret_length,
        field_id=share.field_id,
        issued_at=now,
        expires_at=now + expires_after_seconds,
    )
    payload.validate()
    return payload


def build_signed_recovery_share(
    payload: RecoverySharePayload,
    *,
    signing_identity: WorkerSigningIdentity,
    sequence_number: int,
    nonce: str,
) -> SignedEnvelope:
    if sequence_number < 1:
        raise RecoveryWireError("recovery sequence numbers start at 1")
    if not nonce:
        raise RecoveryWireError("recovery envelope nonce must not be empty")
    if payload.holder_worker_id != signing_identity.worker_id:
        raise RecoveryWireError("share holder must match the signing worker identity")
    payload_hash = hashlib.sha256(
        recovery_share_payload_hash_input(payload).encode("utf-8")
    ).hexdigest()
    fields = EnvelopeFields(
        message_type=MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_SHARE,
        worker_id=payload.holder_worker_id,
        run_id=payload.run_id,
        round_id=payload.round_id,
        model_version=payload.model_version,
        message_stream=MESSAGE_STREAM_SECURE_AGGREGATION,
        sequence_number=sequence_number,
        signing_key_id=signing_identity.key_id,
        payload_hash=payload_hash,
        issued_at=payload.issued_at,
        expires_at=payload.expires_at,
        nonce=nonce,
    )
    try:
        return sign_envelope(fields, signing_identity)
    except SignedEnvelopeError as exc:
        raise RecoveryWireError(str(exc)) from exc


__all__ = [
    "MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_SHARE",
    "RECOVERY_FIELD_ID",
    "RECOVERY_SHARE_SCHEMA_VERSION",
    "RecoverySharePayload",
    "RecoveryWireError",
    "build_signed_recovery_share",
    "payload_from_recovery_share",
    "recovery_share_payload_hash_input",
]
