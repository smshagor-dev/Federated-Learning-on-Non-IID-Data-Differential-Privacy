"""Tensor and weight mask generation -- Python mirror of
cpp/coordinator/include/fl_coordinator/secure_aggregation_tensor_mask.hpp
and its .cpp. See that header's docstring for the full rationale (Work
Packages T, U): combines crypto.py's primitives with pairwise_mask.py's
ring arithmetic into the actual per-tensor-element masking operation.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from fl_platform.secure_aggregation.crypto import (
    CHACHA20_KEY_LENGTH,
    CHACHA20_NONCE_LENGTH,
    chacha20_keystream,
    derive_purpose_key,
)
from fl_platform.secure_aggregation.fixed_point_encoding import UINT64_MASK
from fl_platform.secure_aggregation.pairwise_mask import SignedMask, apply_pairwise_mask, sum_masked_values


@dataclass(slots=True)
class PeerMaskStream:
    peer_participant_id: str
    sign: str
    mask_values: list[int]


def derive_tensor_mask_stream(
    shared_secret: bytes, purpose_label: str, canonical_context: str, element_count: int
) -> list[int]:
    """Byte-for-byte mirror of derive_tensor_mask_stream's C++
    counterpart: one ChaCha20 keystream, 8 bytes per element,
    interpreted little-endian via struct.unpack -- the same byte order
    the C++ side assembles by hand, so both languages derive identical
    mask values from an identical shared secret and canonical context.
    """
    if element_count == 0:
        return []
    purpose_key = derive_purpose_key(shared_secret, purpose_label, canonical_context, CHACHA20_KEY_LENGTH)
    zero_nonce = b"\x00" * CHACHA20_NONCE_LENGTH
    keystream = chacha20_keystream(purpose_key, zero_nonce, 0, element_count * 8)
    return list(struct.unpack(f"<{element_count}Q", keystream))


def mask_tensor(encoded_tensor: list[int], peer_streams: list[PeerMaskStream]) -> list[int]:
    masked = [value & UINT64_MASK for value in encoded_tensor]
    for peer in peer_streams:
        if len(peer.mask_values) != len(encoded_tensor):
            raise ValueError(
                f"mask_tensor: peer {peer.peer_participant_id!r} mask stream length does not match the "
                "tensor's element count"
            )
        for i in range(len(encoded_tensor)):
            masked[i] = apply_pairwise_mask(masked[i], peer.mask_values[i], peer.sign)
    return masked


def sum_masked_tensors(per_participant_masked_tensors: list[list[int]]) -> list[int]:
    if not per_participant_masked_tensors:
        raise ValueError("sum_masked_tensors: at least one participant's masked tensor is required")
    element_count = len(per_participant_masked_tensors[0])
    for tensor in per_participant_masked_tensors:
        if len(tensor) != element_count:
            raise ValueError("sum_masked_tensors: all participants' masked tensors must have the identical element count")
    return [
        sum_masked_values([tensor[i] for tensor in per_participant_masked_tensors]) for i in range(element_count)
    ]


def derive_weight_mask(shared_secret: bytes, purpose_label: str, canonical_context: str) -> int:
    return derive_tensor_mask_stream(shared_secret, purpose_label, canonical_context, 1)[0]


__all__ = [
    "PeerMaskStream",
    "SignedMask",
    "derive_tensor_mask_stream",
    "derive_weight_mask",
    "mask_tensor",
    "sum_masked_tensors",
]
