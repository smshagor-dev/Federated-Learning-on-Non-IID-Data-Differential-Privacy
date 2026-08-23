"""Temporary CI bootstrap for the round-deadline hardening branch only."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    data = target.read_text(encoding="utf-8")
    count = data.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected exactly one patch anchor, found {count}: {old[:100]!r}"
        )
    target.write_text(data.replace(old, new, 1), encoding="utf-8")


def insert_after(path: str, anchor: str, addition: str) -> None:
    replace_once(path, anchor, anchor + addition)


def patch() -> None:
    replace_once(
        "cpp/coordinator/include/fl_coordinator/task_dispatcher.hpp",
        "    void enqueue(const std::vector<ClientTaskDescriptor>& descriptors);\n",
        "    void enqueue(\n"
        "        const std::vector<ClientTaskDescriptor>& descriptors,\n"
        "        const std::map<std::string, std::uint32_t>& initial_attempts = {});\n",
    )

    replace_once(
        "cpp/coordinator/src/task_dispatcher.cpp",
        '''void TaskDispatcher::enqueue(const std::vector<ClientTaskDescriptor>& descriptors) {
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
''',
        '''void TaskDispatcher::enqueue(
    const std::vector<ClientTaskDescriptor>& descriptors,
    const std::map<std::string, std::uint32_t>& initial_attempts) {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& descriptor : descriptors) {
        const auto task_id = "task-" + std::to_string(++task_sequence_);
        DispatchedTask task;
        task.task_id = task_id;
        task.descriptor = descriptor;
        if (const auto attempt = initial_attempts.find(descriptor.client_id);
            attempt != initial_attempts.end()) {
            task.attempt = attempt->second;
        }
        if (task.attempt > 0 && task.attempt >= max_retries_) {
            task.state = TaskState::kFailed;
        } else {
            task.state = TaskState::kPending;
            pending_queue_.push_back(task_id);
        }
        tasks_[task_id] = std::move(task);
    }
}
''',
    )

    insert_after(
        "cpp/coordinator/include/fl_coordinator/run_manager.hpp",
        "    std::map<std::string, ActiveLease> active_leases_;\n",
        "    // Highest lease attempt already issued for each client in the current round.\n"
        "    // Checkpointed so a coordinator restart never resets max_task_retries.\n"
        "    std::map<std::string, std::uint32_t> client_retry_attempts_;\n",
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''      checkpoint_directory_(std::move(checkpoint_directory)),
      global_model_(make_zero_collection(config_.manifest)) {
    // Privacy Engineering phase: see docs/user-level-dp.md. Sample rate
''',
        '''      checkpoint_directory_(std::move(checkpoint_directory)),
      global_model_(make_zero_collection(config_.manifest)) {
    const auto selectable_clients =
        std::min<std::size_t>(config_.target_clients_per_round, config_.client_ids.size());
    if (config_.minimum_valid_results == 0 ||
        config_.minimum_valid_results > selectable_clients) {
        throw RunManagerError(
            "minimum_valid_results must be between 1 and the selectable cohort size");
    }

    // Privacy Engineering phase: see docs/user-level-dp.md. Sample rate
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''std::optional<RoundSnapshot> RunInstance::round_snapshot(std::uint64_t round_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (round_id != current_round_id_ || !dispatcher_) {
        return std::nullopt;
    }
    RoundSnapshot snapshot;
    snapshot.run_id = config_.run_id;
    snapshot.round_id = current_round_id_;
    snapshot.state = state_machine_.state();
    snapshot.selected_clients = current_cohort_;
    snapshot.completed_client_ids = dispatcher_->completed_client_ids();
    snapshot.failed_client_ids = dispatcher_->failed_client_ids();
    snapshot.minimum_valid_results = config_.minimum_valid_results;
    return snapshot;
}
''',
        '''std::optional<RoundSnapshot> RunInstance::round_snapshot(std::uint64_t round_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (round_id != current_round_id_) {
        return std::nullopt;
    }
    RoundSnapshot snapshot;
    snapshot.run_id = config_.run_id;
    snapshot.round_id = current_round_id_;
    snapshot.state = state_machine_.state();
    snapshot.selected_clients = current_cohort_;
    for (const auto& [client_id, _] : round_results_) {
        snapshot.completed_client_ids.push_back(client_id);
    }
    std::set<std::string> failed = failed_clients_;
    if (dispatcher_) {
        for (const auto& client_id : dispatcher_->failed_client_ids()) {
            failed.insert(client_id);
        }
    }
    snapshot.failed_client_ids.assign(failed.begin(), failed.end());
    snapshot.timed_out_client_ids.assign(timed_out_clients_.begin(), timed_out_clients_.end());
    snapshot.minimum_valid_results = config_.minimum_valid_results;
    snapshot.round_started_at_unix_s = round_started_at_unix_s_;
    snapshot.round_deadline_at_unix_s = round_deadline_at_unix_s_;
    return snapshot;
}
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    if (current == fl::core::RunState::kWaitingForClients) {
        if (!dispatcher_) {
            // dispatcher_ is never checkpointed (see round_results_'s
            // doc comment); after a restore into WAITING_FOR_CLIENTS,
            // reconstruct it before doing anything else.
            rebuild_dispatcher_after_restore(now_unix_s);
        }
        for (const auto& client_id : dispatcher_->failed_client_ids()) {
            failed_clients_.insert(client_id);
            active_leases_.erase(client_id);
        }
        const auto completed = round_results_.size();
        // A round is settled (no more results can possibly arrive) only
        // when every cohort member is accounted for as completed or
        // permanently failed — not when this process's freshly-rebuilt
        // dispatcher_ happens to hold no outstanding tasks, since tasks
        // still leased to a different (possibly still-running) process
        // are deliberately excluded from that rebuild and must not be
        // mistaken for "already resolved" (see
        // rebuild_dispatcher_after_restore).
        const auto settled = (completed + failed_clients_.size()) >= current_cohort_.size();
        if (completed >= config_.minimum_valid_results) {
            finalize_round(now_unix_s);
        } else if (settled) {
            transition(fl::core::RunState::kFailed,
                       "insufficient valid results for round " + std::to_string(current_round_id_),
                       now_unix_s);
            emit(CoordinatorEventType::kRunFailed, "insufficient valid results", now_unix_s);
        }
    }
''',
        '''    if (current == fl::core::RunState::kWaitingForClients) {
        if (config_.round_timeout_seconds > 0 && round_deadline_at_unix_s_ <= 0.0) {
            round_started_at_unix_s_ = now_unix_s;
            round_deadline_at_unix_s_ =
                now_unix_s + static_cast<double>(config_.round_timeout_seconds);
            save_checkpoint(now_unix_s);
        }
        if (!dispatcher_) {
            rebuild_dispatcher_after_restore(now_unix_s);
        }
        for (const auto& client_id : dispatcher_->failed_client_ids()) {
            failed_clients_.insert(client_id);
            active_leases_.erase(client_id);
        }

        const auto completed = round_results_.size();
        const auto settled = (completed + failed_clients_.size()) >= current_cohort_.size();
        if (completed >= current_cohort_.size()) {
            finalize_round(now_unix_s);
            return;
        }
        if (settled) {
            if (completed >= config_.minimum_valid_results) {
                finalize_round(now_unix_s);
            } else {
                transition(fl::core::RunState::kFailed,
                           "insufficient valid results after retry exhaustion for round " +
                               std::to_string(current_round_id_),
                           now_unix_s);
                emit(CoordinatorEventType::kRunFailed,
                     "insufficient valid results after retry exhaustion",
                     now_unix_s,
                     {{"completed_clients", std::to_string(completed)},
                      {"minimum_valid_results", std::to_string(config_.minimum_valid_results)}});
            }
            return;
        }

        if (round_deadline_at_unix_s_ > 0.0 && now_unix_s >= round_deadline_at_unix_s_) {
            for (const auto& client_id : current_cohort_) {
                if (round_results_.contains(client_id) || failed_clients_.contains(client_id)) {
                    continue;
                }
                std::string worker_id;
                const auto lease = active_leases_.find(client_id);
                if (lease != active_leases_.end()) {
                    worker_id = lease->second.worker_id;
                    if (!worker_id.empty()) {
                        worker_registry_->clear_current_task(worker_id);
                        worker_registry_->record_failure(worker_id);
                    }
                    active_leases_.erase(lease);
                }
                failed_clients_.insert(client_id);
                timed_out_clients_.insert(client_id);
                emit(CoordinatorEventType::kTaskFailed,
                     "round deadline exceeded",
                     now_unix_s,
                     {{"client_id", client_id},
                      {"worker_id", worker_id},
                      {"failure_kind", "round_timeout"}});
            }

            if (completed >= config_.minimum_valid_results) {
                finalize_round(now_unix_s);
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
                      {"minimum_valid_results", std::to_string(config_.minimum_valid_results)}});
            }
        }
    }
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    round_results_.clear();
    active_leases_.clear();
    failed_clients_.clear();
    dispatcher_ =
        std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);
''',
        '''    round_results_.clear();
    active_leases_.clear();
    failed_clients_.clear();
    timed_out_clients_.clear();
    client_retry_attempts_.clear();
    round_started_at_unix_s_ = now_unix_s;
    round_deadline_at_unix_s_ = config_.round_timeout_seconds > 0
                                    ? now_unix_s + static_cast<double>(config_.round_timeout_seconds)
                                    : 0.0;
    dispatcher_ =
        std::make_unique<TaskDispatcher>(config_.task_lease_seconds, config_.max_task_retries);
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    for (const auto& client_id : current_cohort_) {
        if (round_results_.contains(client_id)) {
            continue;  // already submitted; don't re-dispatch
        }
        const auto lease_it = active_leases_.find(client_id);
''',
        '''    for (const auto& client_id : current_cohort_) {
        if (round_results_.contains(client_id) || failed_clients_.contains(client_id)) {
            continue;  // already resolved; never re-dispatch after restore
        }
        const auto lease_it = active_leases_.find(client_id);
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    dispatcher_->enqueue(descriptors);
}

void RunInstance::finalize_round(double now_unix_s) {
''',
        '''    dispatcher_->enqueue(descriptors, client_retry_attempts_);
}

void RunInstance::finalize_round(double now_unix_s) {
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''        // bound, which is exact for uniform weighting and a documented
        // approximation for capped_sample_count/normalized_bounded (see
        // docs/user-level-dp.md's "central Gaussian noise" section).
        const double effective_cohort_size =
            std::max<std::uint32_t>(config_.target_clients_per_round, 1);
''',
        '''        // bound. A deadline/retry-driven partial cohort must use the
        // accepted update count or the coordinator would under-noise by
        // pretending missing clients contributed to the denominator.
        const double effective_cohort_size =
            static_cast<double>(std::max<std::size_t>(updates.size(), 1));
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    if (task.has_value()) {
        worker_registry_->set_current_task(worker_id, task->task_id);
        active_leases_[task->descriptor.client_id] =
''',
        '''    if (task.has_value()) {
        worker_registry_->set_current_task(worker_id, task->task_id);
        client_retry_attempts_[task->descriptor.client_id] = task->attempt;
        active_leases_[task->descriptor.client_id] =
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    const auto client_id = result.update.client_id;

    // the Algorithm Expansion phase: reject a submission carrying any tensor name the
''',
        '''    const auto client_id = result.update.client_id;

    if (state_machine_.state() == fl::core::RunState::kWaitingForClients &&
        round_deadline_at_unix_s_ > 0.0 && now_unix_s >= round_deadline_at_unix_s_) {
        reason = "late result: round deadline already exceeded";
        worker_registry_->clear_current_task(worker_id);
        worker_registry_->record_failure(worker_id);
        active_leases_.erase(client_id);
        failed_clients_.insert(client_id);
        timed_out_clients_.insert(client_id);
        emit(CoordinatorEventType::kClientResultRejected,
             reason,
             now_unix_s,
             {{"client_id", client_id}, {"task_id", task_id}, {"failure_kind", "round_timeout"}});
        save_checkpoint(now_unix_s);
        return false;
    }

    // the Algorithm Expansion phase: reject a submission carrying any tensor name the
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    body << "current_round=" << current_round_id_ << "\\n";
    body << "max_rounds=" << config_.max_rounds << "\\n";
''',
        '''    body << "current_round=" << current_round_id_ << "\\n";
    body << "round_started_at_unix_s=" << std::setprecision(17) << round_started_at_unix_s_ << "\\n";
    body << "round_deadline_at_unix_s=" << std::setprecision(17) << round_deadline_at_unix_s_ << "\\n";
    body << "max_rounds=" << config_.max_rounds << "\\n";
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    body << "failed_client_count=" << failed_clients_.size() << "\\n";
    for (const auto& client_id : failed_clients_) {
        body << "failed_client=" << client_id << "\\n";
    }
    body << "personalization_metric_count=" << personalization_metrics_by_client_.size() << "\\n";
''',
        '''    body << "client_retry_attempt_count=" << client_retry_attempts_.size() << "\\n";
    for (const auto& [client_id, attempt] : client_retry_attempts_) {
        body << "client_retry_attempt=" << client_id << "\\t" << attempt << "\\n";
    }
    body << "failed_client_count=" << failed_clients_.size() << "\\n";
    for (const auto& client_id : failed_clients_) {
        body << "failed_client=" << client_id << "\\n";
    }
    body << "timed_out_client_count=" << timed_out_clients_.size() << "\\n";
    for (const auto& client_id : timed_out_clients_) {
        body << "timed_out_client=" << client_id << "\\n";
    }
    body << "personalization_metric_count=" << personalization_metrics_by_client_.size() << "\\n";
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''        } else if (key == "current_round") {
            current_round_id_ = std::stoull(value);
        } else if (key == "model_version") {
''',
        '''        } else if (key == "current_round") {
            current_round_id_ = std::stoull(value);
        } else if (key == "round_started_at_unix_s") {
            round_started_at_unix_s_ = std::stod(value);
        } else if (key == "round_deadline_at_unix_s") {
            round_deadline_at_unix_s_ = std::stod(value);
        } else if (key == "model_version") {
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    if (found_leases != expected_leases) {
        throw std::runtime_error("coordinator checkpoint truncated for active_lease");
    }

    failed_clients_.clear();
''',
        '''    if (found_leases != expected_leases) {
        throw std::runtime_error("coordinator checkpoint truncated for active_lease");
    }

    client_retry_attempts_.clear();
    std::size_t expected_attempts = 0;
    std::size_t found_attempts = 0;
    for (const auto& [key, value] : fields) {
        if (key == "client_retry_attempt_count") {
            expected_attempts = std::stoull(value);
        } else if (key == "client_retry_attempt") {
            const auto parts = split(value, '\\t');
            if (parts.size() != 2) {
                throw std::runtime_error("malformed client_retry_attempt checkpoint line");
            }
            client_retry_attempts_[parts[0]] =
                static_cast<std::uint32_t>(std::stoul(parts[1]));
            ++found_attempts;
        }
    }
    if (found_attempts != expected_attempts) {
        throw std::runtime_error("coordinator checkpoint truncated for client_retry_attempt");
    }

    failed_clients_.clear();
''',
    )

    replace_once(
        "cpp/coordinator/src/run_manager.cpp",
        '''    if (found_failed != expected_failed) {
        throw std::runtime_error("coordinator checkpoint truncated for failed_client");
    }

    personalization_metrics_by_client_.clear();
''',
        '''    if (found_failed != expected_failed) {
        throw std::runtime_error("coordinator checkpoint truncated for failed_client");
    }

    timed_out_clients_.clear();
    std::size_t expected_timed_out = 0;
    std::size_t found_timed_out = 0;
    for (const auto& [key, value] : fields) {
        if (key == "timed_out_client_count") {
            expected_timed_out = std::stoull(value);
        } else if (key == "timed_out_client") {
            timed_out_clients_.insert(value);
            ++found_timed_out;
        }
    }
    if (found_timed_out != expected_timed_out) {
        throw std::runtime_error("coordinator checkpoint truncated for timed_out_client");
    }

    personalization_metrics_by_client_.clear();
''',
    )

    replace_once(
        "cpp/coordinator/main.cpp",
        '''#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
''',
        '''#include <atomic>
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
''',
    )

    replace_once(
        "cpp/coordinator/main.cpp",
        '''    fl::coordinator::CoordinatorConfig config;
    fl::coordinator::RunManager manager(config, "checkpoints", "scaffold_state");

''',
        '''    fl::coordinator::CoordinatorConfig config;
    fl::coordinator::RunManager manager(config, "checkpoints", "scaffold_state");

    std::chrono::milliseconds round_watchdog_interval{1000};
    if (const char* raw = std::getenv("FL_ROUND_WATCHDOG_INTERVAL_MS"); raw != nullptr) {
        try {
            const auto parsed = std::stoull(raw);
            if (parsed == 0 || parsed > 60000) {
                throw std::out_of_range("watchdog interval outside 1..60000 ms");
            }
            round_watchdog_interval = std::chrono::milliseconds(parsed);
        } catch (const std::exception& error) {
            std::cerr << "invalid FL_ROUND_WATCHDOG_INTERVAL_MS: " << error.what() << "\\n";
            return 1;
        }
    }

''',
    )

    replace_once(
        "cpp/coordinator/main.cpp",
        '''    std::cout << "fl coordinator gRPC server listening on " << bind_address
              << " (transport_mode=" << fl::coordinator::to_string(transport_mode) << ")"
              << std::endl;
    server->Wait();
    return 0;
}
''',
        '''    std::cout << "fl coordinator gRPC server listening on " << bind_address
              << " (transport_mode=" << fl::coordinator::to_string(transport_mode) << ")"
              << std::endl;

    std::atomic<bool> watchdog_stop{false};
    std::thread round_watchdog([&]() {
        while (!watchdog_stop.load(std::memory_order_relaxed)) {
            const auto tick = now_unix_s();
            for (const auto& run_id : manager.list_run_ids()) {
                try {
                    manager.get(run_id).advance(tick);
                } catch (const std::exception& error) {
                    std::cerr << "round watchdog run_id=" << run_id
                              << " error=" << error.what() << std::endl;
                }
            }
            std::this_thread::sleep_for(round_watchdog_interval);
        }
    });

    server->Wait();
    watchdog_stop.store(true, std::memory_order_relaxed);
    round_watchdog.join();
    return 0;
}
''',
    )

    replace_once(
        "infra/compose/docker-compose.dev.yml",
        '''    environment:
      FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT: "true"
    healthcheck:
''',
        '''    environment:
      FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT: "true"
      FL_ROUND_WATCHDOG_INTERVAL_MS: "${FL_ROUND_WATCHDOG_INTERVAL_MS:-1000}"
    healthcheck:
''',
    )

    test_file = ROOT / "cpp/coordinator/tests/round_fault_tolerance_test.cpp"
    if test_file.exists():
        raise RuntimeError(f"{test_file}: expected new file")
    test_file.write_text(
        r'''#include "fl_coordinator/run_manager.hpp"
#include "test_support.hpp"

#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

namespace fl::coordinator::testing {
namespace {

fl::core::ModelManifest fault_manifest() {
    return fl::core::ModelManifest{
        .model_id = "fault-toy",
        .model_version = "v0",
        .tensors = {fl::core::TensorDescriptor{
            .name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}},
    };
}

RunConfig fault_config(const std::string& run_id,
                       std::uint32_t clients,
                       std::uint32_t minimum,
                       std::uint32_t timeout_seconds,
                       std::uint32_t lease_seconds = 30,
                       std::uint32_t retries = 3) {
    RunConfig config;
    config.run_id = run_id;
    config.manifest = fault_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.target_clients_per_round = clients;
    config.total_clients = clients;
    config.max_rounds = 1;
    config.minimum_valid_results = minimum;
    config.round_timeout_seconds = timeout_seconds;
    config.task_lease_seconds = lease_seconds;
    config.max_task_retries = retries;
    for (std::uint32_t index = 0; index < clients; ++index) {
        config.client_ids.push_back("client-" + std::to_string(index));
    }
    return config;
}

ClientResultSubmission fault_result(const DispatchedTask& task, double value) {
    ClientResultSubmission result;
    result.update.run_id = task.descriptor.run_id;
    result.update.round_id = task.descriptor.round_id;
    result.update.client_id = task.descriptor.client_id;
    result.update.update_id = "update-" + task.descriptor.client_id;
    result.update.nonce = "nonce-" + task.descriptor.client_id;
    result.update.base_model_version = task.descriptor.model_version;
    result.update.algorithm = task.descriptor.algorithm;
    result.update.sample_count = 1;
    result.update.delta.insert(fl::core::TensorBuffer(
        fl::core::TensorDescriptor{
            .name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32},
        {value}));
    return result;
}

void register_fault_workers(RunManager& manager, std::uint32_t count) {
    for (std::uint32_t index = 0; index < count; ++index) {
        manager.worker_registry().register_worker(
            "worker-" + std::to_string(index), WorkerCapability{}, 0.0);
    }
}

std::vector<DispatchedTask> acquire_fault_tasks(RunInstance& run,
                                                std::uint32_t count,
                                                double now) {
    std::vector<DispatchedTask> tasks;
    for (std::uint32_t index = 0; index < count; ++index) {
        auto task = run.acquire_task("worker-" + std::to_string(index), now);
        check(task.has_value(), "expected selected client task to be available");
        if (task.has_value()) {
            tasks.push_back(*task);
        }
    }
    return tasks;
}

}  // namespace

void run_round_fault_tolerance_tests(const std::string& scratch_dir) {
    std::filesystem::remove_all(scratch_dir);
    CoordinatorConfig coordinator_config;

    {
        RunManager manager(coordinator_config, scratch_dir + "/early/cp", scratch_dir + "/early/sc");
        register_fault_workers(manager, 3);
        manager.create_run(fault_config("no-early", 3, 2, 10), 100.0);
        auto& run = manager.get("no-early");
        run.start("trace", 100.0);
        run.advance(100.0);
        const auto tasks = acquire_fault_tasks(run, 3, 100.0);
        std::string reason;
        check(run.submit_client_result("worker-0", tasks[0].task_id, tasks[0].lease_id,
                                       fault_result(tasks[0], 1.0), 101.0, reason),
              "first result accepted");
        check(run.submit_client_result("worker-1", tasks[1].task_id, tasks[1].lease_id,
                                       fault_result(tasks[1], 1.0), 102.0, reason),
              "second result accepted");
        run.advance(105.0);
        check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
              "minimum quorum does not finalize before deadline while another client can finish");
        check(run.snapshot().model_version == "v0", "model is not released by fastest quorum");
        const auto round = run.round_snapshot(1);
        check(round.has_value() && std::abs(round->round_deadline_at_unix_s - 110.0) < 1e-9,
              "round snapshot exposes the absolute deadline");

        check(run.submit_client_result("worker-2", tasks[2].task_id, tasks[2].lease_id,
                                       fault_result(tasks[2], 1.0), 106.0, reason),
              "third result accepted");
        run.advance(106.0);
        check(run.snapshot().state == fl::core::RunState::kCompleted &&
                  run.snapshot().model_version == "v1",
              "full cohort finalizes normally before deadline");
    }

    {
        RunManager manager(coordinator_config,
                           scratch_dir + "/partial/cp",
                           scratch_dir + "/partial/sc");
        register_fault_workers(manager, 3);
        manager.create_run(fault_config("partial", 3, 2, 10), 200.0);
        auto& run = manager.get("partial");
        run.start("trace", 200.0);
        run.advance(200.0);
        const auto tasks = acquire_fault_tasks(run, 3, 200.0);
        std::string reason;
        check(run.submit_client_result("worker-0", tasks[0].task_id, tasks[0].lease_id,
                                       fault_result(tasks[0], 1.0), 201.0, reason),
              "partial first result accepted");
        check(run.submit_client_result("worker-1", tasks[1].task_id, tasks[1].lease_id,
                                       fault_result(tasks[1], 1.0), 202.0, reason),
              "partial second result accepted");
        run.advance(209.0);
        check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
              "partial cohort waits until deadline");
        run.advance(210.0);
        check(run.snapshot().state == fl::core::RunState::kCompleted &&
                  run.snapshot().model_version == "v1",
              "deadline releases partial cohort when quorum is satisfied");
        const auto round = run.round_snapshot(1);
        check(round.has_value() && round->timed_out_client_ids.size() == 1,
              "deadline records unresolved client as timed out");
    }

    {
        RunManager manager(coordinator_config,
                           scratch_dir + "/insufficient/cp",
                           scratch_dir + "/insufficient/sc");
        register_fault_workers(manager, 3);
        manager.create_run(fault_config("insufficient", 3, 2, 10), 300.0);
        auto& run = manager.get("insufficient");
        run.start("trace", 300.0);
        run.advance(300.0);
        const auto tasks = acquire_fault_tasks(run, 3, 300.0);
        std::string reason;
        check(run.submit_client_result("worker-0", tasks[0].task_id, tasks[0].lease_id,
                                       fault_result(tasks[0], 1.0), 301.0, reason),
              "insufficient first result accepted");
        run.advance(310.0);
        check(run.snapshot().state == fl::core::RunState::kFailed,
              "deadline fails run when quorum is not satisfied");
        check(run.snapshot().model_version == "v0",
              "failed deadline never publishes below quorum");
        const auto round = run.round_snapshot(1);
        check(round.has_value() && round->timed_out_client_ids.size() == 2,
              "all unresolved clients are classified as timed out");
    }

    {
        const auto config = fault_config("retry-recovery", 1, 1, 30, 5, 2);
        {
            RunManager manager(coordinator_config, scratch_dir + "/retry/cp", scratch_dir + "/retry/sc");
            register_fault_workers(manager, 1);
            manager.create_run(config, 400.0);
            auto& run = manager.get("retry-recovery");
            run.start("trace", 400.0);
            run.advance(400.0);
            const auto first = run.acquire_task("worker-0", 400.0);
            check(first.has_value() && first->attempt == 1, "first lease uses attempt one");
        }
        {
            RunManager manager(coordinator_config, scratch_dir + "/retry/cp", scratch_dir + "/retry/sc");
            register_fault_workers(manager, 1);
            manager.create_run(config, 406.0);
            auto& run = manager.get("retry-recovery");
            run.restore_from_checkpoint();
            const auto second = run.acquire_task("worker-0", 406.0);
            check(second.has_value() && second->attempt == 2,
                  "restart resumes retry counter instead of resetting it");
        }
        {
            RunManager manager(coordinator_config, scratch_dir + "/retry/cp", scratch_dir + "/retry/sc");
            register_fault_workers(manager, 1);
            manager.create_run(config, 412.0);
            auto& run = manager.get("retry-recovery");
            run.restore_from_checkpoint();
            run.advance(412.0);
            check(run.snapshot().state == fl::core::RunState::kFailed,
                  "expired final retry remains exhausted after another restart");
            check(!run.acquire_task("worker-0", 412.0).has_value(),
                  "no third lease exists after retry exhaustion");
        }
    }

    {
        const auto config = fault_config("deadline-recovery", 2, 1, 20, 30, 3);
        {
            RunManager manager(coordinator_config,
                               scratch_dir + "/deadline/cp",
                               scratch_dir + "/deadline/sc");
            manager.create_run(config, 500.0);
            auto& run = manager.get("deadline-recovery");
            run.start("trace", 500.0);
            run.advance(500.0);
            const auto round = run.round_snapshot(1);
            check(round.has_value() && std::abs(round->round_deadline_at_unix_s - 520.0) < 1e-9,
                  "initial deadline is fixed from round dispatch time");
        }
        {
            RunManager manager(coordinator_config,
                               scratch_dir + "/deadline/cp",
                               scratch_dir + "/deadline/sc");
            manager.create_run(config, 510.0);
            auto& run = manager.get("deadline-recovery");
            run.restore_from_checkpoint();
            const auto restored = run.round_snapshot(1);
            check(restored.has_value() &&
                      std::abs(restored->round_deadline_at_unix_s - 520.0) < 1e-9,
                  "restart preserves original absolute deadline");
            run.advance(519.0);
            check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
                  "restored run remains active before original deadline");
            run.advance(520.0);
            check(run.snapshot().state == fl::core::RunState::kFailed,
                  "restored run expires at original deadline");
        }
    }

    {
        RunManager manager(coordinator_config,
                           scratch_dir + "/invalid/cp",
                           scratch_dir + "/invalid/sc");
        expect_throw(
            [&]() { manager.create_run(fault_config("invalid", 2, 3, 10), 0.0); },
            "minimum_valid_results above selectable cohort is rejected");
    }
}

}  // namespace fl::coordinator::testing
''',
        encoding="utf-8",
    )

    replace_once(
        "cpp/CMakeLists.txt",
        '''    coordinator/tests/secure_aggregation_session_store_test.cpp
    coordinator/tests/test_main.cpp
''',
        '''    coordinator/tests/secure_aggregation_session_store_test.cpp
    coordinator/tests/round_fault_tolerance_test.cpp
    coordinator/tests/test_main.cpp
''',
    )

    replace_once(
        "cpp/coordinator/tests/test_main.cpp",
        '''void run_secure_aggregation_session_store_tests(const std::string& scratch_dir);
}  // namespace fl::coordinator::testing
''',
        '''void run_secure_aggregation_session_store_tests(const std::string& scratch_dir);
void run_round_fault_tolerance_tests(const std::string& scratch_dir);
}  // namespace fl::coordinator::testing
''',
    )
    replace_once(
        "cpp/coordinator/tests/test_main.cpp",
        '''    guarded("[29/29] secure_aggregation_session_store", [&]() {
        fl::coordinator::testing::run_secure_aggregation_session_store_tests(
            scratch_dir + "/secure_aggregation_session_store");
    });

    return fl::coordinator::testing::g_failures == 0 ? 0 : 1;
''',
        '''    guarded("[29/30] secure_aggregation_session_store", [&]() {
        fl::coordinator::testing::run_secure_aggregation_session_store_tests(
            scratch_dir + "/secure_aggregation_session_store");
    });
    guarded("[30/30] round_fault_tolerance", [&]() {
        fl::coordinator::testing::run_round_fault_tolerance_tests(
            scratch_dir + "/round_fault_tolerance");
    });

    return fl::coordinator::testing::g_failures == 0 ? 0 : 1;
''',
    )

    docs = ROOT / "docs/coordinator-runtime.md"
    docs_text = docs.read_text(encoding="utf-8")
    marker = "## Round deadline and retry fault tolerance"
    if marker not in docs_text:
        docs.write_text(
            docs_text.rstrip()
            + "\n\n## Round deadline and retry fault tolerance\n\n"
            + "Distributed rounds use `round_timeout_seconds` as an absolute wall-clock deadline. "
            + "`minimum_valid_results` is a deadline or settlement quorum, not a fastest-client early-release target. "
            + "Before the deadline the coordinator waits for the full selected cohort unless all remaining tasks are permanently settled. "
            + "At the deadline unresolved clients are recorded as timed out; the accepted partial cohort is aggregated only when the quorum is met, otherwise the run fails without publishing a new model.\n\n"
            + "The deadline, timeout classification, active leases, accepted results, and per-client retry-attempt counters are checkpointed. "
            + "A coordinator restart preserves both the original deadline and the remaining retry budget. "
            + "The production gRPC server advances all runs from a watchdog even when no workers are polling; set `FL_ROUND_WATCHDOG_INTERVAL_MS` from 1 to 60000 milliseconds (default `1000`).\n",
            encoding="utf-8",
        )


def commit_and_push() -> None:
    paths = [
        "cpp/coordinator/include/fl_coordinator/task_dispatcher.hpp",
        "cpp/coordinator/src/task_dispatcher.cpp",
        "cpp/coordinator/include/fl_coordinator/run_manager.hpp",
        "cpp/coordinator/src/run_manager.cpp",
        "cpp/coordinator/main.cpp",
        "cpp/coordinator/tests/round_fault_tolerance_test.cpp",
        "cpp/coordinator/tests/test_main.cpp",
        "cpp/CMakeLists.txt",
        "infra/compose/docker-compose.dev.yml",
        "docs/coordinator-runtime.md",
    ]
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], cwd=ROOT, check=True)
    subprocess.run(
        [
            "git",
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        ],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(["git", "add", *paths], cwd=ROOT, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Harden distributed round deadlines and retries"],
        cwd=ROOT,
        check=True,
    )
    subprocess.run(
        ["git", "push", "origin", "HEAD:round-deadline-hardening"],
        cwd=ROOT,
        check=True,
    )


def main() -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("round deadline bootstrap skipped outside GitHub Actions")
        return 0
    if os.environ.get("GITHUB_REF_NAME") != "round-deadline-hardening":
        print("round deadline bootstrap skipped on non-target branch")
        return 0
    patch()
    commit_and_push()
    print("round deadline hardening patch committed and pushed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
