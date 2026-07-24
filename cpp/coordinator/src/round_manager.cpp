#include "fl_coordinator/round_manager.hpp"

#include <algorithm>
#include <random>

namespace fl::coordinator {

std::vector<std::string> select_cohort(
    const std::vector<std::string>& all_client_ids,
    std::uint64_t round_id,
    std::uint64_t seed,
    std::uint32_t target_clients_per_round
) {
    std::vector<std::string> pool = all_client_ids;
    std::sort(pool.begin(), pool.end());  // canonicalize input order first

    // Mix round_id into the seed so consecutive rounds don't all draw the
    // identical permutation, while staying a pure function of
    // (seed, round_id) for restart-time reproducibility.
    std::mt19937_64 rng(seed ^ (round_id * 0x9E3779B97F4A7C15ULL));
    std::shuffle(pool.begin(), pool.end(), rng);

    const auto count = std::min<std::size_t>(target_clients_per_round, pool.size());
    return std::vector<std::string>(pool.begin(), pool.begin() + static_cast<std::ptrdiff_t>(count));
}

}  // namespace fl::coordinator
