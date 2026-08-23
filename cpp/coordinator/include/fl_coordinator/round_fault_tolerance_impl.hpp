#pragma once

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <sstream>
#include <stdexcept>
#include <system_error>
#include <thread>

namespace fl::coordinator::round_fault_tolerance_detail {

struct DeadlineRecord {
    std::string run_id;
    std::uint64_t round_id{0};
    double started_at_unix_s{0.0};
    double deadline_at_unix_s{0.0};
    std::uint32_t minimum_valid_results{0};
    std::set<std::string> timed_out_clients;
};

inline std::string deadline_path(const std::string& directory, const std::string& run_id) {
    return (std::filesystem::path(directory) / (run_id + ".round-deadline")).string();
}

inline void persist_deadline_record(const std::string& path, const DeadlineRecord& record) {
    std::filesystem::create_directories(std::filesystem::path(path).parent_path());
    const auto temporary = path + ".tmp";
    {
        std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
        if (!out) {
            throw std::runtime_error("failed to create round deadline state: " + temporary);
        }
        out << "schema_version=1\n";
        out << "run_id=" << record.run_id << "\n";
        out << "round_id=" << record.round_id << "\n";
        out << "started_at_unix_s=" << std::setprecision(17) << record.started_at_unix_s << "\n";
        out << "deadline_at_unix_s=" << std::setprecision(17) << record.deadline_at_unix_s << "\n";
        out << "minimum_valid_results=" << record.minimum_valid_results << "\n";
        out << "timed_out_client_count=" << record.timed_out_clients.size() << "\n";
        for (const auto& client_id : record.timed_out_clients) {
            out << "timed_out_client=" << client_id << "\n";
        }
        out.flush();
        if (!out) {
            throw std::runtime_error("failed to write round deadline state: " + temporary);
        }
    }
    std::error_code error;
    std::filesystem::rename(temporary, path, error);
    if (error) {
        std::filesystem::remove(path, error);
        error.clear();
        std::filesystem::rename(temporary, path, error);
        if (error) {
            throw std::runtime_error("failed to replace round deadline state: " + error.message());
        }
    }
}

inline std::optional<DeadlineRecord> load_deadline_record(const std::string& path) {
    if (!std::filesystem::exists(path)) {
        return std::nullopt;
    }
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("failed to open round deadline state: " + path);
    }
    DeadlineRecord record;
    std::size_t expected_timed_out = 0;
    bool schema_seen = false;
    std::string line;
    while (std::getline(in, line)) {
        if (line.empty()) {
            continue;
        }
        const auto position = line.find('=');
        if (position == std::string::npos) {
            throw std::runtime_error("malformed round deadline state line");
        }
        const auto key = line.substr(0, position);
        const auto value = line.substr(position + 1);
        if (key == "schema_version") {
            if (value != "1") {
                throw std::runtime_error("unsupported round deadline state schema");
            }
            schema_seen = true;
        } else if (key == "run_id") {
            record.run_id = value;
        } else if (key == "round_id") {
            record.round_id = std::stoull(value);
        } else if (key == "started_at_unix_s") {
            record.started_at_unix_s = std::stod(value);
        } else if (key == "deadline_at_unix_s") {
            record.deadline_at_unix_s = std::stod(value);
        } else if (key == "minimum_valid_results") {
            record.minimum_valid_results = static_cast<std::uint32_t>(std::stoul(value));
        } else if (key == "timed_out_client_count") {
            expected_timed_out = std::stoull(value);
        } else if (key == "timed_out_client") {
            record.timed_out_clients.insert(value);
        }
    }
    if (!schema_seen || record.run_id.empty() || record.round_id == 0 ||
        record.started_at_unix_s <= 0.0 || record.deadline_at_unix_s <= record.started_at_unix_s ||
        record.minimum_valid_results == 0) {
        throw std::runtime_error("round deadline state is incomplete or invalid");
    }
    if (record.timed_out_clients.size() != expected_timed_out) {
        throw std::runtime_error("round deadline state timed-out client count mismatch");
    }
    return record;
}

inline double system_now_unix_s() {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count()) /
           1000.0;
}

inline std::optional<std::chrono::milliseconds> watchdog_interval_from_env() {
    const char* raw = std::getenv("FL_ROUND_WATCHDOG_INTERVAL_MS");
    if (raw == nullptr || *raw == '\0') {
        return std::nullopt;
    }
    try {
        const auto value = std::stoll(raw);
        if (value <= 0 || value > 60'000) {
            throw std::out_of_range("watchdog interval outside supported range");
        }
        return std::chrono::milliseconds(value);
    } catch (const std::exception&) {
        throw std::runtime_error(
            "FL_ROUND_WATCHDOG_INTERVAL_MS must be an integer in the range 1..60000");
    }
}

}  // namespace fl::coordinator::round_fault_tolerance_detail

namespace fl::coordinator {

inline bool RunInstance::enforce_round_fault_tolerance(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (state_machine_.state() != fl::core::RunState::kWaitingForClients ||
        current_round_id_ == 0 || config_.round_timeout_seconds == 0) {
        return false;
    }

    const auto requested_minimum = minimum_result_policy_.requested;
    if (requested_minimum == 0 || requested_minimum > config_.target_clients_per_round) {
        transition(fl::core::RunState::kFailed,
                   "invalid minimum_valid_results for selected cohort",
                   now_unix_s);
        emit(CoordinatorEventType::kRunFailed,
             "invalid minimum_valid_results for selected cohort",
             now_unix_s);
        return true;
    }

    const auto path = round_fault_tolerance_detail::deadline_path(checkpoint_directory_, config_.run_id);
    auto persisted = round_fault_tolerance_detail::load_deadline_record(path);
    round_fault_tolerance_detail::DeadlineRecord deadline;
    if (persisted.has_value() && persisted->run_id == config_.run_id &&
        persisted->round_id == current_round_id_) {
        deadline = *persisted;
        if (deadline.minimum_valid_results != requested_minimum) {
            transition(fl::core::RunState::kFailed,
                       "persisted minimum_valid_results does not match run configuration",
                       now_unix_s);
            emit(CoordinatorEventType::kRunFailed,
                 "persisted minimum_valid_results does not match run configuration",
                 now_unix_s);
            return true;
        }
    } else {
        deadline.run_id = config_.run_id;
        deadline.round_id = current_round_id_;
        deadline.started_at_unix_s = now_unix_s;
        deadline.deadline_at_unix_s =
            now_unix_s + static_cast<double>(config_.round_timeout_seconds);
        deadline.minimum_valid_results = requested_minimum;
        round_fault_tolerance_detail::persist_deadline_record(path, deadline);
    }

    round_started_at_unix_s_ = deadline.started_at_unix_s;
    round_deadline_at_unix_s_ = deadline.deadline_at_unix_s;
    timed_out_clients_ = deadline.timed_out_clients;

    if (!dispatcher_) {
        rebuild_dispatcher_after_restore(now_unix_s);
    }
    for (const auto& client_id : dispatcher_->failed_client_ids()) {
        failed_clients_.insert(client_id);
        active_leases_.erase(client_id);
    }

    const auto completed = round_results_.size();
    const auto settled = (completed + failed_clients_.size()) >= current_cohort_.size();

    const auto finalize_with_actual_cohort = [&]() {
        // The privacy accountant was constructed from the configured client
        // selection probability and remains unchanged. Only the cleartext
        // central-noise sensitivity denominator must reflect the accepted
        // aggregate size when a deadline/dropout produces a partial cohort.
        const auto configured_target = config_.target_clients_per_round;
        config_.target_clients_per_round = static_cast<std::uint32_t>(completed);
        try {
            finalize_round(now_unix_s);
        } catch (...) {
            config_.target_clients_per_round = configured_target;
            throw;
        }
        config_.target_clients_per_round = configured_target;
    };

    if (settled && now_unix_s < round_deadline_at_unix_s_) {
        if (completed >= requested_minimum) {
            finalize_with_actual_cohort();
        } else {
            transition(fl::core::RunState::kFailed,
                       "insufficient valid results after retry exhaustion for round " +
                           std::to_string(current_round_id_),
                       now_unix_s);
            emit(CoordinatorEventType::kRunFailed,
                 "insufficient valid results after retry exhaustion",
                 now_unix_s,
                 {{"completed_clients", std::to_string(completed)},
                  {"minimum_valid_results", std::to_string(requested_minimum)}});
        }
        return true;
    }

    if (now_unix_s < round_deadline_at_unix_s_) {
        return false;
    }

    for (const auto& client_id : current_cohort_) {
        if (round_results_.contains(client_id) || failed_clients_.contains(client_id)) {
            continue;
        }
        auto lease = active_leases_.find(client_id);
        std::string worker_id;
        if (lease != active_leases_.end()) {
            worker_id = lease->second.worker_id;
            if (!worker_id.empty()) {
                worker_registry_->clear_current_task(worker_id);
            }
            active_leases_.erase(lease);
        }
        failed_clients_.insert(client_id);
        timed_out_clients_.insert(client_id);
        deadline.timed_out_clients.insert(client_id);
        emit(CoordinatorEventType::kTaskFailed,
             "round deadline exceeded",
             now_unix_s,
             {{"client_id", client_id},
              {"worker_id", worker_id},
              {"failure_kind", "round_timeout"},
              {"deadline_at_unix_s", std::to_string(round_deadline_at_unix_s_)}});
    }
    round_fault_tolerance_detail::persist_deadline_record(path, deadline);

    if (completed >= requested_minimum) {
        finalize_with_actual_cohort();
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
              {"minimum_valid_results", std::to_string(requested_minimum)}});
    }
    return true;
}

inline RoundFaultToleranceSnapshot RunInstance::round_fault_tolerance_snapshot(
    double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    RoundFaultToleranceSnapshot snapshot;
    snapshot.round_id = current_round_id_;
    snapshot.completed_clients = round_results_.size();
    snapshot.failed_clients = failed_clients_.size();
    snapshot.timed_out_clients = timed_out_clients_.size();
    snapshot.minimum_valid_results = minimum_result_policy_.requested;
    snapshot.round_started_at_unix_s = round_started_at_unix_s_;
    snapshot.round_deadline_at_unix_s = round_deadline_at_unix_s_;
    snapshot.deadline_reached =
        round_deadline_at_unix_s_ > 0.0 && now_unix_s >= round_deadline_at_unix_s_;

    const auto path = round_fault_tolerance_detail::deadline_path(checkpoint_directory_, config_.run_id);
    if (auto persisted = round_fault_tolerance_detail::load_deadline_record(path);
        persisted.has_value() && persisted->round_id == current_round_id_ &&
        persisted->run_id == config_.run_id) {
        snapshot.minimum_valid_results = persisted->minimum_valid_results;
        snapshot.round_started_at_unix_s = persisted->started_at_unix_s;
        snapshot.round_deadline_at_unix_s = persisted->deadline_at_unix_s;
        snapshot.timed_out_clients = persisted->timed_out_clients.size();
        snapshot.deadline_reached = now_unix_s >= persisted->deadline_at_unix_s;
    }
    return snapshot;
}

inline void RunManager::run_round_watchdog(std::stop_token stop_token) {
    std::optional<std::chrono::milliseconds> interval;
    try {
        interval = round_fault_tolerance_detail::watchdog_interval_from_env();
    } catch (const std::exception& error) {
        std::cerr << "round watchdog disabled: " << error.what() << '\n';
        return;
    }
    if (!interval.has_value()) {
        return;
    }

    while (!stop_token.stop_requested()) {
        const auto now = round_fault_tolerance_detail::system_now_unix_s();
        for (const auto& run_id : list_run_ids()) {
            if (stop_token.stop_requested()) {
                break;
            }
            try {
                get(run_id).enforce_round_fault_tolerance(now);
            } catch (const std::exception& error) {
                std::cerr << "round watchdog error: run_id=" << run_id
                          << " error=" << error.what() << '\n';
            }
        }
        std::this_thread::sleep_for(*interval);
    }
}

}  // namespace fl::coordinator
