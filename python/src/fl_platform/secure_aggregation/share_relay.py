"""End-to-end encrypted pre-dropout recovery-share relay primitives.

A secure-aggregation participant threshold-shares its session-scoped ephemeral
X25519 private key before masked-update submission. Each holder's Shamir share
is encrypted under an owner/holder X25519 pairwise secret and can therefore be
stored/forwarded by the coordinator without exposing the raw share value.

The coordinator-facing relay object contains ciphertext and signed metadata
only. The 66-byte Mersenne-521 field element is decrypted only by the intended
holder. This module is protobuf/gRPC independent so the cryptographic contract
can be tested without generated bindings.
"""

from __future__ import annotations

import hashlib
import math
import re
import secrets
import time
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from fl_platform.secure_aggregation.crypto import (
    CHACHA20_KEY_LENGTH,
    CHACHA20_NONCE_LENGTH,
    derive_purpose_key,
    derive_x25519_shared_secret,
)
from fl_platform.secure_aggregation.threshold_recovery import (
    FIELD_ID,
    RecoveryShare,
    make_recovery_receipt,
)
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_SECURE_AGGREGATION,
    EnvelopeFields,
    SignedEnvelope,
    sign_envelope,
)
from fl_platform.security.signing_identity import WorkerSigningIdentity

RELAY_SCHEMA_VERSION = 1
MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_RELAY = 16
HKDF_PURPOSE_RECOVERY_SHARE_RELAY = "recovery_share_relay_aead"
RELAY_FIELD_BYTES = 66
MAX_RELAY_CIPHERTEXT_BYTES = RELAY_FIELD_BYTES + 16  # Poly1305 tag
_SAFE_BINDING = re.compile(r"^[A-Za-z0-9._:/@+\-]{1,256}$")
_LOWER_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


class RecoveryShareRelayError(RuntimeError):
    """Raised when a recovery-share relay cannot be safely built or opened."""


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise RecoveryShareRelayError("relay timestamp must be finite")
    return format(value, ".17g")


def _validate_binding(name: str, value: str) -> None:
    if not _SAFE_BINDING.fullmatch(value):
        raise RecoveryShareRelayError(
            f"{name} must use the canonical printable ASCII identifier vocabulary"
        )


@dataclass(frozen=True, slots=True)
class EncryptedRecoveryShareRelay:
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
    secret_digest: str
    secret_length: int
    nonce_hex: str
    ciphertext_hex: str
    ciphertext_hash: str
    issued_at: float
    expires_at: float
    field_id: str = FIELD_ID
    schema_version: int = RELAY_SCHEMA_VERSION

    def validate(self) -> None:
        for name, value in (
            ("session_id", self.session_id),
            ("run_id", self.run_id),
            ("model_version", self.model_version),
            ("owner_worker_id", self.owner_worker_id),
            ("holder_worker_id", self.holder_worker_id),
        ):
            _validate_binding(name, value)
        if self.owner_worker_id == self.holder_worker_id:
            raise RecoveryShareRelayError(
                "relay owner and holder must be different workers"
            )
        if self.schema_version != RELAY_SCHEMA_VERSION:
            raise RecoveryShareRelayError("unsupported recovery relay schema version")
        if self.field_id != FIELD_ID:
            raise RecoveryShareRelayError("unsupported recovery relay field")
        if self.round_id < 0 or self.generation < 0:
            raise RecoveryShareRelayError(
                "round_id and generation must be non-negative"
            )
        if not 2 <= self.threshold <= self.total_shares:
            raise RecoveryShareRelayError("threshold must be in [2, total_shares]")
        if not 1 <= self.share_index <= self.total_shares:
            raise RecoveryShareRelayError(
                "share_index is outside the declared share set"
            )
        if not 1 <= self.secret_length <= 64:
            raise RecoveryShareRelayError("secret_length must be in [1, 64]")
        for name, value in (
            ("cohort_commitment", self.cohort_commitment),
            ("secret_digest", self.secret_digest),
            ("ciphertext_hash", self.ciphertext_hash),
        ):
            if not _LOWER_HEX_64.fullmatch(value):
                raise RecoveryShareRelayError(f"{name} must be lowercase SHA-256 hex")
        try:
            nonce = bytes.fromhex(self.nonce_hex)
            ciphertext = bytes.fromhex(self.ciphertext_hex)
        except ValueError as exc:
            raise RecoveryShareRelayError(
                "relay nonce/ciphertext is not canonical hex"
            ) from exc
        if self.nonce_hex != nonce.hex() or len(nonce) != CHACHA20_NONCE_LENGTH:
            raise RecoveryShareRelayError(
                "relay nonce must be canonical 12-byte lowercase hex"
            )
        if self.ciphertext_hex != ciphertext.hex():
            raise RecoveryShareRelayError(
                "relay ciphertext must be canonical lowercase hex"
            )
        if len(ciphertext) != MAX_RELAY_CIPHERTEXT_BYTES:
            raise RecoveryShareRelayError("relay ciphertext has an unexpected length")
        if not secrets.compare_digest(
            hashlib.sha256(ciphertext).hexdigest(), self.ciphertext_hash
        ):
            raise RecoveryShareRelayError(
                "relay ciphertext_hash does not match ciphertext"
            )
        if not math.isfinite(self.issued_at) or not math.isfinite(self.expires_at):
            raise RecoveryShareRelayError("relay timestamps must be finite")
        if self.expires_at <= self.issued_at:
            raise RecoveryShareRelayError(
                "relay expires_at must be strictly after issued_at"
            )


def relay_key_context(relay: EncryptedRecoveryShareRelay) -> str:
    """Context binding for the owner/holder AEAD key derivation."""
    relay.validate()
    return "\x1e".join(
        (
            f"schema_version={relay.schema_version}",
            f"session_id={relay.session_id}",
            f"run_id={relay.run_id}",
            f"round_id={relay.round_id}",
            f"model_version={relay.model_version}",
            f"cohort_commitment={relay.cohort_commitment}",
            f"owner_worker_id={relay.owner_worker_id}",
            f"holder_worker_id={relay.holder_worker_id}",
            f"generation={relay.generation}",
            f"share_index={relay.share_index}",
        )
    )


def relay_aad(relay: EncryptedRecoveryShareRelay) -> bytes:
    """Canonical metadata authenticated by ChaCha20-Poly1305 and outer signature."""
    relay.validate()
    return (
        "{"
        f'"cohort_commitment":"{relay.cohort_commitment}",'
        f'"expires_at":{_canonical_float(relay.expires_at)},'
        f'"field_id":"{relay.field_id}",'
        f'"generation":{relay.generation},'
        f'"holder_worker_id":"{relay.holder_worker_id}",'
        f'"issued_at":{_canonical_float(relay.issued_at)},'
        f'"model_version":"{relay.model_version}",'
        f'"owner_worker_id":"{relay.owner_worker_id}",'
        f'"round_id":{relay.round_id},'
        f'"run_id":"{relay.run_id}",'
        f'"schema_version":{relay.schema_version},'
        f'"secret_digest":"{relay.secret_digest}",'
        f'"secret_length":{relay.secret_length},'
        f'"session_id":"{relay.session_id}",'
        f'"share_index":{relay.share_index},'
        f'"threshold":{relay.threshold},'
        f'"total_shares":{relay.total_shares}'
        "}"
    ).encode()


def relay_payload_hash_input(relay: EncryptedRecoveryShareRelay) -> str:
    """Canonical coordinator-facing payload hash input for SignedWorkerEnvelope."""
    relay.validate()
    aad_text = relay_aad(relay).decode("utf-8")
    # Ciphertext and nonce are intentionally outside the AEAD AAD construction
    # but are covered by the worker's outer Ed25519 envelope signature.
    return (
        aad_text[:-1]
        + f',"ciphertext_hash":"{relay.ciphertext_hash}"'
        + f',"ciphertext_hex":"{relay.ciphertext_hex}"'
        + f',"nonce_hex":"{relay.nonce_hex}"'
        + "}"
    )


def _derive_relay_key(
    *,
    self_private_key_raw: bytes,
    peer_public_key_raw: bytes,
    relay: EncryptedRecoveryShareRelay,
) -> bytes:
    shared_secret = derive_x25519_shared_secret(
        self_private_key_raw,
        peer_public_key_raw,
    )
    return derive_purpose_key(
        shared_secret,
        HKDF_PURPOSE_RECOVERY_SHARE_RELAY,
        relay_key_context(relay),
        CHACHA20_KEY_LENGTH,
    )


def encrypt_recovery_share_for_holder(
    share: RecoveryShare,
    *,
    owner_private_key_raw: bytes,
    holder_public_key_raw: bytes,
    run_id: str,
    round_id: int,
    model_version: str,
    cohort_commitment: str,
    issued_at: float | None = None,
    expires_after_seconds: float = 300.0,
) -> EncryptedRecoveryShareRelay:
    """Encrypt one owner-created Shamir share for its assigned holder."""
    make_recovery_receipt(share)  # structural/field-range validation
    if expires_after_seconds <= 0.0 or not math.isfinite(expires_after_seconds):
        raise RecoveryShareRelayError(
            "expires_after_seconds must be finite and positive"
        )
    now = time.time() if issued_at is None else float(issued_at)
    nonce = secrets.token_bytes(CHACHA20_NONCE_LENGTH)

    # Build a temporary relay with the final metadata and placeholder ciphertext
    # so key/AAD derivation cannot depend on ciphertext bytes themselves.
    template = EncryptedRecoveryShareRelay(
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
        secret_digest=share.secret_digest,
        secret_length=share.secret_length,
        nonce_hex=nonce.hex(),
        ciphertext_hex="00" * MAX_RELAY_CIPHERTEXT_BYTES,
        ciphertext_hash=hashlib.sha256(
            b"\x00" * MAX_RELAY_CIPHERTEXT_BYTES
        ).hexdigest(),
        issued_at=now,
        expires_at=now + expires_after_seconds,
        field_id=share.field_id,
    )
    key = _derive_relay_key(
        self_private_key_raw=owner_private_key_raw,
        peer_public_key_raw=holder_public_key_raw,
        relay=template,
    )
    plaintext = share.value.to_bytes(RELAY_FIELD_BYTES, "big")
    ciphertext = ChaCha20Poly1305(key).encrypt(nonce, plaintext, relay_aad(template))
    relay = EncryptedRecoveryShareRelay(
        session_id=template.session_id,
        run_id=template.run_id,
        round_id=template.round_id,
        model_version=template.model_version,
        cohort_commitment=template.cohort_commitment,
        owner_worker_id=template.owner_worker_id,
        holder_worker_id=template.holder_worker_id,
        generation=template.generation,
        threshold=template.threshold,
        total_shares=template.total_shares,
        share_index=template.share_index,
        secret_digest=template.secret_digest,
        secret_length=template.secret_length,
        nonce_hex=template.nonce_hex,
        ciphertext_hex=ciphertext.hex(),
        ciphertext_hash=hashlib.sha256(ciphertext).hexdigest(),
        issued_at=template.issued_at,
        expires_at=template.expires_at,
        field_id=template.field_id,
    )
    relay.validate()
    return relay


def decrypt_recovery_share_from_owner(
    relay: EncryptedRecoveryShareRelay,
    *,
    holder_private_key_raw: bytes,
    owner_public_key_raw: bytes,
    expected_holder_worker_id: str,
) -> RecoveryShare:
    """Decrypt and validate a relayed share at the intended holder only."""
    relay.validate()
    if relay.holder_worker_id != expected_holder_worker_id:
        raise RecoveryShareRelayError("relay is addressed to a different holder")
    key = _derive_relay_key(
        self_private_key_raw=holder_private_key_raw,
        peer_public_key_raw=owner_public_key_raw,
        relay=relay,
    )
    try:
        plaintext = ChaCha20Poly1305(key).decrypt(
            bytes.fromhex(relay.nonce_hex),
            bytes.fromhex(relay.ciphertext_hex),
            relay_aad(relay),
        )
    except InvalidTag as exc:
        raise RecoveryShareRelayError(
            "recovery relay authentication failed; ciphertext/AAD/key mismatch"
        ) from exc
    if len(plaintext) != RELAY_FIELD_BYTES:
        raise RecoveryShareRelayError(
            "decrypted recovery share has an invalid field width"
        )
    share = RecoveryShare(
        session_id=relay.session_id,
        owner_id=relay.owner_worker_id,
        holder_id=relay.holder_worker_id,
        generation=relay.generation,
        threshold=relay.threshold,
        total_shares=relay.total_shares,
        index=relay.share_index,
        value=int.from_bytes(plaintext, "big"),
        secret_digest=relay.secret_digest,
        secret_length=relay.secret_length,
        field_id=relay.field_id,
    )
    make_recovery_receipt(share)
    return share


def build_signed_recovery_relay(
    relay: EncryptedRecoveryShareRelay,
    *,
    signing_identity: WorkerSigningIdentity,
    sequence_number: int,
    envelope_nonce: str,
) -> SignedEnvelope:
    """Sign ciphertext metadata for coordinator admission without exposing plaintext."""
    relay.validate()
    if relay.owner_worker_id != signing_identity.worker_id:
        raise RecoveryShareRelayError(
            "relay owner must match the signing worker identity"
        )
    if sequence_number < 1 or not envelope_nonce:
        raise RecoveryShareRelayError(
            "relay envelope requires sequence >= 1 and a nonce"
        )
    payload_hash = hashlib.sha256(
        relay_payload_hash_input(relay).encode("utf-8")
    ).hexdigest()
    return sign_envelope(
        EnvelopeFields(
            message_type=MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_RELAY,
            worker_id=relay.owner_worker_id,
            run_id=relay.run_id,
            round_id=relay.round_id,
            model_version=relay.model_version,
            message_stream=MESSAGE_STREAM_SECURE_AGGREGATION,
            sequence_number=sequence_number,
            signing_key_id=signing_identity.key_id,
            payload_hash=payload_hash,
            issued_at=relay.issued_at,
            expires_at=relay.expires_at,
            nonce=envelope_nonce,
        ),
        signing_identity,
    )


__all__ = [
    "EncryptedRecoveryShareRelay",
    "HKDF_PURPOSE_RECOVERY_SHARE_RELAY",
    "MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_RELAY",
    "RecoveryShareRelayError",
    "build_signed_recovery_relay",
    "decrypt_recovery_share_from_owner",
    "encrypt_recovery_share_for_holder",
    "relay_aad",
    "relay_key_context",
    "relay_payload_hash_input",
]
