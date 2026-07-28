"""Cryptographic primitive wrappers -- Python mirror of
cpp/coordinator/src/secure_aggregation_crypto.cpp (and its header,
cpp/coordinator/include/fl_coordinator/secure_aggregation_crypto.hpp).
See docs/secure-aggregation-cryptographic-provider.md for the vetted-
provider decision this file implements: PyNaCl's low-level
``nacl.bindings`` for raw X25519 (never ``nacl.public.Box``, which
layers HSalsa20 on top of the raw scalar multiplication and would not
produce the same shared-secret bytes as OpenSSL's EVP_PKEY_derive on
the C++ side), the `cryptography` package for HKDF-SHA-256 and ChaCha20
(IETF/RFC 8439 variant), and stdlib ``hashlib`` for SHA-256.

Every function here takes/returns plain ``bytes`` (never a wire
message), matching the C++ module's "protobuf-free, wire-contract-free"
scope -- see that header's docstring for the full rationale.
"""

from __future__ import annotations

import hashlib
import struct

import nacl.bindings
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from fl_platform.secure_aggregation.cohort_state_machine import (
    SecureAggregationSessionConfig,
)

X25519_KEY_LENGTH = 32
SHA256_DIGEST_LENGTH = 32
CHACHA20_KEY_LENGTH = 32
# RFC 8439 IETF variant: 12-byte nonce + 32-bit little-endian block
# counter -- see docs/secure-aggregation-cryptographic-provider.md
# Section 6 for why this variant was selected for cross-language parity
# with OpenSSL.
CHACHA20_NONCE_LENGTH = 12

HKDF_DOMAIN_SALT = b"FL_PLATFORM_SECURE_AGGREGATION_HKDF_SALT_V1"

HKDF_PURPOSE_TENSOR_MASK_STREAM = "tensor_mask_stream"
HKDF_PURPOSE_WEIGHT_MASK_STREAM = "weight_mask_stream"
# Secure Adaptive Clipping with Private Indicator Aggregation slice: a
# third sibling label, domain-separating the masked clipping-indicator
# scalar from both tensor and weight masks -- see
# docs/secure-adaptive-clipping-semantics.md section 14.
HKDF_PURPOSE_CLIPPING_INDICATOR_MASK_STREAM = "clipping_indicator_mask_stream"

_COHORT_COMMITMENT_PREFIX = b"FL_PLATFORM_SECAGG_COHORT_COMMITMENT_V1\x00"
_SESSION_CONFIG_HASH_PREFIX = b"FL_PLATFORM_SECAGG_SESSION_CONFIG_V1\x00"
_FIELD_SEPARATOR = b"\x1e"


class SecureAggregationCryptoError(Exception):
    pass


def generate_x25519_keypair() -> tuple[bytes, bytes]:
    """Returns (private_key_raw, public_key_raw), both 32 bytes.
    ``crypto_scalarmult_base`` performs RFC 7748's internal clamping
    itself, so a raw 32-byte random private scalar is a valid input --
    no separate clamping step is needed here (mirrors
    generate_x25519_keypair()'s C++ counterpart, which relies on
    OpenSSL's EVP_PKEY_keygen doing the equivalent internally).
    """
    private_key = nacl.bindings.randombytes(X25519_KEY_LENGTH)
    public_key = nacl.bindings.crypto_scalarmult_base(private_key)
    return private_key, public_key


def derive_x25519_shared_secret(
    self_private_key_raw: bytes, peer_public_key_raw: bytes
) -> bytes:
    if len(self_private_key_raw) != X25519_KEY_LENGTH:
        raise SecureAggregationCryptoError(
            "derive_x25519_shared_secret: self private key is not 32 bytes"
        )
    if len(peer_public_key_raw) != X25519_KEY_LENGTH:
        raise SecureAggregationCryptoError(
            "derive_x25519_shared_secret: peer public key is not 32 bytes"
        )

    # nacl.bindings.crypto_scalarmult is libsodium's raw X25519 scalar
    # multiplication (RFC 7748) -- byte-identical output to OpenSSL's
    # EVP_PKEY_derive on an X25519 EVP_PKEY pair, unlike
    # nacl.public.Box/PrivateKey (which additionally runs HSalsa20 over
    # the raw shared point and would NOT match the C++ side).
    secret = nacl.bindings.crypto_scalarmult(self_private_key_raw, peer_public_key_raw)

    # Work Package P: reject a degenerate all-zero shared secret --
    # same rationale as the C++ implementation's identical check.
    if secret == b"\x00" * X25519_KEY_LENGTH:
        raise SecureAggregationCryptoError(
            "derive_x25519_shared_secret: derived an all-zero shared secret -- this "
            "indicates a degenerate/low-order peer public key and is rejected, never "
            "used as mask-generation seed material"
        )
    return secret


def hkdf_sha256(salt: bytes, ikm: bytes, info: bytes, output_length: int) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=output_length, salt=salt, info=info
    ).derive(ikm)


def derive_purpose_key(
    shared_secret: bytes,
    purpose_label: str,
    canonical_context: str,
    output_length: int = CHACHA20_KEY_LENGTH,
) -> bytes:
    # Same NUL-separated info construction as the C++ implementation's
    # derive_purpose_key -- see that function's doc comment for why the
    # context is a caller responsibility, not computed inside this
    # module.
    info = purpose_label.encode("utf-8") + b"\x00" + canonical_context.encode("utf-8")
    return hkdf_sha256(HKDF_DOMAIN_SALT, shared_secret, info, output_length)


def chacha20_keystream(
    key: bytes, nonce: bytes, initial_counter: int, length: int
) -> bytes:
    if len(key) != CHACHA20_KEY_LENGTH:
        raise SecureAggregationCryptoError("chacha20_keystream: key is not 32 bytes")
    if len(nonce) != CHACHA20_NONCE_LENGTH:
        raise SecureAggregationCryptoError("chacha20_keystream: nonce is not 12 bytes")

    # `cryptography`'s ChaCha20 takes a single 16-byte value: a 32-bit
    # little-endian counter followed by the 12-byte nonce -- the exact
    # same layout as the 16-byte IV OpenSSL's EVP_chacha20 consumes
    # (confirmed in docs/secure-aggregation-cryptographic-provider.md
    # Section 6), so this is real cross-language parity, not a
    # coincidental match.
    counter_and_nonce = struct.pack("<I", initial_counter) + nonce
    cipher = Cipher(algorithms.ChaCha20(key, counter_and_nonce), mode=None)
    encryptor = cipher.encryptor()
    keystream = encryptor.update(b"\x00" * length) + encryptor.finalize()
    return keystream


def sha256_digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compute_masked_values_checksum(values: list[int]) -> str:
    """Byte-for-byte mirror of compute_masked_values_checksum's
    canonical little-endian uint64 encoding in
    secure_aggregation_session_manager.cpp (a private helper there, not
    exported via any header -- this is a from-scratch Python
    reimplementation of that exact convention, not a generated binding).
    Used both to compute a real MaskedClientUpdate's own
    checksum/masked_weight_checksum fields (Work Area K) and, by the
    coordinator's SubmitMaskedClientUpdate handler, to independently
    verify them -- see docs/secure-aggregation-masked-update.md."""
    if not values:
        return sha256_hex(b"")
    packed = struct.pack(f"<{len(values)}Q", *values)
    return sha256_hex(packed)


def compute_cohort_commitment(
    session_id: str,
    run_id: str,
    round_id: int,
    model_version: str,
    ordered_participant_ids: list[str],
) -> str:
    """Byte-for-byte mirror of compute_cohort_commitment's canonical
    serialization in secure_aggregation_crypto.cpp -- see that
    function's doc comment for why participant order is preserved
    (never sorted) and
    fixtures/secure_aggregation/cohort_commitment_golden.json for the
    frozen cross-language reference vector both implementations are
    checked against.
    """
    parts = [_COHORT_COMMITMENT_PREFIX]
    parts.append(f"session_id={session_id}".encode() + _FIELD_SEPARATOR)
    parts.append(f"run_id={run_id}".encode() + _FIELD_SEPARATOR)
    parts.append(f"round_id={round_id}".encode() + _FIELD_SEPARATOR)
    parts.append(f"model_version={model_version}".encode() + _FIELD_SEPARATOR)
    parts.append(
        f"participant_count={len(ordered_participant_ids)}".encode() + _FIELD_SEPARATOR
    )
    for i, participant_id in enumerate(ordered_participant_ids):
        parts.append(f"participant[{i}]={participant_id}".encode() + _FIELD_SEPARATOR)
    return sha256_hex(b"".join(parts))


def _format_float(value: float) -> str:
    # C++'s operator<<(ostream&, double) for these session-config
    # fields (every one of which is either 0.0 or a small,
    # non-scientific-notation-range value in every profile this
    # project uses) prints using the default floatfield, which for a
    # value like 0.0 or 100.0 produces "0" / "100" (no trailing ".0",
    # unlike Python's str(float)). Explicit here rather than assumed,
    # so a future field with a genuinely fractional default doesn't
    # silently diverge between languages without this function being
    # revisited.
    if value == int(value):
        return str(int(value))
    return repr(value)


def compute_session_configuration_hash(config: SecureAggregationSessionConfig) -> str:
    """Byte-for-byte mirror of compute_session_configuration_hash's
    canonical serialization in secure_aggregation_crypto.cpp. See
    fixtures/secure_aggregation/session_configuration_hash_golden.json
    for the frozen cross-language reference vector.
    """
    parts = [_SESSION_CONFIG_HASH_PREFIX]

    def field(name: str, value: object) -> None:
        parts.append(f"{name}={value}".encode() + _FIELD_SEPARATOR)

    field("schema_version", config.schema_version)
    field("protocol_version", config.protocol_version)
    field("provider", config.provider)
    field("session_id", config.session_id)
    field("run_id", config.run_id)
    field("round_id", config.round_id)
    field("model_version", config.model_version)
    field("aggregation_algorithm", config.aggregation_algorithm)
    field("cohort_size", config.cohort_size)
    field("minimum_cohort_size", config.minimum_cohort_size)
    field("ordered_participant_count", len(config.ordered_participant_ids))
    for i, participant_id in enumerate(config.ordered_participant_ids):
        field(f"ordered_participant[{i}]", participant_id)
    field("tensor_manifest_hash", config.tensor_manifest_hash)
    field("model_manifest_hash", config.model_manifest_hash)
    field("domain_profile", config.domain_profile)
    field("scale_factor", _format_float(config.scale_factor))
    field("max_absolute_update_bound", _format_float(config.max_absolute_update_bound))
    field("max_client_weight", config.max_client_weight)
    field("max_aggregate_bound", config.max_aggregate_bound)
    field("mask_generator_profile", config.mask_generator_profile)
    field("key_agreement_profile", config.key_agreement_profile)
    field("key_derivation_profile", config.key_derivation_profile)
    field("session_created_at_unix_s", _format_float(config.session_created_at_unix_s))
    field(
        "key_advertisement_deadline_unix_s",
        _format_float(config.key_advertisement_deadline_unix_s),
    )
    field(
        "masked_update_deadline_unix_s",
        _format_float(config.masked_update_deadline_unix_s),
    )
    field("session_expiry_unix_s", _format_float(config.session_expiry_unix_s))
    field("coordinator_signing_key_id", config.coordinator_signing_key_id)

    return sha256_hex(b"".join(parts))
