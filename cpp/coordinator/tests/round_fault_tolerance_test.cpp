#include "fl_coordinator/run_manager.hpp"
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

std::vector<DispatchedTask> acquire_fault_tasks(RunInstance& run, std::uint32_t count, double now) {
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
        RunManager manager(
            coordinator_config, scratch_dir + "/early/cp", scratch_dir + "/early/sc");
        register_fault_workers(manager, 3);
        manager.create_run(fault_config("no-early", 3, 2, 10), 100.0);
        auto& run = manager.get("no-early");
        run.start("trace", 100.0);
        run.advance(100.0);
        const auto tasks = acquire_fault_tasks(run, 3, 100.0);
        std::string reason;
        check(run.submit_client_result("worker-0",
                                       tasks[0].task_id,
                                       tasks[0].lease_id,
                                       fault_result(tasks[0], 1.0),
                                       101.0,
                                       reason),
              "first result accepted");
        check(run.submit_client_result("worker-1",
                                       tasks[1].task_id,
                                       tasks[1].lease_id,
                                       fault_result(tasks[1], 1.0),
                                       102.0,
                                       reason),
              "second result accepted");
        run.advance(105.0);
        check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
              "minimum quorum does not finalize before deadline while another client can finish");
        check(run.snapshot().model_version == "v0", "model is not released by fastest quorum");
        const auto round = run.round_snapshot(1);
        check(round.has_value() && std::abs(round->round_deadline_at_unix_s - 110.0) < 1e-9,
              "round snapshot exposes the absolute deadline");

        check(run.submit_client_result("worker-2",
                                       tasks[2].task_id,
                                       tasks[2].lease_id,
                                       fault_result(tasks[2], 1.0),
                                       106.0,
                                       reason),
              "third result accepted");
        run.advance(106.0);
        check(run.snapshot().state == fl::core::RunState::kCompleted &&
                  run.snapshot().model_version == "v1",
              "full cohort finalizes normally before deadline");
    }

    {
        RunManager manager(
            coordinator_config, scratch_dir + "/partial/cp", scratch_dir + "/partial/sc");
        register_fault_workers(manager, 3);
        manager.create_run(fault_config("partial", 3, 2, 10), 200.0);
        auto& run = manager.get("partial");
        run.start("trace", 200.0);
        run.advance(200.0);
        const auto tasks = acquire_fault_tasks(run, 3, 200.0);
        std::string reason;
        check(run.submit_client_result("worker-0",
                                       tasks[0].task_id,
                                       tasks[0].lease_id,
                                       fault_result(tasks[0], 1.0),
                                       201.0,
                                       reason),
              "partial first result accepted");
        check(run.submit_client_result("worker-1",
                                       tasks[1].task_id,
                                       tasks[1].lease_id,
                                       fault_result(tasks[1], 1.0),
                                       202.0,
                                       reason),
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
        RunManager manager(
            coordinator_config, scratch_dir + "/insufficient/cp", scratch_dir + "/insufficient/sc");
        register_fault_workers(manager, 3);
        manager.create_run(fault_config("insufficient", 3, 2, 10), 300.0);
        auto& run = manager.get("insufficient");
        run.start("trace", 300.0);
        run.advance(300.0);
        const auto tasks = acquire_fault_tasks(run, 3, 300.0);
        std::string reason;
        check(run.submit_client_result("worker-0",
                                       tasks[0].task_id,
                                       tasks[0].lease_id,
                                       fault_result(tasks[0], 1.0),
                                       301.0,
                                       reason),
              "insufficient first result accepted");
        run.advance(310.0);
        check(run.snapshot().state == fl::core::RunState::kFailed,
              "deadline fails run when quorum is not satisfied");
        check(run.snapshot().model_version == "v0", "failed deadline never publishes below quorum");
        const auto round = run.round_snapshot(1);
        check(round.has_value() && round->timed_out_client_ids.size() == 2,
              "all unresolved clients are classified as timed out");
    }

    {
        const auto config = fault_config("retry-recovery", 1, 1, 30, 5, 2);
        {
            RunManager manager(
                coordinator_config, scratch_dir + "/retry/cp", scratch_dir + "/retry/sc");
            register_fault_workers(manager, 1);
            manager.create_run(config, 400.0);
            auto& run = manager.get("retry-recovery");
            run.start("trace", 400.0);
            run.advance(400.0);
            const auto first = run.acquire_task("worker-0", 400.0);
            check(first.has_value() && first->attempt == 1, "first lease uses attempt one");
        }
        {
            RunManager manager(
                coordinator_config, scratch_dir + "/retry/cp", scratch_dir + "/retry/sc");
            register_fault_workers(manager, 1);
            manager.create_run(config, 406.0);
            auto& run = manager.get("retry-recovery");
            run.restore_from_checkpoint();
            const auto second = run.acquire_task("worker-0", 406.0);
            check(second.has_value() && second->attempt == 2,
                  "restart resumes retry counter instead of resetting it");
        }
        {
            RunManager manager(
                coordinator_config, scratch_dir + "/retry/cp", scratch_dir + "/retry/sc");
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
            RunManager manager(
                coordinator_config, scratch_dir + "/deadline/cp", scratch_dir + "/deadline/sc");
            manager.create_run(config, 500.0);
            auto& run = manager.get("deadline-recovery");
            run.start("trace", 500.0);
            run.advance(500.0);
            const auto round = run.round_snapshot(1);
            check(round.has_value() && std::abs(round->round_deadline_at_unix_s - 520.0) < 1e-9,
                  "initial deadline is fixed from round dispatch time");
        }
        {
            RunManager manager(
                coordinator_config, scratch_dir + "/deadline/cp", scratch_dir + "/deadline/sc");
            manager.create_run(config, 510.0);
            auto& run = manager.get("deadline-recovery");
            run.restore_from_checkpoint();
            const auto restored = run.round_snapshot(1);
            check(
                restored.has_value() && std::abs(restored->round_deadline_at_unix_s - 520.0) < 1e-9,
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
        RunManager manager(
            coordinator_config, scratch_dir + "/invalid/cp", scratch_dir + "/invalid/sc");
        expect_throw([&]() { manager.create_run(fault_config("invalid", 2, 3, 10), 0.0); },
                     "minimum_valid_results above selectable cohort is rejected");
    }
}

}  // namespace fl::coordinator::testing
