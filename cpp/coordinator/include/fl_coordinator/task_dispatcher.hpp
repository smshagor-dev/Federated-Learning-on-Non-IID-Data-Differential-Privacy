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

    bool sample_level_dp_active{false};
    fl::core::SampleLevelDPConfig sample_level_privacy;
};

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

struct ClientResultSubmission {
    fl::core::ClientUpdate update;
    fl::core::TensorCollection refreshed_client_control_variate;
    std::optional<PersonalizationMetricRecord> personalization_metrics;
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

class TaskDispatcher {
  public:
    TaskDispatcher(std::uint32_t lease_seconds, std::uint32_t max_retries);

    void enqueue(const std::vector<ClientTaskDescriptor>& descriptors);

    // Restart-aware enqueue. `initial_attempts` is the number of leases
    // already issued for a client before this dispatcher instance existed.
    // A restored task whose attempt count has already reached max_retries is
    // inserted directly as FAILED and is never put back on the pending queue.
    void enqueue(const std::vector<ClientTaskDescriptor>& descriptors,
                 const std::map<std::string, std::uint32_t>& initial_attempts);

    std::optional<DispatchedTask> acquire(const std::string& worker_id, double now_unix_s);

    void report_progress(const std::string& worker_id,
                         const std::string& task_id,
                         const std::string& lease_id) const;

    bool submit_result(const std::string& worker_id,
                       const std::string& task_id,
                       const std::string& lease_id,
                       ClientResultSubmission result,
                       double now_unix_s,
                       std::string& reason);

    std::vector<std::string> sweep_expired_leases(double now_unix_s);

    std::optional<std::string> cancel_lease_for_worker(const std::string& worker_id,
                                                       double now_unix_s);

    [[nodiscard]] bool has_task(const std::string& task_id) const;
    [[nodiscard]] std::vector<ClientResultSubmission> completed_results() const;
    [[nodiscard]] std::size_t pending_count() const;
    [[nodiscard]] std::size_t leased_count() const;
    [[nodiscard]] std::size_t completed_count() const;
    [[nodiscard]] std::size_t failed_count() const;
    [[nodiscard]] std::vector<std::string> failed_client_ids() const;
    [[nodiscard]] std::vector<std::string> completed_client_ids() const;
    [[nodiscard]] bool all_tasks_settled() const;

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
