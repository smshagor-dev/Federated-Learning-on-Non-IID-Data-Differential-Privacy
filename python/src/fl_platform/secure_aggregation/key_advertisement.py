"""Ephemeral X25519 key advertisement construction and frozen-roster
verification -- Secure Cohort Handshake and Signed Roster Runtime slice
(docs/secure-cohort-handshake-foundation.md), Work items 6/7/12.

The worker-side counterpart to
cpp/coordinator/src/coordinator_service.cpp's
AdvertiseSecureAggregationKey/GetFrozenCohortRoster handlers and
cpp/coordinator/src/secure_aggregation_session_manager.cpp's
compute_frozen_cohort_roster_signing_bytes. Ephemeral key generation
reuses fl_platform.secure_aggregation.crypto.generate_x25519_keypair
directly (already real, tested, from the prior slice) -- no new
key-generation code is written here.
"""

from __future__ import annotations

import time
from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol

import nacl.exceptions
import nacl.signing

from fl_platform.secure_aggregation.crypto import generate_x25519_keypair
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_SECURE_AGGREGATION,
    MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT,
    EnvelopeFields,
    SecureAggregationKeyAdvertisementFields,
    SignedEnvelope,
    secure_aggregation_key_advertisement_payload_hash_input,
    sign_envelope,
)
from fl_platform.security.signed_envelope import (
    sha256_hex as envelope_sha256_hex,
)
from fl_platform.security.signing_identity import WorkerSigningIdentity


class SecureCohortHandshakeError(RuntimeError):
    """Raised on any construction/verification failure in this module."""


@dataclass(slots=True, frozen=True)
class EphemeralKeyPair:
    """A freshly generated X25519 keypair for exactly one secure
    aggregation session. The caller (WorkerService) is responsible for
    keeping `private_key_raw` in session-scoped memory only and
    destroying it (see docs/secure-cohort-handshake-foundation.md's
    restatement of the prior slice's ephemeral-key-lifecycle
    requirements) -- this module never persists it."""

    private_key_raw: bytes
    public_key_raw: bytes


def generate_ephemeral_keypair() -> EphemeralKeyPair:
    private_key_raw, public_key_raw = generate_x25519_keypair()
    return EphemeralKeyPair(
        private_key_raw=private_key_raw, public_key_raw=public_key_raw
    )


def public_key_fingerprint(public_key_raw: bytes) -> str:
    """First 8 raw bytes of the public key, hex-encoded -- same
    convention as coordinator_key_id_for (coordinator_signing_identity.cpp)
    and _key_id_for (signing_identity.py)."""
    return public_key_raw[:8].hex()


def build_signed_key_advertisement(
    *,
    session_id: str,
    run_id: str,
    round_id: int,
    model_version: str,
    worker_id: str,
    client_id: str,
    ephemeral_public_key: bytes,
    signing_identity: WorkerSigningIdentity,
    sequence_number: int,
    nonce: str,
    issued_at: float | None = None,
    expires_at_seconds_from_now: float = 300.0,
) -> tuple[SecureAggregationKeyAdvertisementFields, SignedEnvelope]:
    """Builds and signs a SecureAggregationKeyAdvertisement -- the
    worker-side counterpart to
    AdvertiseSecureAggregationKey's verification pipeline. Returns the
    domain fields (the caller maps these onto the real generated
    protobuf message) and the signed envelope wrapping their hash.
    """
    now = issued_at if issued_at is not None else time.time()
    expires_at = now + expires_at_seconds_from_now

    advertisement_fields = SecureAggregationKeyAdvertisementFields(
        session_id=session_id,
        run_id=run_id,
        round_id=round_id,
        model_version=model_version,
        worker_id=worker_id,
        client_id=client_id,
        ephemeral_public_key_x25519=ephemeral_public_key.hex(),
        public_key_fingerprint=public_key_fingerprint(ephemeral_public_key),
        issued_at=now,
        expires_at=expires_at,
    )
    payload_hash = envelope_sha256_hex(
        secure_aggregation_key_advertisement_payload_hash_input(advertisement_fields)
    )
    envelope_fields = EnvelopeFields(
        message_type=MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT,
        worker_id=worker_id,
        message_stream=MESSAGE_STREAM_SECURE_AGGREGATION,
        sequence_number=sequence_number,
        signing_key_id=signing_identity.key_id,
        payload_hash=payload_hash,
        run_id=run_id,
        round_id=round_id,
        client_id=client_id,
        model_version=model_version,
        issued_at=now,
        expires_at=expires_at,
        nonce=nonce,
    )
    signed_envelope = sign_envelope(envelope_fields, signing_identity)
    return advertisement_fields, signed_envelope


def _format_double(value: float) -> str:
    # Best-effort reproduction of std::ostream's default
    # operator<<(double) formatting (6 significant digits, switching
    # between fixed and scientific notation the same way printf's %g
    # does) -- see compute_frozen_cohort_roster_signing_bytes
    # (secure_aggregation_session_manager.cpp), which never applies
    # std::setprecision before streaming freeze_timestamp/expiry.
    # Python's "%g"/"{:g}" formatting follows the same underlying C
    # library convention, so this matches for the real-world
    # unix-timestamp magnitudes this protocol actually uses --
    # documented as a best-effort convention, not a byte-exact
    # guarantee for arbitrary float values.
    return f"{value:g}"


class FrozenCohortParticipantLike(Protocol):
    """Structural shape of fl.coordinator.v1.FrozenCohortParticipant --
    real generated protobuf messages satisfy this without any
    import-time dependency on the generated bindings (see
    FrozenCohortRosterLike's own docstring for why)."""

    participant_index: int
    worker_id: str
    client_id: str
    ephemeral_public_key_x25519: str
    public_key_fingerprint: str


class FrozenCohortRosterLike(Protocol):
    """Structural shape of fl.coordinator.v1.FrozenCohortRoster --
    frozen_cohort_roster_signing_bytes/verify_frozen_cohort_roster only
    need this shape, not the real generated protobuf message class, so
    callers that don't otherwise need grpc/protobuf (Docker/CI-only
    dependencies in this project) can still call these two functions
    with any object of this shape (including a plain test double)."""

    schema_version: int
    protocol_version: int
    provider: int
    session_id: str
    run_id: str
    round_id: int
    model_version: str
    participants: Collection[FrozenCohortParticipantLike]
    tensor_manifest_hash: str
    fixed_point_profile_hash: str
    cryptographic_profile_hash: str
    cohort_commitment: str
    freeze_timestamp: float
    expiry: float
    coordinator_signing_key_id: str
    payload_hash: str
    signature: str


def frozen_cohort_roster_signing_bytes(roster: FrozenCohortRosterLike) -> bytes:
    """Must match compute_frozen_cohort_roster_signing_bytes
    (secure_aggregation_session_manager.cpp) byte-for-byte."""
    parts: list[str] = ["FL_PLATFORM_SECURE_AGGREGATION_FROZEN_ROSTER_V1", "\x1e"]
    parts.append(f"schema_version={roster.schema_version}\x1e")
    parts.append(f"protocol_version={roster.protocol_version}\x1e")
    parts.append(f"provider={int(roster.provider)}\x1e")
    parts.append(f"session_id={roster.session_id}\x1e")
    parts.append(f"run_id={roster.run_id}\x1e")
    parts.append(f"round_id={roster.round_id}\x1e")
    parts.append(f"model_version={roster.model_version}\x1e")
    parts.append(f"participant_count={len(roster.participants)}\x1e")
    for participant in roster.participants:
        parts.append(
            f"participant[{participant.participant_index}]={participant.worker_id}|"
            f"{participant.client_id}|{participant.ephemeral_public_key_x25519}|"
            f"{participant.public_key_fingerprint}\x1e"
        )
    parts.append(f"tensor_manifest_hash={roster.tensor_manifest_hash}\x1e")
    parts.append(f"fixed_point_profile_hash={roster.fixed_point_profile_hash}\x1e")
    parts.append(f"cryptographic_profile_hash={roster.cryptographic_profile_hash}\x1e")
    parts.append(f"cohort_commitment={roster.cohort_commitment}\x1e")
    parts.append(f"freeze_timestamp={_format_double(roster.freeze_timestamp)}\x1e")
    parts.append(f"expiry={_format_double(roster.expiry)}\x1e")
    return "".join(parts).encode("utf-8")


def verify_frozen_cohort_roster(
    roster: FrozenCohortRosterLike,
    *,
    own_worker_id: str,
    own_client_id: str,
    own_public_key_raw: bytes,
    expected_session_id: str,
    expected_run_id: str,
    expected_round_id: int,
    expected_model_version: str,
    trusted_coordinator_public_key_hex: str,
) -> None:
    """Work item 12: full worker-side frozen-roster verification.
    Raises SecureCohortHandshakeError on any failure -- never returns a
    bool, matching this project's established
    CoordinatorTaskRejectedError-style "raise, never silently proceed"
    convention for security-critical verification. On success, returns
    None and the caller may proceed to the (Tier 2, not this slice)
    masked-training phase.
    """
    if roster.session_id != expected_session_id:
        raise SecureCohortHandshakeError(
            f"frozen roster session_id ({roster.session_id!r}) does not match the "
            f"expected session ({expected_session_id!r})"
        )
    if roster.run_id != expected_run_id or roster.round_id != expected_round_id:
        raise SecureCohortHandshakeError(
            "frozen roster run_id/round_id does not match the expected task binding"
        )
    if roster.model_version != expected_model_version:
        raise SecureCohortHandshakeError(
            "frozen roster model_version does not match the expected task binding"
        )
    if not roster.coordinator_signing_key_id or not roster.signature:
        raise SecureCohortHandshakeError(
            "frozen roster is not signed -- refusing to trust an unsigned roster"
        )

    signing_bytes = frozen_cohort_roster_signing_bytes(roster)
    try:
        verify_key = nacl.signing.VerifyKey(
            bytes.fromhex(trusted_coordinator_public_key_hex)
        )
        verify_key.verify(signing_bytes, bytes.fromhex(roster.signature))
    except (
        nacl.exceptions.BadSignatureError,
        ValueError,
        nacl.exceptions.CryptoError,
    ) as error:
        raise SecureCohortHandshakeError(
            "frozen roster signature verification failed against the trusted "
            "coordinator public key"
        ) from error

    own_public_key_hex = own_public_key_raw.hex()
    own_entry = next(
        (p for p in roster.participants if p.worker_id == own_worker_id), None
    )
    if own_entry is None:
        raise SecureCohortHandshakeError(
            f"own worker_id ({own_worker_id!r}) is not present in the frozen "
            "roster -- refusing to proceed"
        )
    if own_entry.client_id != own_client_id:
        raise SecureCohortHandshakeError(
            "frozen roster's own participant entry has a mismatched client_id"
        )
    if own_entry.ephemeral_public_key_x25519 != own_public_key_hex:
        raise SecureCohortHandshakeError(
            "frozen roster's own participant entry does not match the public key "
            "this worker actually advertised -- the coordinator may have recorded "
            "the wrong key, or the roster has been tampered with"
        )

    seen_worker_ids: set[str] = set()
    for participant in roster.participants:
        if participant.worker_id in seen_worker_ids:
            raise SecureCohortHandshakeError(
                "frozen roster contains a duplicate participant worker_id "
                f"({participant.worker_id!r})"
            )
        seen_worker_ids.add(participant.worker_id)
        raw_key = bytes.fromhex(participant.ephemeral_public_key_x25519)
        if len(raw_key) != 32:
            raise SecureCohortHandshakeError(
                f"frozen roster participant {participant.worker_id!r} has an "
                "invalid public key length"
            )
        if raw_key == b"\x00" * 32:
            raise SecureCohortHandshakeError(
                f"frozen roster participant {participant.worker_id!r} has an "
                "all-zero public key"
            )
