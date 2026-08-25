from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from fl_platform.secure_aggregation.crypto import generate_x25519_keypair
from fl_platform.secure_aggregation.dropout_recovery import (
    create_ephemeral_key_recovery_shares,
    recover_ephemeral_private_key,
)
from fl_platform.secure_aggregation.share_relay import (
    MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_RELAY,
    RecoveryShareRelayError,
    build_signed_recovery_relay,
    decrypt_recovery_share_from_owner,
    encrypt_recovery_share_for_holder,
    relay_payload_hash_input,
)
from fl_platform.security.signed_envelope import envelope_signing_bytes
from fl_platform.security.signing_identity import generate_signing_identity


def _fixture():
    owner_private, owner_public = generate_x25519_keypair()
    holder_a_private, holder_a_public = generate_x25519_keypair()
    holder_b_private, holder_b_public = generate_x25519_keypair()
    shares = create_ephemeral_key_recovery_shares(
        owner_private,
        session_id="relay-session",
        owner_worker_id="owner-1",
        holder_worker_ids=("holder-a", "holder-b"),
        threshold=2,
    )
    return (
        owner_private,
        owner_public,
        holder_a_private,
        holder_a_public,
        holder_b_private,
        holder_b_public,
        shares,
    )


def _encrypt_first():
    fixture = _fixture()
    owner_private, _, _, holder_a_public, _, _, shares = fixture
    relay = encrypt_recovery_share_for_holder(
        shares[0],
        owner_private_key_raw=owner_private,
        holder_public_key_raw=holder_a_public,
        run_id="run-relay",
        round_id=3,
        model_version="v3",
        cohort_commitment="a" * 64,
        issued_at=1000.0,
        expires_after_seconds=120.0,
    )
    return fixture, relay


def test_relay_round_trip_only_exposes_ciphertext_to_coordinator() -> None:
    fixture, relay = _encrypt_first()
    owner_private, owner_public, holder_private, _, _, _, shares = fixture
    raw_share = shares[0].value.to_bytes(66, "big")

    ciphertext = bytes.fromhex(relay.ciphertext_hex)
    assert raw_share not in ciphertext
    assert relay.ciphertext_hash == hashlib.sha256(ciphertext).hexdigest()
    assert not hasattr(relay, "share_value_hex")

    decrypted = decrypt_recovery_share_from_owner(
        relay,
        holder_private_key_raw=holder_private,
        owner_public_key_raw=owner_public,
        expected_holder_worker_id="holder-a",
    )
    assert decrypted == shares[0]
    # The owner key itself is never carried by the relay object.
    assert owner_private.hex() not in relay_payload_hash_input(relay)


def test_two_holders_can_reconstruct_original_ephemeral_key_after_decrypt() -> None:
    fixture = _fixture()
    (
        owner_private,
        owner_public,
        holder_a_private,
        holder_a_public,
        holder_b_private,
        holder_b_public,
        shares,
    ) = fixture
    relays = (
        encrypt_recovery_share_for_holder(
            shares[0],
            owner_private_key_raw=owner_private,
            holder_public_key_raw=holder_a_public,
            run_id="run-relay",
            round_id=3,
            model_version="v3",
            cohort_commitment="a" * 64,
            issued_at=1000.0,
        ),
        encrypt_recovery_share_for_holder(
            shares[1],
            owner_private_key_raw=owner_private,
            holder_public_key_raw=holder_b_public,
            run_id="run-relay",
            round_id=3,
            model_version="v3",
            cohort_commitment="a" * 64,
            issued_at=1000.0,
        ),
    )
    decrypted = (
        decrypt_recovery_share_from_owner(
            relays[0],
            holder_private_key_raw=holder_a_private,
            owner_public_key_raw=owner_public,
            expected_holder_worker_id="holder-a",
        ),
        decrypt_recovery_share_from_owner(
            relays[1],
            holder_private_key_raw=holder_b_private,
            owner_public_key_raw=owner_public,
            expected_holder_worker_id="holder-b",
        ),
    )
    recovered = recover_ephemeral_private_key(
        decrypted,
        expected_public_key_raw=owner_public,
    )
    assert recovered.secret == owner_private


def test_wrong_holder_key_or_recipient_is_rejected() -> None:
    fixture, relay = _encrypt_first()
    _, owner_public, _, _, wrong_private, _, _ = fixture

    with pytest.raises(RecoveryShareRelayError, match="authentication failed"):
        decrypt_recovery_share_from_owner(
            relay,
            holder_private_key_raw=wrong_private,
            owner_public_key_raw=owner_public,
            expected_holder_worker_id="holder-a",
        )

    with pytest.raises(RecoveryShareRelayError, match="different holder"):
        decrypt_recovery_share_from_owner(
            relay,
            holder_private_key_raw=wrong_private,
            owner_public_key_raw=owner_public,
            expected_holder_worker_id="holder-b",
        )


def test_metadata_and_ciphertext_tampering_fail_closed() -> None:
    fixture, relay = _encrypt_first()
    _, owner_public, holder_private, _, _, _, _ = fixture

    rebound = replace(relay, cohort_commitment="b" * 64)
    with pytest.raises(RecoveryShareRelayError, match="authentication failed"):
        decrypt_recovery_share_from_owner(
            rebound,
            holder_private_key_raw=holder_private,
            owner_public_key_raw=owner_public,
            expected_holder_worker_id="holder-a",
        )

    ciphertext = bytearray.fromhex(relay.ciphertext_hex)
    ciphertext[0] ^= 1
    tampered = replace(
        relay,
        ciphertext_hex=bytes(ciphertext).hex(),
        ciphertext_hash=hashlib.sha256(ciphertext).hexdigest(),
    )
    with pytest.raises(RecoveryShareRelayError, match="authentication failed"):
        decrypt_recovery_share_from_owner(
            tampered,
            holder_private_key_raw=holder_private,
            owner_public_key_raw=owner_public,
            expected_holder_worker_id="holder-a",
        )


def test_owner_signs_ciphertext_metadata_with_distinct_message_type() -> None:
    _, relay = _encrypt_first()
    identity = generate_signing_identity("owner-1")
    signed = build_signed_recovery_relay(
        relay,
        signing_identity=identity,
        sequence_number=1,
        envelope_nonce="relay-envelope-nonce",
    )
    assert signed.fields.message_type == MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_RELAY
    assert signed.fields.worker_id == "owner-1"
    assert signed.fields.client_id == ""
    assert signed.fields.task_id == ""
    assert signed.fields.payload_hash == hashlib.sha256(
        relay_payload_hash_input(relay).encode("utf-8")
    ).hexdigest()
    identity.verify_key.verify(
        envelope_signing_bytes(signed.fields),
        bytes.fromhex(signed.signature_hex),
    )


def test_relay_float_serialization_matches_cpp_contract() -> None:
    _, relay = _encrypt_first()
    canonical = relay_payload_hash_input(relay)
    assert '"issued_at":1000,' in canonical
    assert '"expires_at":1120,' in canonical
