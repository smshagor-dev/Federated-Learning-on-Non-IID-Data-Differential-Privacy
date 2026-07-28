// Regression coverage for privacy budget policies (WARN_ONLY/
// STOP_BEFORE_EXCEEDING/STOP_AFTER_CURRENT_ROUND/FAIL_RUN), applied
// independently per mechanism by RunInstance::finalize_round — see
// docs/privacy-budget-policies.md. Drives real rounds through
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

fl::coordinator::RunConfig make_config(const std::string& run_id,
                                       fl::core::PrivacyBudgetPolicy policy,
                                       double epsilon_budget,
                                       std::uint32_t max_rounds = 10) {
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
    config.client_selection_seed = 42;
    config.task_lease_seconds = 60;
    config.max_task_retries = 3;
    config.client_ids = {"client-a", "client-b"};
    config.privacy_mode = fl::core::PrivacyMode::kUserLevelDp;
    // A large noise_multiplier/small target_delta so epsilon grows fast
    // enough that a handful of rounds crosses a small budget — keeps
    // these tests fast without needing hundreds of rounds.
    config.user_level_privacy.noise_multiplier = 0.5;
    config.user_level_privacy.initial_clipping_bound = 10.0;
    config.user_level_privacy.target_delta = 1e-5;
    config.user_level_privacy.epsilon_budget = epsilon_budget;
    config.privacy_noise_seed = 123;
    config.privacy_budget_policy = policy;
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

// Runs rounds until the run leaves kRunning/kWaitingForClients (i.e.
// reaches a terminal state) or max_rounds_to_try rounds have been
// attempted, whichever comes first. Returns the number of rounds that
// actually produced a task (some may be silently refused by
// kStopBeforeExceeding's pre-check, which still counts as "a round was
// attempted" from the caller's perspective).
std::uint32_t drive_until_terminal(fl::coordinator::RunInstance& run,
                                   double& now,
                                   std::uint32_t max_rounds_to_try) {
    std::uint32_t rounds_completed = 0;
    for (std::uint32_t i = 0; i < max_rounds_to_try; ++i) {
        run.advance(now);
        const auto state = run.snapshot().state;
        if (state != fl::core::RunState::kWaitingForClients &&
            state != fl::core::RunState::kRunning) {
            break;
        }
        auto task_a = run.acquire_task("worker-a", now);
        auto task_b = run.acquire_task("worker-b", now);
        if (!task_a.has_value() || !task_b.has_value()) {
            break;  // no more tasks to acquire -> run has stopped dispatching
        }
        std::string reason;
        run.submit_client_result(
            "worker-a", task_a->task_id, task_a->lease_id, make_result(*task_a, 2.0), now, reason);
        run.submit_client_result(
            "worker-b", task_b->task_id, task_b->lease_id, make_result(*task_b, 0.0), now, reason);
        now += 1.0;
        run.advance(now);
        ++rounds_completed;
    }
    return rounds_completed;
}

}  // namespace

void run_privacy_budget_policy_tests() {
    using fl::coordinator::CoordinatorConfig;
    using fl::coordinator::RunManager;

    std::filesystem::remove_all("privacy_budget_policy_test_scratch");
    CoordinatorConfig coordinator_config;

    // --- kWarnOnly: budget is exceeded but the run keeps going all the
    // way to max_rounds — never stopped early. ---
    {
        RunManager manager(coordinator_config,
                           "privacy_budget_policy_test_scratch/checkpoints_warn",
                           "privacy_budget_policy_test_scratch/scaffold_warn");
        manager.create_run(make_config("run-warn",
                                       fl::core::PrivacyBudgetPolicy::kWarnOnly,
                                       /*epsilon_budget=*/0.01,
                                       /*max_rounds=*/5),
                           0.0);
        auto& run = manager.get("run-warn");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        const auto rounds = drive_until_terminal(run, now, 10);
        check(rounds == 5, "kWarnOnly never stops the run early even once budget is exceeded");
        check(run.snapshot().state == fl::core::RunState::kCompleted,
              "kWarnOnly run still completes normally via max_rounds");
        check(run.user_level_ledger().back().epsilon >= 0.01,
              "budget was genuinely exceeded during this run (test premise check)");
    }

    // --- kStopAfterCurrentRound: run stops before reaching max_rounds,
    // in state kCompleted (graceful), once budget is crossed. ---
    {
        RunManager manager(coordinator_config,
                           "privacy_budget_policy_test_scratch/checkpoints_stop_after",
                           "privacy_budget_policy_test_scratch/scaffold_stop_after");
        manager.create_run(make_config("run-stop-after",
                                       fl::core::PrivacyBudgetPolicy::kStopAfterCurrentRound,
                                       /*epsilon_budget=*/0.01,
                                       /*max_rounds=*/10),
                           0.0);
        auto& run = manager.get("run-stop-after");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        const auto rounds = drive_until_terminal(run, now, 10);
        check(rounds < 10, "kStopAfterCurrentRound stops before max_rounds once budget is crossed");
        check(rounds >= 1, "at least one round completes before the stop takes effect");
        check(run.snapshot().state == fl::core::RunState::kCompleted,
              "kStopAfterCurrentRound ends the run in kCompleted (graceful), not kFailed");
    }

    // --- kFailRun: same reactive trigger as kStopAfterCurrentRound, but
    // ends in kFailed instead. ---
    {
        RunManager manager(coordinator_config,
                           "privacy_budget_policy_test_scratch/checkpoints_fail",
                           "privacy_budget_policy_test_scratch/scaffold_fail");
        manager.create_run(make_config("run-fail",
                                       fl::core::PrivacyBudgetPolicy::kFailRun,
                                       /*epsilon_budget=*/0.01,
                                       /*max_rounds=*/10),
                           0.0);
        auto& run = manager.get("run-fail");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        const auto rounds = drive_until_terminal(run, now, 10);
        check(rounds < 10, "kFailRun stops before max_rounds once budget is crossed");
        check(run.snapshot().state == fl::core::RunState::kFailed,
              "kFailRun ends the run in kFailed, distinctly from kStopAfterCurrentRound's "
              "kCompleted");
    }

    // --- kStopBeforeExceeding: the round that WOULD cross the budget is
    // never released at all (its client results are simply dropped) —
    // the ledger's last epsilon must never exceed the budget, unlike the
    // reactive policies above which may cross it by up to one round. ---
    {
        RunManager manager(coordinator_config,
                           "privacy_budget_policy_test_scratch/checkpoints_stop_before",
                           "privacy_budget_policy_test_scratch/scaffold_stop_before");
        manager.create_run(make_config("run-stop-before",
                                       fl::core::PrivacyBudgetPolicy::kStopBeforeExceeding,
                                       /*epsilon_budget=*/0.01,
                                       /*max_rounds=*/10),
                           0.0);
        auto& run = manager.get("run-stop-before");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        const auto rounds = drive_until_terminal(run, now, 10);
        check(rounds < 10, "kStopBeforeExceeding stops before max_rounds");
        check(run.snapshot().state == fl::core::RunState::kCompleted,
              "kStopBeforeExceeding ends the run in kCompleted (graceful), not kFailed");
        if (!run.user_level_ledger().empty()) {
            check(run.user_level_ledger().back().epsilon < 0.01,
                  "kStopBeforeExceeding guarantees the budget is never actually exceeded by any "
                  "released round");
        }
    }

    // --- Unset epsilon_budget (0, the default): no policy ever applies,
    // regardless of its value — run completes normally via max_rounds
    // even under kFailRun. ---
    {
        RunManager manager(coordinator_config,
                           "privacy_budget_policy_test_scratch/checkpoints_unset",
                           "privacy_budget_policy_test_scratch/scaffold_unset");
        manager.create_run(make_config("run-unset",
                                       fl::core::PrivacyBudgetPolicy::kFailRun,
                                       /*epsilon_budget=*/0.0,
                                       /*max_rounds=*/3),
                           0.0);
        auto& run = manager.get("run-unset");
        register_workers(manager);
        run.start("", 0.0);

        double now = 0.0;
        const auto rounds = drive_until_terminal(run, now, 10);
        check(rounds == 3, "unset epsilon_budget means kFailRun never triggers");
        check(run.snapshot().state == fl::core::RunState::kCompleted,
              "run with no budget configured completes normally regardless of policy");
    }
}

}  // namespace fl::coordinator::testing
