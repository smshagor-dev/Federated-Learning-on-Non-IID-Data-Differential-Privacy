#pragma once

// Coordinator secure-aggregation session orchestration -- Secure
// Aggregation Wire Protocol and Live No-Dropout Execution slice, Work
// Package D. See docs/secure-aggregation-wire-protocol-foundation.md
// for the scope decision this class implements against.
//
// Unlike the task specification's suggested shape (an abstract
// interface with one implementation), this is a concrete class --
// matching this codebase's established convention for single-
// implementation persistence/state classes (see
// CoordinatorActiveIdentityStore's identical header-comment
// reasoning). Exactly one implementation is needed; an abstract base
// would only add indirection.
//
// Real orchestration over the prior slice's tested pure-math library
// (CohortStateMachine, fixed-point encoding, pairwise masking, crypto
// primitives, tensor masking) plus the real generated protobuf types
// from Work Package B -- gRPC-gated (needs those generated headers and
// secure_aggregation_crypto.cpp's OpenSSL-backed cohort-commitment
// hashing), same placement reasoning as every other gRPC-gated module
// in this directory.
//
// finalize() deliberately does NOT go through fl::core::AggregatorRegistry/
// Aggregator::aggregate (which takes a std::vector<ClientUpdate> --
// one *individual, cleartext* update per client): that would require
// materializing per-client decoded updates, which is exactly what
// secure aggregation exists to prevent the coordinator from ever
// seeing (see docs/secure-aggregation-threat-model.md's mandatory
// trust statement). Instead, finalize() computes the weighted average
// directly from the already-summed masked ring values --
// mathematically identical to what FedAvg's own weighting does
// internally, computed once over the aggregate instead of once per
// client update.
//
// This manager is NOT wired into CoordinatorServiceImpl this pass --
// see coordinator_service.cpp's six explicit UNIMPLEMENTED RPC
// overrides and docs/secure-aggregation-wire-protocol-foundation.md's
// Tier 2 scope. It is real, tested, callable, in-process orchestration
// logic, not yet reachable over a live gRPC connection.

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

// Real, meaningful validation and state transitions; a thread-safe,
// in-memory session registry. Safe metadata persistence (Secure Cohort
// Handshake and Signed Roster Runtime slice, Work item 2) is optional,
// injected -- when a SecureAggregationSessionStore* is provided, every
// state transition made by the six mutating methods below is recorded
// there before the call returns; contribution/key material itself is
// never persisted (only in-memory, matching Work Package Q's
// persistence prohibition list).
class SecureAggregationSessionManager {
  public:
    explicit SecureAggregationSessionManager(SecureAggregationSessionStore* store = nullptr);

    // Work Package E (partial): validates the config (participant
    // uniqueness, non-empty roster, domain-bounds-safe fixed-point
    // profile via prove_domain_bounds, cohort-size consistency,
    // provider must not be UNSPECIFIED/NONE), rejects a duplicate
    // session_id, and creates a fresh CohortStateMachine in
    // COHORT_FORMING. Throws SecureAggregationSessionManagerError on
    // any validation failure.
    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus create_session(
        const fl::coordinator::v1::SecureAggregationSessionConfig& config, double now_unix_s);

    // Work Package G (the validation subset expressible without a live
    // mTLS/signature context -- session/participant/deadline/public-key
    // checks; signature/replay/sequence verification is the not-yet-
    // written RPC handler's job, Tier 2). Auto-transitions
    // COHORT_FORMING -> KEY_ADVERTISEMENT on the first accepted
    // advertisement. Throws on: unknown session, non-participant
    // worker, duplicate advertisement, invalid/all-zero public key,
    // session/run/round/model-version mismatch, deadline exceeded, or
    // wrong session state.
    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus advertise_key(
        const fl::worker::v1::SecureAggregationKeyAdvertisement& advertisement, double now_unix_s);

    // Work Package I/J: requires every configured participant to have
    // advertised; builds and returns the frozen roster, including a
    // real cohort_commitment (compute_cohort_commitment,
    // secure_aggregation_crypto.cpp) and real profile hashes.
    // When `signing_identity` is provided (the live RPC handler always
    // provides one), the roster is really signed: real canonical bytes,
    // a real sha256_hex payload_hash, and a real Ed25519 signature via
    // sign_with_coordinator_identity. When null (unit tests that don't
    // need a real identity), coordinator_signing_key_id/signature stay
    // empty. Transitions KEY_ADVERTISEMENT -> COHORT_FROZEN. Throws if
    // the cohort is incomplete or the session is in the wrong state.
    [[nodiscard]] fl::coordinator::v1::FrozenCohortRoster freeze_cohort(
        const std::string& session_id,
        double now_unix_s,
        const CoordinatorSigningIdentity* signing_identity = nullptr);

    // Work Package O (the validation subset expressible without a live
    // mTLS/signature context): session/participant/duplicate checks,
    // per-tensor consistency against the first-received contribution's
    // shape (name set + element counts -- this manager has no
    // independently-sourced ModelManifest to validate against, a
    // documented simplification, see the .cpp), and real masked-tensor
    // checksum verification (sha256_hex, secure_aggregation_crypto.cpp).
    // Auto-transitions COHORT_FROZEN -> MASKED_UPDATE_COLLECTION on the
    // first accepted contribution. Callers (a real worker, or this
    // header's own test) are responsible for encoding each tensor
    // value already multiplied by that participant's client weight
    // *before* masking (Work Package M step 11) -- finalize() below
    // only sums and divides by the separately-summed weight; it has no
    // way to "undo" an unweighted contribution after the fact.
    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus submit_masked_update(
        const fl::worker::v1::MaskedClientUpdate& update, double now_unix_s);

    // Work Package T (the pure computation, no live-round/FedAvg-registry
    // integration -- Tier 2): requires exactly one contribution per
    // frozen participant (never a partial cohort -- this is the
    // concrete no-dropout enforcement point). Sums every participant's
    // masked tensors and masked weight in the ring (sum_masked_tensors/
    // sum_masked_values), decodes the result, rejects a zero or
    // negative decoded weight sum, and divides to produce the final
    // weighted-average delta -- see this header's own top comment for
    // why this bypasses fl::core::Aggregator entirely rather than
    // fabricating individual ClientUpdate objects. Transitions
    // MASKED_UPDATE_COLLECTION -> AGGREGATE_VALIDATION -> COMPLETED.
    //
    // Secure User-Level Differential Privacy Runtime slice, Work Areas
    // P/Q: `noise_provider`/`noise_std_dev` are optional (nullptr/0.0
    // by every existing caller that predates this slice -- NONE/
    // SAMPLE_LEVEL secure rounds never pass them, behavior for those
    // is byte-for-byte unchanged). When both are provided, one
    // independent Gaussian(0, noise_std_dev^2) draw is added to every
    // decoded element of every tensor -- in cleartext float space,
    // after decoding the complete-cohort ring sum but BEFORE the
    // divide-by-weight-sum step below, exactly matching
    // docs/secure-user-level-dp-semantics.md section 9's mandated
    // placement (noise on the sum, division afterward). The caller
    // (coordinator_service.cpp) is responsible for deciding whether
    // this round is a secure user-level-DP round and for computing
    // `noise_std_dev = noise_multiplier * effective_sensitivity` --
    // this function has no privacy-mode/accountant knowledge of its
    // own (this class stays a pure protocol/crypto orchestrator, see
    // the header's own top comment); it only ever adds noise when
    // explicitly told to. The unnoised aggregate is never returned,
    // logged, or persisted separately from this noised result -- Work
    // Area Q's "do not expose the unnoised aggregate" requirement is
    // satisfied structurally (no code path here ever constructs a
    // second, unnoised copy of `model_delta` that outlives this
    // function).
    // `expected_weight_sum` (0.0 = skip the check, the default for
    // every pre-existing caller): Work Area L's closest available
    // approximation of "fixed weight exactly one" enforcement. The
    // coordinator can never decode an *individual* masked weight
    // before the complete cohort has already been summed (that would
    // require exactly the per-participant visibility secure
    // aggregation exists to prevent -- see the Mandatory Privacy Trust
    // Statement in docs/secure-user-level-dp-semantics.md), so this is
    // a post-hoc integrity check, not a pre-submission guarantee: once
    // `decoded_weight_sum` is known, it is compared against the
    // caller-supplied expectation (the frozen cohort size, for a
    // fixed-weight-1 session) and the session is aborted
    // (kMaskCancellationFailed -- the same reason a non-positive
    // decoded weight sum already uses, since a mismatch here is the
    // same class of failure: either genuine tampering or a real
    // mask-cancellation bug) if they disagree beyond floating-point
    // tolerance. Catches an honest-but-buggy worker or a real protocol
    // defect; does not and cannot catch a worker that maliciously
    // reports a different weight while masking correctly -- disclosed,
    // not claimed otherwise.
    [[nodiscard]] fl::core::AggregationResult finalize(
        const std::string& session_id,
        double now_unix_s,
        fl::core::NoiseProvider* noise_provider = nullptr,
        double noise_std_dev = 0.0,
        double expected_weight_sum = 0.0);

    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: a read-only sibling to finalize(), called by the caller
    // BEFORE finalize() itself (while the session is still in
    // MASKED_UPDATE_COLLECTION with the complete cohort's contributions
    // present) -- see docs/secure-adaptive-clipping-semantics.md
    // section 17. Sums every contribution's masked_clipping_indicator
    // in the finite ring (pairwise masks cancel for a complete cohort,
    // exactly like the tensor/weight sums finalize() itself decodes),
    // then interprets the result directly as an unsigned count --
    // deliberately NOT run through decode_value()'s fixed-point/
    // scale-factor division, since the indicator was never fixed-point
    // encoded in the first place (a raw {0,1} value cast directly into
    // the ring, see the semantics doc section 14). Throws if the
    // session is unknown, not in MASKED_UPDATE_COLLECTION, the cohort
    // is incomplete, or the decoded count exceeds cohort_size (a
    // real mask-cancellation failure or tampering -- never silently
    // clamped). Never decodes or exposes an individual indicator; only
    // the final aggregate count is ever returned.
    [[nodiscard]] std::uint64_t decode_secure_adaptive_clipping_indicator_count(
        const std::string& session_id) const;

    // Work Package R/S/W: any non-terminal session can be aborted, for
    // any specific reason (never SecureAggregationAbortReason
    // UNSPECIFIED). Throws if the session is unknown or already
    // terminal.
    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus abort(
        const std::string& session_id,
        fl::coordinator::v1::SecureAggregationAbortReason reason,
        double now_unix_s);

    [[nodiscard]] std::optional<fl::coordinator::v1::SecureAggregationSessionStatus> find(
        const std::string& session_id) const;

    [[nodiscard]] std::vector<fl::coordinator::v1::SecureAggregationSessionSummary> list() const;

    // Work item 4/10: returns the live secure-aggregation task binding
    // for (run_id, round_id, worker_id) -- called from AcquireTask's
    // handler while building a task response. Only returns a value
    // when: a session exists for that exact (run_id, round_id), the
    // session is still accepting advertisements (COHORT_FORMING or
    // KEY_ADVERTISEMENT -- binding a task to an already-frozen/
    // completed/aborted session would be meaningless), and worker_id is
    // one of the session's configured participants. Never throws --
    // "no binding" is an ordinary, expected outcome for the vast
    // majority of tasks (secure aggregation disabled, or this worker
    // not selected for this round's cohort).
    [[nodiscard]] std::optional<fl::coordinator::v1::SecureAggregationTaskBinding>
    find_binding_for_participant(const std::string& run_id,
                                 std::uint64_t round_id,
                                 const std::string& worker_id) const;

    // Work item 3: true if a session (in any state, including already
    // frozen/completed/aborted) has ever been created for this exact
    // (run_id, round_id). AcquireTask's handler uses this to decide
    // whether it needs to create a fresh session for this round or one
    // already exists (successfully or not) -- a session is created at
    // most once per round, on the first AcquireTask call that round
    // sees.
    [[nodiscard]] bool has_session_for_run_round(const std::string& run_id,
                                                 std::uint64_t round_id) const;

    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area P: the same lookup as has_session_for_run_round,
    // but returning the full status (including abort_reason) so the
    // cleartext-prohibition check in SubmitClientResult can distinguish
    // "this round never had a secure session" and "this round's secure
    // session was aborted specifically because the run's privacy mode
    // was incompatible, so it deliberately falls back to ordinary
    // unmasked training" (both of which permit cleartext) from every
    // other case (which must reject cleartext -- a worker must never be
    // able to bypass masking by simply not submitting a masked update).
    [[nodiscard]] std::optional<fl::coordinator::v1::SecureAggregationSessionStatus>
    find_status_for_run_round(const std::string& run_id, std::uint64_t round_id) const;

    // Work item 11: returns the already-signed frozen roster for a
    // session, if one has been frozen. Used by the live
    // GetFrozenCohortRoster RPC handler.
    [[nodiscard]] std::optional<fl::coordinator::v1::FrozenCohortRoster> get_frozen_roster(
        const std::string& session_id) const;

    // Work item 15: scans every non-terminal session; any whose
    // key_advertisement_deadline_unix_s has passed while the cohort is
    // still incomplete (state COHORT_FORMING/KEY_ADVERTISEMENT) is
    // aborted (kDeadlineExceeded). Returns the aborted session_ids so
    // the caller (RunInstance::advance(), alongside the existing
    // sweep_expired_leases call) can emit one SecurityEvent per
    // session.
    [[nodiscard]] std::vector<std::string> sweep_expired_advertisement_deadlines(double now_unix_s);

    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area S: the masked-update-collection analogue of
    // sweep_expired_advertisement_deadlines above. Any session still in
    // kMaskedUpdateCollection whose masked_update_deadline_unix_s has
    // passed is aborted (kDeadlineExceeded) -- the Threshold Secret-
    // Sharing Restriction's required frozen-cohort failure behavior:
    // no partial sum is ever decoded, accepted contributions are
    // discarded (the session record itself is left in place for
    // audit/inspection but its contributions_by_worker map is no
    // longer reachable from any live path once terminal), and a retry
    // requires an entirely new session. Returns the aborted session_ids
    // so the caller (RunInstance::advance(), alongside the existing
    // sweep_expired_leases/sweep_expired_advertisement_deadlines calls)
    // can emit one SecurityEvent per session.
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
        // Populated from the first accepted contribution's tensor
        // names/element counts; every later contribution must match
        // exactly. A documented simplification (see submit_masked_update's
        // .cpp comment) standing in for real ModelManifest-shape
        // validation, which this manager has no independent source for
        // this pass.
        std::map<std::string, std::size_t> expected_tensor_element_counts;
        double created_at_unix_s = 0.0;
        double completed_at_unix_s = 0.0;
        std::string aggregate_checksum;
    };

    mutable std::mutex mutex_;
    std::map<std::string, SessionRecord> sessions_;
    // (run_id, round_id) -> session_id, populated in create_session(),
    // used by find_binding_for_participant() -- AcquireTask's handler
    // only knows run_id/round_id/worker_id, never session_id directly.
    std::map<std::pair<std::string, std::uint64_t>, std::string> session_id_by_run_round_;
    SecureAggregationSessionStore* store_ = nullptr;

    // Not locked internally -- callers (the public methods above) hold
    // mutex_ for the duration of the call already; a private helper
    // taking the lock again would deadlock (std::mutex is not
    // recursive by design, matching every other store in this
    // codebase's established convention of non-recursive locking).
    [[nodiscard]] SessionRecord& require_session(const std::string& session_id);
    [[nodiscard]] fl::coordinator::v1::SecureAggregationSessionStatus status_of(
        const SessionRecord& record) const;
    void persist_transition(const SessionRecord& record) const;
};

}  // namespace fl::coordinator
