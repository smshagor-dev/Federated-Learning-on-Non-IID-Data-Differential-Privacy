#pragma once

// Coordinator secure-aggregation session orchestration. The manager owns the
// frozen roster and masked contribution state; it never persists raw masked
// contributions or key material. v3 recovery adds one read-only projection so
// the authenticated recovery service can construct a zero-data mask-correction
// contribution without reaching into private session state.

#include "fl_coordinator/secure_aggregation_session.hpp"
#include "fl_coordinator/secure_aggregation_session_store.hpp"
#include "fl_core/aggregation.hpp"
#include "fl_core/privacy.hpp"

#include "coordinator/coordinator.pb.h"
#include "worker/worker.pb.h"

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace fl::coordinator {

struct CoordinatorSigningIdentity;

class SecureAggregationSessionManagerError : public std::runtime_error {
  public:
    explicit SecureAggregationSessionManagerError(const std::string& what);
};

// Read-only information needed to validate and compute a threshold-dropout
// correction. It contains public/frozen protocol metadata and contributor IDs,
// never raw masked tensors, Shamir shares, reconstructed private keys, or
// decoded client updates.
struct SecureAggregationRecoveryView {
    fl::coordinator::v1::SecureAggregationSessionConfig config;
    fl::coordinator::v1::FrozenCohortRoster frozen_roster;
    std::vector<std::string> contributing_worker_ids;
    std::map<std::string, std::size_t> tensor_element_counts;
};

class SecureAggregationSessionManager {
  public:
    explicit SecureAggregationSessionManager(SecureAggregationSessionStore* store = nullptr);

    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus create_session(
        const fl::coordinator::v1::SecureAggregationSessionConfig& config, double now_unix_s);

    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus advertise_key(
        const fl::worker::v1::SecureAggregationKeyAdvertisement& advertisement, double now_unix_s);

    [[nodiscard]] fl::coordinator::v1::FrozenCohortRoster freeze_cohort(
        const std::string& session_id,
        double now_unix_s,
        const CoordinatorSigningIdentity* signing_identity = nullptr);

    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus submit_masked_update(
        const fl::worker::v1::MaskedClientUpdate& update, double now_unix_s);

    // Finalizes a complete masked cohort. v3 dropout recovery deliberately
    // reuses this unchanged path by first inserting a synthetic correction
    // contribution with zero clear update/weight and only the missing
    // pairwise-mask side. That keeps decoding/noise/aggregation logic in one
    // authoritative implementation.
    [[nodiscard]] fl::core::AggregationResult finalize(
        const std::string& session_id,
        double now_unix_s,
        fl::core::NoiseProvider* noise_provider = nullptr,
        double noise_std_dev = 0.0,
        double expected_weight_sum = 0.0);

    [[nodiscard]] std::uint64_t decode_secure_adaptive_clipping_indicator_count(
        const std::string& session_id) const;

    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus abort(
        const std::string& session_id,
        fl::coordinator::v1::SecureAggregationAbortReason reason,
        double now_unix_s);

    [[nodiscard]] std::optional<fl::coordinator::v1::SecureAggregationSessionStatus> find(
        const std::string& session_id) const;

    [[nodiscard]] std::vector<fl::coordinator::v1::SecureAggregationSessionSummary> list() const;

    [[nodiscard]] std::optional<fl::coordinator::v1::SecureAggregationTaskBinding>
    find_binding_for_participant(const std::string& run_id,
                                 std::uint64_t round_id,
                                 const std::string& worker_id) const;

    [[nodiscard]] bool has_session_for_run_round(const std::string& run_id,
                                                 std::uint64_t round_id) const;

    [[nodiscard]] std::optional<fl::coordinator::v1::SecureAggregationSessionStatus>
    find_status_for_run_round(const std::string& run_id, std::uint64_t round_id) const;

    [[nodiscard]] std::optional<fl::coordinator::v1::FrozenCohortRoster> get_frozen_roster(
        const std::string& session_id) const;

    // v3 threshold recovery: safe, read-only projection used by the recovery
    // RPC. Only a frozen session that has already accepted at least one masked
    // contribution is recoverable; before that, tensor shape is not yet known
    // and synthesizing a correction would be ambiguous.
    [[nodiscard]] std::optional<SecureAggregationRecoveryView> recovery_view(
        const std::string& session_id) const {
        std::lock_guard<std::mutex> lock(mutex_);
        const auto it = sessions_.find(session_id);
        if (it == sessions_.end()) {
            return std::nullopt;
        }
        const auto& record = it->second;
        if (!record.frozen || record.expected_tensor_element_counts.empty() ||
            record.contributions_by_worker.empty()) {
            return std::nullopt;
        }
        SecureAggregationRecoveryView view;
        view.config = record.config;
        view.frozen_roster = record.frozen_roster;
        view.tensor_element_counts = record.expected_tensor_element_counts;
        view.contributing_worker_ids.reserve(record.contributions_by_worker.size());
        for (const auto& [worker_id, contribution] : record.contributions_by_worker) {
            (void)contribution;
            view.contributing_worker_ids.push_back(worker_id);
        }
        return view;
    }

    [[nodiscard]] std::vector<std::string> sweep_expired_advertisement_deadlines(double now_unix_s);

    [[nodiscard]] std::vector<std::string> sweep_expired_masked_update_deadlines(double now_unix_s);

  private:
    struct SessionRecord {
        fl::coordinator::v1::SecureAggregationSessionConfig config;
        CohortStateMachine state_machine{"uninitialized"};
        std::map<std::string, fl::worker::v1::SecureAggregationKeyAdvertisement>
            advertisements_by_worker;
        fl::coordinator::v1::FrozenCohortRoster frozen_roster;
        bool frozen = false;
        std::map<std::string, fl::worker::v1::MaskedClientUpdate> contributions_by_worker;
        // Populated from the first accepted contribution's tensor names and
        // element counts. Every later contribution, including a coordinator-
        // generated recovery correction, must match this exact shape.
        std::map<std::string, std::size_t> expected_tensor_element_counts;
        double created_at_unix_s = 0.0;
        double completed_at_unix_s = 0.0;
        std::string aggregate_checksum;
    };

    mutable std::mutex mutex_;
    std::map<std::string, SessionRecord> sessions_;
    std::map<std::pair<std::string, std::uint64_t>, std::string> session_id_by_run_round_;
    SecureAggregationSessionStore* store_ = nullptr;

    [[nodiscard]] SessionRecord& require_session(const std::string& session_id);
    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus status_of(
        const SessionRecord& record) const;
    void persist_transition(const SessionRecord& record) const;
};

}  // namespace fl::coordinator
