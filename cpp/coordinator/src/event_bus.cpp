#include "fl_coordinator/event_bus.hpp"

namespace fl::coordinator {

std::string to_string(CoordinatorEventType type) {
    switch (type) {
        case CoordinatorEventType::kRunCreated:
            return "RUN_CREATED";
        case CoordinatorEventType::kRunValidated:
            return "RUN_VALIDATED";
        case CoordinatorEventType::kRunStarted:
            return "RUN_STARTED";
        case CoordinatorEventType::kRunPaused:
            return "RUN_PAUSED";
        case CoordinatorEventType::kRunResumed:
            return "RUN_RESUMED";
        case CoordinatorEventType::kRunCanceled:
            return "RUN_CANCELED";
        case CoordinatorEventType::kRunFailed:
            return "RUN_FAILED";
        case CoordinatorEventType::kRoundStarted:
            return "ROUND_STARTED";
        case CoordinatorEventType::kCohortSelected:
            return "COHORT_SELECTED";
        case CoordinatorEventType::kTaskAssigned:
            return "TASK_ASSIGNED";
        case CoordinatorEventType::kWorkerRegistered:
            return "WORKER_REGISTERED";
        case CoordinatorEventType::kWorkerUnhealthy:
            return "WORKER_UNHEALTHY";
        case CoordinatorEventType::kTaskProgress:
            return "TASK_PROGRESS";
        case CoordinatorEventType::kTaskCompleted:
            return "TASK_COMPLETED";
        case CoordinatorEventType::kTaskFailed:
            return "TASK_FAILED";
        case CoordinatorEventType::kClientResultAccepted:
            return "CLIENT_RESULT_ACCEPTED";
        case CoordinatorEventType::kClientResultRejected:
            return "CLIENT_RESULT_REJECTED";
        case CoordinatorEventType::kAggregationStarted:
            return "AGGREGATION_STARTED";
        case CoordinatorEventType::kAggregationCompleted:
            return "AGGREGATION_COMPLETED";
        case CoordinatorEventType::kModelVersionUpdated:
            return "MODEL_VERSION_UPDATED";
        case CoordinatorEventType::kCheckpointCompleted:
            return "CHECKPOINT_COMPLETED";
        case CoordinatorEventType::kRunCompleted:
            return "RUN_COMPLETED";
        default:
            return "UNKNOWN";
    }
}

EventBus::EventBus(std::size_t capacity_per_run) : capacity_per_run_(capacity_per_run) {}

CoordinatorEvent EventBus::publish(CoordinatorEvent event, const std::string& now_iso8601) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto& sequence = sequence_by_run_[event.run_id];
    ++sequence;
    event.event_id = event.run_id + ":" + std::to_string(sequence);
    if (event.timestamp.empty()) {
        event.timestamp = now_iso8601;
    }

    auto& history = history_by_run_[event.run_id];
    history.push_back(event);
    while (history.size() > capacity_per_run_) {
        history.pop_front();  // slow-subscriber policy: drop oldest
    }
    return event;
}

std::vector<CoordinatorEvent> EventBus::poll(
    const std::string& run_id, const std::string& after_event_id
) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<CoordinatorEvent> result;
    auto it = history_by_run_.find(run_id);
    if (it == history_by_run_.end()) {
        return result;
    }
    bool found_marker = after_event_id.empty();
    for (const auto& event : it->second) {
        if (found_marker) {
            result.push_back(event);
        } else if (event.event_id == after_event_id) {
            found_marker = true;
        }
    }
    // If after_event_id was never found (it fell out of the bounded
    // history under the slow-subscriber policy), return everything still
    // retained rather than nothing, so the caller advances instead of
    // spinning forever waiting for a marker that no longer exists.
    if (!found_marker) {
        return std::vector<CoordinatorEvent>(it->second.begin(), it->second.end());
    }
    return result;
}

std::size_t EventBus::history_size(const std::string& run_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = history_by_run_.find(run_id);
    return it == history_by_run_.end() ? 0 : it->second.size();
}

}  // namespace fl::coordinator
