#pragma once

#include "fl_core/aggregation.hpp"
#include "fl_core/privacy.hpp"

#include <cstdint>
#include <deque>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

enum class TaskState {
    kPending,
    kLeased,
    kCompleted,
    kFailed,
};

struct ClientTaskDescriptor {
    std::string run_id;
    std::uint64_t round_id{0};
    std::string client_id;
    std::string model_version;
    fl::core::AggregationAlgorithm algorithm{fl::core::AggregationAlgorithm::kFedAvg};
    std::string dataset_reference;
    std::uint32_t local_epochs{1};
    std::uint32_t batch_size{32};
    double learning_rate{0.01};
    double momentum{0.0};
    double weight_decay{0.0};
    double fedprox_mu{0.0};

    // Privacy Engineering phase: set only when the run's privacy_mode is
    // kSampleLevelDp/kHybridDp — see RunInstance's make_descriptor and
    // docs/hybrid-dp.md. false/default for every other run, unchanged
    // pre-existing behavior.
    bool sample_level_dp_active{false};
    fl::core::SampleLevelDPConfig sample_level_privacy;
};

// One sample-level-DP accounting step, computed by Opacus in the Python
// worker and submitted alongside a client's result — mirrors
// fl.privacy.v1.SampleLevelLedgerEntry. Authority: the Python worker
// computes and owns this value; the C++ coordinator only stores/relays
// it (never recomputes it) — see docs/privacy-ledger.md's authority-
// split note. Its epsilon/delta protect a *different* neighboring
// relation (one training example) than UserLevelLedgerEntry's (one
// client's complete round contribution) — Critical Privacy Rule: never
// combined with it.
struct SampleLevelLedgerEntry {
    std::string run_id;
    std::uint64_t round_id{0};
    std::string client_id;
    double epsilon{0.0};
    double delta{0.0};
    double noise_multiplier{0.0};
    double sample_rate{0.0};
    std::uint64_t steps{0};
    std::string accountant;
    std::string recorded_at;
    std::string entry_id;
};

// the Algorithm Expansion phase: one client's personalization-relevant metrics for one
// round (scalars only — never tensor values). Mirrors
// fl.coordinator.v1.PersonalizationMetricRecord; see
// docs/personalized-evaluation.md and docs/fairness-metrics.md.
struct PersonalizationMetricRecord {
    std::string client_id;
    std::uint64_t round_id{0};
    std::string algorithm;
    double global_local_accuracy{0.0};
    double personalized_local_accuracy{0.0};
    double global_local_loss{0.0};
    double personalized_local_loss{0.0};
    std::uint64_t sample_count{0};
    double personalized_improvement{0.0};
    std::uint32_t personalized_model_version{0};
    std::string recorded_at;
    std::map<std::string, double> algorithm_metrics;
    bool has_personalized_model{false};
};

// Bundles the aggregation-ready ClientUpdate with the SCAFFOLD-specific
// "here is the client's own refreshed control variate to persist" side
// channel. fl::core::ClientUpdate (the Aggregation Core phase) intentionally knows
// nothing about persistence — that coupling belongs at the coordinator
// layer, not the aggregation core.
struct ClientResultSubmission {
    fl::core::ClientUpdate update;
    fl::core::TensorCollection
        refreshed_client_control_variate;  // empty when algorithm != SCAFFOLD
    // the Algorithm Expansion phase: optional — present only for algorithms producing a
    // personalized model (Ditto, Per-FedAvg).
    std::optional<PersonalizationMetricRecord> personalization_metrics;
    // Privacy Engineering phase: present only when the submitting client
    // actually trained under sample-level/hybrid DP — see
    // ClientTaskDescriptor::sample_level_dp_active above.
    std::optional<SampleLevelLedgerEntry> sample_level_privacy;
};

struct DispatchedTask {
    std::string task_id;
    std::string lease_id;
    std::string worker_id;
    ClientTaskDescriptor descriptor;
    TaskState state{TaskState::kPending};
    double lease_expires_at_unix_s{0.0};
    std::uint32_t attempt{0};
    std::optional<ClientResultSubmission> result;
};

class TaskDispatcherError : public std::runtime_error {
  public:
    explicit TaskDispatcherError(const std::string& what);
};

// Pull-based task dispatch for one round of one run. The pending queue is
// bounded by construction: enqueue() is only ever called once per round
// with exactly the selected cohort (never appended to incrementally), so
// there is no unbounded-growth risk to guard against separately.
class TaskDispatcher {
  public:
    TaskDispatcher(std::uint32_t lease_seconds, std::uint32_t max_retries);

    void enqueue(const std::vector<ClientTaskDescriptor>& descriptors);

    // Returns std::nullopt if there is nothing pending. Enforces one
    // active task per worker: a worker that already holds a leased task
    // gets std::nullopt until it submits/fails that task, not a second
    // concurrent lease.
    std::optional<DispatchedTask> acquire(const std::string& worker_id, double now_unix_s);

    // No-op (beyond validation) if the lease doesn't match; progress
    // reports don't currently extend the lease, so a worker that only
    // reports progress but never submits still gets requeued at
    // lease_expires_at like any other stalled worker.
    void report_progress(const std::string& worker_id,
                         const std::string& task_id,
                         const std::string& lease_id) const;

    // Returns true if accepted. Returns false (with `reason` set) for:
    // unknown task_id, lease_id mismatch (stale/duplicate submission),
    // already-completed task_id (duplicate result), or an expired lease
    // (late result).
    bool submit_result(const std::string& worker_id,
                       const std::string& task_id,
                       const std::string& lease_id,
                       ClientResultSubmission result,
                       double now_unix_s,
                       std::string& reason);

    // Requeues (up to max_retries) or permanently fails (beyond
    // max_retries) any leased task whose lease has expired. Returns the
    // client_ids of tasks that were permanently failed in this sweep.
    std::vector<std::string> sweep_expired_leases(double now_unix_s);

    // Work Package M (worker revocation): invalidates whatever task
    // `worker_id` currently holds a leased task on, if any -- applies
    // the exact same requeue-or-permanently-fail retry policy
    // sweep_expired_leases uses for a naturally expired lease (this is
    // an explicitly *forced* early expiry, not a new policy), so a
    // revoked worker's now-orphaned task is not simply lost. Once this
    // returns, that task's lease no longer belongs to `worker_id` -- a
    // subsequent submit_result() call with the old task_id/lease_id
    // fails exactly as it would for any other stale lease (lease
    // mismatch, since a still-pending task has a cleared worker_id/
    // lease_id, or the task may already have been picked up by a
    // different worker).
    // Returns the client_id of the canceled task, or std::nullopt if
    // this worker held no active lease at all.
    std::optional<std::string> cancel_lease_for_worker(const std::string& worker_id,
                                                        double now_unix_s);

    [[nodiscard]] std::vector<ClientResultSubmission> completed_results() const;
    [[nodiscard]] std::size_t pending_count() const;
    [[nodiscard]] std::size_t leased_count() const;
    [[nodiscard]] std::size_t completed_count() const;
    [[nodiscard]] std::size_t failed_count() const;
    [[nodiscard]] std::vector<std::string> failed_client_ids() const;
    [[nodiscard]] std::vector<std::string> completed_client_ids() const;
    [[nodiscard]] bool all_tasks_settled() const;  // every task completed or permanently failed

  private:
    mutable std::mutex mutex_;
    std::deque<std::string> pending_queue_;
    std::map<std::string, DispatchedTask> tasks_;
    std::map<std::string, std::string> worker_active_task_;
    std::uint32_t lease_seconds_;
    std::uint32_t max_retries_;
    std::uint64_t task_sequence_{0};
    std::uint64_t lease_sequence_{0};
};

}  // namespace fl::coordinator
