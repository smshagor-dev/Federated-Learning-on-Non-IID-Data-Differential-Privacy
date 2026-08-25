from __future__ import annotations

import pytest

from fl_platform.secure_aggregation.crypto import generate_x25519_keypair
from fl_platform.secure_aggregation.dropout_recovery import (
    DropoutRecoveryContext,
    DropoutRecoveryError,
    apply_dropout_mask_correction,
    compute_dropout_mask_correction,
    create_ephemeral_key_recovery_shares,
    recover_ephemeral_private_key,
    ring_sum,
)
from fl_platform.secure_aggregation.masked_update import (
    RosterContext,
    WeightedEncodingResult,
    mask_clipping_indicator,
    mask_weighted_delta,
)
from fl_platform.secure_aggregation.pairwise_mask import sum_masked_values
from fl_platform.secure_aggregation.tensor_mask import sum_masked_tensors
from fl_platform.secure_aggregation.threshold_recovery import ThresholdRecoveryError


def _encoding(values: list[int], weight: int) -> WeightedEncodingResult:
    return WeightedEncodingResult(
        tensor_names=("weight",),
        encoded_tensors={"weight": values},
        encoded_weight=weight,
        total_elements=len(values),
        max_quantization_error=0.0,
        mean_quantization_error=0.0,
    )


def _roster(
    self_id: str,
    public_keys: dict[str, bytes],
) -> RosterContext:
    return RosterContext(
        provider=2,
        protocol_version=1,
        session_id="session-dropout",
        run_id="run-dropout",
        round_id=7,
        model_version="model-v3",
        cohort_commitment="cohort-commitment-test",
        tensor_manifest_hash="tensor-manifest",
        fixed_point_profile_hash="fixed-point-profile",
        cryptographic_profile_hash="crypto-profile",
        payload_hash="roster-payload",
        peer_public_keys={
            worker_id: public_key
            for worker_id, public_key in public_keys.items()
            if worker_id != self_id
        },
    )


def test_threshold_recovered_dropout_key_cancels_missing_pairwise_masks() -> None:
    keypairs = {
        worker_id: generate_x25519_keypair()
        for worker_id in ("worker-a", "worker-b", "worker-c")
    }
    public_keys = {
        worker_id: public_key for worker_id, (_, public_key) in keypairs.items()
    }
    survivor_encodings = {
        "worker-a": _encoding([10, 20], 2),
        "worker-b": _encoding([30, 40], 3),
    }
    survivor_indicators = {"worker-a": 1, "worker-b": 0}

    masked_tensors: list[list[int]] = []
    masked_weights: list[int] = []
    masked_indicators: list[int] = []
    for worker_id in ("worker-a", "worker-b"):
        private_key, _ = keypairs[worker_id]
        roster = _roster(worker_id, public_keys)
        tensors, weight = mask_weighted_delta(
            survivor_encodings[worker_id],
            self_worker_id=worker_id,
            self_private_key_raw=private_key,
            roster=roster,
        )
        indicator, _ = mask_clipping_indicator(
            survivor_indicators[worker_id],
            self_worker_id=worker_id,
            self_private_key_raw=private_key,
            roster=roster,
        )
        masked_tensors.append(tensors["weight"])
        masked_weights.append(weight)
        masked_indicators.append(indicator)

    survivor_tensor_sum = sum_masked_tensors(masked_tensors)
    survivor_weight_sum = sum_masked_values(masked_weights)
    survivor_indicator_sum = sum_masked_values(masked_indicators)

    dropout_private_key, dropout_public_key = keypairs["worker-c"]
    shares = create_ephemeral_key_recovery_shares(
        dropout_private_key,
        session_id="session-dropout",
        owner_worker_id="worker-c",
        holder_worker_ids=("worker-a", "worker-b"),
        threshold=2,
        generation=1,
    )
    recovered = recover_ephemeral_private_key(
        shares,
        expected_public_key_raw=dropout_public_key,
    )
    assert recovered.secret == dropout_private_key

    correction = compute_dropout_mask_correction(
        recovered.secret,
        DropoutRecoveryContext(
            provider=2,
            protocol_version=1,
            session_id="session-dropout",
            run_id="run-dropout",
            round_id=7,
            model_version="model-v3",
            cohort_commitment="cohort-commitment-test",
            dropout_worker_id="worker-c",
            dropout_public_key_raw=dropout_public_key,
            survivor_public_keys={
                "worker-a": public_keys["worker-a"],
                "worker-b": public_keys["worker-b"],
            },
            tensor_element_counts={"weight": 2},
        ),
    )
    recovered_tensors, recovered_weight, recovered_indicator = (
        apply_dropout_mask_correction(
            {"weight": survivor_tensor_sum},
            survivor_weight_sum,
            survivor_indicator_sum,
            correction,
        )
    )

    assert recovered_tensors["weight"] == [
        ring_sum((10, 30)),
        ring_sum((20, 40)),
    ]
    assert recovered_weight == ring_sum((2, 3))
    assert recovered_indicator == ring_sum((1, 0))


def test_recovered_private_key_must_match_frozen_roster_public_key() -> None:
    private_key, _ = generate_x25519_keypair()
    _, wrong_public_key = generate_x25519_keypair()
    shares = create_ephemeral_key_recovery_shares(
        private_key,
        session_id="session-dropout",
        owner_worker_id="worker-c",
        holder_worker_ids=("worker-a", "worker-b", "worker-d"),
        threshold=2,
    )
    with pytest.raises(DropoutRecoveryError, match="does not match"):
        recover_ephemeral_private_key(
            shares[:2],
            expected_public_key_raw=wrong_public_key,
        )


def test_less_than_threshold_cannot_recover_ephemeral_private_key() -> None:
    private_key, public_key = generate_x25519_keypair()
    shares = create_ephemeral_key_recovery_shares(
        private_key,
        session_id="session-dropout",
        owner_worker_id="worker-c",
        holder_worker_ids=("worker-a", "worker-b", "worker-d"),
        threshold=2,
    )
    with pytest.raises(ThresholdRecoveryError, match="insufficient"):
        recover_ephemeral_private_key(
            shares[:1],
            expected_public_key_raw=public_key,
        )


def test_dropout_correction_rejects_wrong_private_key_and_tensor_shape() -> None:
    dropout_private, dropout_public = generate_x25519_keypair()
    wrong_private, _ = generate_x25519_keypair()
    _, survivor_public = generate_x25519_keypair()
    context = DropoutRecoveryContext(
        provider=2,
        protocol_version=1,
        session_id="session-dropout",
        run_id="run-dropout",
        round_id=7,
        model_version="model-v3",
        cohort_commitment="commitment",
        dropout_worker_id="worker-c",
        dropout_public_key_raw=dropout_public,
        survivor_public_keys={"worker-a": survivor_public},
        tensor_element_counts={"weight": 2},
    )
    with pytest.raises(DropoutRecoveryError, match="does not match"):
        compute_dropout_mask_correction(wrong_private, context)

    correction = compute_dropout_mask_correction(dropout_private, context)
    with pytest.raises(DropoutRecoveryError, match="length mismatch"):
        apply_dropout_mask_correction(
            {"weight": [1]},
            1,
            0,
            correction,
        )
