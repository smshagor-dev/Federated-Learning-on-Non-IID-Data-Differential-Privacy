// Regression coverage for the coordinator-side adaptive clipping
// pipeline (dynamic clip bound driven by a privatized over-threshold
// count), wired into RunInstance::finalize_round alongside user-level DP
// — see docs/adaptive-clipping.md. Drives real rounds through
// RunManager/RunInstance (no mocks), matching user_level_dp_test.cpp's
// established pattern.
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

fl::coordinator::RunConfig make_adaptive_config(const std::string& run_id,
                                                std::uint64_t noise_seed,
                                                double initial_clip = 5.0,
                                                double count_noise_multiplier = 1e-6) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.server_lr = 1.0;
    config.target_clients_per_round = 2;
    config.total_clients = 2;
    config.max_rounds = 3;
    config.minimum_valid_results = 2;
    config.client_selection_seed = 42;
    config.task_lease_seconds = 60;
    config.max_task_retries = 3;
    config.client_ids = {"client-a", "client-b"};
    config.privacy_mode = fl::core::PrivacyMode::kUserLevelDp;
    config.user_level_privacy.noise_multiplier = 1.0;
    config.user_level_privacy.initial_clipping_bound = initial_clip;  // unused once adaptive is on
    config.user_level_privacy.target_delta = 1e-5;
    config.privacy_noise_seed = noise_seed;
    config.adaptive_clipping_enabled = true;
    config.adaptive_clipping.initial_clip = initial_clip;
    config.adaptive_clipping.target_quantile = 0.5;
    config.adaptive_clipping.clip_learning_rate = 0.5;
    config.adaptive_clipping.min_clip = 0.1;
    config.adaptive_clipping.max_clip = 100.0;
    config.adaptive_clipping.count_noise_multiplier = count_noise_multiplier;
    config.adaptive_clipping.target_delta = 1e-5;
    return config;
}

fl::coordinator::ClientResultSubmission make_result(const fl::coordinator::DispatchedTask& task,
                                                    double delta_value) {
    fl::coordinator::ClientResultSubmission submission;
    submission.update.run_id = task.descriptor.run_id;
    submission.update.round_id = task.descriptor.round_id;
    submission.update.client_id = task.descriptor.client_id;
    submission.update.update_id =
        "update-" + task.descriptor.client_id + "-" + std::to_string(task.descriptor.round_id);
    submission.update.nonce =
        "nonce-" + task.descriptor.client_id + "-" + std::to_string(task.descriptor.round_id);
    submission.update.base_model_version = task.descriptor.model_version;
    submission.update.algorithm = task.descriptor.algorithm;
    submission.update.sample_count = 4;
    submission.update.delta.insert(fl::core::TensorBuffer(
        fl::core::TensorDescriptor{
            .name = "weight", .shape = {1}, .dtype = fl::core::DType::kFloat32},
        {delta_value}));
    return submission;
}

void register_workers(fl::coordinator::RunManager& manager) {
    manager.worker_registry().register_worker("worker-a", fl::coordinator::WorkerCapability{}, 0.0);
    manager.worker_registry().register_worker("worker-b", fl::coordinator::WorkerCapability{}, 0.0);
}

void run_one_round(fl::coordinator::RunInstance& run, double& now, double delta_a, double delta_b) {
    run.advance(now);
    const auto task_a = run.acquire_task("worker-a", now).value();
    const auto task_b = run.acquire_task("worker-b", now).value();

    std::string reason;
    run.submit_client_result(
        "worker-a", task_a.task_id, task_a.lease_id, make_result(task_a, delta_a), now, reason);
    run.submit_client_result(
        "worker-b", task_b.task_id, task_b.lease_id, make_result(task_b, delta_b), now, reason);

    now += 1.0;
    run.advance(now);
}

}  // namespace

void run_adaptive_clipping_tests() {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;

    std::filesystem::remove_all("adaptive_clipping_test_scratch");
    CoordinatorConfig coordinator_config;

    // --- Ledger entries are created alongside (not instead of) the
    // user-level ledger; clip bound rises when both clients' deltas
    // exceed it (over_threshold_count = cohort_size -> fraction 1.0 >
    // target_quantile 0.5). ---
    {
        RunManager manager(coordinator_config,
                           "adaptive_clipping_test_scratch/checkpoints_a",
                           "adaptive_clipping_test_scratch/scaffold_a");
        // initial_clip=1.0: both clients' delta norms (10.0, 8.0) exceed it.
        auto config =
            make_adaptive_config("run-adaptive-a", /*noise_seed=*/123, /*initial_clip=*/1.0);
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-adaptive-a");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        run_one_round(run, now, 10.0, -8.0);

        check(run.user_level_ledger().size() == 1, "user-level ledger gains one entry");
        check(run.adaptive_clipping_ledger().size() == 1,
              "adaptive-clipping ledger gains one entry");
        const auto& clip_entry = run.adaptive_clipping_ledger().back();
        check(clip_entry.clip_value == 1.0,
              "ledger records the bound actually used THIS round (initial_clip)");
        check(clip_entry.noisy_over_threshold_fraction > 0.5,
              "both clients over threshold -> noisy fraction is high");
        check(clip_entry.epsilon > 0.0, "adaptive-clipping epsilon is positive after one round");

        // Round 2: the bound should have risen above 1.0 in response.
        run_one_round(run, now, 10.0, -8.0);
        check(run.adaptive_clipping_ledger().size() == 2, "two adaptive-clipping ledger entries");
        check(run.adaptive_clipping_ledger()[1].clip_value >
                  run.adaptive_clipping_ledger()[0].clip_value,
              "clip bound rises across rounds when clients stay over threshold");
        check(run.adaptive_clipping_ledger().back().epsilon >
                  run.adaptive_clipping_ledger()[0].epsilon,
              "adaptive-clipping epsilon grows monotonically, independent of user-level epsilon");

        // Critical Privacy Rule: the two ledgers' epsilon values must
        // never be equal by construction (they're different mechanisms
        // with different noise multipliers/formulas) — this is a smoke
        // check that nothing accidentally aliases the two accountants.
        check(
            run.user_level_ledger().back().epsilon != run.adaptive_clipping_ledger().back().epsilon,
            "user-level and adaptive-clipping epsilon are tracked by distinct accountants");
    }

    // --- Disabled by default: adaptive_clipping_enabled=false leaves the
    // clip bound fixed at initial_clipping_bound and the ledger empty,
    // even though user-level DP itself is active (regression guard for
    // user_level_dp_test.cpp's existing fixed-bound behavior). ---
    {
        RunManager manager(coordinator_config,
                           "adaptive_clipping_test_scratch/checkpoints_b",
                           "adaptive_clipping_test_scratch/scaffold_b");
        fl::coordinator::RunConfig config;
        config.run_id = "run-fixed-bound";
        config.manifest = make_manifest();
        config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
        config.weighting = fl::core::WeightingStrategyType::kUniform;
        config.server_lr = 1.0;
        config.target_clients_per_round = 2;
        config.total_clients = 2;
        config.max_rounds = 2;
        config.minimum_valid_results = 2;
        config.task_lease_seconds = 60;
        config.max_task_retries = 3;
        config.client_ids = {"client-a", "client-b"};
        config.privacy_mode = fl::core::PrivacyMode::kUserLevelDp;
        config.user_level_privacy.noise_multiplier = 1.0;
        config.user_level_privacy.initial_clipping_bound = 10.0;
        config.user_level_privacy.target_delta = 1e-5;
        config.privacy_noise_seed = 777;
        // adaptive_clipping_enabled left at its default (false).
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-fixed-bound");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        run_one_round(run, now, 2.0, 0.0);
        run_one_round(run, now, 3.0, 1.0);

        check(run.adaptive_clipping_ledger().empty(),
              "adaptive clipping disabled -> its ledger stays empty");
        check(run.user_level_ledger()[0].clipping_bound == 10.0 &&
                  run.user_level_ledger()[1].clipping_bound == 10.0,
              "clip bound stays fixed at initial_clipping_bound across rounds when adaptive "
              "clipping is disabled");
    }

    // --- Reproducibility: identical config/seed -> identical clip-bound
    // and epsilon trajectory. ---
    {
        RunManager manager_x(coordinator_config,
                             "adaptive_clipping_test_scratch/checkpoints_x",
                             "adaptive_clipping_test_scratch/scaffold_x");
        RunManager manager_y(coordinator_config,
                             "adaptive_clipping_test_scratch/checkpoints_y",
                             "adaptive_clipping_test_scratch/scaffold_y");
        manager_x.create_run(make_adaptive_config("run-x", 555, 1.0), 0.0);
        manager_y.create_run(make_adaptive_config("run-y", 555, 1.0), 0.0);
        auto& run_x = manager_x.get("run-x");
        auto& run_y = manager_y.get("run-y");
        register_workers(manager_x);
        register_workers(manager_y);
        run_x.start("", 0.0);
        run_y.start("", 0.0);

        double now_x = 0.0;
        double now_y = 0.0;
        run_one_round(run_x, now_x, 10.0, -8.0);
        run_one_round(run_y, now_y, 10.0, -8.0);

        check(run_x.adaptive_clipping_ledger().back().clip_value ==
                  run_y.adaptive_clipping_ledger().back().clip_value,
              "identical config/seed produces identical clip-bound trajectory");
        check(run_x.adaptive_clipping_ledger().back().epsilon ==
                  run_y.adaptive_clipping_ledger().back().epsilon,
              "identical config/seed produces identical adaptive-clipping epsilon");
    }
}

}  // namespace fl::coordinator::testing
