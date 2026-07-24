#pragma once

#include <cstdint>

namespace fl::coordinator {

struct CoordinatorConfig {
    std::uint32_t max_concurrent_runs{16};
    std::uint32_t default_heartbeat_interval_seconds{10};
    std::uint32_t default_task_poll_interval_seconds{2};
    std::uint32_t missed_heartbeat_threshold{3};
    std::uint32_t default_task_lease_seconds{60};
    std::uint32_t default_max_task_retries{3};
    std::uint32_t event_bus_capacity_per_run{1000};
};

}  // namespace fl::coordinator
