#pragma once

// Deterministic fixed-point finite-domain encoding for the Secure
// Aggregation Protocol Foundation and No-Dropout Masked-Sum Core slice
// -- see docs/fixed-point-secure-encoding.md for the full design
// record and docs/secure-aggregation-protocol-foundation.md for why
// this lives in the non-gRPC-gated fl_coordinator library (pure
// integer/floating-point math, no cryptographic primitive dependency,
// so it builds and is unit-testable on this Windows/MSVC development
// machine exactly like every other coordinator domain type).
//
// Domain choice (Work Package G): a power-of-two ring, modulo 2^64,
// represented with native `std::int64_t`/`std::uint64_t` two's-
// complement arithmetic. This is a deliberate choice over a prime
// field: unsigned 64-bit wraparound *is* arithmetic modulo 2^64 (well
// defined by the C++ standard for unsigned integer types), so ring
// addition is exactly `uint64_t` addition, ring negation is exactly
// `uint64_t` negation (two's complement), and decoding a value known
// to be within the signed range is exactly reinterpreting the same 64
// bits as `int64_t` -- no modular-reduction-by-division step, no
// prime-field inverse, nothing hand-rolled beyond what the language
// already guarantees. Mirrored bit-for-bit in Python via
// `value & 0xFFFFFFFFFFFFFFFF` (see
// python/src/fl_platform/secure_aggregation/domain_profile.py).
//
// Never masks raw IEEE-754 bytes: every value entering the ring is
// first fixed-point-quantized by encode_value() below, never a
// reinterpret_cast of a double's bit pattern.

#include <cstdint>
#include <limits>
#include <string>

namespace fl::coordinator {

// Schema version for FixedPointEncodingProfile's own wire/config
// representation (Work Package F) -- bump only on an incompatible
// field-meaning change, not on adding a new profile with different
// values.
inline constexpr std::uint32_t kFixedPointEncodingSchemaVersion = 1;

// One deterministic rounding rule, used everywhere in both languages:
// round-half-away-from-zero (not round-half-to-even/banker's
// rounding, which is language- and locale-sensitive in some
// standard-library implementations, and not truncation, which biases
// every encoded value toward zero). Implemented with plain arithmetic
// (no locale-dependent formatting function, no architecture-dependent
// extended-precision x87 path -- see fixed_point_encoding.cpp's
// implementation comment for how the extended-precision hazard is
// avoided).
enum class RoundingRule {
    kRoundHalfAwayFromZero,
};

std::string to_string(RoundingRule rule);

// Why an encode/decode call was rejected -- every rejection is a
// typed value, never a silently-clamped or silently-wrapped result.
enum class EncodingRejectionReason {
    kNone,
    kNonFiniteInput,        // NaN or +/-Infinity
    kMagnitudeOverflow,     // |value| exceeds the profile's configured max_input_magnitude
    kEncodedValueOverflow,  // the quantized value does not fit in the signed decode range
};

std::string to_string(EncodingRejectionReason reason);

// A full, versioned fixed-point + domain configuration -- Work
// Packages F and G combined into one profile, since the domain bound
// proof (G) is only meaningful in terms of a specific encoding
// profile's scale factor and magnitude limits (F). Every field the
// task specification requires is represented; nothing here is
// inferred implicitly at encode/decode time.
struct FixedPointEncodingProfile {
    std::uint32_t schema_version = kFixedPointEncodingSchemaVersion;

    // Domain: modulo 2^64, always -- not configurable per-run, so it
    // is not a struct field; see this header's own comment for why.
    // `signed_decoding_boundary` below is derived from this fixed
    // choice (2^63) and is exposed as a named constant, not a magic
    // number, so callers reading a bounds-proof failure message know
    // exactly what boundary was compared against.
    static constexpr std::int64_t kSignedDecodingBoundary =
        std::numeric_limits<std::int64_t>::max();  // 2^63 - 1

    RoundingRule rounding_rule = RoundingRule::kRoundHalfAwayFromZero;

    // scale_factor: the encoded integer is round(value * scale_factor).
    // Must be a positive, finite value. A power-of-two scale factor
    // (e.g. 2^20) is recommended (multiplication/division by it is
    // then exact in binary floating point up to the mantissa's own
    // precision) but not enforced -- any positive double is accepted,
    // documented as a recommendation, not a requirement, in
    // docs/fixed-point-secure-encoding.md.
    double scale_factor = 1048576.0;  // 2^20 -- ~6 decimal digits of precision

    // The largest |value| (before scaling) this profile will encode
    // without rejecting as kMagnitudeOverflow. Chosen generously for
    // this project's tiny synthetic models/clipped updates -- see
    // docs/fixed-point-secure-encoding.md for the reasoning and how a
    // real deployment would tune this against its own clipping bound.
    double max_input_magnitude = 100.0;

    // The largest single client weight (e.g. sample count) this
    // profile's aggregate-bound proof assumes -- Work Package U.
    std::uint64_t max_client_weight = 1'000'000;

    // The largest cohort size this profile's aggregate-bound proof
    // assumes -- Work Package G.
    std::uint64_t max_cohort_size = 10'000;

    // Required headroom (in the signed domain's own units, i.e. ring
    // elements) between the worst-case proven aggregate magnitude and
    // kSignedDecodingBoundary -- Work Package G's "+ safety margin"
    // term. Not a stylistic nicety: real floating-point-to-fixed-point
    // rounding, and the fact max_input_magnitude/max_client_weight are
    // themselves configured bounds rather than hard language-level
    // limits, both eat into the margin between "should never happen"
    // and "the ring silently wraps a legitimate large aggregate into
    // an incorrect decoded value" -- the single failure mode Work
    // Package G explicitly prohibits.
    std::uint64_t safety_margin = 1ULL << 8;  // 256 ring elements
};

// Work Package G's required safety inequality, computed and checked
// explicitly -- never assumed. Returns true (profile is safe to use)
// or false (reject the run configuration; the caller must not proceed
// with masking under this profile). `overflow` is set to true if the
// bound computation itself would overflow unsigned 64-bit arithmetic
// before ever reaching the comparison (an even harder failure than an
// unsafe-but-computable bound) -- checked explicitly with
// multiplication-overflow guards, not left to silently wrap.
struct DomainBoundsProof {
    bool safe = false;
    bool computation_overflowed = false;
    // The proven worst-case |aggregate value| in ring units, valid
    // only when computation_overflowed is false.
    std::uint64_t worst_case_aggregate_magnitude = 0;
    std::string explanation;
};

[[nodiscard]] DomainBoundsProof prove_domain_bounds(const FixedPointEncodingProfile& profile);

// Secure User-Level Differential Privacy Runtime slice, Work Area H:
// the worst-case L2 norm of one user's whole-update quantization-error
// vector, across every element in every tensor the run's manifest
// covers. Each element's quantization error is bounded by half the
// smallest representable step (round-half-away-from-zero's own proven
// bound, `0.5 / scale_factor` -- see encode_value's `quantization_error`
// field), and the worst-case L2 norm of an error vector whose every
// component simultaneously sits at that per-element bound is
// `sqrt(total_element_count) * (0.5 / scale_factor)`. See
// docs/secure-user-level-dp-semantics.md section 11 for the full
// derivation this mirrors exactly (Python:
// fl_platform.secure_aggregation.user_level_clipping.compute_quantization_margin) --
// both sides must compute the identical value, verified by a
// cross-language fixture.
[[nodiscard]] double compute_quantization_margin(std::uint64_t total_element_count,
                                                 const FixedPointEncodingProfile& profile);

// effective_sensitivity = clip_norm + quantization_margin -- the value
// central Gaussian noise must be calibrated against, never the
// optimistic unquantized clip_norm alone. A trivial sum, given its own
// named function so every call site documents intent identically and
// so C++/Python stay byte-for-byte in step by construction (the same
// two named quantities are always combined the same way).
[[nodiscard]] double compute_effective_sensitivity(double clip_norm, double quantization_margin);

// Result of encoding one scalar value into the ring.
struct EncodeResult {
    bool ok = false;
    EncodingRejectionReason reason = EncodingRejectionReason::kNone;
    // Valid only when ok is true. This is the *signed* quantized
    // integer (before any ring/mask arithmetic is applied elsewhere)
    // -- callers add pairwise masks to `static_cast<std::uint64_t>(encoded)`
    // to enter the masked domain; see secure_aggregation_mask.hpp.
    std::int64_t encoded = 0;
    // |value * scale_factor - encoded| / scale_factor -- the real
    // quantization error introduced by rounding, in the *original*
    // (unscaled) units. Reported even on success so a caller can
    // aggregate quantization-error statistics (Work Package V's
    // "quantization-error summary" field), never silently discarded.
    double quantization_error = 0.0;
};

[[nodiscard]] EncodeResult encode_value(double value, const FixedPointEncodingProfile& profile);

// Decodes a final (unmasked, already-summed) ring value back to a
// double, dividing by scale_factor. This function assumes its input
// is already known-safe (i.e. produced by summing values that passed
// prove_domain_bounds() for the profile in use) -- it performs no
// bounds re-proof of its own, since by the time a real aggregate sum
// reaches this function the proof already happened once, at
// session-configuration time, for the whole run, not per decode call.
[[nodiscard]] double decode_value(std::int64_t ring_value,
                                  const FixedPointEncodingProfile& profile);

}  // namespace fl::coordinator
