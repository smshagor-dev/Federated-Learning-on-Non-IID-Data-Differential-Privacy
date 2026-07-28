"""Deterministic fixed-point finite-domain encoding -- Python mirror of
cpp/coordinator/include/fl_coordinator/secure_aggregation_encoding.hpp.
See that header's docstring for the full domain-choice rationale (ring
modulo 2^64, two's-complement reinterpretation, why no modular-
reduction step is hand-rolled) and docs/fixed-point-secure-encoding.md
for the design record. This file re-derives that same reasoning in
Python terms rather than restating it; read the C++ header first if the
"why" here seems terse.

The ring is emulated with plain Python ``int`` plus explicit
``& UINT64_MASK`` masking at every wraparound point (Python ints don't
overflow on their own, so wraparound must be applied by hand -- the
opposite problem from C++, where wraparound is automatic and the
concern is instead accidental *signed* overflow, which is undefined
behavior there and simply doesn't exist for Python's arbitrary-
precision ints).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SCHEMA_VERSION = 1

ROUNDING_RULE_ROUND_HALF_AWAY_FROM_ZERO = "round_half_away_from_zero"

REJECTION_NONE = "none"
REJECTION_NON_FINITE_INPUT = "non_finite_input"
REJECTION_MAGNITUDE_OVERFLOW = "magnitude_overflow"
REJECTION_ENCODED_VALUE_OVERFLOW = "encoded_value_overflow"

UINT64_MASK = (1 << 64) - 1
INT64_MAX = (
    1 << 63
) - 1  # signed decoding boundary, matches C++ kSignedDecodingBoundary
INT64_MIN = -(1 << 63)


@dataclass(slots=True)
class FixedPointEncodingProfile:
    schema_version: int = SCHEMA_VERSION
    rounding_rule: str = ROUNDING_RULE_ROUND_HALF_AWAY_FROM_ZERO
    scale_factor: float = 1048576.0  # 2^20, matches the C++ default exactly
    max_input_magnitude: float = 100.0
    max_client_weight: int = 1_000_000
    max_cohort_size: int = 10_000
    safety_margin: int = 1 << 8  # 256 ring elements


@dataclass(slots=True)
class DomainBoundsProof:
    safe: bool = False
    computation_overflowed: bool = False
    worst_case_aggregate_magnitude: int = 0
    explanation: str = ""


def prove_domain_bounds(profile: FixedPointEncodingProfile) -> DomainBoundsProof:
    """Work Package G's required safety inequality, checked explicitly
    against a 64-bit unsigned ceiling (UINT64_MASK) even though Python's
    own ``int`` type has no such limit -- the ring this profile's values
    will actually be masked/summed in on the C++ side *is* a real 64-bit
    ring, so a profile this function calls safe must be provably safe
    there too, not merely representable in Python's unbounded integers.
    """
    proof = DomainBoundsProof()

    worst_case_single_encoded_d = math.ceil(
        profile.max_input_magnitude * profile.scale_factor + 0.5
    )
    if (
        not math.isfinite(worst_case_single_encoded_d)
        or worst_case_single_encoded_d < 0
    ):
        proof.computation_overflowed = True
        proof.explanation = (
            "max_input_magnitude * scale_factor does not fit in the domain's own "
            "64-bit accumulator before any cohort/weight scaling is even applied"
        )
        return proof
    worst_case_single_encoded = int(worst_case_single_encoded_d)
    if worst_case_single_encoded > UINT64_MASK:
        proof.computation_overflowed = True
        proof.explanation = (
            "max_input_magnitude * scale_factor does not fit in the domain's own "
            "64-bit accumulator before any cohort/weight scaling is even applied"
        )
        return proof

    weighted = worst_case_single_encoded * profile.max_client_weight
    if weighted > UINT64_MASK:
        proof.computation_overflowed = True
        proof.explanation = (
            "worst_case_single_encoded * max_client_weight overflows a "
            "64-bit accumulator"
        )
        return proof

    aggregate = weighted * profile.max_cohort_size
    if aggregate > UINT64_MASK:
        proof.computation_overflowed = True
        proof.explanation = (
            "weighted_max_magnitude * max_cohort_size overflows a 64-bit accumulator"
        )
        return proof

    aggregate_with_margin = aggregate + profile.safety_margin
    if aggregate_with_margin > UINT64_MASK:
        proof.computation_overflowed = True
        proof.explanation = (
            "worst_case_aggregate_magnitude + safety_margin overflows a "
            "64-bit accumulator"
        )
        return proof

    proof.worst_case_aggregate_magnitude = aggregate
    proof.computation_overflowed = False

    if aggregate_with_margin >= INT64_MAX:
        proof.safe = False
        proof.explanation = (
            f"proven worst-case aggregate magnitude ({aggregate}) plus safety margin "
            f"({profile.safety_margin}) is not strictly less than the signed decoding "
            f"boundary ({INT64_MAX}) -- this profile's "
            "scale_factor/max_input_magnitude/max_client_weight/"
            "max_cohort_size combination is rejected, not silently permitted to "
            "risk wrapping a legitimate aggregate into an incorrect decoded value"
        )
        return proof

    proof.safe = True
    proof.explanation = (
        f"worst-case aggregate magnitude {aggregate} + safety margin "
        f"{profile.safety_margin} "
        f"< signed decoding boundary {INT64_MAX}"
    )
    return proof


@dataclass(slots=True)
class EncodeResult:
    ok: bool = False
    reason: str = REJECTION_NONE
    encoded: int = 0
    quantization_error: float = 0.0


def _round_half_away_from_zero(value: float) -> float:
    """Deliberately not ``round()`` (Python's builtin rounds half-to-
    even/banker's rounding for floats via IEEE-754 semantics in some
    cases) -- this must match C++'s ``std::round`` (round-half-away-
    from-zero) bit for bit, so it is implemented explicitly rather than
    relying on a same-named-but-different-behavior stdlib default.
    """
    if value >= 0:
        return math.floor(value + 0.5)
    return math.ceil(value - 0.5)


def encode_value(value: float, profile: FixedPointEncodingProfile) -> EncodeResult:
    if not math.isfinite(value):
        return EncodeResult(ok=False, reason=REJECTION_NON_FINITE_INPUT)

    # -0.0 == 0.0 in Python float comparison, and abs(-0.0) == 0.0, so
    # negative zero needs no special-case branch here either -- mirrors
    # the C++ implementation's identical reasoning.
    if abs(value) > profile.max_input_magnitude:
        return EncodeResult(ok=False, reason=REJECTION_MAGNITUDE_OVERFLOW)

    scaled = value * profile.scale_factor
    rounded = _round_half_away_from_zero(scaled)

    if rounded > float(INT64_MAX) or rounded < float(INT64_MIN):
        return EncodeResult(ok=False, reason=REJECTION_ENCODED_VALUE_OVERFLOW)

    encoded = int(rounded)
    quantization_error = abs(scaled - rounded) / profile.scale_factor
    return EncodeResult(
        ok=True,
        reason=REJECTION_NONE,
        encoded=encoded,
        quantization_error=quantization_error,
    )


def to_signed_ring_value(unsigned_ring_value: int) -> int:
    """Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    slice: two's-complement reinterpretation of an unsigned 64-bit ring
    value (as produced by ``sum_masked_tensors``/``sum_masked_values``,
    both of which always return a value in ``[0, UINT64_MASK]``) back
    into a signed int64-range Python int -- the exact Python-side
    counterpart to the C++ aggregate-decoding call site's
    ``static_cast<std::int64_t>(ring_value)`` (implementation-defined in
    the C++ standard before C++20, but two's-complement on every real
    platform this project targets; guaranteed as of C++20). Without
    this step, a ring value representing a mathematically negative sum
    (which wraps into the upper half of the unsigned 64-bit range) would
    decode as an enormous positive number instead of the correct
    negative one -- confirmed live: an early version of this slice's own
    validation code called ``decode_value`` directly on a raw summed
    ring value and produced exactly that garbage result for a
    per-element sum that was legitimately negative. ``decode_value``
    itself is unchanged (its own existing tests already pass it
    genuinely-signed values straight from ``encode_value``'s own
    output, never an unsigned ring sum) -- this is a new, additive
    function, not a modification of established, tested behavior."""
    if unsigned_ring_value > INT64_MAX:
        return unsigned_ring_value - (1 << 64)
    return unsigned_ring_value


def decode_value(ring_value: int, profile: FixedPointEncodingProfile) -> float:
    return ring_value / profile.scale_factor
