#include "fl_coordinator/run_manager.hpp"

// Preserve every implementation that is unrelated to round fault tolerance.
// Only the four entry points below are renamed and wrapped; the original source
// remains byte-for-byte in run_manager_legacy.cpp.
#define advance advance_legacy
#define acquire_task acquire_task_legacy
#define submit_client_result submit_client_result_legacy
#define create_run create_run_legacy
#include "run_manager_legacy.cpp"
#undef create_run
#undef submit_client_result
#undef acquire_task
#undef advance

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <stop_token>
#include <thread>

namespace fl::coordinator {
namespace {

struct RoundRuntimeState {
    std::string run_id;
    std::uint64_t round_id{0};
    double started_at_unix_s{0.0};
    double deadline_at_unix_s{0.0};
    std::uint32_t minimum_valid_results{0};
    std::map<std::string, std::uint32_t> retry_attempts;
    std::set<std::string> deferred_lease_clients;
    std::set<std::string> timed_out_clients;
};

std::string runtime_state_path(const std::string& directory, const std::string& run_id) {
    return (std::filesystem::path(directory) / (run_id + ".round-runtime")).string();
}

void persist_runtime_state(const std::string& path, const RoundRuntimeState& state) {
    std::ostringstream body;
    body << "schema_version=1\n";
    body << "run_id=" << state.run_id << "\n";
    body << "round_id=" << state.round_id << "\n";
    body << "started_at_unix_s=" << std::setprecision(17) << state.started_at_unix_s << "\n";
    body << "deadline_at_unix_s=" << std::setprecision(17) << state.deadline_at_unix_s << "\n";
    body << "minimum_valid_results=" << state.minimum_valid_results << "\n";
    body << "retry_attempt_count=" << state.retry_attempts.size() << "\n";
    for (const auto& [client_id, attempt] : state.retry_attempts) {
        body << "retry_attempt=" << client_id << "\t" << attempt << "\n";
    }
    body << "deferred_lease_count=" << state.deferred_lease_clients.size() << "\n";
    for (const auto& client_id : state.deferred_lease_clients) {
        body << "deferred_lease_client=" << client_id << "\n";
    }
    body << "timed_out_client_count=" << state.timed_out_clients.size() << "\n";
    for (const auto& client_id : state.timed_out_clients) {
        body << "timed_out_client=" << client_id << "\n";
    }

    const auto body_text = body.str();
    std::ostringstream payload;
    payload << body_text << "checksum=" << hash_to_hex(fnv1a_hash(body_text)) << "\n";

    std::filesystem::create_directories(std::filesystem::path(path).parent_path());
    const auto temporary = path + ".tmp";
    {
        std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
        if (!out) {
            throw std::runtime_error("failed to create round runtime state: " + temporary);
        }
        out << payload.str();
        out.flush();
        if (!out) {
            throw std::runtime_error("failed to write round runtime state: " + temporary);
        }
    }
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        std::filesystem::remove(path, error);
        error.clear();
        std::filesystem::rename(temporary, path, error);
        if (error) {
            throw std::runtime_error("failed to replace round runtime state: " + error.message());
        }
    }
}

std::optional<RoundRuntimeState> load_runtime_state(const std::string& path) {
    if (!std::filesystem::exists(path)) {
        return std::nullopt;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("failed to open round runtime state: " + path);
    }
    std::ostringstream buffer;
    buffer << in.rdbuf();
    const auto payload = buffer.str();
    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw std::runtime_error("round runtime state truncated: missing checksum");
    }
    const auto body = payload.substr(0, marker + 1);
    auto checksum = payload.substr(marker + std::string("\nchecksum=").size());
    while (!checksum.empty() && (checksum.back() == '\n' || checksum.back() == '\r')) {
        checksum.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum) {
        throw std::runtime_error("round runtime state checksum mismatch");
    }

    RoundRuntimeState state;
    std::size_t expected_attempts = 0;
    std::size_t expected_deferred = 0;
    std::size_t expected_timed_out = 0;
    bool schema_seen = false;
    std::stringstream stream(body);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty()) {
            continue;
        }
        const auto equals = line.find('=');
        if (equals == std::string::npos) {
            throw std::runtime_error("malformed round runtime state line");
        }
        const auto key = line.substr(0, equals);
        const auto value = line.substr(equals + 1);
        if (key == "schema_version") {
            if (value != "1") {
                throw std::runtime_error("unsupported round runtime state schema");
            }
            schema_seen = true;
        } else if (key == "run_id") {
            state.run_id = value;
        } else if (key == "round_id") {
            state.round_id = std::stoull(value);
        } else if (key == "started_at_unix_s") {
            state.started_at_unix_s = std::stod(value);
        } else if (key == "deadline_at_unix_s") {
            state.deadline_at_unix_s = std::stod(value);
        } else if (key == "minimum_valid_results") {
            state.minimum_valid_results = static_cast<std::uint32_t>(std::stoul(value));
        } else if (key == "retry_attempt_count") {
            expected_attempts = std::stoull(value);
        } else if (key == "retry_attempt") {
            const auto fields = split(value, '\t');
            if (fields.size() != 2) {
                throw std::runtime_error("malformed retry_attempt round runtime state line");
            }
            state.retry_attempts[fields[0]] =
                static_cast<std::uint32_t>(std::stoul(fields[1]));
        } else if (key == "deferred_lease_count") {
            expected_deferred = std::stoull(value);
        } else if (key == "deferred_lease_client") {
            state.deferred_lease_clients.insert(value);
        } else if (key == "timed_out_client_count") {
            expected_timed_out = std::stoull(value);
        } else if (key == "timed_out_client") {
            state.timed_out_clients.insert(value);
        }
    }
    if (!schema_seen || state.run_id.empty() || state.round_id == 0 ||
        state.minimum_valid_results == 0 || state.started_at_unix_s < 0.0 ||
        state.deadline_at_unix_s < 0.0) {
        throw std::runtime_error("round runtime state is incomplete or invalid");
    }
    if (state.retry_attempts.size() != expected_attempts ||
        state.deferred_lease_clients.size() != expected_deferred ||
        state.timed_out_clients.size() != expected_timed_out) {
        throw std::runtime_error("round runtime state count mismatch");
    }
    return state;
}

std::optional<std::chrono::milliseconds> configured_watchdog_interval() {
    const char* raw = std::getenv("FL_ROUND_WATCHDOG_INTERVAL_MS");
    if (raw == nullptr || *raw == '\0') {
        return std::nullopt;
    }
    try {
        const auto value = std::stoll(raw);
        if (value <= 0 || value > 60'000) {
            throw std::out_of_range("outside 1..60000 ms");
        }
        return std::chrono::milliseconds(value);
    } catch (const std::exception& error) {
        throw RunManagerError(std::string("invalid FL_ROUND_WATCHDOG_INTERVAL_MS: ") +
                              error.what());
    }
}

double runtime_now_unix_s() {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count()) /
           1000.0;
}

struct WatchdogRegistry {
    std::mutex mutex;
    std::map<RunManager*, std::unique_ptr<std::jthread>> threads;
};

WatchdogRegistry& watchdog_registry() {
    static WatchdogRegistry registry;
    return registry;
}

void ensure_watchdog(RunManager* manager, std::chrono::milliseconds interval) {
    auto& registry = watchdog_registry();
    std::lock_guard<std::mutex> lock(registry.mutex);
    if (registry.threads.contains(manager)) {
        return;
    }
    registry.threads[manager] = std::make_unique<std::jthread>(
        [manager, interval](std::stop_token stop_token) {
            while (!stop_token.stop_requested()) {
                const auto now = runtime_now_unix_s();
                for (const auto& run_id : manager->list_run_ids()) {
                    if (stop_token.stop_requested()) {
                        break;
                    }
                    try {
                        manager->get(run_id).advance(now);
                    } catch (const std::exception& error) {
                        std::cerr << "round watchdog run_id=" << run_id
                                  << " error=" << error.what() << '\n';
                    }
                }
                std::this_thread::sleep_for(interval);
            }
        });
}

}  // namespace

void RunInstance::advance(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto current = state_machine_.state();

    if (current == fl::core::RunState::kRunning) {
        if (current_round_id_ >= config_.max_rounds) {
            transition(fl::core::RunState::kCompleted, "max_rounds reached", now_unix_s);
            emit(CoordinatorEventType::kRunCompleted, "", now_unix_s);
            return;
        }
        begin_round(now_unix_s);
        timed_out_clients_.clear();
        round_started_at_unix_s_ = now_unix_s;
        round_deadline_at_unix_s_ =
            config_.round_timeout_seconds > 0
                ? now_unix_s + static_cast<double>(config_.round_timeout_seconds)
                : 0.0;
        RoundRuntimeState state;
        state.run_id = config_.run_id;
        state.round_id = current_round_id_;
        state.started_at_unix_s = round_started_at_unix_s_;
        state.deadline_at_unix_s = round_deadline_at_unix_s_;
        state.minimum_valid_results = config_.minimum_valid_results;
        persist_runtime_state(runtime_state_path(checkpoint_directory_, config_.run_id), state);
        transition(fl::core::RunState::kWaitingForClients, "round dispatched", now_unix_s);
        return;
    }

    if (current != fl::core::RunState::kWaitingForClients) {
        return;
    }

    const auto path = runtime_state_path(checkpoint_directory_, config_.run_id);
    auto loaded = load_runtime_state(path);
    RoundRuntimeState state;
    if (loaded.has_value() && loaded->run_id == config_.run_id &&
        loaded->round_id == current_round_id_) {
        state = *loaded;
    } else {
        // Additive migration for checkpoints created before this runtime state
        // existed. An active checkpointed lease proves at least one attempt was
        // already issued; the deadline starts at the first hardened tick.
        state.run_id = config_.run_id;
        state.round_id = current_round_id_;
        state.started_at_unix_s = now_unix_s;
        state.deadline_at_unix_s =
            config_.round_timeout_seconds > 0
                ? now_unix_s + static_cast<double>(config_.round_timeout_seconds)
                : 0.0;
        state.minimum_valid_results = config_.minimum_valid_results;
        for (const auto& [client_id, lease] : active_leases_) {
            state.retry_attempts[client_id] = 1;
            if (now_unix_s <= lease.lease_expires_at_unix_s) {
                state.deferred_lease_clients.insert(client_id);
            }
        }
        persist_runtime_state(path, state);
    }

    round_started_at_unix_s_ = state.started_at_unix_s;
    round_deadline_at_unix_s_ = state.deadline_at_unix_s;
    timed_out_clients_ = state.timed_out_clients;

    if (state.minimum_valid_results == 0 || current_cohort_.empty() ||
        state.minimum_valid_results > current_cohort_.size()) {
        transition(fl::core::RunState::kFailed,
                   "minimum_valid_results exceeds the selected cohort",
                   now_unix_s);
        emit(CoordinatorEventType::kRunFailed,
             "minimum_valid_results exceeds the selected cohort",
             now_unix_s);
        return;
    }

    bool checkpoint_changed = false;
    bool runtime_changed = false;

    if (!dispatcher_) {
        dispatcher_ =
            std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);
        std::vector<ClientTaskDescriptor> descriptors;
        for (const auto& client_id : current_cohort_) {
            if (round_results_.contains(client_id) || failed_clients_.contains(client_id)) {
                continue;
            }
            const auto lease = active_leases_.find(client_id);
            if (lease != active_leases_.end() &&
                now_unix_s <= lease->second.lease_expires_at_unix_s) {
                state.retry_attempts[client_id] =
                    std::max<std::uint32_t>(state.retry_attempts[client_id], 1);
                state.deferred_lease_clients.insert(client_id);
                runtime_changed = true;
                continue;
            }
            if (lease != active_leases_.end()) {
                worker_registry_->clear_current_task(lease->second.worker_id);
                active_leases_.erase(lease);
                checkpoint_changed = true;
            }
            descriptors.push_back(
                make_descriptor(config_, current_round_id_, model_version_, client_id));
        }
        dispatcher_->enqueue(descriptors, state.retry_attempts);
    }

    for (auto lease = active_leases_.begin(); lease != active_leases_.end();) {
        if (now_unix_s <= lease->second.lease_expires_at_unix_s) {
            ++lease;
            continue;
        }
        const auto client_id = lease->first;
        worker_registry_->clear_current_task(lease->second.worker_id);
        if (state.deferred_lease_clients.erase(client_id) > 0 &&
            !round_results_.contains(client_id) && !failed_clients_.contains(client_id)) {
            dispatcher_->enqueue(
                {make_descriptor(config_, current_round_id_, model_version_, client_id)},
                state.retry_attempts);
            runtime_changed = true;
        }
        lease = active_leases_.erase(lease);
        checkpoint_changed = true;
    }

    const auto permanently_failed = dispatcher_->sweep_expired_leases(now_unix_s);
    for (const auto& client_id : permanently_failed) {
        failed_clients_.insert(client_id);
        active_leases_.erase(client_id);
        checkpoint_changed = true;
    }
    for (const auto& client_id : dispatcher_->failed_client_ids()) {
        failed_clients_.insert(client_id);
    }

    if (runtime_changed) {
        persist_runtime_state(path, state);
    }
    if (checkpoint_changed) {
        save_checkpoint(now_unix_s);
    }

    const auto completed = round_results_.size();
    const auto settled = (completed + failed_clients_.size()) >= current_cohort_.size();

    const auto finalize_partial = [&]() {
        const auto configured_target = config_.target_clients_per_round;
        config_.target_clients_per_round =
            static_cast<std::uint32_t>(std::max<std::size_t>(completed, 1));
        try {
            finalize_round(now_unix_s);
        } catch (...) {
            config_.target_clients_per_round = configured_target;
            throw;
        }
        config_.target_clients_per_round = configured_target;
    };

    if (completed >= current_cohort_.size()) {
        finalize_round(now_unix_s);
        return;
    }

    if (settled) {
        if (completed >= state.minimum_valid_results) {
            finalize_partial();
        } else {
            transition(fl::core::RunState::kFailed,
                       "insufficient valid results after retry exhaustion for round " +
                           std::to_string(current_round_id_),
                       now_unix_s);
            emit(CoordinatorEventType::kRunFailed,
                 "insufficient valid results after retry exhaustion",
                 now_unix_s,
                 {{"completed_clients", std::to_string(completed)},
                  {"minimum_valid_results", std::to_string(state.minimum_valid_results)}});
        }
        return;
    }

    if (state.deadline_at_unix_s <= 0.0 || now_unix_s < state.deadline_at_unix_s) {
        return;
    }

    for (const auto& client_id : current_cohort_) {
        if (round_results_.contains(client_id) || failed_clients_.contains(client_id)) {
            continue;
        }
        if (const auto lease = active_leases_.find(client_id); lease != active_leases_.end()) {
            worker_registry_->clear_current_task(lease->second.worker_id);
            worker_registry_->record_failure(lease->second.worker_id);
            active_leases_.erase(lease);
        }
        failed_clients_.insert(client_id);
        timed_out_clients_.insert(client_id);
        state.timed_out_clients.insert(client_id);
        state.deferred_lease_clients.erase(client_id);
        emit(CoordinatorEventType::kTaskFailed,
             "round deadline exceeded",
             now_unix_s,
             {{"client_id", client_id}, {"failure_kind", "round_timeout"}});
    }
    persist_runtime_state(path, state);
    save_checkpoint(now_unix_s);

    if (completed >= state.minimum_valid_results) {
        finalize_partial();
    } else {
        transition(fl::core::RunState::kFailed,
                   "round deadline exceeded with insufficient valid results for round " +
                       std::to_string(current_round_id_),
                   now_unix_s);
        emit(CoordinatorEventType::kRunFailed,
             "round deadline exceeded with insufficient valid results",
             now_unix_s,
             {{"completed_clients", std::to_string(completed)},
              {"timed_out_clients", std::to_string(timed_out_clients_.size())},
              {"minimum_valid_results", std::to_string(state.minimum_valid_results)}});
    }
}

std::optional<DispatchedTask> RunInstance::acquire_task(const std::string& worker_id,
                                                        double now_unix_s) {
    advance(now_unix_s);
    auto task = acquire_task_legacy(worker_id, now_unix_s);
    if (!task.has_value()) {
        return std::nullopt;
    }

    std::lock_guard<std::mutex> lock(mutex_);
    const auto path = runtime_state_path(checkpoint_directory_, config_.run_id);
    auto state = load_runtime_state(path);
    if (state.has_value() && state->round_id == current_round_id_ &&
        state->run_id == config_.run_id) {
        state->retry_attempts[task->descriptor.client_id] = task->attempt;
        state->deferred_lease_clients.erase(task->descriptor.client_id);
        persist_runtime_state(path, *state);
    }
    return task;
}

bool RunInstance::submit_client_result(const std::string& worker_id,
                                       const std::string& task_id,
                                       const std::string& lease_id,
                                       ClientResultSubmission result,
                                       double now_unix_s,
                                       std::string& reason) {
    advance(now_unix_s);
    if (snapshot().state != fl::core::RunState::kWaitingForClients) {
        reason = "late result: round is no longer accepting client results";
        worker_registry_->clear_current_task(worker_id);
        worker_registry_->record_failure(worker_id);
        return false;
    }
    const auto accepted = submit_client_result_legacy(
        worker_id, task_id, lease_id, std::move(result), now_unix_s, reason);
    if (accepted) {
        advance(now_unix_s);
    }
    return accepted;
}

std::string RunManager::create_run(RunConfig config, double now_unix_s) {
    const auto watchdog_interval = configured_watchdog_interval();
    if (config.minimum_valid_results == 0 ||
        config.minimum_valid_results > config.target_clients_per_round ||
        (!config.client_ids.empty() && config.minimum_valid_results > config.client_ids.size())) {
        throw RunManagerError(
            "minimum_valid_results must be between 1 and the selectable cohort size");
    }
    auto run_id = create_run_legacy(std::move(config), now_unix_s);
    if (watchdog_interval.has_value()) {
        ensure_watchdog(this, *watchdog_interval);
    }
    return run_id;
}

RunManager::~RunManager() {
    std::unique_ptr<std::jthread> thread;
    auto& registry = watchdog_registry();
    {
        std::lock_guard<std::mutex> lock(registry.mutex);
        const auto found = registry.threads.find(this);
        if (found != registry.threads.end()) {
            found->second->request_stop();
            thread = std::move(found->second);
            registry.threads.erase(found);
        }
    }
    if (thread != nullptr && thread->joinable()) {
        thread->join();
    }
}

}  // namespace fl::coordinator
