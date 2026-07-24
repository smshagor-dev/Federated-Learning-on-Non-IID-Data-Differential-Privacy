#pragma once

#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <string>
#include <vector>

namespace fl::coordinator {

enum class CoordinatorEventType {
    kRunCreated,
    kRunValidated,
    kRunStarted,
    kRunPaused,
    kRunResumed,
    kRunCanceled,
    kRunFailed,
    kRoundStarted,
    kCohortSelected,
    kTaskAssigned,
    kWorkerRegistered,
    kWorkerUnhealthy,
    kTaskProgress,
    kTaskCompleted,
    kTaskFailed,
    kClientResultAccepted,
    kClientResultRejected,
    kAggregationStarted,
    kAggregationCompleted,
    kModelVersionUpdated,
    kCheckpointCompleted,
    kRunCompleted,
};

std::string to_string(CoordinatorEventType type);

// No tensor payloads, no secrets, no raw client updates ever go in an
// event — only identifiers, small scalars, and short strings. Every field
// below matches the required event categories; unused ones are left at
// their default (empty string / zero), never omitted, so a fixed-shape
// struct stays easy to reason about across languages instead of a
// variant-shaped payload.
struct CoordinatorEvent {
    std::string event_id;
    std::string run_id;
    std::uint64_t round_id{0};
    CoordinatorEventType type{CoordinatorEventType::kRunCreated};
    std::string client_id;
    std::string worker_id;
    std::string model_version;
    std::string timestamp;
    std::string trace_id;
    std::string reason;
    std::map<std::string, std::string> metadata;
};

// Bounded, per-run, in-order event history with pull-based subscription
// (a subscriber polls next() rather than being pushed to, which keeps
// this library dependency-free and is exactly what a gRPC server-stream
// handler would loop over). Slow-subscriber policy: once a run's history
// exceeds its capacity, the oldest events are dropped — a subscriber that
// falls behind by more than `capacity` events observes a gap rather than
// unbounded memory growth or blocking the publisher.
class EventBus {
public:
    explicit EventBus(std::size_t capacity_per_run = 1000);

    // Fills in event_id (monotonic per-run sequence) and timestamp if not
    // already set; returns the fully-populated event as published.
    CoordinatorEvent publish(CoordinatorEvent event, const std::string& now_iso8601);

    // Returns events for `run_id` with event_id greater than
    // `after_event_id` (empty string == from the beginning of what's
    // still retained), in publish order. This is what a StreamRunEvents
    // gRPC handler would call in a loop, using the last-seen event_id as
    // the next call's `after_event_id`.
    [[nodiscard]] std::vector<CoordinatorEvent> poll(
        const std::string& run_id, const std::string& after_event_id
    ) const;

    [[nodiscard]] std::size_t history_size(const std::string& run_id) const;

private:
    mutable std::mutex mutex_;
    std::size_t capacity_per_run_;
    std::map<std::string, std::deque<CoordinatorEvent>> history_by_run_;
    std::map<std::string, std::uint64_t> sequence_by_run_;
};

}  // namespace fl::coordinator
