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

fl::coordinator::RunConfig make_config(const std::string& run_id, std::uint32_t max_rounds) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.server_lr = 1.0;
    config.target_clients_per_round = 2;
    config.total_clients = 2;
    config.max_rounds = max_rounds;
    config.minimum_valid_results = 2;
    config.client_selection_seed = 7;
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
    return submission;
}

void register_workers(fl::coordinator::RunManager& manager) {
    manager.worker_registry().register_worker("worker-a", fl::coordinator::WorkerCapability{}, 0.0);
    manager.worker_registry().register_worker("worker-b", fl::coordinator::WorkerCapability{}, 0.0);
}

void run_one_round(fl::coordinator::RunInstance& run, double& now) {
    run.advance(now);
    const auto task_a = run.acquire_task("worker-a", now).value();
    const auto task_b = run.acquire_task("worker-b", now).value();
    std::string reason;
    run.submit_client_result("worker-a", task_a.task_id, task_a.lease_id, make_result(task_a, 2.0), now, reason);
    run.submit_client_result("worker-b", task_b.task_id, task_b.lease_id, make_result(task_b, 0.0), now, reason);
    now += 1.0;
    run.advance(now);
}

}  // namespace

void run_recovery_tests(const std::string& scratch_dir) {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;

    std::filesystem::remove_all(scratch_dir);
    CoordinatorConfig coordinator_config;

    // ---- Control: uninterrupted two-round run ------------------------ //
    std::string control_model_version;
    double control_weight_value = 0.0;
    {
        RunManager manager(coordinator_config, scratch_dir + "/checkpoints_control", scratch_dir + "/scaffold_control");
        register_workers(manager);
        auto config = make_config("run-recover", 2);
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-recover");
        double now = 0.0;
        run.start("trace", now);
        run_one_round(run, now);
        run_one_round(run, now);
        control_model_version = run.snapshot().model_version;
        check(run.snapshot().state == fl::core::RunState::kCompleted, "control run reaches COMPLETED after 2 rounds");
    }

    // ---- Interrupted-and-recovered: crash after round 1, resume ------ //
    {
        const std::string checkpoint_dir = scratch_dir + "/checkpoints_recovered";
        const std::string scaffold_dir = scratch_dir + "/scaffold_recovered";
        auto config = make_config("run-recover", 2);

        std::string version_after_round_1;
        {
            // "Process A": runs round 1, then is dropped (simulating a
            // crash) without ever running round 2.
            RunManager manager_a(coordinator_config, checkpoint_dir, scaffold_dir);
        register_workers(manager_a);
            manager_a.create_run(config, 0.0);
            auto& run = manager_a.get("run-recover");
            double now = 0.0;
            run.start("trace", now);
            run_one_round(run, now);
            version_after_round_1 = run.snapshot().model_version;
            check(run.snapshot().current_round == 1, "process A completes exactly round 1 before the simulated crash");
            check(run.snapshot().state == fl::core::RunState::kRunning, "process A is resting in RUNNING, ready for round 2, at crash time");
            // manager_a goes out of scope here: all in-memory state is
            // gone, exactly like a killed process. Only the checkpoint
            // file on disk survives.
        }

        {
            // "Process B": a fresh coordinator process. Recreates the run
            // with the same original config (see run_manager.hpp's note
            // on why the config itself isn't part of the checkpoint) and
            // then restores round/model/optimizer state from disk before
            // doing anything else.
            RunManager manager_b(coordinator_config, checkpoint_dir, scaffold_dir);
        register_workers(manager_b);
            manager_b.create_run(config, 100.0);  // fresh RunInstance, starts at CREATED/round 0
            auto& run = manager_b.get("run-recover");

            run.restore_from_checkpoint();
            check(run.snapshot().current_round == 1, "restore_from_checkpoint recovers the completed round number");
            check(run.snapshot().model_version == version_after_round_1, "restore_from_checkpoint recovers the model version");
            check(run.snapshot().state == fl::core::RunState::kRunning, "restore_from_checkpoint recovers the resting RUNNING state");

            // Continue from where process A left off. This must aggregate
            // round 2 exactly once — not re-aggregate round 1.
            double now = 101.0;
            run_one_round(run, now);

            check(run.snapshot().current_round == 2, "recovery resumes at round 2, not a repeat of round 1 (no double aggregation)");
            check(run.snapshot().state == fl::core::RunState::kCompleted, "the recovered run reaches COMPLETED after its remaining round");
            check(
                run.snapshot().model_version == control_model_version,
                "the recovered run produces the identical final model_version as the uninterrupted control run"
            );
        }
    }
}

}  // namespace fl::coordinator::testing
