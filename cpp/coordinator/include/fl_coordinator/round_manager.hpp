#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace fl::coordinator {

struct RoundManagerConfig {
    std::uint32_t target_clients_per_round{1};
    std::uint32_t minimum_valid_results{1};
    std::uint32_t round_timeout_seconds{300};
    std::uint64_t client_selection_seed{0};
};

// Deterministic seeded client selection. Given the same
// (all_client_ids, round_id, seed), always returns the same cohort, in
// the same order — required so a coordinator restart (Work Package G)
// that replays round `round_id` selects the identical cohort rather than
// a different random sample.
std::vector<std::string> select_cohort(
    const std::vector<std::string>& all_client_ids,
    std::uint64_t round_id,
    std::uint64_t seed,
    std::uint32_t target_clients_per_round
);

}  // namespace fl::coordinator
