#pragma once

#include "fl_coordinator/coordinator_config.hpp"
#include "fl_coordinator/event_bus.hpp"
#include "fl_coordinator/round_manager.hpp"
#include "fl_coordinator/scaffold_client_state.hpp"
#include "fl_coordinator/task_dispatcher.hpp"
#include "fl_coordinator/worker_registry.hpp"
#include "fl_core/aggregation.hpp"
#include "fl_core/coordinator.hpp"

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

struct RunConfig {
    std::string run_id;
    fl::core::ModelManifest manifest;
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
    std::uint32_t round_timeout_seconds{300};
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
    std::uint32_t minimum_valid_results{0};
};

// One run's full domain state: the reused Milestone-2 RunStateMachine for
// lifecycle, a per-round TaskDispatcher, the aggregator/optimizer state,
// and (for SCAFFOLD) the global control variate. Deliberately holds no
// gRPC/transport types — a gRPC handler (or the local-dev TCP bridge, or
// a direct unit test) all drive this identically through plain method
// calls. See docs/coordinator-runtime.md.
class RunInstance {
public:
    RunInstance(
        RunConfig config,
        CoordinatorConfig coordinator_config,
        EventBus& event_bus,
        WorkerRegistry& worker_registry,
        ClientAlgorithmStateStore* scaffold_store,
        std::string checkpoint_directory
    );

    [[nodiscard]] const std::string& run_id() const { return config_.run_id; }
    [[nodiscard]] RunSnapshot snapshot() const;
    [[nodiscard]] std::optional<RoundSnapshot> round_snapshot(std::uint64_t round_id) const;
    [[nodiscard]] const fl::core::ModelManifest& manifest() const { return config_.manifest; }

    void start(const std::string& trace_id, double now_unix_s);
    void pause(const std::string& reason, const std::string& trace_id, double now_unix_s);
    void resume(const std::string& trace_id, double now_unix_s);
    void cancel(const std::string& reason, const std::string& trace_id, double now_unix_s);

    // Drives the round lifecycle one step: sweeps expired leases, starts
    // the next round if idle, or finalizes (validates + aggregates +
    // checkpoints + advances) the current round if enough results are in.
    // Safe to call repeatedly/frequently (e.g. on every heartbeat and
    // every result submission) — it is a no-op when there is nothing to
    // do at the current state.
    void advance(double now_unix_s);

    [[nodiscard]] std::optional<DispatchedTask> acquire_task(const std::string& worker_id, double now_unix_s);

    // For SCAFFOLD tasks: the global control variate plus this client's
    // own control variate (zero-initialized and persisted on first
    // participation), to be attached to the ClientTrainingTask the
    // transport layer builds around the DispatchedTask above. Empty
    // collections for non-SCAFFOLD algorithms.
    [[nodiscard]] std::pair<fl::core::TensorCollection, fl::core::TensorCollection> scaffold_control_variates_for(
        const std::string& client_id
    ) const;

    void report_task_progress(const std::string& worker_id, const std::string& task_id, const std::string& lease_id);
    bool submit_client_result(
        const std::string& worker_id,
        const std::string& task_id,
        const std::string& lease_id,
        ClientResultSubmission result,
        double now_unix_s,
        std::string& reason
    );

    // Persist/restore the full run state (Work Package G). save_checkpoint
    // is also called automatically at the end of every finalize_round.
    void save_checkpoint(double now_unix_s) const;
    void restore_from_checkpoint();

private:
    void transition(fl::core::RunState next, const std::string& reason, double now_unix_s);
    void begin_round(double now_unix_s);
    void finalize_round(double now_unix_s);
    // Reconstructs dispatcher_ for the *current* round after a restore
    // (dispatcher_ itself is not checkpointed — only round_results_ and
    // active_leases_ are). Enqueues fresh tasks only for cohort members
    // that are neither already completed (round_results_) nor still
    // validly leased elsewhere (active_leases_, unexpired) — a client
    // whose lease has since expired is enqueued fresh for retry.
    void rebuild_dispatcher_after_restore(double now_unix_s);
    void apply_deferred_safepoint_actions(double now_unix_s);
    CoordinatorEvent emit(
        CoordinatorEventType type,
        const std::string& reason,
        double now_unix_s,
        std::map<std::string, std::string> metadata = {}
    );
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
    // Source of truth for "which clients in the current round have an
    // accepted result" — checkpointed, unlike dispatcher_'s in-memory
    // task/lease bookkeeping, so a restart mid-round does not lose
    // already-submitted results and does not re-dispatch already-served
    // clients. Keyed by client_id.
    std::map<std::string, ClientResultSubmission> round_results_;

    // Source of truth for "which client currently has an outstanding
    // lease, to which worker, expiring when" — checkpointed for the same
    // reason as round_results_ above. dispatcher_'s own in-memory lease
    // bookkeeping only survives within one process; this is what lets a
    // task acquired by one CLI-bridge invocation still be validated and
    // accepted by a later, separate invocation's submit_client_result.
    struct ActiveLease {
        std::string worker_id;
        std::string task_id;
        std::string lease_id;
        double lease_expires_at_unix_s{0.0};
    };
    std::map<std::string, ActiveLease> active_leases_;

    // Clients whose task exhausted its retries (permanently failed) this
    // round — checkpointed for the same reason as round_results_ and
    // active_leases_: a round is only "settled" (no more results
    // possible) once every cohort member is accounted for as completed
    // or failed, and that accounting must survive a process boundary.
    std::set<std::string> failed_clients_;
    std::uint64_t current_round_id_{0};
    std::string model_version_{"v0"};
    fl::core::TensorCollection global_model_;
    fl::core::OptimizerState optimizer_state_;
    fl::core::TensorCollection scaffold_global_control_;
    bool pause_requested_{false};
    bool cancel_requested_{false};
    std::string trace_id_;
};

// Owns every run plus the coordinator-wide worker registry and event bus.
// No global mutable singleton: a RunManager instance is constructed
// explicitly (by main(), by a test, or by the local TCP bridge) and
// passed to whatever needs it.
class RunManager {
public:
    explicit RunManager(
        CoordinatorConfig config,
        std::string checkpoint_root_directory,
        std::string scaffold_state_root_directory
    );

    // Rejects a duplicate run_id. Returns the created run_id.
    std::string create_run(RunConfig config, double now_unix_s);

    [[nodiscard]] RunInstance& get(const std::string& run_id);
    [[nodiscard]] const RunInstance& get(const std::string& run_id) const;
    [[nodiscard]] std::vector<std::string> list_run_ids() const;

    [[nodiscard]] WorkerRegistry& worker_registry() { return worker_registry_; }
    [[nodiscard]] EventBus& event_bus() { return event_bus_; }
    [[nodiscard]] ClientAlgorithmStateStore& scaffold_store() { return *scaffold_store_; }

    // Recovery (Work Package G): a checkpoint file alone does not carry
    // the original RunConfig (client pool, hyperparameters, manifest) —
    // in a full system that config would come from wherever CreateRun's
    // request was persisted (e.g. the Go control plane's run record),
    // which is out of this milestone's scope. Recovery is therefore:
    // call create_run() with the original config to get a fresh
    // RunInstance, then call get(run_id).restore_from_checkpoint() to
    // overwrite its round/model/optimizer state from disk before serving
    // any requests against it. See docs/coordinator-recovery.md and
    // cpp/coordinator/tests/recovery_test.cpp for the exact sequence.

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
