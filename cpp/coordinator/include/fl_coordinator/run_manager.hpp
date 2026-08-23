#pragma once

#include "fl_coordinator/coordinator_config.hpp"
#include "fl_coordinator/event_bus.hpp"
#include "fl_coordinator/round_manager.hpp"
#include "fl_coordinator/scaffold_client_state.hpp"
#include "fl_coordinator/task_dispatcher.hpp"
#include "fl_coordinator/worker_registry.hpp"
#include "fl_core/aggregation.hpp"
#include "fl_core/coordinator.hpp"
#include "fl_core/privacy.hpp"

#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fl::coordinator {

class RunManagerError : public std::runtime_error {
  public:
    explicit RunManagerError(const std::string& what);
};

// Identifies which submitted tensor names the coordinator may aggregate
// (shared backbone) vs. which must never be aggregated (personalized head,
// frozen). Empty means no manifest was declared for this run and preserves
// the existing permissive FedAvg/FedProx/SCAFFOLD behavior.
struct AggregationManifest {
    std::vector<std::string> shared_parameter_names;
    std::vector<std::string> personalized_parameter_names;
    std::vector<std::string> frozen_parameter_names;
    std::string schema_hash;

    [[nodiscard]] bool is_declared() const {
        return !shared_parameter_names.empty() || !personalized_parameter_names.empty() ||
               !frozen_parameter_names.empty();
    }
};

struct RunConfig {
    std::string run_id;
    fl::core::ModelManifest manifest;
    AggregationManifest aggregation_manifest;
    fl::core::AggregationAlgorithm algorithm{fl::core::AggregationAlgorithm::kFedAvg};
    fl::core::WeightingStrategyType weighting{fl::core::WeightingStrategyType::kSampleCount};
    double server_lr{1.0};
    double beta1{0.9};
    double beta2{0.99};
    double tau{1e-3};
    double contribution_cap{1.0};
    std::uint32_t target_clients_per_round{1};
    std::uint32_t total_clients{1};
    std::uint32_t max_rounds{1};
    // Absolute wall-clock deadline for one communication round. A value of
    // zero keeps the historical no-deadline behavior for old callers; the
    // canonical distributed execution path supplies a positive value.
    std::uint32_t round_timeout_seconds{300};
    // Minimum accepted client results required when the cohort settles or the
    // absolute round deadline is reached. This is not an early-finalization
    // target: before the deadline the coordinator waits for the full cohort
    // unless every remaining client has already permanently failed.
    std::uint32_t minimum_valid_results{1};
    std::uint64_t client_selection_seed{0};
    std::uint32_t task_lease_seconds{60};
    std::uint32_t max_task_retries{3};
    std::uint32_t local_epochs{1};
    std::uint32_t batch_size{32};
    double learning_rate{0.01};
    double momentum{0.0};
    double weight_decay{0.0};
    double fedprox_mu{0.0};
    std::vector<std::string> client_ids;

    fl::core::PrivacyMode privacy_mode{fl::core::PrivacyMode::kNone};
    fl::core::SampleLevelDPConfig sample_level_privacy;
    fl::core::UserLevelDPConfig user_level_privacy;
    std::uint64_t privacy_noise_seed{0};

    bool adaptive_clipping_enabled{false};
    fl::core::AdaptiveClippingConfig adaptive_clipping;

    fl::core::PrivacyBudgetPolicy privacy_budget_policy{fl::core::PrivacyBudgetPolicy::kWarnOnly};
    double warning_threshold_fraction{0.0};
};

struct UserLevelLedgerEntry {
    std::string run_id;
    std::uint64_t round_id{0};
    double epsilon{0.0};
    double delta{0.0};
    double noise_multiplier{0.0};
    double clipping_bound{0.0};
    std::uint32_t num_clients{0};
    double committed_at_unix_s{0.0};
};

struct AdaptiveClippingLedgerEntry {
    std::string run_id;
    std::uint64_t round_id{0};
    double epsilon{0.0};
    double delta{0.0};
    double clip_value{0.0};
    double noisy_over_threshold_fraction{0.0};
};

struct PrivacyMetricsSnapshot {
    std::string run_id;
    std::uint64_t round_id{0};
    bool has_sample_level{false};
    double sample_epsilon{0.0};
    double sample_delta{0.0};
    bool has_user_level{false};
    double user_epsilon{0.0};
    double user_delta{0.0};
    bool has_clipping{false};
    double clipping_epsilon{0.0};
    double clipping_delta{0.0};
    double current_clip_value{0.0};
};

struct PrivacyProjection {
    bool has_sample_level{false};
    double sample_current_epsilon{0.0};
    double sample_projected_next_epsilon{0.0};
    double sample_budget_remaining{0.0};
    bool has_user_level{false};
    double user_current_epsilon{0.0};
    double user_projected_next_epsilon{0.0};
    double user_budget_remaining{0.0};
    bool has_clipping{false};
    double clipping_current_epsilon{0.0};
    double clipping_projected_next_epsilon{0.0};
    double clipping_budget_remaining{0.0};
};

struct RunSnapshot {
    std::string run_id;
    fl::core::RunState state{fl::core::RunState::kCreated};
    std::uint64_t current_round{0};
    std::uint32_t max_rounds{0};
    std::string model_version;
    fl::core::AggregationAlgorithm algorithm{fl::core::AggregationAlgorithm::kFedAvg};
    std::size_t registered_workers{0};
    std::size_t healthy_workers{0};
};

struct RoundSnapshot {
    std::string run_id;
    std::uint64_t round_id{0};
    fl::core::RunState state{fl::core::RunState::kWaitingForClients};
    std::vector<std::string> selected_clients;
    std::vector<std::string> completed_client_ids;
    std::vector<std::string> failed_client_ids;
    std::vector<std::string> timed_out_client_ids;
    std::uint32_t minimum_valid_results{0};
    double round_started_at_unix_s{0.0};
    double round_deadline_at_unix_s{0.0};
};

class RunInstance {
  public:
    RunInstance(RunConfig config,
                CoordinatorConfig coordinator_config,
                EventBus& event_bus,
                WorkerRegistry& worker_registry,
                ClientAlgorithmStateStore* scaffold_store,
                std::string checkpoint_directory);

    [[nodiscard]] const std::string& run_id() const { return config_.run_id; }
    [[nodiscard]] RunSnapshot snapshot() const;
    [[nodiscard]] std::optional<RoundSnapshot> round_snapshot(std::uint64_t round_id) const;
    [[nodiscard]] const fl::core::ModelManifest& manifest() const { return config_.manifest; }
    [[nodiscard]] fl::core::PrivacyMode privacy_mode() const { return config_.privacy_mode; }
    [[nodiscard]] bool adaptive_clipping_enabled() const {
        return config_.adaptive_clipping_enabled;
    }
    [[nodiscard]] fl::core::AggregationAlgorithm algorithm() const { return config_.algorithm; }
    [[nodiscard]] fl::core::WeightingStrategyType weighting() const { return config_.weighting; }
    [[nodiscard]] const fl::core::UserLevelDPConfig& user_level_privacy() const {
        return config_.user_level_privacy;
    }
    [[nodiscard]] bool sample_level_dp_active() const {
        return config_.privacy_mode == fl::core::PrivacyMode::kSampleLevelDp ||
               config_.privacy_mode == fl::core::PrivacyMode::kHybridDp;
    }
    [[nodiscard]] const fl::core::SampleLevelDPConfig& sample_level_privacy() const {
        return config_.sample_level_privacy;
    }
    [[nodiscard]] bool secure_adaptive_clipping_active() const {
        return adaptive_clip_controller_ != nullptr;
    }
    [[nodiscard]] const fl::core::AdaptiveClippingConfig& adaptive_clipping_config() const {
        return config_.adaptive_clipping;
    }
    [[nodiscard]] double current_adaptive_clip_bound() const;
    [[nodiscard]] std::uint64_t adaptive_clip_state_step_count() const;
    [[nodiscard]] double project_user_level_epsilon_after_one_more_step() const;
    [[nodiscard]] fl::core::NoiseProvider* user_level_noise_provider() const {
        return noise_provider_.get();
    }

    void start(const std::string& trace_id, double now_unix_s);
    void pause(const std::string& reason, const std::string& trace_id, double now_unix_s);
    void resume(const std::string& trace_id, double now_unix_s);
    void cancel(const std::string& reason, const std::string& trace_id, double now_unix_s);

    // Drives the round lifecycle one step. A round finalizes before its
    // deadline only when the entire selected cohort is settled. At the
    // absolute deadline every unresolved client is classified as timed out;
    // the partial cohort is released only if minimum_valid_results is met,
    // otherwise the run fails. The deadline is persisted in the coordinator
    // checkpoint so retries/restarts never extend the round silently.
    void advance(double now_unix_s);

    [[nodiscard]] std::optional<DispatchedTask> acquire_task(const std::string& worker_id,
                                                             double now_unix_s);

    [[nodiscard]] std::pair<fl::core::TensorCollection, fl::core::TensorCollection>
    scaffold_control_variates_for(const std::string& client_id) const;

    void report_task_progress(const std::string& worker_id,
                              const std::string& task_id,
                              const std::string& lease_id);
    bool submit_client_result(const std::string& worker_id,
                              const std::string& task_id,
                              const std::string& lease_id,
                              ClientResultSubmission result,
                              double now_unix_s,
                              std::string& reason);

    bool cancel_lease_for_worker(const std::string& worker_id,
                                 const std::string& reason,
                                 double now_unix_s);

    [[nodiscard]] std::vector<PersonalizationMetricRecord> personalization_summary() const;

    [[nodiscard]] bool apply_secure_aggregate_and_advance(
        std::uint64_t round_id,
        const fl::core::AggregationResult& aggregate,
        double now_unix_s,
        std::optional<std::uint64_t> indicator_over_threshold_count = std::nullopt);

    void save_checkpoint(double now_unix_s) const;
    void restore_from_checkpoint();

    [[nodiscard]] const std::vector<UserLevelLedgerEntry>& user_level_ledger() const {
        return user_level_ledger_;
    }
    [[nodiscard]] const std::vector<AdaptiveClippingLedgerEntry>& adaptive_clipping_ledger() const {
        return adaptive_clipping_ledger_;
    }
    [[nodiscard]] const std::vector<SampleLevelLedgerEntry>& sample_level_ledger() const {
        return sample_level_ledger_;
    }
    void append_sample_level_ledger_entry(SampleLevelLedgerEntry entry);

    [[nodiscard]] PrivacyMetricsSnapshot privacy_metrics_snapshot() const;
    [[nodiscard]] PrivacyProjection privacy_projection() const;

  private:
    void transition(fl::core::RunState next, const std::string& reason, double now_unix_s);
    void begin_round(double now_unix_s);
    void finalize_round(double now_unix_s);
    void rebuild_dispatcher_after_restore(double now_unix_s);
    void apply_deferred_safepoint_actions(double now_unix_s);
    CoordinatorEvent emit(CoordinatorEventType type,
                          const std::string& reason,
                          double now_unix_s,
                          std::map<std::string, std::string> metadata = {});
    [[nodiscard]] std::string checkpoint_path() const;

    mutable std::mutex mutex_;
    RunConfig config_;
    CoordinatorConfig coordinator_config_;
    EventBus* event_bus_;
    WorkerRegistry* worker_registry_;
    ClientAlgorithmStateStore* scaffold_store_;
    std::string checkpoint_directory_;

    fl::core::RunStateMachine state_machine_;
    std::unique_ptr<TaskDispatcher> dispatcher_;
    std::vector<std::string> current_cohort_;

    std::unique_ptr<fl::core::UserLevelAccountant> user_level_accountant_;
    std::unique_ptr<fl::core::NoiseProvider> noise_provider_;
    std::vector<UserLevelLedgerEntry> user_level_ledger_;
    std::unique_ptr<fl::core::AdaptiveClipController> adaptive_clip_controller_;
    std::vector<AdaptiveClippingLedgerEntry> adaptive_clipping_ledger_;
    std::vector<SampleLevelLedgerEntry> sample_level_ledger_;
    std::map<std::string, ClientResultSubmission> round_results_;

    std::map<std::string, PersonalizationMetricRecord> personalization_metrics_by_client_;

    struct ActiveLease {
        std::string worker_id;
        std::string task_id;
        std::string lease_id;
        double lease_expires_at_unix_s{0.0};
    };
    std::map<std::string, ActiveLease> active_leases_;
    // Highest lease attempt already issued for each client in the current round.
    // Checkpointed so a coordinator restart never resets max_task_retries.
    std::map<std::string, std::uint32_t> client_retry_attempts_;

    std::set<std::string> failed_clients_;
    // Subset of failed_clients_ whose failure reason is the absolute round
    // deadline rather than retry exhaustion/revocation. Kept separately so
    // observability can distinguish worker/client timeout from other failure.
    std::set<std::string> timed_out_clients_;
    std::uint64_t current_round_id_{0};
    double round_started_at_unix_s_{0.0};
    double round_deadline_at_unix_s_{0.0};
    std::string model_version_{"v0"};
    fl::core::TensorCollection global_model_;
    fl::core::OptimizerState optimizer_state_;
    fl::core::TensorCollection scaffold_global_control_;
    bool pause_requested_{false};
    bool cancel_requested_{false};
    std::string trace_id_;
};

class RunManager {
  public:
    explicit RunManager(CoordinatorConfig config,
                        std::string checkpoint_root_directory,
                        std::string scaffold_state_root_directory);

    std::string create_run(RunConfig config, double now_unix_s);

    [[nodiscard]] RunInstance& get(const std::string& run_id);
    [[nodiscard]] const RunInstance& get(const std::string& run_id) const;
    [[nodiscard]] std::vector<std::string> list_run_ids() const;

    std::uint32_t cancel_leases_for_worker(const std::string& worker_id,
                                           const std::string& reason,
                                           double now_unix_s);

    [[nodiscard]] WorkerRegistry& worker_registry() { return worker_registry_; }
    [[nodiscard]] EventBus& event_bus() { return event_bus_; }
    [[nodiscard]] ClientAlgorithmStateStore& scaffold_store() { return *scaffold_store_; }

  private:
    CoordinatorConfig config_;
    std::string checkpoint_root_directory_;
    std::string scaffold_state_root_directory_;
    mutable std::mutex mutex_;
    std::map<std::string, std::unique_ptr<RunInstance>> runs_;
    WorkerRegistry worker_registry_;
    EventBus event_bus_;
    std::unique_ptr<ClientAlgorithmStateStore> scaffold_store_;
};

}  // namespace fl::coordinator
