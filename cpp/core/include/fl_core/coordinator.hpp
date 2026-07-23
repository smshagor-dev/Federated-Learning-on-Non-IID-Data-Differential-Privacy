#pragma once

#include "fl_core/aggregation.hpp"

#include <cstdint>
#include <optional>
#include <random>
#include <string>
#include <vector>

namespace fl::core {

enum class RunState {
    kCreated,
    kValidating,
    kInitializing,
    kReady,
    kQueued,
    kRunning,
    kWaitingForClients,
    kAggregating,
    kEvaluating,
    kCheckpointing,
    kPausing,
    kPaused,
    kCompleted,
    kFailed,
    kCanceling,
    kCanceled,
};

struct TransitionRecord {
    RunState from;
    RunState to;
    std::string timestamp;
    std::string reason;
};

class RunStateMachine {
public:
    explicit RunStateMachine(RunState initial = RunState::kCreated);

    [[nodiscard]] RunState state() const;
    [[nodiscard]] const std::vector<TransitionRecord>& history() const;
    void transition_to(RunState next, const std::string& reason, const std::string& timestamp);

private:
    [[nodiscard]] bool is_transition_allowed(RunState next) const;

    RunState state_;
    std::vector<TransitionRecord> history_;
};

struct ClientMetadata {
    std::string client_id;
    double availability_score{1.0};
    bool excluded{false};
    std::uint64_t last_selected_round{0};
};

struct WorkerMetadata {
    std::string worker_id;
    bool healthy{true};
    std::size_t max_clients{1};
};

struct SchedulingOptions {
    std::uint64_t seed{0};
    std::size_t target_clients{1};
    std::uint64_t current_round{1};
    std::uint64_t cooldown_rounds{0};
};

class ClientScheduler {
public:
    [[nodiscard]] std::vector<std::string> sample_clients(
        const std::vector<ClientMetadata>& clients,
        const SchedulingOptions& options
    ) const;
};

struct RoundContext {
    std::string run_id;
    std::uint64_t round_id{0};
    std::string model_version;
    AggregationAlgorithm algorithm{AggregationAlgorithm::kFedAvg};
    std::vector<std::string> selected_clients;
};

struct CoordinatorCheckpoint {
    std::string run_id;
    RunState run_state{RunState::kCreated};
    RoundContext round;
    OptimizerState optimizer_state;
};

class CheckpointStore {
public:
    static std::string serialize(const CoordinatorCheckpoint& checkpoint);
    static CoordinatorCheckpoint deserialize(const std::string& payload);
};

std::string to_string(RunState state);
RunState run_state_from_string(const std::string& value);

}  // namespace fl::core
