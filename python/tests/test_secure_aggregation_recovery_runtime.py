from __future__ import annotations

from dataclasses import replace

import pytest

from fl_platform.secure_aggregation.crypto import generate_x25519_keypair
from fl_platform.secure_aggregation.dropout_recovery import (
    create_ephemeral_key_recovery_shares,
)
from fl_platform.secure_aggregation.recovery_runtime import (
    LiveRecoveryRegistry,
    RecoveryAdmissionError,
    RecoverySessionView,
)
from fl_platform.secure_aggregation.recovery_wire import payload_from_recovery_share


def _fixture():
    owner_private, owner_public = generate_x25519_keypair()
    _, public_a = generate_x25519_keypair()
    _, public_b = generate_x25519_keypair()
    shares = create_ephemeral_key_recovery_shares(
        owner_private,
        session_id="session-live",
        owner_worker_id="worker-dropout",
        holder_worker_ids=("worker-a", "worker-b"),
        threshold=2,
    )
    payloads = tuple(
        payload_from_recovery_share(
            share,
            run_id="run-live",
            round_id=5,
            model_version="v5",
            cohort_commitment="c" * 64,
            issued_at=1000.0,
            expires_after_seconds=120.0,
        )
        for share in shares
    )
    session = RecoverySessionView(
        session_id="session-live",
        run_id="run-live",
        round_id=5,
        model_version="v5",
        cohort_commitment="c" * 64,
        participant_public_keys={
            "worker-dropout": owner_public,
            "worker-a": public_a,
            "worker-b": public_b,
        },
        submitted_contributors=frozenset({"worker-a", "worker-b"}),
    )
    return owner_private, payloads, session


def test_threshold_recovery_waits_for_threshold_and_recovers_exact_key() -> None:
    owner_private, payloads, session = _fixture()
    registry = LiveRecoveryRegistry()

    first = registry.submit(payloads[0], session)
    assert first.accepted
    assert first.share_count == 1
    assert first.threshold == 2
    assert not first.recoverable
    assert first.recovered_secret is None

    second = registry.submit(payloads[1], session)
    assert second.accepted
    assert second.share_count == 2
    assert second.recoverable
    assert second.recovered_secret is not None
    assert second.recovered_secret.secret == owner_private
    assert second.recovered_secret.contributing_holders == ("worker-a", "worker-b")


def test_durable_snapshot_contains_receipts_not_share_values() -> None:
    _, payloads, session = _fixture()
    registry = LiveRecoveryRegistry()
    registry.submit(payloads[0], session)
    receipts = registry.snapshot_receipts(session.session_id)
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.holder_id == "worker-a"
    assert receipt.share_commitment
    assert not hasattr(receipt, "value")


def test_recovery_owner_must_be_exactly_the_missing_contributor() -> None:
    _, payloads, session = _fixture()
    with pytest.raises(RecoveryAdmissionError, match="owner is not"):
        LiveRecoveryRegistry().submit(
            replace(payloads[0], owner_worker_id="worker-b"),
            session,
        )


def test_foreign_holder_is_not_a_surviving_contributor() -> None:
    _, payloads, session = _fixture()
    with pytest.raises(RecoveryAdmissionError, match="holder must be"):
        LiveRecoveryRegistry().submit(
            replace(payloads[0], holder_worker_id="worker-z"),
            session,
        )


def test_multiple_dropouts_are_fail_closed_in_initial_live_policy() -> None:
    _, payloads, session = _fixture()
    bad_session = replace(
        session,
        submitted_contributors=frozenset({"worker-a"}),
    )
    with pytest.raises(RecoveryAdmissionError, match="exactly one missing"):
        LiveRecoveryRegistry().submit(payloads[0], bad_session)


def test_private_round_recovery_is_not_silently_enabled() -> None:
    _, payloads, session = _fixture()
    private_session = replace(session, privacy_mode="sample_level_dp")
    with pytest.raises(RecoveryAdmissionError, match="non-private"):
        LiveRecoveryRegistry().submit(payloads[0], private_session)


def test_frozen_cohort_commitment_is_signed_and_enforced() -> None:
    _, payloads, session = _fixture()
    with pytest.raises(RecoveryAdmissionError, match="commitment mismatch"):
        LiveRecoveryRegistry().submit(
            replace(payloads[0], cohort_commitment="d" * 64),
            session,
        )
