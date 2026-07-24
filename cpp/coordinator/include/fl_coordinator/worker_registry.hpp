#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

enum class WorkerStatus {
    kRegistering,
    kIdle,
    kBusy,
    kUnhealthy,
    kDisconnected,
    kDraining,
};

std::string to_string(WorkerStatus status);

struct WorkerCapability {
    std::string device;
    std::uint32_t cpu_count{0};
    bool gpu_available{false};
    std::uint32_t gpu_count{0};
    std::uint64_t available_memory_bytes{0};
    std::vector<std::string> supported_model_formats;
    std::vector<std::string> supported_algorithms;
};

struct WorkerInfo {
    std::string worker_id;
    WorkerCapability capability;
    WorkerStatus status{WorkerStatus::kRegistering};
    double registered_at_unix_s{0.0};
    double last_heartbeat_unix_s{0.0};
    std::string current_task_id;
    std::uint32_t consecutive_failures{0};
};

class WorkerRegistryError : public std::runtime_error {
public:
    explicit WorkerRegistryError(const std::string& what);
};

// Tracks every worker that has ever registered with this coordinator
// process. Time is always passed in explicitly (unix seconds) rather than
// read from the wall clock internally, so heartbeat-expiry behavior is
// deterministically testable without sleeping in tests.
class WorkerRegistry {
public:
    WorkerRegistry(std::uint32_t missed_heartbeat_threshold, double heartbeat_interval_seconds);

    // Registering an already-registered, non-disconnected worker_id is
    // treated as an idempotent refresh (a worker retrying registration
    // after a network blip should not be punished with an error) rather
    // than rejected outright. A worker_id that was previously marked
    // DISCONNECTED can always re-register cleanly.
    WorkerInfo register_worker(const std::string& worker_id, WorkerCapability capability, double now_unix_s);

    WorkerInfo heartbeat(
        const std::string& worker_id, WorkerStatus status, const std::string& current_task_id, double now_unix_s
    );

    void mark_disconnected(const std::string& worker_id);

    // Marks any worker whose last heartbeat is older than
    // missed_heartbeat_threshold * heartbeat_interval_seconds as
    // UNHEALTHY. Returns the worker_ids that transitioned to UNHEALTHY in
    // this sweep (not those already unhealthy), so callers can emit one
    // event per transition rather than one per sweep per worker.
    std::vector<std::string> sweep_unhealthy(double now_unix_s);

    [[nodiscard]] std::optional<WorkerInfo> get(const std::string& worker_id) const;
    [[nodiscard]] std::vector<WorkerInfo> list() const;
    [[nodiscard]] std::size_t healthy_count() const;
    [[nodiscard]] std::size_t registered_count() const;

    void set_current_task(const std::string& worker_id, const std::string& task_id);
    void clear_current_task(const std::string& worker_id);
    void record_failure(const std::string& worker_id);
    void record_success(const std::string& worker_id);

private:
    mutable std::mutex mutex_;
    std::map<std::string, WorkerInfo> workers_;
    std::uint32_t missed_heartbeat_threshold_;
    double heartbeat_interval_seconds_;
};

}  // namespace fl::coordinator
