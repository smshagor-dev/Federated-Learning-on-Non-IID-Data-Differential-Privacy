#include "fl_coordinator/secure_aggregation_encoding.hpp"

#include <cmath>

namespace fl::coordinator {

std::string to_string(RoundingRule rule) {
    switch (rule) {
        case RoundingRule::kRoundHalfAwayFromZero:
            return "round_half_away_from_zero";
    }
    return "unknown";
}

std::string to_string(EncodingRejectionReason reason) {
    switch (reason) {
        case EncodingRejectionReason::kNone:
            return "none";
        case EncodingRejectionReason::kNonFiniteInput:
            return "non_finite_input";
        case EncodingRejectionReason::kMagnitudeOverflow:
            return "magnitude_overflow";
        case EncodingRejectionReason::kEncodedValueOverflow:
            return "encoded_value_overflow";
    }
    return "unknown";
}

namespace {

// Round-half-away-from-zero on a plain `double`, deliberately not
// using std::round (which the C standard already specifies as
// round-half-away-from-zero, so this wrapper is *not* correcting a
// wrong stdlib default -- it exists so this file has one, explicit,
// documented call site to point to rather than an implicit dependency
// on std::round's documented-but-easy-to-forget rounding mode). No
// extended-precision (x87 80-bit) hazard: `double` arithmetic here
// never crosses a function-call ABI boundary that would force a
// truncation to 64-bit precision at a different point in Debug vs
// Release, since every operation is a single std:: call, not a chain
// of inline arithmetic a compiler might keep in an 80-bit register
// only in one build configuration.
double round_half_away_from_zero(double value) {
    return std::round(value);
}

}  // namespace

DomainBoundsProof prove_domain_bounds(const FixedPointEncodingProfile& profile) {
    DomainBoundsProof proof;

    // Worst-case single encoded magnitude: max_input_magnitude *
    // scale_factor, rounded up (ceil) since encode_value() rounds to
    // nearest -- the true worst case after rounding can be up to 0.5
    // ring units larger than the unrounded product.
    const double worst_case_single_encoded_d =
        std::ceil(profile.max_input_magnitude * profile.scale_factor + 0.5);
    if (!std::isfinite(worst_case_single_encoded_d) || worst_case_single_encoded_d < 0.0 ||
        worst_case_single_encoded_d >
            static_cast<double>(std::numeric_limits<std::uint64_t>::max())) {
        proof.safe = false;
        proof.computation_overflowed = true;
        proof.explanation =
            "max_input_magnitude * scale_factor does not fit in the domain's own "
            "64-bit accumulator before any cohort/weight scaling is even applied";
        return proof;
    }
    const auto worst_case_single_encoded = static_cast<std::uint64_t>(worst_case_single_encoded_d);

    // weighted = worst_case_single_encoded * max_client_weight,
    // checked for overflow explicitly (never left to silently wrap --
    // that is exactly the failure mode this function exists to
    // prevent).
    std::uint64_t weighted = 0;
    if (worst_case_single_encoded != 0 &&
        profile.max_client_weight >
            std::numeric_limits<std::uint64_t>::max() / worst_case_single_encoded) {
        proof.safe = false;
        proof.computation_overflowed = true;
        proof.explanation =
            "worst_case_single_encoded * max_client_weight overflows a 64-bit accumulator";
        return proof;
    }
    weighted = worst_case_single_encoded * profile.max_client_weight;

    // aggregate = weighted * max_cohort_size, same overflow discipline.
    std::uint64_t aggregate = 0;
    if (weighted != 0 &&
        profile.max_cohort_size > std::numeric_limits<std::uint64_t>::max() / weighted) {
        proof.safe = false;
        proof.computation_overflowed = true;
        proof.explanation =
            "weighted_max_magnitude * max_cohort_size overflows a 64-bit accumulator";
        return proof;
    }
    aggregate = weighted * profile.max_cohort_size;

    // aggregate + safety_margin, checked for overflow.
    if (aggregate > std::numeric_limits<std::uint64_t>::max() - profile.safety_margin) {
        proof.safe = false;
        proof.computation_overflowed = true;
        proof.explanation =
            "worst_case_aggregate_magnitude + safety_margin overflows a 64-bit accumulator";
        return proof;
    }
    const std::uint64_t aggregate_with_margin = aggregate + profile.safety_margin;

    proof.worst_case_aggregate_magnitude = aggregate;
    proof.computation_overflowed = false;

    // Work Package G's required inequality:
    //   max encoded magnitude * max client weight * max cohort size
    //     + safety margin < signed decoding boundary
    const auto boundary =
        static_cast<std::uint64_t>(FixedPointEncodingProfile::kSignedDecodingBoundary);
    if (aggregate_with_margin >= boundary) {
        proof.safe = false;
        proof.explanation =
            "proven worst-case aggregate magnitude (" + std::to_string(aggregate) +
            ") plus safety margin (" + std::to_string(profile.safety_margin) +
            ") is not strictly less than the signed decoding boundary (" +
            std::to_string(boundary) +
            ") -- this profile's scale_factor/max_input_magnitude/max_client_weight/"
            "max_cohort_size combination is rejected, not silently permitted to risk "
            "wrapping a legitimate aggregate into an incorrect decoded value";
        return proof;
    }

    proof.safe = true;
    proof.explanation = "worst-case aggregate magnitude " + std::to_string(aggregate) +
                        " + safety margin " + std::to_string(profile.safety_margin) +
                        " < signed decoding boundary " + std::to_string(boundary);
    return proof;
}

EncodeResult encode_value(double value, const FixedPointEncodingProfile& profile) {
    EncodeResult result;

    if (!std::isfinite(value)) {
        // Catches NaN and +/-Infinity in one check -- std::isfinite is
        // false for both, so kNonFiniteInput covers both required
        // rejection cases (Work Package H: "NaN rejection",
        // "Infinity rejection") without needing separate branches.
        result.ok = false;
        result.reason = EncodingRejectionReason::kNonFiniteInput;
        return result;
    }

    // Negative-zero handling (Work Package H): -0.0 == 0.0 is true in
    // IEEE-754 comparison, and 0.0 * scale_factor == 0.0 regardless of
    // the input's sign bit, so -0.0 encodes identically to 0.0 with no
    // special-case branch needed -- verified by a dedicated fixture
    // (see the encoding test file), not merely asserted here.
    if (std::abs(value) > profile.max_input_magnitude) {
        result.ok = false;
        result.reason = EncodingRejectionReason::kMagnitudeOverflow;
        return result;
    }

    const double scaled = value * profile.scale_factor;
    const double rounded = round_half_away_from_zero(scaled);

    // kSignedDecodingBoundary (2^63 - 1) is not exactly representable
    // as a double (53-bit mantissa) -- static_cast rounds it up to
    // 2^63 for this comparison, which makes this check very slightly
    // conservative (it can reject a handful of values in
    // [2^63-1, 2^63) that would technically still fit in int64_t).
    // Deliberate: erring toward rejection here is safe: this
    // per-value check is a last-resort guard for a misconfigured
    // profile, never the primary safety mechanism (prove_domain_bounds
    // is, applied once per session against the profile as a whole,
    // with a real, non-approximated safety margin far below this
    // boundary in every profile this project actually uses).
    if (rounded > static_cast<double>(FixedPointEncodingProfile::kSignedDecodingBoundary) ||
        rounded < -static_cast<double>(FixedPointEncodingProfile::kSignedDecodingBoundary)) {
        result.ok = false;
        result.reason = EncodingRejectionReason::kEncodedValueOverflow;
        return result;
    }

    result.ok = true;
    result.reason = EncodingRejectionReason::kNone;
    result.encoded = static_cast<std::int64_t>(rounded);
    result.quantization_error = std::abs(scaled - rounded) / profile.scale_factor;
    return result;
}

double decode_value(std::int64_t ring_value, const FixedPointEncodingProfile& profile) {
    return static_cast<double>(ring_value) / profile.scale_factor;
}

double compute_quantization_margin(std::uint64_t total_element_count,
                                   const FixedPointEncodingProfile& profile) {
    if (profile.scale_factor <= 0.0 || !std::isfinite(profile.scale_factor)) {
        return std::numeric_limits<double>::infinity();
    }
    const double per_element_bound = 0.5 / profile.scale_factor;
    return std::sqrt(static_cast<double>(total_element_count)) * per_element_bound;
}

double compute_effective_sensitivity(double clip_norm, double quantization_margin) {
    return clip_norm + quantization_margin;
}

}  // namespace fl::coordinator
