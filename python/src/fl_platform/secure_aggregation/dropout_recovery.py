"""Threshold dropout recovery bound to the live pairwise-mask protocol.

The recoverable secret is exactly one dropped participant's 32-byte ephemeral
X25519 private key. Once a threshold of surviving holders resubmits shares, the
key is reconstructed and verified against the public key in the signed frozen
roster. The coordinator can then reproduce only the dropped participant's
pairwise mask contributions and add those missing opposite-signed masks to the
surviving masked aggregate.

This module proves the protocol math and provides worker/coordinator domain
primitives. It is not, by itself, a claim that the C++ gRPC service transports
recovery shares yet.
"""

from __future__ import annotations

from dataclasses import dataclass

import nacl.bindings

from fl_platform.secure_aggregation.crypto import (
    HKDF_PURPOSE_CLIPPING_INDICATOR_MASK_STREAM,
    HKDF_PURPOSE_TENSOR_MASK_STREAM,
    HKDF_PURPOSE_WEIGHT_MASK_STREAM,
    X25519_KEY_LENGTH,
    derive_x25519_shared_secret,
)
from fl_platform.secure_aggregation.fixed_point_encoding import UINT64_MASK
from fl_platform.secure_aggregation.masked_update import canonical_mask_context
from fl_platform.secure_aggregation.pairwise_mask import (
    SignedMask,
    mask_encoded_value,
    resolve_pairwise_mask_sign,
    sum_masked_values,
)
from fl_platform.secure_aggregation.tensor_mask import (
    PeerMaskStream,
    derive_tensor_mask_stream,
    derive_weight_mask,
    mask_tensor,
)
from fl_platform.secure_aggregation.threshold_recovery import (
    RecoveredSecret,
    RecoveryShare,
    create_recovery_shares,
    reconstruct_recovery_secret,
)


class DropoutRecoveryError(RuntimeError):
    """Raised when recovered key material cannot safely cancel dropout masks."""


@dataclass(frozen=True, slots=True)
class DropoutRecoveryContext:
    provider: int
    protocol_version: int
    session_id: str
    run_id: str
    round_id: int
    model_version: str
    cohort_commitment: str
    dropout_worker_id: str
    dropout_public_key_raw: bytes
    survivor_public_keys: dict[str, bytes]
    tensor_element_counts: dict[str, int]

    def validate(self) -> None:
        required = (
            self.session_id,
            self.run_id,
            self.model_version,
            self.cohort_commitment,
            self.dropout_worker_id,
        )
        if any(not value for value in required):
            raise ValueError("dropout recovery context contains an empty binding")
        if len(self.dropout_public_key_raw) != X25519_KEY_LENGTH:
            raise ValueError("dropout public key must be exactly 32 bytes")
        if not self.survivor_public_keys:
            raise ValueError("at least one surviving peer is required")
        if self.dropout_worker_id in self.survivor_public_keys:
            raise ValueError("dropout worker cannot also be a survivor")
        if any(
            not worker_id or len(public_key) != X25519_KEY_LENGTH
            for worker_id, public_key in self.survivor_public_keys.items()
        ):
            raise ValueError("survivor identities and X25519 public keys are invalid")
        if not self.tensor_element_counts:
            raise ValueError("tensor element counts must not be empty")
        if any(
            not name or count <= 0
            for name, count in self.tensor_element_counts.items()
        ):
            raise ValueError("tensor names/counts must be non-empty and positive")


@dataclass(frozen=True, slots=True)
class DropoutMaskCorrection:
    dropout_worker_id: str
    tensor_corrections: dict[str, tuple[int, ...]]
    weight_correction: int
    clipping_indicator_correction: int


def create_ephemeral_key_recovery_shares(
    private_key_raw: bytes,
    *,
    session_id: str,
    owner_worker_id: str,
    holder_worker_ids: tuple[str, ...],
    threshold: int,
    generation: int = 0,
) -> tuple[RecoveryShare, ...]:
    """Threshold-share one session-scoped ephemeral X25519 private key."""
    if len(private_key_raw) != X25519_KEY_LENGTH:
        raise ValueError("ephemeral X25519 private key must be exactly 32 bytes")
    return create_recovery_shares(
        private_key_raw,
        session_id=session_id,
        owner_id=owner_worker_id,
        holder_ids=holder_worker_ids,
        threshold=threshold,
        generation=generation,
    )


def recover_ephemeral_private_key(
    shares: tuple[RecoveryShare, ...],
    *,
    expected_public_key_raw: bytes,
) -> RecoveredSecret:
    """Reconstruct a dropout key and bind it to the frozen-roster public key."""
    if len(expected_public_key_raw) != X25519_KEY_LENGTH:
        raise DropoutRecoveryError("expected dropout public key is not 32 bytes")
    recovered = reconstruct_recovery_secret(shares)
    if len(recovered.secret) != X25519_KEY_LENGTH:
        raise DropoutRecoveryError("recovered dropout private key is not 32 bytes")
    derived_public = nacl.bindings.crypto_scalarmult_base(recovered.secret)
    if derived_public != expected_public_key_raw:
        raise DropoutRecoveryError(
            "recovered dropout private key does not match the frozen-roster public key"
        )
    return recovered


def compute_dropout_mask_correction(
    dropout_private_key_raw: bytes,
    context: DropoutRecoveryContext,
) -> DropoutMaskCorrection:
    """Reproduce the absent participant's pairwise side of every mask stream."""
    context.validate()
    if len(dropout_private_key_raw) != X25519_KEY_LENGTH:
        raise DropoutRecoveryError("dropout private key is not 32 bytes")
    if (
        nacl.bindings.crypto_scalarmult_base(dropout_private_key_raw)
        != context.dropout_public_key_raw
    ):
        raise DropoutRecoveryError(
            "dropout private key does not match the frozen-roster public key"
        )

    tensor_streams: dict[str, list[PeerMaskStream]] = {
        name: [] for name in context.tensor_element_counts
    }
    weight_masks: list[SignedMask] = []
    clipping_masks: list[SignedMask] = []

    for survivor_id, survivor_public_key in sorted(
        context.survivor_public_keys.items()
    ):
        shared_secret = derive_x25519_shared_secret(
            dropout_private_key_raw,
            survivor_public_key,
        )
        sign = resolve_pairwise_mask_sign(
            context.dropout_worker_id,
            survivor_id,
        )
        for tensor_name, element_count in sorted(
            context.tensor_element_counts.items()
        ):
            mask_context = canonical_mask_context(
                provider=context.provider,
                protocol_version=context.protocol_version,
                session_id=context.session_id,
                run_id=context.run_id,
                round_id=context.round_id,
                model_version=context.model_version,
                cohort_commitment=context.cohort_commitment,
                self_participant_id=context.dropout_worker_id,
                peer_participant_id=survivor_id,
                tensor_name=tensor_name,
            )
            tensor_streams[tensor_name].append(
                PeerMaskStream(
                    peer_participant_id=survivor_id,
                    sign=sign,
                    mask_values=derive_tensor_mask_stream(
                        shared_secret,
                        HKDF_PURPOSE_TENSOR_MASK_STREAM,
                        mask_context,
                        element_count,
                    ),
                )
            )

        scalar_context = canonical_mask_context(
            provider=context.provider,
            protocol_version=context.protocol_version,
            session_id=context.session_id,
            run_id=context.run_id,
            round_id=context.round_id,
            model_version=context.model_version,
            cohort_commitment=context.cohort_commitment,
            self_participant_id=context.dropout_worker_id,
            peer_participant_id=survivor_id,
            tensor_name="",
        )
        weight_masks.append(
            SignedMask(
                mask=derive_weight_mask(
                    shared_secret,
                    HKDF_PURPOSE_WEIGHT_MASK_STREAM,
                    scalar_context,
                ),
                sign=sign,
            )
        )
        clipping_masks.append(
            SignedMask(
                mask=derive_weight_mask(
                    shared_secret,
                    HKDF_PURPOSE_CLIPPING_INDICATOR_MASK_STREAM,
                    scalar_context,
                ),
                sign=sign,
            )
        )

    tensor_corrections = {
        name: tuple(mask_tensor([0] * count, tensor_streams[name]))
        for name, count in sorted(context.tensor_element_counts.items())
    }
    return DropoutMaskCorrection(
        dropout_worker_id=context.dropout_worker_id,
        tensor_corrections=tensor_corrections,
        weight_correction=mask_encoded_value(0, weight_masks),
        clipping_indicator_correction=mask_encoded_value(0, clipping_masks),
    )


def apply_dropout_mask_correction(
    masked_tensor_sums: dict[str, list[int]],
    masked_weight_sum: int,
    masked_clipping_indicator_sum: int,
    correction: DropoutMaskCorrection,
) -> tuple[dict[str, list[int]], int, int]:
    """Add missing opposite-signed pairwise masks to the survivor aggregate."""
    if set(masked_tensor_sums) != set(correction.tensor_corrections):
        raise DropoutRecoveryError("dropout correction tensor set mismatch")
    recovered_tensors: dict[str, list[int]] = {}
    for name, correction_values in correction.tensor_corrections.items():
        aggregate_values = masked_tensor_sums[name]
        if len(aggregate_values) != len(correction_values):
            raise DropoutRecoveryError(
                f"dropout correction tensor length mismatch for {name!r}"
            )
        recovered_tensors[name] = [
            sum_masked_values([aggregate, missing])
            for aggregate, missing in zip(
                aggregate_values,
                correction_values,
                strict=True,
            )
        ]
    recovered_weight = sum_masked_values(
        [masked_weight_sum, correction.weight_correction]
    )
    recovered_indicator = sum_masked_values(
        [
            masked_clipping_indicator_sum,
            correction.clipping_indicator_correction,
        ]
    )
    return recovered_tensors, recovered_weight, recovered_indicator


def ring_sum(values: tuple[int, ...]) -> int:
    """Named helper for tests/callers comparing recovered ring values."""
    return sum(values) & UINT64_MASK


__all__ = [
    "DropoutMaskCorrection",
    "DropoutRecoveryContext",
    "DropoutRecoveryError",
    "apply_dropout_mask_correction",
    "compute_dropout_mask_correction",
    "create_ephemeral_key_recovery_shares",
    "recover_ephemeral_private_key",
    "ring_sum",
]
