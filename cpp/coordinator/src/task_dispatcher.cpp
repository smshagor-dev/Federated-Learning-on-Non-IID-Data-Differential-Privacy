#include "fl_coordinator/task_dispatcher.hpp"

namespace fl::coordinator {

TaskDispatcherError::TaskDispatcherError(const std::string& what) : std::runtime_error(what) {}

TaskDispatcher::TaskDispatcher(std::uint32_t lease_seconds, std::uint32_t max_retries)
    : lease_seconds_(lease_seconds), max_retries_(max_retries) {}

void TaskDispatcher::enqueue(const std::vector<ClientTaskDescriptor>& descriptors) {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& descriptor : descriptors) {
        const auto task_id = "task-" + std::to_string(++task_sequence_);
        DispatchedTask task;
        task.task_id = task_id;
        task.descriptor = descriptor;
        task.state = TaskState::kPending;
        tasks_[task_id] = std::move(task);
        pending_queue_.push_back(task_id);
    }
}

std::optional<DispatchedTask> TaskDispatcher::acquire(const std::string& worker_id, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (worker_active_task_.contains(worker_id)) {
        return std::nullopt;  // one active task per worker
    }
    if (pending_queue_.empty()) {
        return std::nullopt;
    }
    const auto task_id = pending_queue_.front();
    pending_queue_.pop_front();

    auto& task = tasks_.at(task_id);
    task.worker_id = worker_id;
    task.state = TaskState::kLeased;
    task.lease_id = "lease-" + std::to_string(++lease_sequence_);
    task.lease_expires_at_unix_s = now_unix_s + static_cast<double>(lease_seconds_);
    ++task.attempt;
    worker_active_task_[worker_id] = task_id;
    return task;
}

void TaskDispatcher::report_progress(
    const std::string& worker_id, const std::string& task_id, const std::string& lease_id
) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = tasks_.find(task_id);
    if (it == tasks_.end() || it->second.worker_id != worker_id || it->second.lease_id != lease_id) {
        throw TaskDispatcherError("progress report for unknown or mismatched task/lease: " + task_id);
    }
}

bool TaskDispatcher::submit_result(
    const std::string& worker_id,
    const std::string& task_id,
    const std::string& lease_id,
    ClientResultSubmission result,
    double now_unix_s,
    std::string& reason
) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = tasks_.find(task_id);
    if (it == tasks_.end()) {
        reason = "unknown task_id";
        return false;
    }
    auto& task = it->second;
    if (task.state == TaskState::kCompleted) {
        reason = "duplicate result: task already completed";
        return false;
    }
    if (task.state == TaskState::kFailed) {
        reason = "task already permanently failed (retries exhausted)";
        return false;
    }
    if (task.worker_id != worker_id || task.lease_id != lease_id) {
        reason = "lease mismatch: result does not belong to the current lease holder";
        return false;
    }
    if (now_unix_s > task.lease_expires_at_unix_s) {
        reason = "late result: lease already expired";
        return false;
    }

    task.state = TaskState::kCompleted;
    task.result = std::move(result);
    worker_active_task_.erase(worker_id);
    reason.clear();
    return true;
}

std::vector<std::string> TaskDispatcher::sweep_expired_leases(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> permanently_failed_client_ids;
    for (auto& [task_id, task] : tasks_) {
        if (task.state != TaskState::kLeased) {
            continue;
        }
        if (now_unix_s <= task.lease_expires_at_unix_s) {
            continue;
        }
        worker_active_task_.erase(task.worker_id);
        if (task.attempt >= max_retries_) {
            task.state = TaskState::kFailed;
            permanently_failed_client_ids.push_back(task.descriptor.client_id);
        } else {
            task.state = TaskState::kPending;
            task.worker_id.clear();
            task.lease_id.clear();
            pending_queue_.push_back(task_id);
        }
    }
    return permanently_failed_client_ids;
}

std::vector<ClientResultSubmission> TaskDispatcher::completed_results() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<ClientResultSubmission> results;
    for (const auto& [task_id, task] : tasks_) {
        if (task.state == TaskState::kCompleted && task.result.has_value()) {
            results.push_back(*task.result);
        }
    }
    return results;
}

std::size_t TaskDispatcher::pending_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return pending_queue_.size();
}

std::size_t TaskDispatcher::leased_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t count = 0;
    for (const auto& [task_id, task] : tasks_) {
        if (task.state == TaskState::kLeased) {
            ++count;
        }
    }
    return count;
}

std::size_t TaskDispatcher::completed_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t count = 0;
    for (const auto& [task_id, task] : tasks_) {
        if (task.state == TaskState::kCompleted) {
            ++count;
        }
    }
    return count;
}

std::size_t TaskDispatcher::failed_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::size_t count = 0;
    for (const auto& [task_id, task] : tasks_) {
        if (task.state == TaskState::kFailed) {
            ++count;
        }
    }
    return count;
}

std::vector<std::string> TaskDispatcher::failed_client_ids() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> ids;
    for (const auto& [task_id, task] : tasks_) {
        if (task.state == TaskState::kFailed) {
            ids.push_back(task.descriptor.client_id);
        }
    }
    return ids;
}

std::vector<std::string> TaskDispatcher::completed_client_ids() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> ids;
    for (const auto& [task_id, task] : tasks_) {
        if (task.state == TaskState::kCompleted) {
            ids.push_back(task.descriptor.client_id);
        }
    }
    return ids;
}

bool TaskDispatcher::all_tasks_settled() const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [task_id, task] : tasks_) {
        if (task.state != TaskState::kCompleted && task.state != TaskState::kFailed) {
            return false;
        }
    }
    return true;
}

}  // namespace fl::coordinator
