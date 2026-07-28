// Regression coverage for compatible-worker-only task assignment
// (docs/worker-privacy-capabilities.md): a worker that never advertised
// supports_sample_level_dp at registration time must never receive a
// task from a sample-level/hybrid-DP run — there is no silent fallback
// to non-private training. See RunInstance::acquire_task's gate.
#include "fl_coordinator/run_manager.hpp"
#include "test_support.hpp"

#include <filesystem>

namespace fl::coordinator::testing {

namespace {

fl::core::ModelManifest make_manifest() {
    return fl::core::ModelManifest{
        .model_id = "toy",
        .model_version = "v0",
        .tensors = {fl::core::TensorDescriptor{
            .name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32}},
    };
}

fl::coordinator::RunConfig make_hybrid_config(const std::string& run_id) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.server_lr = 1.0;
    config.target_clients_per_round = 2;
    config.total_clients = 2;
    config.max_rounds = 2;
    config.minimum_valid_results = 2;
    config.client_selection_seed = 42;
    config.task_lease_seconds = 60;
    config.max_task_retries = 3;
    config.client_ids = {"client-a", "client-b"};
    config.privacy_mode = fl::core::PrivacyMode::kSampleLevelDp;
    config.sample_level_privacy.noise_multiplier = 0.9;
    config.sample_level_privacy.max_grad_norm = 1.2;
    config.sample_level_privacy.target_delta = 1e-6;
    return config;
}

fl::coordinator::RunConfig make_non_private_config(const std::string& run_id) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.server_lr = 1.0;
    config.target_clients_per_round = 2;
    config.total_clients = 2;
    config.max_rounds = 2;
    config.minimum_valid_results = 2;
    config.client_selection_seed = 42;
    config.task_lease_seconds = 60;
    config.max_task_retries = 3;
    config.client_ids = {"client-a", "client-b"};
    // privacy_mode left at its default (kNone).
    return config;
}

}  // namespace

void run_worker_privacy_capability_tests() {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;

    std::filesystem::remove_all("worker_privacy_capability_test_scratch");
    CoordinatorConfig coordinator_config;

    // --- A worker that never advertised supports_sample_level_dp gets
    // no task at all from a sample-level-DP run — not an error, just
    // std::nullopt, same as "nothing pending right now". ---
    {
        RunManager manager(coordinator_config,
                           "worker_privacy_capability_test_scratch/checkpoints_a",
                           "worker_privacy_capability_test_scratch/scaffold_a");
        manager.create_run(make_hybrid_config("run-incompatible-worker"), 0.0);
        auto& run = manager.get("run-incompatible-worker");
        // Default-constructed WorkerCapability: supports_sample_level_dp
        // is false (the "never advertised" case).
        manager.worker_registry().register_worker(
            "worker-plain", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("", 0.0);

        double now = 0.0;
        run.advance(now);
        const auto task = run.acquire_task("worker-plain", now);
        check(!task.has_value(),
              "a worker without supports_sample_level_dp never receives a task from a "
              "sample-level-DP run");
    }

    // --- A worker that DOES advertise support receives tasks normally
    // from the same kind of run — proves the gate isn't blocking
    // everything, only incompatible workers. ---
    {
        RunManager manager(coordinator_config,
                           "worker_privacy_capability_test_scratch/checkpoints_b",
                           "worker_privacy_capability_test_scratch/scaffold_b");
        manager.create_run(make_hybrid_config("run-compatible-worker"), 0.0);
        auto& run = manager.get("run-compatible-worker");
        fl::coordinator::WorkerCapability capability;
        capability.privacy.supports_sample_level_dp = true;
        capability.privacy.opacus_version = "1.6.0";
        capability.privacy.supported_accountants = {"rdp"};
        manager.worker_registry().register_worker("worker-capable", capability, 0.0);
        run.start("", 0.0);

        double now = 0.0;
        run.advance(now);
        const auto task = run.acquire_task("worker-capable", now);
        check(task.has_value(),
              "a worker that advertises supports_sample_level_dp receives a task normally");
        check(task->descriptor.sample_level_dp_active,
              "the task it receives is correctly marked sample_level_dp_active");
    }

    // --- A worker without privacy capability is completely unaffected
    // when the run itself is not private at all — the gate must never
    // apply to non-private runs. ---
    {
        RunManager manager(coordinator_config,
                           "worker_privacy_capability_test_scratch/checkpoints_c",
                           "worker_privacy_capability_test_scratch/scaffold_c");
        manager.create_run(make_non_private_config("run-non-private"), 0.0);
        auto& run = manager.get("run-non-private");
        manager.worker_registry().register_worker(
            "worker-plain", fl::coordinator::WorkerCapability{}, 0.0);
        run.start("", 0.0);

        double now = 0.0;
        run.advance(now);
        const auto task = run.acquire_task("worker-plain", now);
        check(task.has_value(),
              "a worker without privacy capability still receives tasks from a non-private run");
    }

    // --- A worker_id that was never registered at all (not just
    // lacking capability) is also safely refused, not a crash/UB. ---
    {
        RunManager manager(coordinator_config,
                           "worker_privacy_capability_test_scratch/checkpoints_d",
                           "worker_privacy_capability_test_scratch/scaffold_d");
        manager.create_run(make_hybrid_config("run-unregistered-worker"), 0.0);
        auto& run = manager.get("run-unregistered-worker");
        run.start("", 0.0);

        double now = 0.0;
        run.advance(now);
        const auto task = run.acquire_task("worker-never-registered", now);
        check(!task.has_value(), "an unregistered worker_id is safely refused, not a crash");
    }
}

}  // namespace fl::coordinator::testing
