"""Pairwise mask sign rule and ring arithmetic -- Python mirror of
cpp/coordinator/include/fl_coordinator/secure_aggregation_mask.hpp. See
that header's docstring for the cancellation-property rationale (Work
Package R): summed across a complete, correctly-ordered cohort, every
pairwise mask is added exactly once and subtracted exactly once, so the
total pairwise-mask contribution to the aggregate sum is exactly zero
in the ring.

Ring: modulo 2^64, emulated with plain Python ``int`` plus explicit
``& UINT64_MASK`` masking at every addition/subtraction -- see
fixed_point_encoding.py's module docstring for why this masking must be
applied by hand in Python (unlike C++, where ``uint64_t`` wraparound is
automatic).
"""

from __future__ import annotations

from dataclasses import dataclass

from fl_platform.secure_aggregation.fixed_point_encoding import UINT64_MASK

MASK_SIGN_ADD = "add"
MASK_SIGN_SUBTRACT = "subtract"


def participant_sorts_before(a: str, b: str) -> bool:
    """Canonical, locale-independent, ordinal ordering of two participant
    identity strings -- Python's default string comparison already
    compares by Unicode code point (never locale-aware, never case-
    folded), which agrees with C++'s ``std::string::compare`` byte-wise
    ordering for the ASCII participant identifiers this protocol uses in
    practice (documented, not merely assumed: this project's worker
    identifiers are ASCII, per docs/worker-identity.md's naming
    convention).
    """
    return a < b


def resolve_pairwise_mask_sign(
    self_participant_id: str, peer_participant_id: str
) -> str:
    if self_participant_id == peer_participant_id:
        raise ValueError(
            "resolve_pairwise_mask_sign: a participant cannot derive a pairwise mask "
            "against itself (duplicate participant identity)"
        )
    return (
        MASK_SIGN_ADD
        if participant_sorts_before(self_participant_id, peer_participant_id)
        else MASK_SIGN_SUBTRACT
    )


def apply_pairwise_mask(accumulator: int, mask: int, sign: str) -> int:
    if sign == MASK_SIGN_ADD:
        return (accumulator + mask) & UINT64_MASK
    if sign == MASK_SIGN_SUBTRACT:
        return (accumulator - mask) & UINT64_MASK
    raise ValueError(f"apply_pairwise_mask: unrecognized sign {sign!r}")


@dataclass(slots=True)
class SignedMask:
    mask: int
    sign: str = MASK_SIGN_ADD


def mask_encoded_value(
    base_encoded_value: int, pairwise_masks: list[SignedMask]
) -> int:
    """``base_encoded_value`` is the *signed* quantized integer produced
    by ``fixed_point_encoding.encode_value`` (in ``[INT64_MIN,
    INT64_MAX]``). ``& UINT64_MASK`` performs the same two's-complement
    reinterpretation as the C++ implementation's
    ``static_cast<uint64_t>(int64_t)`` -- the correct ring representative
    for a negative signed value is its two's-complement bit pattern, not
    a separate modular-reduction computation.
    """
    accumulator = base_encoded_value & UINT64_MASK
    for signed_mask in pairwise_masks:
        accumulator = apply_pairwise_mask(
            accumulator, signed_mask.mask, signed_mask.sign
        )
    return accumulator


def sum_masked_values(masked_values: list[int]) -> int:
    total = 0
    for value in masked_values:
        total = (total + value) & UINT64_MASK
    return total
