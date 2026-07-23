#include "fl_core/coordinator.hpp"

#include <algorithm>
#include <sstream>
#include <stdexcept>

namespace fl::core {

namespace {

std::vector<RunState> allowed_next_states(RunState state) {
    switch (state) {
        case RunState::kCreated:
            return {RunState::kValidating, RunState::kFailed, RunState::kCanceled};
        case RunState::kValidating:
            return {RunState::kInitializing, RunState::kFailed, RunState::kCanceled};
        case RunState::kInitializing:
            return {RunState::kReady, RunState::kFailed, RunState::kCanceled};
        case RunState::kReady:
            return {RunState::kQueued, RunState::kFailed, RunState::kCanceled};
        case RunState::kQueued:
            return {RunState::kRunning, RunState::kCanceling, RunState::kFailed};
        case RunState::kRunning:
            return {RunState::kWaitingForClients, RunState::kPausing, RunState::kCanceling, RunState::kFailed};
        case RunState::kWaitingForClients:
            return {RunState::kAggregating, RunState::kPausing, RunState::kCanceling, RunState::kFailed};
        case RunState::kAggregating:
            return {RunState::kEvaluating, RunState::kCheckpointing, RunState::kFailed};
        case RunState::kEvaluating:
            return {RunState::kCheckpointing, RunState::kRunning, RunState::kCompleted, RunState::kFailed};
        case RunState::kCheckpointing:
            return {RunState::kRunning, RunState::kPaused, RunState::kCompleted, RunState::kFailed};
        case RunState::kPausing:
            return {RunState::kPaused, RunState::kFailed};
        case RunState::kPaused:
            return {RunState::kQueued, RunState::kCanceling, RunState::kFailed};
        case RunState::kCompleted:
        case RunState::kFailed:
        case RunState::kCanceled:
            return {};
        case RunState::kCanceling:
            return {RunState::kCanceled, RunState::kFailed};
        default:
            return {};
    }
}

std::vector<std::string> split(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream(value);
    std::string item;
    while (std::getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

}  // namespace

RunStateMachine::RunStateMachine(RunState initial) : state_(initial) {}

RunState RunStateMachine::state() const {
    return state_;
}

const std::vector<TransitionRecord>& RunStateMachine::history() const {
    return history_;
}

void RunStateMachine::transition_to(
    RunState next,
    const std::string& reason,
    const std::string& timestamp
) {
    if (!is_transition_allowed(next)) {
        throw std::invalid_argument("invalid state transition");
    }
    history_.push_back(TransitionRecord{
        .from = state_,
        .to = next,
        .timestamp = timestamp,
        .reason = reason,
    });
    state_ = next;
}

bool RunStateMachine::is_transition_allowed(RunState next) const {
    const auto next_states = allowed_next_states(state_);
    return std::find(next_states.begin(), next_states.end(), next) != next_states.end();
}

std::vector<std::string> ClientScheduler::sample_clients(
    const std::vector<ClientMetadata>& clients,
    const SchedulingOptions& options
) const {
    std::vector<ClientMetadata> eligible;
    for (const auto& client : clients) {
        if (client.excluded) {
            continue;
        }
        if (options.cooldown_rounds > 0 &&
            client.last_selected_round > 0 &&
            options.current_round > client.last_selected_round &&
            options.current_round - client.last_selected_round <= options.cooldown_rounds) {
            continue;
        }
        eligible.push_back(client);
    }
    if (eligible.empty()) {
        return {};
    }

    std::mt19937_64 rng(options.seed);
    std::shuffle(eligible.begin(), eligible.end(), rng);
    std::stable_sort(
        eligible.begin(),
        eligible.end(),
        [](const ClientMetadata& lhs, const ClientMetadata& rhs) {
            return lhs.availability_score > rhs.availability_score;
        }
    );

    std::vector<std::string> selected;
    const auto limit = std::min(options.target_clients, eligible.size());
    for (std::size_t index = 0; index < limit; ++index) {
        selected.push_back(eligible[index].client_id);
    }
    return selected;
}

std::string CheckpointStore::serialize(const CoordinatorCheckpoint& checkpoint) {
    std::ostringstream out;
    out << "run_id=" << checkpoint.run_id << "\n";
    out << "run_state=" << to_string(checkpoint.run_state) << "\n";
    out << "round_id=" << checkpoint.round.round_id << "\n";
    out << "model_version=" << checkpoint.round.model_version << "\n";
    out << "algorithm=" << to_string(checkpoint.round.algorithm) << "\n";
    out << "clients=";
    for (std::size_t index = 0; index < checkpoint.round.selected_clients.size(); ++index) {
        if (index > 0) {
            out << ",";
        }
        out << checkpoint.round.selected_clients[index];
    }
    out << "\n";
    out << "optimizer_step=" << checkpoint.optimizer_state.step << "\n";
    return out.str();
}

CoordinatorCheckpoint CheckpointStore::deserialize(const std::string& payload) {
    CoordinatorCheckpoint checkpoint;
    std::stringstream stream(payload);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty()) {
            continue;
        }
        const auto position = line.find('=');
        if (position == std::string::npos) {
            throw std::invalid_argument("invalid checkpoint line");
        }
        const auto key = line.substr(0, position);
        const auto value = line.substr(position + 1);
        if (key == "run_id") {
            checkpoint.run_id = value;
            checkpoint.round.run_id = value;
        } else if (key == "run_state") {
            checkpoint.run_state = run_state_from_string(value);
        } else if (key == "round_id") {
            checkpoint.round.round_id = std::stoull(value);
        } else if (key == "model_version") {
            checkpoint.round.model_version = value;
        } else if (key == "algorithm") {
            if (value == "fedavg") {
                checkpoint.round.algorithm = AggregationAlgorithm::kFedAvg;
            } else if (value == "fedprox") {
                checkpoint.round.algorithm = AggregationAlgorithm::kFedProx;
            } else if (value == "scaffold") {
                checkpoint.round.algorithm = AggregationAlgorithm::kScaffold;
            } else if (value == "fedadagrad") {
                checkpoint.round.algorithm = AggregationAlgorithm::kFedAdagrad;
            } else if (value == "fedadam") {
                checkpoint.round.algorithm = AggregationAlgorithm::kFedAdam;
            } else if (value == "fedyogi") {
                checkpoint.round.algorithm = AggregationAlgorithm::kFedYogi;
            } else {
                throw std::invalid_argument("unknown algorithm string");
            }
        } else if (key == "clients") {
            checkpoint.round.selected_clients = value.empty() ? std::vector<std::string>{} : split(value, ',');
        } else if (key == "optimizer_step") {
            checkpoint.optimizer_state.step = std::stoull(value);
        }
    }
    return checkpoint;
}

std::string to_string(RunState state) {
    switch (state) {
        case RunState::kCreated:
            return "CREATED";
        case RunState::kValidating:
            return "VALIDATING";
        case RunState::kInitializing:
            return "INITIALIZING";
        case RunState::kReady:
            return "READY";
        case RunState::kQueued:
            return "QUEUED";
        case RunState::kRunning:
            return "RUNNING";
        case RunState::kWaitingForClients:
            return "WAITING_FOR_CLIENTS";
        case RunState::kAggregating:
            return "AGGREGATING";
        case RunState::kEvaluating:
            return "EVALUATING";
        case RunState::kCheckpointing:
            return "CHECKPOINTING";
        case RunState::kPausing:
            return "PAUSING";
        case RunState::kPaused:
            return "PAUSED";
        case RunState::kCompleted:
            return "COMPLETED";
        case RunState::kFailed:
            return "FAILED";
        case RunState::kCanceling:
            return "CANCELING";
        case RunState::kCanceled:
            return "CANCELED";
        default:
            return "UNKNOWN";
    }
}

RunState run_state_from_string(const std::string& value) {
    if (value == "CREATED") return RunState::kCreated;
    if (value == "VALIDATING") return RunState::kValidating;
    if (value == "INITIALIZING") return RunState::kInitializing;
    if (value == "READY") return RunState::kReady;
    if (value == "QUEUED") return RunState::kQueued;
    if (value == "RUNNING") return RunState::kRunning;
    if (value == "WAITING_FOR_CLIENTS") return RunState::kWaitingForClients;
    if (value == "AGGREGATING") return RunState::kAggregating;
    if (value == "EVALUATING") return RunState::kEvaluating;
    if (value == "CHECKPOINTING") return RunState::kCheckpointing;
    if (value == "PAUSING") return RunState::kPausing;
    if (value == "PAUSED") return RunState::kPaused;
    if (value == "COMPLETED") return RunState::kCompleted;
    if (value == "FAILED") return RunState::kFailed;
    if (value == "CANCELING") return RunState::kCanceling;
    if (value == "CANCELED") return RunState::kCanceled;
    throw std::invalid_argument("unknown run state");
}

}  // namespace fl::core
