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

// the Algorithm Expansion phase: identifies which submitted tensor names the coordinator
// may aggregate (shared backbone) vs. which must never be aggregated
// (personalized head, frozen). Mirrors
// fl.coordinator.v1.AggregationManifest — see
// docs/aggregation-manifests.md. Empty (default-constructed, i.e. no
// names declared anywhere) means "no manifest was declared for this
// run," which submit_client_result treats permissively for backward
// compatibility with FedAvg/FedProx/SCAFFOLD's existing single-tensor
// tests, rather than rejecting every tensor name as "not on the list."
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

    // Privacy Engineering phase: kNone preserves all pre-existing
    // behavior exactly (finalize_round's clip/noise step is skipped
    // entirely) — see docs/user-level-dp.md.
    fl::core::PrivacyMode privacy_mode{fl::core::PrivacyMode::kNone};
    // Only consulted (and only sent to workers via ClientTaskDescriptor)
    // when privacy_mode is kSampleLevelDp/kHybridDp — see
    // docs/hybrid-dp.md. The C++ coordinator never applies this
    // mechanism itself; it only relays config out and ledger entries in.
    fl::core::SampleLevelDPConfig sample_level_privacy;
    fl::core::UserLevelDPConfig user_level_privacy;
    // 0 (default): use a real, non-deterministic SecureNoiseProvider —
    // the runtime default. Non-zero: use a DeterministicNoiseProvider
    // seeded from this value, for tests only. Deliberately a separate
    // field from client_selection_seed — see docs/user-level-dp.md's
    // "no seed reuse between ordinary experiment seeds and runtime
    // privacy noise" requirement.
    std::uint64_t privacy_noise_seed{0};

    // Adaptive clipping (docs/adaptive-clipping.md): only consulted when
    // privacy_mode is kUserLevelDp/kHybridDp. When disabled (the
    // default), the clip bound stays fixed at
    // user_level_privacy.initial_clipping_bound for the run's entire
    // duration — unchanged pre-existing behavior. When enabled, the
    // bound instead starts at adaptive_clipping.initial_clip and moves
    // every round according to the (separately accounted) noised
    // over-threshold count.
    bool adaptive_clipping_enabled{false};
    fl::core::AdaptiveClippingConfig adaptive_clipping;

    // Privacy budget policies (docs/privacy-budget-policies.md): applied
    // independently to user-level DP and adaptive clipping (the two
    // mechanisms this coordinator owns an accountant for — sample-level
    // DP's accounting lives entirely in each Python worker's Opacus
    // instance, out of this coordinator's control-flow reach). A
    // mechanism whose epsilon_budget is unset (0) is never affected by
    // this policy, regardless of its value.
    fl::core::PrivacyBudgetPolicy privacy_budget_policy{fl::core::PrivacyBudgetPolicy::kWarnOnly};
    // Fraction of epsilon_budget at which a kPrivacyBudgetWarning event
    // fires (e.g. 0.8 = warn at 80% of budget), independent of
    // privacy_budget_policy (a warning can fire even under kFailRun,
    // ahead of the actual stop). 0 disables early warnings entirely.
    double warning_threshold_fraction{0.0};
};

// One user-level-DP accounting step, owned and appended by the
// coordinator (never by Go, never recomputed downstream) — mirrors
// fl.privacy.v1.UserLevelLedgerEntry. See docs/privacy-ledger.md's
// authority-split note.
struct UserLevelLedgerEntry {
    std::string run_id;
    std::uint64_t round_id{0};
    double epsilon{0.0};
    double delta{0.0};
    double noise_multiplier{0.0};
    double clipping_bound{0.0};
    std::uint32_t num_clients{0};
    // Secure User-Level DP Operations, Observability, and Release
    // Evidence slice, Work Area G: wall-clock commit time, additive --
    // needed for the privacy runtime-health model's "last successful
    // round" field. 0.0 for ledger entries committed before this field
    // existed (none currently persisted across restarts, so this is
    // purely a forward-looking addition, not a migration concern).
    double committed_at_unix_s{0.0};
};

// One adaptive-clipping accounting step — mirrors
// fl.privacy.v1.AdaptiveClippingLedgerEntry. Its epsilon/delta protect
// the clip-bound statistic's neighboring relation, distinct from
// UserLevelLedgerEntry's — see the Critical Privacy Rule note in
// fl_core/privacy.hpp. Never combined with user_level_ledger entries.
struct AdaptiveClippingLedgerEntry {
    std::string run_id;
    std::uint64_t round_id{0};
    double epsilon{0.0};
    double delta{0.0};
    // The bound this round's clipping actually used (not the bound
    // computed *for* next round — see finalize_round's ordering).
    double clip_value{0.0};
    double noisy_over_threshold_fraction{0.0};
};

// Point-in-time summary across all three (independent) mechanisms for
// one run — mirrors fl.privacy.v1.PrivacyMetricsSnapshot. Each
// mechanism's has_*/epsilon/delta trio is populated only if that
// mechanism is actually active for this run; the three are never
// combined into a single number anywhere in this struct or its wire
// mapping (Critical Privacy Rule).
struct PrivacyMetricsSnapshot {
    std::string run_id;
    std::uint64_t round_id{0};
    bool has_sample_level{false};
    // Worst-case (max) epsilon across all clients that have submitted a
    // sample-level entry so far this run — a documented summarization
    // choice (see docs/privacy-ledger.md), not a combination of separate
    // mechanisms: it is still purely the sample-level mechanism's own
    // epsilon, just reduced across the per-client entries the way a
    // "how exposed is the most-exposed participant" dashboard wants.
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

// One-step-ahead preview, mirrors fl.privacy.v1.PrivacyProjection.
// budget_remaining is epsilon_budget - current_epsilon for whichever
// mechanism configured a nonzero epsilon_budget; +infinity when no
// budget was configured (see *DPConfig::epsilon_budget's "0 means
// unset" convention) — a deliberate, documented sentinel rather than 0
// or -1, so "no budget set" is never confusable with "budget exhausted".
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
    std::uint32_t minimum_valid_results{0};
};

// One run's full domain state: the reused Aggregation-Core RunStateMachine for
// lifecycle, a per-round TaskDispatcher, the aggregator/optimizer state,
// and (for SCAFFOLD) the global control variate. Deliberately holds no
// gRPC/transport types — a gRPC handler (or the local-dev TCP bridge, or
// a direct unit test) all drive this identically through plain method
// calls. See docs/coordinator-runtime.md.
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
    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area AB: narrow accessors (matching run_id()/manifest()'s
    // existing one-line-getter convention) so coordinator_service.cpp's
    // AcquireTask handler can decide secure-aggregation privacy-mode
    // compatibility at session-creation time without a new gRPC-gated
    // dependency on this protobuf-free library (fl::core::PrivacyMode
    // and bool are both already protobuf-free).
    [[nodiscard]] fl::core::PrivacyMode privacy_mode() const { return config_.privacy_mode; }
    [[nodiscard]] bool adaptive_clipping_enabled() const { return config_.adaptive_clipping_enabled; }
    [[nodiscard]] fl::core::AggregationAlgorithm algorithm() const { return config_.algorithm; }
    // Secure User-Level Differential Privacy Runtime slice, Work Areas
    // D/N: two more narrow accessors, same convention as the three
    // above. weighting() lets AcquireTask reject
    // SECURE_USER_LEVEL_DP_VARIABLE_WEIGHT_UNSUPPORTED before a secure
    // session is ever created; user_level_privacy() exposes the
    // already-existing noise_multiplier/initial_clipping_bound/
    // target_delta/epsilon_budget this run's (non-secure) user-level-DP
    // path already uses, reused unchanged for the secure path (see
    // docs/secure-user-level-dp-runtime-audit.md finding #4/#9).
    [[nodiscard]] fl::core::WeightingStrategyType weighting() const { return config_.weighting; }
    [[nodiscard]] const fl::core::UserLevelDPConfig& user_level_privacy() const {
        return config_.user_level_privacy;
    }
    // Secure Hybrid Differential Privacy Runtime slice: same convention
    // as user_level_privacy() above, for the sample-level half of a
    // kHybridDp secure session's compatibility gate. Activity is
    // derived from privacy_mode() (kSampleLevelDp/kHybridDp), matching
    // ClientTaskDescriptor::sample_level_dp_active's own existing
    // derivation in make_descriptor() -- there is no separate stored
    // "active" bool on RunConfig, by design (single source of truth).
    [[nodiscard]] bool sample_level_dp_active() const {
        return config_.privacy_mode == fl::core::PrivacyMode::kSampleLevelDp ||
               config_.privacy_mode == fl::core::PrivacyMode::kHybridDp;
    }
    [[nodiscard]] const fl::core::SampleLevelDPConfig& sample_level_privacy() const {
        return config_.sample_level_privacy;
    }
    // Work Area N's "reserve" pre-check: a non-mutating projection of
    // what get_epsilon() would read after one more accountant step,
    // exactly like the existing (non-secure) STOP_BEFORE_EXCEEDING
    // check already uses -- see docs/secure-user-level-dp-semantics.md
    // section 12. Throws std::logic_error if privacy_mode is not
    // kUserLevelDp (user_level_accountant_ is only constructed then) --
    // callers must check privacy_mode() first, matching every other
    // accessor's "caller already knows this is meaningful" convention
    // in this class.
    [[nodiscard]] double project_user_level_epsilon_after_one_more_step() const;
    // Work Areas P/Q: the same CryptoSecureNoiseProvider instance the
    // non-secure user-level-DP path already uses -- returned as a raw,
    // non-owning pointer so coordinator_service.cpp's SubmitMaskedClientUpdate
    // handler can pass it into SecureAggregationSessionManager::finalize()
    // without this protobuf-free class taking any dependency on that
    // gRPC-gated one. nullptr if privacy_mode is not kUserLevelDp.
    [[nodiscard]] fl::core::NoiseProvider* user_level_noise_provider() const {
        return noise_provider_.get();
    }

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

    [[nodiscard]] std::optional<DispatchedTask> acquire_task(const std::string& worker_id,
                                                             double now_unix_s);

    // For SCAFFOLD tasks: the global control variate plus this client's
    // own control variate (zero-initialized and persisted on first
    // participation), to be attached to the ClientTrainingTask the
    // transport layer builds around the DispatchedTask above. Empty
    // collections for non-SCAFFOLD algorithms.
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

    // Work Package M (worker revocation): cancels whatever task
    // `worker_id` currently holds an active lease on *within this run*,
    // if any. Keeps dispatcher_'s bookkeeping and this run's own
    // checkpointed active_leases_ (see that field's doc comment on why
    // both exist) in sync, emits a kTaskCanceledByRevocation event, and
    // persists the change. Returns true if a lease was actually
    // canceled (false is not an error -- it just means this worker had
    // no active task in this particular run).
    bool cancel_lease_for_worker(const std::string& worker_id,
                                 const std::string& reason,
                                 double now_unix_s);

    // the Algorithm Expansion phase: latest personalization metric record per client that
    // has ever submitted one for this run (not full history — see
    // fl.coordinator.v1.PersonalizationSummaryResponse). Aggregated
    // fairness statistics are deliberately NOT computed here — see
    // docs/fairness-metrics.md for why that's a Go/Python concern, not a
    // coordinator one.
    [[nodiscard]] std::vector<PersonalizationMetricRecord> personalization_summary() const;

    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area Y: applies an externally-computed (secure-
    // aggregation) AggregationResult to advance the model version and
    // checkpoint -- the bridge coordinator_service.cpp's
    // SubmitMaskedClientUpdate handler needs, since
    // SecureAggregationSessionManager (gRPC-gated) cannot be a member
    // of this protobuf-free class (see
    // docs/secure-aggregation-masked-runtime-audit.md's architectural
    // decision). Deliberately NOT a refactor of finalize_round() --
    // that function's own SCAFFOLD/privacy-ledger/adaptive-clipping
    // concerns do not apply to a secure round (which never receives a
    // per-client ClientResultSubmission at all, see Work Area P), so
    // this is a smaller, independent function mirroring only
    // finalize_round()'s model-advance + checkpoint tail. Zero changes
    // to finalize_round() itself.
    //
    // Returns false (a safe no-op, not an exception) if `round_id` does
    // not match the run's current round or the run is not in
    // kWaitingForClients -- the caller (the RPC handler) must treat
    // that as "this round already advanced / this round is not the
    // live one" and never re-apply. Only `aggregate.model_delta`'s
    // tensors that are also present in the run's own manifest are
    // applied (a defensive intersection, not an assumption the two
    // always match exactly) -- control_delta/optimizer_state are never
    // consulted (SecureAggregationSessionManager::finalize() does not
    // meaningfully populate them; this provider is FedAvg-only this
    // slice, see Work Area Z). Throws std::runtime_error only for a
    // genuine internal-consistency failure (a manifest tensor shape
    // mismatch) -- the caller must treat that as a failed finalization,
    // never a successful one, and is responsible for reflecting that
    // honestly (this function cannot itself un-complete the secure
    // session, which SecureAggregationSessionManager::finalize()
    // already marked COMPLETED before this is ever called -- a known,
    // narrow, disclosed residual-inconsistency window, see
    // docs/known-limitations.md).
    [[nodiscard]] bool apply_secure_aggregate_and_advance(std::uint64_t round_id,
                                                           const fl::core::AggregationResult& aggregate,
                                                           double now_unix_s);

    // Persist/restore the full run state (Work Package G). save_checkpoint
    // is also called automatically at the end of every finalize_round.
    void save_checkpoint(double now_unix_s) const;
    void restore_from_checkpoint();

    // Privacy Engineering phase: full history of this run's user-level-DP
    // accounting steps — see docs/privacy-ledger.md. Empty for any run
    // whose privacy_mode is not kUserLevelDp/kHybridDp.
    [[nodiscard]] const std::vector<UserLevelLedgerEntry>& user_level_ledger() const {
        return user_level_ledger_;
    }

    // Privacy Engineering phase: full history of this run's adaptive-
    // clipping accounting steps — see docs/privacy-ledger.md. Empty
    // unless privacy_mode is kUserLevelDp/kHybridDp AND
    // adaptive_clipping_enabled.
    [[nodiscard]] const std::vector<AdaptiveClippingLedgerEntry>& adaptive_clipping_ledger() const {
        return adaptive_clipping_ledger_;
    }

    // Privacy Engineering phase: sample-level entries submitted by
    // workers alongside their results (see submit_client_result). The
    // coordinator stores/relays these verbatim — it never recomputes or
    // validates the epsilon value itself, only the run_id/round_id
    // linkage. Empty unless privacy_mode is kSampleLevelDp/kHybridDp.
    [[nodiscard]] const std::vector<SampleLevelLedgerEntry>& sample_level_ledger() const {
        return sample_level_ledger_;
    }
    // Secure Hybrid Differential Privacy Runtime slice: appends one
    // already-verified sample-level entry, for the secure
    // (SubmitMaskedClientUpdate) path -- the masked-tensor payload
    // never reaches this class, only the independently signed and
    // already-verified accounting metadata (coordinator_service.cpp
    // verifies signature/binding/replay/monotonicity before calling
    // this). Mirrors submit_client_result's existing (cleartext-path)
    // sample_level_ledger_.push_back behavior, factored out as its own
    // narrow method so the secure path does not have to go through the
    // full submit_client_result/ClientResultSubmission machinery (which
    // assumes a per-round dispatched task/lease this secure path
    // doesn't use the same way). Thread-safe: takes the same mutex_
    // every other mutating method on this class does.
    void append_sample_level_ledger_entry(SampleLevelLedgerEntry entry);

    // Backs the GetPrivacyMetrics/GetPrivacyProjection RPCs — see
    // coordinator_service.cpp. Safe to call regardless of privacy_mode;
    // returns all-false/all-zero for a non-private run.
    [[nodiscard]] PrivacyMetricsSnapshot privacy_metrics_snapshot() const;
    [[nodiscard]] PrivacyProjection privacy_projection() const;

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

    // Privacy Engineering phase: constructed only when config_.privacy_mode
    // is kUserLevelDp/kHybridDp (see the constructor). Reconstructed (not
    // checkpointed directly) on restore from the checkpointed step count —
    // see restore_from_checkpoint's privacy-state handling and
    // docs/coordinator-recovery.md.
    std::unique_ptr<fl::core::UserLevelAccountant> user_level_accountant_;
    std::unique_ptr<fl::core::NoiseProvider> noise_provider_;
    std::vector<UserLevelLedgerEntry> user_level_ledger_;
    // Constructed only when config_.adaptive_clipping_enabled is also
    // true (in addition to user-level/hybrid DP being active). Borrows
    // noise_provider_ above — must be declared after it so destruction
    // order tears this down first, before the provider it references.
    std::unique_ptr<fl::core::AdaptiveClipController> adaptive_clip_controller_;
    std::vector<AdaptiveClippingLedgerEntry> adaptive_clipping_ledger_;
    std::vector<SampleLevelLedgerEntry> sample_level_ledger_;
    // Source of truth for "which clients in the current round have an
    // accepted result" — checkpointed, unlike dispatcher_'s in-memory
    // task/lease bookkeeping, so a restart mid-round does not lose
    // already-submitted results and does not re-dispatch already-served
    // clients. Keyed by client_id.
    std::map<std::string, ClientResultSubmission> round_results_;

    // the Algorithm Expansion phase: latest personalization metric record per client
    // (across all rounds so far, not just the current one — a client's
    // most recent personalization state is meaningful even between
    // rounds it doesn't participate in). Checkpointed for the same
    // restart-durability reason as round_results_.
    std::map<std::string, PersonalizationMetricRecord> personalization_metrics_by_client_;

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
    explicit RunManager(CoordinatorConfig config,
                        std::string checkpoint_root_directory,
                        std::string scaffold_state_root_directory);

    // Rejects a duplicate run_id. Returns the created run_id.
    std::string create_run(RunConfig config, double now_unix_s);

    [[nodiscard]] RunInstance& get(const std::string& run_id);
    [[nodiscard]] const RunInstance& get(const std::string& run_id) const;
    [[nodiscard]] std::vector<std::string> list_run_ids() const;

    // Work Package M (worker revocation): a worker may hold an active
    // lease in any of several concurrently-running runs (WorkerIdentityRegistry
    // is global; RunInstance/TaskDispatcher state is per-run) -- this
    // iterates every run and cancels `worker_id`'s active lease in each
    // one it holds one in. Returns the number of runs in which a lease
    // was actually canceled.
    std::uint32_t cancel_leases_for_worker(const std::string& worker_id,
                                           const std::string& reason,
                                           double now_unix_s);

    [[nodiscard]] WorkerRegistry& worker_registry() { return worker_registry_; }
    [[nodiscard]] EventBus& event_bus() { return event_bus_; }
    [[nodiscard]] ClientAlgorithmStateStore& scaffold_store() { return *scaffold_store_; }

    // Recovery (Work Package G): a checkpoint file alone does not carry
    // the original RunConfig (client pool, hyperparameters, manifest) —
    // in a full system that config would come from wherever CreateRun's
    // request was persisted (e.g. the Go control plane's run record),
    // which is out of this phase's scope. Recovery is therefore:
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
