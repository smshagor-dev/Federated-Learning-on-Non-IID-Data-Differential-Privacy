from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from fl_platform.secure_aggregation.dropout_recovery import (
    create_ephemeral_key_recovery_shares,
)
from fl_platform.secure_aggregation.recovery_wire import (
    MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_SHARE,
    RecoverySharePayload,
    RecoveryWireError,
    build_signed_recovery_share,
    payload_from_recovery_share,
    recovery_share_payload_hash_input,
)
from fl_platform.security.signed_envelope import envelope_signing_bytes
from fl_platform.security.signing_identity import generate_signing_identity


def _payload() -> RecoverySharePayload:
    shares = create_ephemeral_key_recovery_shares(
        bytes(range(1, 33)),
        session_id="session-7",
        owner_worker_id="worker-dropout",
        holder_worker_ids=("worker-a", "worker-b", "worker-c"),
        threshold=2,
    )
    return payload_from_recovery_share(
        shares[0],
        run_id="run-9",
        round_id=4,
        model_version="v4",
        cohort_commitment="a" * 64,
        issued_at=1000.0,
        expires_after_seconds=60.0,
    )


def test_recovery_payload_hash_is_deterministic_and_bound() -> None:
    payload = _payload()
    first = recovery_share_payload_hash_input(payload)
    second = recovery_share_payload_hash_input(payload)
    assert first == second
    assert '"holder_worker_id":"worker-a"' in first
    assert '"owner_worker_id":"worker-dropout"' in first
    assert '"cohort_commitment":"' + "a" * 64 + '"' in first

    changed = replace(payload, cohort_commitment="b" * 64)
    assert recovery_share_payload_hash_input(changed) != first


def test_signed_recovery_share_uses_independent_message_type() -> None:
    payload = _payload()
    identity = generate_signing_identity("worker-a")
    signed = build_signed_recovery_share(
        payload,
        signing_identity=identity,
        sequence_number=1,
        nonce="nonce-1",
    )
    assert signed.fields.message_type == MESSAGE_TYPE_SECURE_AGGREGATION_RECOVERY_SHARE
    assert signed.fields.worker_id == "worker-a"
    assert signed.fields.run_id == "run-9"
    assert signed.fields.round_id == 4
    assert signed.fields.payload_hash == hashlib.sha256(
        recovery_share_payload_hash_input(payload).encode("utf-8")
    ).hexdigest()
    identity.verify_key.verify(
        envelope_signing_bytes(signed.fields),
        bytes.fromhex(signed.signature_hex),
    )


def test_holder_must_match_signing_identity() -> None:
    with pytest.raises(RecoveryWireError, match="holder must match"):
        build_signed_recovery_share(
            _payload(),
            signing_identity=generate_signing_identity("worker-b"),
            sequence_number=1,
            nonce="nonce-1",
        )


def test_owner_cannot_submit_its_own_recovery_share() -> None:
    payload = _payload()
    invalid = replace(
        payload,
        owner_worker_id="worker-a",
        holder_worker_id="worker-a",
    )
    with pytest.raises(RecoveryWireError, match="owner and holder"):
        invalid.validate()


def test_share_hex_and_field_are_fail_closed() -> None:
    payload = _payload()
    with pytest.raises(RecoveryWireError, match="share_value_hex"):
        replace(payload, share_value_hex="NOT-HEX").validate()

    with pytest.raises(RecoveryWireError, match="unsupported recovery field"):
        replace(payload, field_id="unknown-field").validate()
