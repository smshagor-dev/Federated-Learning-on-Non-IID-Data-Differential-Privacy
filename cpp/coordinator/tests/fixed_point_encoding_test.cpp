#include "fl_coordinator/secure_aggregation_encoding.hpp"
#include "test_support.hpp"

#include <cmath>
#include <limits>

namespace fl::coordinator::testing {

void run_fixed_point_encoding_tests() {
    using fl::coordinator::EncodingRejectionReason;
    using fl::coordinator::FixedPointEncodingProfile;
    using fl::coordinator::RoundingRule;
    using fl::coordinator::decode_value;
    using fl::coordinator::encode_value;
    using fl::coordinator::prove_domain_bounds;

    // Default profile round-trips a representative sample -- positive,
    // negative, zero, negative zero, and a value requiring rounding.
    {
        FixedPointEncodingProfile profile;

        const auto positive = encode_value(3.5, profile);
        check(positive.ok, "3.5 encodes successfully under the default profile");
        check(decode_value(positive.encoded, profile) == 3.5, "3.5 round-trips exactly (power-of-two scale factor)");

        const auto negative = encode_value(-3.5, profile);
        check(negative.ok, "-3.5 encodes successfully");
        check(decode_value(negative.encoded, profile) == -3.5, "-3.5 round-trips exactly");

        const auto zero = encode_value(0.0, profile);
        check(zero.ok && zero.encoded == 0, "0.0 encodes to exactly 0");

        const auto negative_zero = encode_value(-0.0, profile);
        check(negative_zero.ok && negative_zero.encoded == 0,
              "-0.0 encodes identically to 0.0 (no special-case branch needed)");

        // 1 / scale_factor is exactly representable (power-of-two scale
        // factor), so this is a genuine halfway case at the quantization
        // grid, not a floating-point-imprecision artifact.
        const double half_step = 0.5 / profile.scale_factor;
        const auto halfway = encode_value(half_step, profile);
        check(halfway.ok && halfway.encoded == 1, "a value exactly halfway between two grid points rounds away from zero");
        const auto halfway_negative = encode_value(-half_step, profile);
        check(halfway_negative.ok && halfway_negative.encoded == -1,
              "a negative halfway value rounds away from zero (toward -1, not 0)");
    }

    // Rejection cases: NaN, +/-Infinity, magnitude overflow.
    {
        FixedPointEncodingProfile profile;

        const auto nan_result = encode_value(std::numeric_limits<double>::quiet_NaN(), profile);
        check(!nan_result.ok && nan_result.reason == EncodingRejectionReason::kNonFiniteInput,
              "NaN is rejected as non-finite");

        const auto inf_result = encode_value(std::numeric_limits<double>::infinity(), profile);
        check(!inf_result.ok && inf_result.reason == EncodingRejectionReason::kNonFiniteInput,
              "+Infinity is rejected as non-finite");

        const auto neg_inf_result = encode_value(-std::numeric_limits<double>::infinity(), profile);
        check(!neg_inf_result.ok && neg_inf_result.reason == EncodingRejectionReason::kNonFiniteInput,
              "-Infinity is rejected as non-finite");

        const auto too_large = encode_value(profile.max_input_magnitude + 1.0, profile);
        check(!too_large.ok && too_large.reason == EncodingRejectionReason::kMagnitudeOverflow,
              "a value exceeding max_input_magnitude is rejected");

        const auto at_boundary = encode_value(profile.max_input_magnitude, profile);
        check(at_boundary.ok, "a value exactly at max_input_magnitude is accepted (inclusive boundary)");
    }

    // Quantization error is reported honestly for a non-exact value.
    {
        FixedPointEncodingProfile profile;
        const auto result = encode_value(1.0 / 3.0, profile);
        check(result.ok, "1/3 encodes successfully (well within magnitude bound)");
        check(result.quantization_error >= 0.0 && result.quantization_error < 1.0 / profile.scale_factor,
              "quantization error for a non-exact value is small and non-negative");
    }

    // Domain bounds proof: the default profile must be safe.
    {
        FixedPointEncodingProfile profile;
        const auto proof = prove_domain_bounds(profile);
        check(proof.safe, "the default fixed-point encoding profile proves domain-safe");
        check(!proof.computation_overflowed, "the default profile's bound computation does not overflow");
        check(proof.worst_case_aggregate_magnitude <
                  static_cast<std::uint64_t>(FixedPointEncodingProfile::kSignedDecodingBoundary),
              "the proven worst-case aggregate magnitude is strictly less than the signed decoding boundary");
    }

    // A deliberately unsafe profile (huge scale factor, huge cohort) is
    // rejected, not silently permitted.
    {
        FixedPointEncodingProfile profile;
        profile.scale_factor = 1e12;
        profile.max_input_magnitude = 1e6;
        profile.max_client_weight = std::numeric_limits<std::uint64_t>::max() / 2;
        profile.max_cohort_size = std::numeric_limits<std::uint64_t>::max() / 2;

        const auto proof = prove_domain_bounds(profile);
        check(!proof.safe, "a profile whose worst-case aggregate cannot fit is rejected as unsafe");
        check(!proof.explanation.empty(), "an unsafe profile's proof carries a human-readable explanation");
    }

    // An intermediate computation that itself overflows uint64_t is
    // reported as computation_overflowed, distinct from merely unsafe.
    {
        FixedPointEncodingProfile profile;
        profile.scale_factor = 1e300;
        profile.max_input_magnitude = 1e300;

        const auto proof = prove_domain_bounds(profile);
        check(!proof.safe, "a profile whose own single-value bound cannot fit in uint64_t is unsafe");
        check(proof.computation_overflowed,
              "the overflow is reported distinctly via computation_overflowed, not conflated with 'merely unsafe'");
    }

    // Golden fixture values -- mirrors
    // fixtures/secure_aggregation/fixed_point_encoding_golden.json
    // exactly (id-for-id, value-for-value). Every expected_encoded value
    // there was derived by hand (round-half-away-from-zero on
    // value * scale_factor, computed independently of this
    // implementation), not by running this code and capturing its
    // output -- see that file's header comment. This block is the C++
    // side of the cross-language golden-fixture requirement (Work
    // Package AL); a Python test loads the same stored file once the
    // Python mirror exists.
    {
        FixedPointEncodingProfile profile;  // must match the fixture's "profile" block exactly

        const auto positive_exact = encode_value(3.5, profile);
        check(positive_exact.ok && positive_exact.encoded == 3670016,
              "golden fixture 'positive_exact': encode(3.5) == 3670016");

        const auto negative_exact = encode_value(-3.5, profile);
        check(negative_exact.ok && negative_exact.encoded == -3670016,
              "golden fixture 'negative_exact': encode(-3.5) == -3670016");

        const auto zero = encode_value(0.0, profile);
        check(zero.ok && zero.encoded == 0, "golden fixture 'zero': encode(0.0) == 0");

        const auto negative_zero = encode_value(-0.0, profile);
        check(negative_zero.ok && negative_zero.encoded == 0, "golden fixture 'negative_zero': encode(-0.0) == 0");

        const auto halfway_positive = encode_value(0.000000476837158203125, profile);
        check(halfway_positive.ok && halfway_positive.encoded == 1,
              "golden fixture 'halfway_positive': encode(0.5/2^20) == 1");

        const auto halfway_negative = encode_value(-0.000000476837158203125, profile);
        check(halfway_negative.ok && halfway_negative.encoded == -1,
              "golden fixture 'halfway_negative': encode(-0.5/2^20) == -1");

        const auto non_terminating_positive = encode_value(0.3333333333333333, profile);
        check(non_terminating_positive.ok && non_terminating_positive.encoded == 349525,
              "golden fixture 'non_terminating_positive': encode(1/3) == 349525");

        const auto non_terminating_negative = encode_value(-0.3333333333333333, profile);
        check(non_terminating_negative.ok && non_terminating_negative.encoded == -349525,
              "golden fixture 'non_terminating_negative': encode(-1/3) == -349525");

        const auto max_safe_positive = encode_value(100.0, profile);
        check(max_safe_positive.ok && max_safe_positive.encoded == 104857600,
              "golden fixture 'max_safe_positive': encode(100.0) == 104857600");

        const auto max_safe_negative = encode_value(-100.0, profile);
        check(max_safe_negative.ok && max_safe_negative.encoded == -104857600,
              "golden fixture 'max_safe_negative': encode(-100.0) == -104857600");

        const auto near_max_positive = encode_value(99.999999, profile);
        check(near_max_positive.ok && near_max_positive.encoded == 104857599,
              "golden fixture 'near_max_positive': encode(99.999999) == 104857599");

        const auto near_max_negative = encode_value(-99.999999, profile);
        check(near_max_negative.ok && near_max_negative.encoded == -104857599,
              "golden fixture 'near_max_negative': encode(-99.999999) == -104857599");

        const auto magnitude_overflow_positive = encode_value(100.0000001, profile);
        check(!magnitude_overflow_positive.ok &&
                  magnitude_overflow_positive.reason == EncodingRejectionReason::kMagnitudeOverflow,
              "golden fixture 'magnitude_overflow_positive': rejected as magnitude_overflow");

        const auto magnitude_overflow_negative = encode_value(-100.0000001, profile);
        check(!magnitude_overflow_negative.ok &&
                  magnitude_overflow_negative.reason == EncodingRejectionReason::kMagnitudeOverflow,
              "golden fixture 'magnitude_overflow_negative': rejected as magnitude_overflow");

        check(decode_value(3670016, profile) == 3.5, "golden fixture 'decode_positive_exact': decode(3670016) == 3.5");
        check(decode_value(-3670016, profile) == -3.5,
              "golden fixture 'decode_negative_exact': decode(-3670016) == -3.5");
        check(decode_value(0, profile) == 0.0, "golden fixture 'decode_zero': decode(0) == 0.0");

        const auto bounds_proof = prove_domain_bounds(profile);
        check(bounds_proof.safe && !bounds_proof.computation_overflowed &&
                  bounds_proof.worst_case_aggregate_magnitude == 1048576010000000000ULL,
              "golden fixture 'default_profile_is_safe': worst-case aggregate magnitude == "
              "1048576010000000000, hand-derived from 104857601 * 1e6 * 1e4");
    }

    // Enum string round-trips.
    {
        check(to_string(RoundingRule::kRoundHalfAwayFromZero) == "round_half_away_from_zero",
              "RoundingRule::kRoundHalfAwayFromZero stringifies as expected");
        check(to_string(EncodingRejectionReason::kNone) == "none", "EncodingRejectionReason::kNone stringifies as 'none'");
        check(to_string(EncodingRejectionReason::kMagnitudeOverflow) == "magnitude_overflow",
              "EncodingRejectionReason::kMagnitudeOverflow stringifies as expected");
    }

    // Secure User-Level Differential Privacy Runtime slice, Work Area
    // H: compute_quantization_margin / compute_effective_sensitivity.
    // The Python mirror (user_level_clipping.py) is cross-checked
    // separately via manual computation matching this exact hand-
    // derived value (sqrt(1000) * 0.5/1048576.0 ~= 1.5078914929239174e-05),
    // not re-derived here from a different formula.
    {
        using fl::coordinator::compute_effective_sensitivity;
        using fl::coordinator::compute_quantization_margin;

        FixedPointEncodingProfile profile;
        const double margin = compute_quantization_margin(1000, profile);
        check(std::abs(margin - 1.5078914929239174e-05) < 1e-12,
              "compute_quantization_margin(1000, default profile) matches the hand-derived value "
              "sqrt(1000) * (0.5/scale_factor)");

        check(compute_quantization_margin(0, profile) == 0.0,
              "compute_quantization_margin: zero elements yields zero margin");

        const double bigger_margin = compute_quantization_margin(1'000'000, profile);
        check(bigger_margin > margin,
              "compute_quantization_margin: more elements yields a strictly larger margin (sqrt "
              "growth, monotonic)");

        FixedPointEncodingProfile zero_scale_profile = profile;
        zero_scale_profile.scale_factor = 0.0;
        check(std::isinf(compute_quantization_margin(1000, zero_scale_profile)),
              "compute_quantization_margin: a non-positive scale_factor yields +inf, never a "
              "silently-wrong finite value");

        const double sensitivity = compute_effective_sensitivity(2.5, margin);
        check(std::abs(sensitivity - (2.5 + margin)) < 1e-15,
              "compute_effective_sensitivity(clip_norm, margin) == clip_norm + margin exactly");

        // The "unsafe quantization margin" rejection boundary
        // AcquireTask's session-creation gate uses: effective_sensitivity
        // must stay strictly below max_input_magnitude for a clip_norm
        // this close to it to remain safely encodable.
        check(compute_effective_sensitivity(profile.max_input_magnitude, margin) >
                  profile.max_input_magnitude,
              "a clip_norm equal to max_input_magnitude always produces an effective_sensitivity "
              "that exceeds it once any nonzero quantization margin is added -- confirms the "
              "AcquireTask rejection boundary is reachable, not vacuous");
    }
}

}  // namespace fl::coordinator::testing
