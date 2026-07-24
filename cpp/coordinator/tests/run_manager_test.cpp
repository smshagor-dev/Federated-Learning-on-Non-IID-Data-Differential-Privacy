#include "fl_coordinator/run_manager.hpp"
#include "test_support.hpp"

#include <filesystem>

namespace fl::coordinator::testing {

namespace {

fl::core::ModelManifest make_manifest() {
    return fl::core::ModelManifest{
        .model_id = "toy",
        .model_version = "v0",
        .tensors = {fl::core::TensorDescriptor{.name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}},
    };
}

fl::coordinator::RunConfig make_config(
    const std::string& run_id, fl::core::AggregationAlgorithm algorithm, std::uint32_t max_rounds = 2
) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = algorithm;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.server_lr = 1.0;
    config.beta1 = 0.9;
    config.beta2 = 0.99;
    config.tau = 1e-3;
    config.target_clients_per_round = 2;
    config.total_clients = 2;
    config.max_rounds = max_rounds;
    config.minimum_valid_results = 2;
    config.client_selection_seed = 42;
    config.task_lease_seconds = 60;
    config.max_task_retries = 3;
    config.client_ids = {"client-a", "client-b"};
    return config;
}

fl::coordinator::ClientResultSubmission make_result(const fl::coordinator::DispatchedTask& task, double delta_value) {
    fl::coordinator::ClientResultSubmission submission;
    submission.update.run_id = task.descriptor.run_id;
    submission.update.round_id = task.descriptor.round_id;
    submission.update.client_id = task.descriptor.client_id;
    submission.update.update_id = "update-" + task.descriptor.client_id + "-" + std::to_string(task.descriptor.round_id);
    submission.update.nonce = "nonce-" + task.descriptor.client_id + "-" + std::to_string(task.descriptor.round_id);
    submission.update.base_model_version = task.descriptor.model_version;
    submission.update.algorithm = task.descriptor.algorithm;
    submission.update.sample_count = 4;
    submission.update.delta.insert(
        fl::core::TensorBuffer(fl::core::TensorDescriptor{.name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}, {delta_value})
    );
    if (task.descriptor.algorithm == fl::core::AggregationAlgorithm::kScaffold) {
        submission.update.control_delta.insert(
            fl::core::TensorBuffer(fl::core::TensorDescriptor{.name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}, {0.01})
        );
        submission.refreshed_client_control_variate.insert(
            fl::core::TensorBuffer(fl::core::TensorDescriptor{.name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}, {0.05})
        );
    }
    return submission;
}

void register_workers(fl::coordinator::RunManager& manager) {
    manager.worker_registry().register_worker("worker-a", fl::coordinator::WorkerCapability{}, 0.0);
    manager.worker_registry().register_worker("worker-b", fl::coordinator::WorkerCapability{}, 0.0);
}

// Drives one full round to completion for a run already in kRunning,
// returning the aggregated model_version after the round.
std::string run_one_round(fl::coordinator::RunInstance& run, double& now) {
    run.advance(now);  // kRunning -> kWaitingForClients (dispatches the round)
    const auto task_a = run.acquire_task("worker-a", now).value();
    const auto task_b = run.acquire_task("worker-b", now).value();

    std::string reason;
    run.submit_client_result("worker-a", task_a.task_id, task_a.lease_id, make_result(task_a, 2.0), now, reason);
    run.submit_client_result("worker-b", task_b.task_id, task_b.lease_id, make_result(task_b, 0.0), now, reason);

    now += 1.0;
    run.advance(now);  // kWaitingForClients -> kAggregating -> kCheckpointing -> kRunning/kCompleted
    return run.snapshot().model_version;
}

}  // namespace

void run_run_manager_tests() {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;
    using fl::coordinator::RunManagerError;

    // Each RunManager below writes checkpoints/SCAFFOLD state under
    // coordinator_test_scratch/*; without cleaning it first, re-running
    // this binary would see stale files from a previous run (e.g. a
    // client's SCAFFOLD state "already saved" at a version that no
    // longer matches this run's fresh model_version).
    std::filesystem::remove_all("coordinator_test_scratch");

    CoordinatorConfig coordinator_config;
    coordinator_config.max_concurrent_runs = 2;

    {
        RunManager manager(coordinator_config, "coordinator_test_scratch/checkpoints_dup", "coordinator_test_scratch/scaffold_dup");
        manager.create_run(make_config("run-dup", fl::core::AggregationAlgorithm::kFedAvg), 0.0);
        expect_throw(
            [&]() { manager.create_run(make_config("run-dup", fl::core::AggregationAlgorithm::kFedAvg), 0.0); },
            "creating a run with a duplicate run_id is rejected"
        );
    }

    {
        RunManager manager(coordinator_config, "coordinator_test_scratch/checkpoints_full", "coordinator_test_scratch/scaffold_full");
        manager.create_run(make_config("run-1", fl::core::AggregationAlgorithm::kFedAvg), 0.0);
        manager.create_run(make_config("run-2", fl::core::AggregationAlgorithm::kFedAvg), 0.0);
        expect_throw(
            [&]() { manager.create_run(make_config("run-3", fl::core::AggregationAlgorithm::kFedAvg), 0.0); },
            "creating beyond max_concurrent_runs is rejected"
        );
    }

    // ---- Full FedAvg round lifecycle -------------------------------- //
    {
        RunManager manager(coordinator_config, "coordinator_test_scratch/checkpoints_fedavg", "coordinator_test_scratch/scaffold_fedavg");
        register_workers(manager);
        auto config = make_config("run-fedavg", fl::core::AggregationAlgorithm::kFedAvg, /*max_rounds=*/2);
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-fedavg");

        double now = 0.0;
        run.start("trace-1", now);
        check(run.snapshot().state == fl::core::RunState::kRunning, "start() drives a fresh run to RUNNING");

        // Idempotent start: calling start() again while already running
        // must not create a second execution loop (no exception, no
        // observable change beyond a no-op).
        expect_no_throw([&]() { run.start("trace-1", now); }, "starting an already-running run is a no-op, not an error");

        const auto version_after_round_1 = run_one_round(run, now);
        check(version_after_round_1 == "v1", "model_version advances after the first round aggregates");
        check(run.snapshot().current_round == 1, "current_round reflects the completed round");

        const auto version_after_round_2 = run_one_round(run, now);
        check(version_after_round_2 == "v2", "model_version advances again after the second round");
        check(run.snapshot().state == fl::core::RunState::kCompleted, "run reaches COMPLETED after max_rounds");

        expect_throw(
            [&]() { run.resume("trace-1", now); }, "resuming a completed run must fail"
        );
        expect_throw(
            [&]() { run.start("trace-1", now); }, "starting a completed run must fail"
        );
    }

    // ---- Pause deferred until a safe point, cancel idempotent -------- //
    {
        RunManager manager(coordinator_config, "coordinator_test_scratch/checkpoints_pause", "coordinator_test_scratch/scaffold_pause");
        auto config = make_config("run-pause", fl::core::AggregationAlgorithm::kFedAvg, /*max_rounds=*/5);
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-pause");

        double now = 0.0;
        run.start("trace-1", now);
        run.advance(now);  // dispatch round 1
        check(run.snapshot().state == fl::core::RunState::kWaitingForClients, "round dispatch moves to WAITING_FOR_CLIENTS");

        // Pause while WAITING_FOR_CLIENTS is a safe point (no aggregation
        // in progress yet) -> takes effect immediately.
        run.pause("operator requested", "trace-1", now);
        check(run.snapshot().state == fl::core::RunState::kPaused, "pause() during WAITING_FOR_CLIENTS applies immediately");

        run.resume("trace-1", now);
        check(run.snapshot().state == fl::core::RunState::kRunning || run.snapshot().state == fl::core::RunState::kWaitingForClients,
              "resume() returns the run to an active state");

        // Cancel is idempotent: calling it twice must not throw and must
        // return the CANCELED state both times.
        run.cancel("test cancel", "trace-1", now);
        check(run.snapshot().state == fl::core::RunState::kCanceled, "cancel() transitions a running-ish run to CANCELED");
        expect_no_throw([&]() { run.cancel("test cancel again", "trace-1", now); },
                        "canceling an already-canceled run is idempotent, not an error");
        check(run.snapshot().state == fl::core::RunState::kCanceled, "a repeated cancel still reports CANCELED");
    }

    // ---- Duplicate / late result rejection through the run's own API - //
    {
        RunManager manager(coordinator_config, "coordinator_test_scratch/checkpoints_dupresult", "coordinator_test_scratch/scaffold_dupresult");
        register_workers(manager);
        auto config = make_config("run-dupresult", fl::core::AggregationAlgorithm::kFedAvg, 1);
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-dupresult");

        double now = 0.0;
        run.start("trace-1", now);
        run.advance(now);
        const auto task = run.acquire_task("worker-a", now).value();

        std::string reason;
        const auto accepted = run.submit_client_result("worker-a", task.task_id, task.lease_id, make_result(task, 1.0), now, reason);
        check(accepted, "a valid first submission is accepted");

        std::string duplicate_reason;
        const auto duplicate = run.submit_client_result(
            "worker-a", task.task_id, task.lease_id, make_result(task, 9.0), now, duplicate_reason
        );
        check(!duplicate, "resubmitting the same task_id is rejected as a duplicate result");
    }

    // ---- SCAFFOLD round: global + per-client control variate flow --- //
    {
        RunManager manager(coordinator_config, "coordinator_test_scratch/checkpoints_scaffold", "coordinator_test_scratch/scaffold_scaffold");
        register_workers(manager);
        auto config = make_config("run-scaffold", fl::core::AggregationAlgorithm::kScaffold, 1);
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-scaffold");

        double now = 0.0;
        run.start("trace-1", now);
        run.advance(now);
        const auto task_a = run.acquire_task("worker-a", now).value();

        // First participation: client control variate must be zero (no
        // prior state saved).
        const auto [global_before, client_before] = run.scaffold_control_variates_for("client-a");
        check(client_before.at("weight").values()[0] == 0.0, "a client's control variate zero-initializes on first participation");

        const auto task_b = run.acquire_task("worker-b", now).value();
        std::string reason;
        run.submit_client_result("worker-a", task_a.task_id, task_a.lease_id, make_result(task_a, 2.0), now, reason);
        run.submit_client_result("worker-b", task_b.task_id, task_b.lease_id, make_result(task_b, 0.0), now, reason);
        run.advance(now);

        check(run.snapshot().state == fl::core::RunState::kCompleted, "single-round SCAFFOLD run completes");

        auto& store = manager.scaffold_store();
        const auto persisted = store.load("run-scaffold", "client-a", "v1");
        check(persisted.has_value(), "the client's refreshed control variate is persisted after the round");
        check(persisted->control_variate.at("weight").values()[0] == 0.05,
              "the persisted control variate matches what the client submitted");
    }
}

}  // namespace fl::coordinator::testing
