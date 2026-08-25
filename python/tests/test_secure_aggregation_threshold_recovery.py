from __future__ import annotations

from dataclasses import replace

import pytest

from fl_platform.secure_aggregation.threshold_recovery import (
    ThresholdRecoveryCoordinator,
    ThresholdRecoveryError,
    create_recovery_shares,
    reconstruct_recovery_secret,
)


def _shares(secret: bytes = b"\x00" + b"mask-seed" * 3):
    return create_recovery_shares(
        secret,
        session_id="session-7",
        owner_id="dropout-client",
        holder_ids=("a", "b", "c", "d", "e"),
        threshold=3,
        generation=2,
    )


def test_any_threshold_subset_recovers_exact_opaque_seed() -> None:
    shares = _shares()
    expected = b"\x00" + b"mask-seed" * 3
    for subset in (
        (shares[0], shares[1], shares[2]),
        (shares[0], shares[2], shares[4]),
        (shares[1], shares[3], shares[4]),
    ):
        recovered = reconstruct_recovery_secret(subset)
        assert recovered.secret == expected
        assert recovered.session_id == "session-7"
        assert recovered.owner_id == "dropout-client"
        assert recovered.generation == 2


def test_less_than_threshold_fails_closed() -> None:
    shares = _shares()
    with pytest.raises(ThresholdRecoveryError, match="insufficient"):
        reconstruct_recovery_secret(shares[:2])


def test_cross_session_and_cross_generation_shares_are_rejected() -> None:
    shares = _shares()
    cross_session = replace(shares[2], session_id="session-other")
    with pytest.raises(ThresholdRecoveryError, match="context mismatch"):
        reconstruct_recovery_secret((shares[0], shares[1], cross_session))

    cross_generation = replace(shares[2], generation=3)
    with pytest.raises(ThresholdRecoveryError, match="context mismatch"):
        reconstruct_recovery_secret((shares[0], shares[1], cross_generation))


def test_duplicate_holder_or_index_is_rejected() -> None:
    shares = _shares()
    duplicate_holder = replace(shares[2], holder_id=shares[0].holder_id)
    with pytest.raises(ThresholdRecoveryError, match="duplicate"):
        reconstruct_recovery_secret((shares[0], shares[1], duplicate_holder))

    duplicate_index = replace(shares[2], index=shares[0].index)
    with pytest.raises(ThresholdRecoveryError, match="duplicate"):
        reconstruct_recovery_secret((shares[0], shares[1], duplicate_index))


def test_tampered_share_fails_digest_validation() -> None:
    shares = _shares()
    tampered = replace(shares[2], value=(shares[2].value + 1))
    with pytest.raises(ThresholdRecoveryError, match="digest validation"):
        reconstruct_recovery_secret((shares[0], shares[1], tampered))


def test_restartable_coordinator_recovers_after_threshold_arrives() -> None:
    shares = _shares()
    coordinator = ThresholdRecoveryCoordinator("session-7")
    coordinator.submit(shares[0])
    coordinator.submit(shares[2])
    assert not coordinator.can_recover("dropout-client", generation=2)

    restored = ThresholdRecoveryCoordinator.restore(
        "session-7",
        coordinator.snapshot(),
    )
    restored.submit(shares[4])
    assert restored.can_recover("dropout-client", generation=2)
    recovered = restored.recover("dropout-client", generation=2)
    assert recovered.secret == b"\x00" + b"mask-seed" * 3


def test_idempotent_replay_is_allowed_but_conflicting_replay_is_rejected() -> None:
    shares = _shares()
    coordinator = ThresholdRecoveryCoordinator("session-7")
    coordinator.submit(shares[0])
    coordinator.submit(shares[0])
    assert len(coordinator.snapshot()) == 1

    conflict = replace(shares[0], value=shares[0].value + 1)
    with pytest.raises(ThresholdRecoveryError, match="conflicting"):
        coordinator.submit(conflict)


def test_coordinator_rejects_wrong_session_and_inconsistent_metadata() -> None:
    shares = _shares()
    coordinator = ThresholdRecoveryCoordinator("session-7")
    with pytest.raises(ThresholdRecoveryError, match="different session"):
        coordinator.submit(replace(shares[0], session_id="session-other"))

    coordinator.submit(shares[0])
    with pytest.raises(ThresholdRecoveryError, match="metadata"):
        coordinator.submit(replace(shares[1], threshold=4))


def test_share_generation_rejects_invalid_threshold_and_duplicate_holders() -> None:
    with pytest.raises(ValueError, match="threshold"):
        create_recovery_shares(
            b"seed",
            session_id="s",
            owner_id="o",
            holder_ids=("a", "b"),
            threshold=1,
        )
    with pytest.raises(ValueError, match="unique"):
        create_recovery_shares(
            b"seed",
            session_id="s",
            owner_id="o",
            holder_ids=("a", "a", "b"),
            threshold=2,
        )
