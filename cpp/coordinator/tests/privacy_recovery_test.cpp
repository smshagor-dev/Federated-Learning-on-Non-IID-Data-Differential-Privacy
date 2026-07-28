// Regression coverage for privacy state surviving a coordinator restart
// (docs/coordinator-recovery.md): before this, a restart would silently
// reset every accountant to zero steps and the adaptive clip bound back
// to initial_clip, understating every mechanism's true cumulative
// epsilon after recovery — a real privacy-accounting correctness bug,
// not just a lost-history inconvenience. Mirrors recovery_test.cpp's
// established "run rounds, restart via a fresh RunManager +
// restore_from_checkpoint, compare" pattern.
#include "fl_coordinator/run_manager.hpp"
#include "test_support.hpp"

#include <cmath>
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

fl::coordinator::RunConfig make_config(const std::string& run_id) {
    fl::coordinator::RunConfig config;
    config.run_id = run_id;
    config.manifest = make_manifest();
    config.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    config.weighting = fl::core::WeightingStrategyType::kUniform;
    config.server_lr = 1.0;
    config.target_clients_per_round = 2;
    config.total_clients = 2;
    config.max_rounds = 10;
    config.minimum_valid_results = 2;
    config.client_selection_seed = 7;
    config.task_lease_seconds = 60;
    config.max_task_retries = 3;
    config.client_ids = {"client-a", "client-b"};

    config.privacy_mode = fl::core::PrivacyMode::kUserLevelDp;
    config.user_level_privacy.noise_multiplier = 1.0;
    config.user_level_privacy.initial_clipping_bound = 1.0;  // small: both clients over threshold
    config.user_level_privacy.target_delta = 1e-5;
    config.privacy_noise_seed = 999;
    config.adaptive_clipping_enabled = true;
    config.adaptive_clipping.initial_clip = 1.0;
    config.adaptive_clipping.target_quantile = 0.5;
    config.adaptive_clipping.clip_learning_rate = 0.5;
    config.adaptive_clipping.min_clip = 0.1;
    config.adaptive_clipping.max_clip = 100.0;
    config.adaptive_clipping.count_noise_multiplier = 1e-6;
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

    fl::coordinator::SampleLevelLedgerEntry entry;
    entry.run_id = task.descriptor.run_id;
    entry.round_id = task.descriptor.round_id;
    entry.client_id = task.descriptor.client_id;
    entry.epsilon = 0.5;
    entry.delta = 1e-6;
    entry.noise_multiplier = 0.9;
    entry.sample_rate = 0.25;
    entry.steps = 4;
    entry.accountant = "rdp";
    entry.recorded_at = "2026-01-01T00:00:00Z";
    entry.entry_id =
        "entry-" + task.descriptor.client_id + "-" + std::to_string(task.descriptor.round_id);
    submission.sample_level_privacy = std::move(entry);

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

void run_privacy_recovery_tests(const std::string& scratch_dir) {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;

    std::filesystem::remove_all(scratch_dir);
    CoordinatorConfig coordinator_config;
    const auto config = make_config("run-privacy-recovery");

    // ---- Phase 1: run two rounds, capture state just before "restart" ---- //
    double now = 0.0;
    double pre_restart_user_epsilon = 0.0;
    double pre_restart_clip_epsilon = 0.0;
    std::size_t pre_restart_sample_ledger_size = 0;
    {
        RunManager manager(
            coordinator_config, scratch_dir + "/checkpoints", scratch_dir + "/scaffold");
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-privacy-recovery");
        register_workers(manager);
        run.start("", 0.0);

        run_one_round(run, now, 3.0, -3.0);
        run_one_round(run, now, 3.0, -3.0);

        pre_restart_user_epsilon = run.user_level_ledger().back().epsilon;
        pre_restart_clip_epsilon = run.adaptive_clipping_ledger().back().epsilon;
        pre_restart_sample_ledger_size = run.sample_level_ledger().size();

        check(run.user_level_ledger().size() == 2, "two user-level entries before restart");
        check(run.adaptive_clipping_ledger().size() == 2, "two clipping entries before restart");
        check(pre_restart_sample_ledger_size == 4,
              "four sample-level entries before restart (2 "
              "clients x 2 rounds)");
    }
    // RunManager (and its RunInstance) is now destroyed — simulating a
    // full process restart. Everything below reconstructs purely from
    // the checkpoint file on disk.

    // ---- Phase 2: "restart" via a fresh RunManager + restore ---- //
    {
        RunManager manager(
            coordinator_config, scratch_dir + "/checkpoints", scratch_dir + "/scaffold");
        manager.create_run(config, 0.0);
        auto& run = manager.get("run-privacy-recovery");
        run.restore_from_checkpoint();
        register_workers(manager);

        check(run.user_level_ledger().size() == 2,
              "user-level ledger history survives restart (was silently lost before this fix)");
        check(std::abs(run.user_level_ledger().back().epsilon - pre_restart_user_epsilon) < 1e-12,
              "restored user-level ledger's last epsilon matches exactly");
        check(run.adaptive_clipping_ledger().size() == 2,
              "adaptive-clipping ledger history survives restart");
        check(std::abs(run.adaptive_clipping_ledger().back().epsilon - pre_restart_clip_epsilon) <
                  1e-12,
              "restored adaptive-clipping ledger's last epsilon matches exactly");
        check(run.sample_level_ledger().size() == pre_restart_sample_ledger_size,
              "sample-level ledger history survives restart");

        // The real correctness question: does the NEXT round continue
        // the accountants' trajectories from where they left off, or
        // does it silently restart them at zero (a privacy-accounting
        // regression that would understate real cumulative epsilon)?
        run_one_round(run, now, 3.0, -3.0);

        check(run.user_level_ledger().size() == 3,
              "a post-restart round adds a third user-level entry");
        check(run.user_level_ledger().back().epsilon > pre_restart_user_epsilon,
              "user-level epsilon continues growing from its pre-restart value, not from zero");

        check(run.adaptive_clipping_ledger().size() == 3,
              "a post-restart round adds a third clipping entry");
        check(run.adaptive_clipping_ledger().back().epsilon > pre_restart_clip_epsilon,
              "adaptive-clipping epsilon continues growing from its pre-restart value, not from "
              "zero");
        // The clip bound used by the post-restart round must continue
        // from where round 2 left the controller (both clients are
        // consistently over-threshold in this test, so the bound rises
        // every round) — not reset back down to initial_clip=1.0, which
        // is what a broken restore would silently produce instead.
        check(run.adaptive_clipping_ledger()[2].clip_value >
                  run.adaptive_clipping_ledger()[1].clip_value,
              "adaptive clip bound continues rising post-restart, not reset to initial_clip");
    }
}

}  // namespace fl::coordinator::testing
