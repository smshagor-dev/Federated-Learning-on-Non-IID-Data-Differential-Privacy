#define run_run_manager_tests run_run_manager_tests_legacy
#include "run_manager_test_legacy.cpp"
#undef run_run_manager_tests

#include <filesystem>
#include <string>
#include <vector>

namespace fl::coordinator::testing {
namespace {

fl::core::ModelManifest runtime_fault_manifest() {
    return fl::core::ModelManifest{
        .model_id = "round-runtime-toy",
        .model_version = "v0",
        .tensors = {fl::core::TensorDescriptor{
            .name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}},
    };
}

RunConfig runtime_fault_config(const std::string& run_id,
                               std::uint32_t clients,
                               std::uint32_t minimum,
                               std::uint32_t timeout_seconds,
                               std::uint32_t lease_seconds = 30,
                               std::uint32_t retries = 3) {
    RunConfig config;
    config.run_id = run_id;
    config.manifest = runtime_fault_manifest();
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
        config.client_ids.push_back("runtime-client-" + std::to_string(index));
    }
    return config;
}

ClientResultSubmission runtime_fault_result(const DispatchedTask& task, double value) {
    ClientResultSubmission result;
    result.update.run_id = task.descriptor.run_id;
    result.update.round_id = task.descriptor.round_id;
    result.update.client_id = task.descriptor.client_id;
    result.update.update_id = "runtime-update-" + task.descriptor.client_id;
    result.update.nonce = "runtime-nonce-" + task.descriptor.client_id;
    result.update.base_model_version = task.descriptor.model_version;
    result.update.algorithm = task.descriptor.algorithm;
    result.update.sample_count = 1;
    result.update.delta.insert(fl::core::TensorBuffer(
        fl::core::TensorDescriptor{
            .name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32},
        {value}));
    return result;
}

void register_runtime_workers(RunManager& manager, std::uint32_t count) {
    for (std::uint32_t index = 0; index < count; ++index) {
        manager.worker_registry().register_worker(
            "runtime-worker-" + std::to_string(index), WorkerCapability{}, 0.0);
    }
}

std::vector<DispatchedTask> acquire_runtime_tasks(RunInstance& run,
                                                  std::uint32_t count,
                                                  double now) {
    std::vector<DispatchedTask> tasks;
    for (std::uint32_t index = 0; index < count; ++index) {
        auto task = run.acquire_task("runtime-worker-" + std::to_string(index), now);
        check(task.has_value(), "round runtime: expected selected client task");
        if (task.has_value()) {
            tasks.push_back(*task);
        }
    }
    return tasks;
}

void run_round_runtime_hardening_tests() {
    const std::string scratch = "round_runtime_test_scratch";
    std::filesystem::remove_all(scratch);
    CoordinatorConfig coordinator_config;

    {
        RunManager manager(coordinator_config, scratch + "/full/cp", scratch + "/full/sc");
        register_runtime_workers(manager, 3);
        manager.create_run(runtime_fault_config("full-cohort", 3, 2, 10), 100.0);
        auto& run = manager.get("full-cohort");
        run.start("trace", 100.0);
        run.advance(100.0);
        const auto tasks = acquire_runtime_tasks(run, 3, 100.0);
        std::string reason;
        check(run.submit_client_result("runtime-worker-0",
                                       tasks[0].task_id,
                                       tasks[0].lease_id,
                                       runtime_fault_result(tasks[0], 1.0),
                                       101.0,
                                       reason),
              "round runtime: first result accepted");
        check(run.submit_client_result("runtime-worker-1",
                                       tasks[1].task_id,
                                       tasks[1].lease_id,
                                       runtime_fault_result(tasks[1], 1.0),
                                       102.0,
                                       reason),
              "round runtime: second result accepted");
        check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
              "round runtime: minimum quorum does not release fastest clients early");
        check(run.snapshot().model_version == "v0",
              "round runtime: model is unchanged before full cohort/deadline");
        check(run.submit_client_result("runtime-worker-2",
                                       tasks[2].task_id,
                                       tasks[2].lease_id,
                                       runtime_fault_result(tasks[2], 1.0),
                                       106.0,
                                       reason),
              "round runtime: final cohort result accepted");
        run.advance(106.0);
        check(run.snapshot().state == fl::core::RunState::kCompleted &&
                  run.snapshot().model_version == "v1",
              "round runtime: full cohort finalizes on the next coordinator tick before deadline");
    }

    {
        RunManager manager(coordinator_config, scratch + "/partial/cp", scratch + "/partial/sc");
        register_runtime_workers(manager, 3);
        manager.create_run(runtime_fault_config("partial-deadline", 3, 2, 10), 200.0);
        auto& run = manager.get("partial-deadline");
        run.start("trace", 200.0);
        run.advance(200.0);
        const auto tasks = acquire_runtime_tasks(run, 3, 200.0);
        std::string reason;
        check(run.submit_client_result("runtime-worker-0",
                                       tasks[0].task_id,
                                       tasks[0].lease_id,
                                       runtime_fault_result(tasks[0], 1.0),
                                       201.0,
                                       reason),
              "round runtime: partial first result accepted");
        check(run.submit_client_result("runtime-worker-1",
                                       tasks[1].task_id,
                                       tasks[1].lease_id,
                                       runtime_fault_result(tasks[1], 1.0),
                                       202.0,
                                       reason),
              "round runtime: partial second result accepted");
        run.advance(209.0);
        check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
              "round runtime: quorum waits until absolute deadline");
        run.advance(210.0);
        check(run.snapshot().state == fl::core::RunState::kCompleted &&
                  run.snapshot().model_version == "v1",
              "round runtime: deadline releases partial cohort when quorum is met");
    }

    {
        RunManager manager(
            coordinator_config, scratch + "/insufficient/cp", scratch + "/insufficient/sc");
        register_runtime_workers(manager, 3);
        manager.create_run(runtime_fault_config("insufficient", 3, 2, 10), 300.0);
        auto& run = manager.get("insufficient");
        run.start("trace", 300.0);
        run.advance(300.0);
        const auto tasks = acquire_runtime_tasks(run, 3, 300.0);
        std::string reason;
        check(run.submit_client_result("runtime-worker-0",
                                       tasks[0].task_id,
                                       tasks[0].lease_id,
                                       runtime_fault_result(tasks[0], 1.0),
                                       301.0,
                                       reason),
              "round runtime: insufficient first result accepted");
        run.advance(310.0);
        check(run.snapshot().state == fl::core::RunState::kFailed,
              "round runtime: below-quorum deadline fails closed");
        check(run.snapshot().model_version == "v0",
              "round runtime: below-quorum deadline never publishes a model");
    }

    {
        const auto config = runtime_fault_config("retry-restart", 1, 1, 30, 5, 2);
        {
            RunManager manager(
                coordinator_config, scratch + "/retry/cp", scratch + "/retry/sc");
            register_runtime_workers(manager, 1);
            manager.create_run(config, 400.0);
            auto& run = manager.get("retry-restart");
            run.start("trace", 400.0);
            run.advance(400.0);
            const auto first = run.acquire_task("runtime-worker-0", 400.0);
            check(first.has_value() && first->attempt == 1,
                  "round runtime: first lease uses attempt one");
        }
        {
            RunManager manager(
                coordinator_config, scratch + "/retry/cp", scratch + "/retry/sc");
            register_runtime_workers(manager, 1);
            manager.create_run(config, 403.0);
            auto& run = manager.get("retry-restart");
            run.restore_from_checkpoint();
            check(!run.acquire_task("runtime-worker-0", 403.0).has_value(),
                  "round runtime: restart does not duplicate a still-valid lease");
            const auto second = run.acquire_task("runtime-worker-0", 406.0);
            check(second.has_value() && second->attempt == 2,
                  "round runtime: expired checkpoint lease resumes at attempt two");
        }
        {
            RunManager manager(
                coordinator_config, scratch + "/retry/cp", scratch + "/retry/sc");
            register_runtime_workers(manager, 1);
            manager.create_run(config, 412.0);
            auto& run = manager.get("retry-restart");
            run.restore_from_checkpoint();
            run.advance(412.0);
            check(run.snapshot().state == fl::core::RunState::kFailed,
                  "round runtime: exhausted retry budget remains exhausted after restart");
            check(!run.acquire_task("runtime-worker-0", 412.0).has_value(),
                  "round runtime: no third lease after max retries");
        }
    }

    {
        const auto config = runtime_fault_config("deadline-restart", 2, 1, 20);
        {
            RunManager manager(
                coordinator_config, scratch + "/deadline/cp", scratch + "/deadline/sc");
            manager.create_run(config, 500.0);
            auto& run = manager.get("deadline-restart");
            run.start("trace", 500.0);
            run.advance(500.0);
        }
        {
            RunManager manager(
                coordinator_config, scratch + "/deadline/cp", scratch + "/deadline/sc");
            manager.create_run(config, 510.0);
            auto& run = manager.get("deadline-restart");
            run.restore_from_checkpoint();
            run.advance(519.0);
            check(run.snapshot().state == fl::core::RunState::kWaitingForClients,
                  "round runtime: restored run stays active before original deadline");
            run.advance(520.0);
            check(run.snapshot().state == fl::core::RunState::kFailed,
                  "round runtime: restored run expires at original deadline, not a reset deadline");
        }
    }

    {
        RunManager manager(coordinator_config, scratch + "/invalid/cp", scratch + "/invalid/sc");
        expect_throw(
            [&]() { manager.create_run(runtime_fault_config("invalid", 2, 3, 10), 0.0); },
            "round runtime: minimum_valid_results above selectable cohort is rejected");
    }
}

}  // namespace

void run_run_manager_tests() {
    run_run_manager_tests_legacy();
    run_round_runtime_hardening_tests();
}

}  // namespace fl::coordinator::testing
