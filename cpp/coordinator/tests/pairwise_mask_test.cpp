#include "fl_coordinator/secure_aggregation_mask.hpp"
#include "test_support.hpp"

#include <cstdint>
#include <limits>
#include <vector>

namespace fl::coordinator::testing {

void run_pairwise_mask_tests() {
    using fl::coordinator::PairwiseMaskSign;
    using fl::coordinator::SignedMask;
    using fl::coordinator::apply_pairwise_mask;
    using fl::coordinator::mask_encoded_value;
    using fl::coordinator::participant_sorts_before;
    using fl::coordinator::resolve_pairwise_mask_sign;
    using fl::coordinator::sum_masked_values;

    // Canonical ordering is a plain ordinal byte comparison.
    {
        check(participant_sorts_before("worker-1", "worker-2"), "worker-1 sorts before worker-2");
        check(!participant_sorts_before("worker-2", "worker-1"), "worker-2 does not sort before worker-1");
        check(!participant_sorts_before("worker-1", "worker-1"), "a participant does not sort before itself");
    }

    // Sign resolution: lower-ordered adds, higher-ordered subtracts.
    {
        check(resolve_pairwise_mask_sign("worker-1", "worker-2") == PairwiseMaskSign::kAdd,
              "the lower-ordered participant (worker-1) adds against a higher peer (worker-2)");
        check(resolve_pairwise_mask_sign("worker-2", "worker-1") == PairwiseMaskSign::kSubtract,
              "the higher-ordered participant (worker-2) subtracts against a lower peer (worker-1)");
        expect_throw([]() { (void)resolve_pairwise_mask_sign("worker-1", "worker-1"); },
                     "resolving a pairwise sign against oneself is rejected as a caller error");
    }

    // Ring arithmetic: wraparound add/subtract.
    {
        check(apply_pairwise_mask(5, 3, PairwiseMaskSign::kAdd) == 8, "apply_pairwise_mask add is plain addition");
        check(apply_pairwise_mask(5, 3, PairwiseMaskSign::kSubtract) == 2,
              "apply_pairwise_mask subtract is plain subtraction");
        check(apply_pairwise_mask(0, 1, PairwiseMaskSign::kSubtract) == std::numeric_limits<std::uint64_t>::max(),
              "subtracting past zero wraps around the full 2^64 ring, exactly as ring subtraction requires");
        check(apply_pairwise_mask(std::numeric_limits<std::uint64_t>::max(), 1, PairwiseMaskSign::kAdd) == 0,
              "adding past the top of the ring wraps back to zero");
    }

    // mask_encoded_value combines a signed base value with a set of
    // pairwise masks entirely in ring arithmetic.
    {
        const std::vector<SignedMask> masks{
            SignedMask{10, PairwiseMaskSign::kAdd},
            SignedMask{4, PairwiseMaskSign::kSubtract},
        };
        const auto masked = mask_encoded_value(100, masks);
        check(masked == 106, "masking 100 with (+10, -4) yields 106");

        const auto masked_negative_base = mask_encoded_value(-100, masks);
        // -100 as uint64_t is (2^64 - 100); + 10 - 4 = 2^64 - 94.
        check(masked_negative_base == static_cast<std::uint64_t>(-94),
              "masking a negative base value stays correct under two's-complement ring reinterpretation");
    }

    // The core cancellation property: for a complete, correctly-ordered
    // cohort, the sum of every pairwise mask contribution (each applied
    // once as +mask by the lower participant and once as -mask by the
    // higher participant) is exactly zero in the ring. This is the
    // mathematical foundation the entire no-dropout masked-sum protocol
    // depends on -- proven here directly, not merely asserted in a
    // comment.
    {
        const std::vector<std::string> cohort{"worker-1", "worker-2", "worker-3", "worker-4"};
        // A fixed, arbitrary pairwise mask value per unordered pair.
        const std::uint64_t mask_ab = 0x1111111111111111ULL;
        const std::uint64_t mask_ac = 0x2222222222222222ULL;
        const std::uint64_t mask_ad = 0x3333333333333333ULL;
        const std::uint64_t mask_bc = 0x4444444444444444ULL;
        const std::uint64_t mask_bd = 0x5555555555555555ULL;
        const std::uint64_t mask_cd = 0x6666666666666666ULL;

        // worker-1 (lowest): adds every pairwise mask against 2, 3, 4.
        const std::uint64_t worker1_contribution =
            apply_pairwise_mask(apply_pairwise_mask(mask_ab, mask_ac, PairwiseMaskSign::kAdd), mask_ad,
                                 PairwiseMaskSign::kAdd);
        // worker-2: subtracts against 1 (lower), adds against 3, 4 (higher).
        std::uint64_t worker2_contribution = 0;
        worker2_contribution = apply_pairwise_mask(worker2_contribution, mask_ab, PairwiseMaskSign::kSubtract);
        worker2_contribution = apply_pairwise_mask(worker2_contribution, mask_bc, PairwiseMaskSign::kAdd);
        worker2_contribution = apply_pairwise_mask(worker2_contribution, mask_bd, PairwiseMaskSign::kAdd);
        // worker-3: subtracts against 1, 2 (lower), adds against 4 (higher).
        std::uint64_t worker3_contribution = 0;
        worker3_contribution = apply_pairwise_mask(worker3_contribution, mask_ac, PairwiseMaskSign::kSubtract);
        worker3_contribution = apply_pairwise_mask(worker3_contribution, mask_bc, PairwiseMaskSign::kSubtract);
        worker3_contribution = apply_pairwise_mask(worker3_contribution, mask_cd, PairwiseMaskSign::kAdd);
        // worker-4 (highest): subtracts every pairwise mask against 1, 2, 3.
        std::uint64_t worker4_contribution = 0;
        worker4_contribution = apply_pairwise_mask(worker4_contribution, mask_ad, PairwiseMaskSign::kSubtract);
        worker4_contribution = apply_pairwise_mask(worker4_contribution, mask_bd, PairwiseMaskSign::kSubtract);
        worker4_contribution = apply_pairwise_mask(worker4_contribution, mask_cd, PairwiseMaskSign::kSubtract);

        const auto total = sum_masked_values(
            {worker1_contribution, worker2_contribution, worker3_contribution, worker4_contribution});
        check(total == 0, "the sum of all pairwise mask contributions across a complete cohort of 4 is exactly zero");
    }

    // sum_masked_values on an empty set is zero (the additive identity),
    // and on a single value is that value unchanged.
    {
        check(sum_masked_values({}) == 0, "summing an empty set of masked values yields zero");
        check(sum_masked_values({42}) == 42, "summing a single masked value yields that value");
    }
}

}  // namespace fl::coordinator::testing
