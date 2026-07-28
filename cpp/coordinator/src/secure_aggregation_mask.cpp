#include "fl_coordinator/secure_aggregation_mask.hpp"

#include <stdexcept>

namespace fl::coordinator {

bool participant_sorts_before(const std::string& a, const std::string& b) {
    return a.compare(b) < 0;
}

PairwiseMaskSign resolve_pairwise_mask_sign(const std::string& self_participant_id,
                                            const std::string& peer_participant_id) {
    if (self_participant_id == peer_participant_id) {
        throw std::invalid_argument(
            "resolve_pairwise_mask_sign: a participant cannot derive a pairwise mask against "
            "itself (duplicate participant identity)");
    }
    return participant_sorts_before(self_participant_id, peer_participant_id)
               ? PairwiseMaskSign::kAdd
               : PairwiseMaskSign::kSubtract;
}

std::uint64_t apply_pairwise_mask(std::uint64_t accumulator,
                                  std::uint64_t mask,
                                  PairwiseMaskSign sign) {
    switch (sign) {
        case PairwiseMaskSign::kAdd:
            return accumulator + mask;  // defined-behavior uint64_t wraparound == ring addition
        case PairwiseMaskSign::kSubtract:
            return accumulator - mask;  // defined-behavior uint64_t wraparound == ring subtraction
    }
    return accumulator;
}

std::uint64_t mask_encoded_value(std::int64_t base_encoded_value,
                                 const std::vector<SignedMask>& pairwise_masks) {
    // Two's-complement reinterpretation: a signed int64_t value's bit
    // pattern, read as uint64_t, *is* the correct ring representative
    // for that signed value modulo 2^64 -- see
    // secure_aggregation_encoding.hpp's header comment for why this
    // requires no separate "encode into ring" step.
    auto accumulator = static_cast<std::uint64_t>(base_encoded_value);
    for (const auto& signed_mask : pairwise_masks) {
        accumulator = apply_pairwise_mask(accumulator, signed_mask.mask, signed_mask.sign);
    }
    return accumulator;
}

std::uint64_t sum_masked_values(const std::vector<std::uint64_t>& masked_values) {
    std::uint64_t sum = 0;
    for (const auto& value : masked_values) {
        sum += value;  // defined-behavior uint64_t wraparound == ring addition
    }
    return sum;
}

}  // namespace fl::coordinator
