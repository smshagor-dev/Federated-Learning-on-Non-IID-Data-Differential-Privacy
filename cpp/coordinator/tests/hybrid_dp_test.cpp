// Regression coverage for hybrid DP mode (kHybridDp): sample-level DP
// (computed in Python, relayed through the coordinator only) active
// simultaneously with user-level DP (clip -> aggregate -> noise, applied
// centrally by this coordinator) — see docs/hybrid-dp.md.
//
// CRITICAL PRIVACY RULE: this test's central assertion is that the two
// mechanisms' epsilon/delta never mix — separate ledgers, separate
// accountants, never summed or averaged together anywhere.
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

fl::coordinator::RunConfig make_hybrid_config(const std::string& run_id, std::uint64_t noise_seed) {
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

    config.privacy_mode = fl::core::PrivacyMode::kHybridDp;
    config.sample_level_privacy.noise_multiplier = 0.8;
    config.sample_level_privacy.max_grad_norm = 1.5;
    config.sample_level_privacy.target_delta = 1e-6;
    config.sample_level_privacy.accountant = "rdp";
    config.sample_level_privacy.poisson_sampling = true;

    config.user_level_privacy.noise_multiplier = 1.0;
    config.user_level_privacy.initial_clipping_bound = 10.0;
    config.user_level_privacy.target_delta = 1e-5;
    config.privacy_noise_seed = noise_seed;
    return config;
}

fl::coordinator::ClientResultSubmission make_result(const fl::coordinator::DispatchedTask& task,
                                                    double delta_value, double sample_epsilon) {
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

    // Simulates what the Python worker would have computed via Opacus
    // and submitted alongside its result — see
    // fl_platform.worker.service's SampleLevelLedgerEntry construction.
    fl::coordinator::SampleLevelLedgerEntry entry;
    entry.run_id = task.descriptor.run_id;
    entry.round_id = task.descriptor.round_id;
    entry.client_id = task.descriptor.client_id;
    entry.epsilon = sample_epsilon;
    entry.delta = 1e-6;
    entry.noise_multiplier = 0.8;
    entry.sample_rate = 0.25;
    entry.steps = 4;
    entry.accountant = "rdp";
    entry.recorded_at = "2026-01-01T00:00:00Z";
    entry.entry_id = "entry-" + task.descriptor.client_id + "-" + std::to_string(task.descriptor.round_id);
    submission.sample_level_privacy = std::move(entry);

    return submission;
}

void register_workers(fl::coordinator::RunManager& manager) {
    // Compatible-worker-only task assignment (docs/worker-privacy-
    // capabilities.md): a hybrid-DP run only dispatches to workers that
    // advertise supports_sample_level_dp at registration time.
    fl::coordinator::WorkerCapability capability;
    capability.privacy.supports_sample_level_dp = true;
    manager.worker_registry().register_worker("worker-a", capability, 0.0);
    manager.worker_registry().register_worker("worker-b", capability, 0.0);
}

}  // namespace

void run_hybrid_dp_tests() {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;

    std::filesystem::remove_all("hybrid_dp_test_scratch");
    CoordinatorConfig coordinator_config;

    RunManager manager(coordinator_config, "hybrid_dp_test_scratch/checkpoints",
                       "hybrid_dp_test_scratch/scaffold");
    manager.create_run(make_hybrid_config("run-hybrid", /*noise_seed=*/321), 0.0);
    auto& run = manager.get("run-hybrid");
    register_workers(manager);
    run.start("", 0.0);

    double now = 0.0;
    run.advance(now);
    const auto task_a = run.acquire_task("worker-a", now).value();
    const auto task_b = run.acquire_task("worker-b", now).value();

    // Dispatch itself must carry the sample-level config to the worker —
    // this is the actual "hybrid wiring" gap this test exists to close:
    // a run in kHybridDp mode must mark every dispatched task as
    // sample-level-DP-active, not just apply user-level DP centrally.
    check(task_a.descriptor.sample_level_dp_active,
          "hybrid mode marks dispatched tasks sample_level_dp_active");
    check(task_a.descriptor.sample_level_privacy.noise_multiplier == 0.8,
          "dispatched task carries the configured sample-level noise_multiplier");
    check(task_a.descriptor.sample_level_privacy.max_grad_norm == 1.5,
          "dispatched task carries the configured sample-level max_grad_norm");
    check(task_a.descriptor.sample_level_privacy.accountant == "rdp",
          "dispatched task carries the configured sample-level accountant");

    std::string reason;
    run.submit_client_result("worker-a", task_a.task_id, task_a.lease_id,
                             make_result(task_a, 2.0, /*sample_epsilon=*/1.2), now, reason);
    run.submit_client_result("worker-b", task_b.task_id, task_b.lease_id,
                             make_result(task_b, 0.0, /*sample_epsilon=*/1.3), now, reason);

    now += 1.0;
    run.advance(now);

    // Both ledgers populated, entirely independently.
    check(run.sample_level_ledger().size() == 2,
          "both clients' sample-level entries are stored (relayed, not recomputed)");
    check(run.user_level_ledger().size() == 1, "one user-level ledger entry after one round");

    // Entries land in submission order (worker-a submitted first, above),
    // not in "client-a"/"client-b" lexical order — select_cohort's seeded
    // shuffle decides which client each worker's task actually belongs
    // to, so assert against the tasks' real client_id rather than
    // hardcoding an assignment that depends on shuffle internals.
    const auto& sample_entries = run.sample_level_ledger();
    check(sample_entries[0].client_id == task_a.descriptor.client_id &&
              sample_entries[0].epsilon == 1.2,
          "first-submitted client's sample-level entry is stored verbatim");
    check(sample_entries[1].client_id == task_b.descriptor.client_id &&
              sample_entries[1].epsilon == 1.3,
          "second-submitted client's sample-level entry is stored verbatim");

    // Critical Privacy Rule: never combined. The user-level epsilon is a
    // single per-round value protecting a completely different
    // neighboring relation than either client's sample-level epsilon —
    // assert they are computed independently (not equal, not summed
    // into one field anywhere in the public API).
    const double user_epsilon = run.user_level_ledger().back().epsilon;
    check(user_epsilon > 0.0, "user-level epsilon is positive");
    check(user_epsilon != sample_entries[0].epsilon && user_epsilon != sample_entries[1].epsilon,
          "user-level epsilon is tracked independently from either client's sample-level epsilon");

    // A second round confirms both ledgers keep growing independently
    // (not just a first-round coincidence).
    run.advance(now);
    const auto task_a2 = run.acquire_task("worker-a", now).value();
    const auto task_b2 = run.acquire_task("worker-b", now).value();
    run.submit_client_result("worker-a", task_a2.task_id, task_a2.lease_id,
                             make_result(task_a2, 3.0, /*sample_epsilon=*/2.1), now, reason);
    run.submit_client_result("worker-b", task_b2.task_id, task_b2.lease_id,
                             make_result(task_b2, 1.0, /*sample_epsilon=*/2.2), now, reason);
    now += 1.0;
    run.advance(now);

    check(run.sample_level_ledger().size() == 4, "sample-level ledger grows across rounds");
    check(run.user_level_ledger().size() == 2, "user-level ledger grows across rounds");
    check(run.user_level_ledger().back().epsilon > user_epsilon,
          "user-level epsilon still grows monotonically under hybrid mode");
}

}  // namespace fl::coordinator::testing
