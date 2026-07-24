#include "fl_coordinator/worker_registry.hpp"

namespace fl::coordinator {

std::string to_string(WorkerStatus status) {
    switch (status) {
        case WorkerStatus::kRegistering:
            return "REGISTERING";
        case WorkerStatus::kIdle:
            return "IDLE";
        case WorkerStatus::kBusy:
            return "BUSY";
        case WorkerStatus::kUnhealthy:
            return "UNHEALTHY";
        case WorkerStatus::kDisconnected:
            return "DISCONNECTED";
        case WorkerStatus::kDraining:
            return "DRAINING";
        default:
            return "UNKNOWN";
    }
}

WorkerRegistryError::WorkerRegistryError(const std::string& what) : std::runtime_error(what) {}

WorkerRegistry::WorkerRegistry(std::uint32_t missed_heartbeat_threshold, double heartbeat_interval_seconds)
    : missed_heartbeat_threshold_(missed_heartbeat_threshold),
      heartbeat_interval_seconds_(heartbeat_interval_seconds) {}

WorkerInfo WorkerRegistry::register_worker(
    const std::string& worker_id, WorkerCapability capability, double now_unix_s
) {
    if (worker_id.empty()) {
        throw WorkerRegistryError("worker_id must not be empty");
    }
    std::lock_guard<std::mutex> lock(mutex_);
    auto existing = workers_.find(worker_id);
    if (existing != workers_.end() && existing->second.status != WorkerStatus::kDisconnected) {
        // Idempotent refresh: same worker re-registering (e.g. after a
        // network blip) rather than a duplicate-ID collision.
        existing->second.capability = std::move(capability);
        existing->second.last_heartbeat_unix_s = now_unix_s;
        return existing->second;
    }
    WorkerInfo info;
    info.worker_id = worker_id;
    info.capability = std::move(capability);
    info.status = WorkerStatus::kRegistering;
    info.registered_at_unix_s = now_unix_s;
    info.last_heartbeat_unix_s = now_unix_s;
    workers_[worker_id] = info;
    return info;
}

WorkerInfo WorkerRegistry::heartbeat(
    const std::string& worker_id, WorkerStatus status, const std::string& current_task_id, double now_unix_s
) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        throw WorkerRegistryError("heartbeat from unregistered worker_id: " + worker_id);
    }
    it->second.status = status;
    it->second.last_heartbeat_unix_s = now_unix_s;
    it->second.current_task_id = current_task_id;
    return it->second;
}

void WorkerRegistry::mark_disconnected(const std::string& worker_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        throw WorkerRegistryError("cannot disconnect unregistered worker_id: " + worker_id);
    }
    it->second.status = WorkerStatus::kDisconnected;
    it->second.current_task_id.clear();
}

std::vector<std::string> WorkerRegistry::sweep_unhealthy(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const double threshold_seconds =
        static_cast<double>(missed_heartbeat_threshold_) * heartbeat_interval_seconds_;
    std::vector<std::string> newly_unhealthy;
    for (auto& [worker_id, info] : workers_) {
        if (info.status == WorkerStatus::kDisconnected || info.status == WorkerStatus::kUnhealthy) {
            continue;
        }
        if (now_unix_s - info.last_heartbeat_unix_s > threshold_seconds) {
            info.status = WorkerStatus::kUnhealthy;
            newly_unhealthy.push_back(worker_id);
        }
    }
    return newly_unhealthy;
}

std::optional<WorkerInfo> WorkerRegistry::get(const std::string& worker_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::vector<WorkerInfo> WorkerRegistry::list() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<WorkerInfo> result;
    result.reserve(workers_.size());
    for (const auto& [worker_id, info] : workers_) {
        result.push_back(info);
    }
    return result;
}

std::size_t WorkerRegistry::healthy_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t count = 0;
    for (const auto& [worker_id, info] : workers_) {
        if (info.status == WorkerStatus::kIdle || info.status == WorkerStatus::kBusy) {
            ++count;
        }
    }
    return count;
}

std::size_t WorkerRegistry::registered_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return workers_.size();
}

void WorkerRegistry::set_current_task(const std::string& worker_id, const std::string& task_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        throw WorkerRegistryError("cannot assign task to unregistered worker_id: " + worker_id);
    }
    it->second.current_task_id = task_id;
    it->second.status = WorkerStatus::kBusy;
}

void WorkerRegistry::clear_current_task(const std::string& worker_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        return;
    }
    it->second.current_task_id.clear();
    if (it->second.status == WorkerStatus::kBusy) {
        it->second.status = WorkerStatus::kIdle;
    }
}

void WorkerRegistry::record_failure(const std::string& worker_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        return;
    }
    ++it->second.consecutive_failures;
}

void WorkerRegistry::record_success(const std::string& worker_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = workers_.find(worker_id);
    if (it == workers_.end()) {
        return;
    }
    it->second.consecutive_failures = 0;
}

}  // namespace fl::coordinator
