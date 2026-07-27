#pragma once

// Real gRPC service adapter over fl_coordinator's RunManager/RunInstance
// domain layer. This header (and its .cpp) are only compiled when gRPC
// is actually found (see cpp/CMakeLists.txt's optional
// fl_coordinator_grpc_server target, gated on find_package(gRPC)) — this
// machine has no local C++ gRPC toolchain (see docs/coordinator-runtime.md
// for the full explanation), so this code has been written to the same
// standard as the rest of fl_coordinator but has only been build-checked
// in CI (ubuntu-latest, where `apt-get install libgrpc++-dev
// protobuf-compiler-grpc` provides everything needed), not locally.
//
// The class deliberately does nothing but translate between protobuf
// messages and RunManager/RunInstance calls — all actual domain logic
// (validation, idempotency, checkpointing, event ordering) lives in
// fl_coordinator and is unit-tested there (cpp/coordinator/tests/),
// independent of whether gRPC is available.

#include "coordinator/coordinator.grpc.pb.h"
#include "fl_coordinator/accountant_monotonicity_store.hpp"
#include "fl_coordinator/coordinator_signing_identity.hpp"
#include "fl_coordinator/coordinator_signing_key_registry.hpp"
#include "fl_coordinator/coordinator_task_sequence_store.hpp"
#include "fl_coordinator/idempotency_store.hpp"
#include "fl_coordinator/replay_protection_store.hpp"
#include "fl_coordinator/run_manager.hpp"
#include "fl_coordinator/secure_aggregation_session_manager.hpp"
#include "fl_coordinator/security_audit_journal.hpp"
#include "fl_coordinator/security_event_journal.hpp"
#include "fl_coordinator/signing_key_registry.hpp"
#include "fl_coordinator/transport_credentials.hpp"
#include "fl_coordinator/worker_identity_registry.hpp"

#include <chrono>
#include <mutex>
#include <set>

namespace fl::coordinator {

class CoordinatorServiceImpl final : public fl::coordinator::v1::CoordinatorService::Service {
  public:
    // identity_registry/replay_store are both optional (nullptr by
    // default) so every existing call site (coordinator_service_test.cpp,
    // and any code written before this slice) keeps compiling and
    // behaving exactly as before. main.cpp passes real, file-backed
    // instances of both for the live server. See coordinator_service.cpp's
    // Heartbeat for what a null replay_store means today (signed
    // heartbeats are still required and verified, but nonce/sequence
    // replay is not checked -- see docs/signed-worker-envelopes.md).
    //
    // allow_unsigned_client_results defaults to true (unlike Heartbeat,
    // SubmitClientResult had substantial existing test coverage
    // predating signed envelopes -- see coordinator_service_test.cpp --
    // so the *default* preserves that unsigned-legacy behavior for
    // tests and any other caller that doesn't opt in). main.cpp
    // explicitly passes false unless FL_ALLOW_UNSIGNED_CLIENT_RESULTS=true
    // is set, matching FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT's
    // identical fail-closed-by-default, explicit-opt-in-for-dev pattern
    // -- see docs/signed-client-results.md.
    // allow_unsigned_privacy_records mirrors allow_unsigned_client_results'
    // exact backward-compatibility reasoning: coordinator_service_test.cpp
    // has substantial existing coverage submitting sample_level_privacy
    // with no signed envelope at all -- see docs/signed-privacy-records.md.
    // monotonicity_store is optional (nullptr by default, like
    // identity_registry/replay_store) so accountant-step/epsilon
    // monotonicity is only enforced when main.cpp actually wires a real,
    // file-backed instance for the live server.
    // signing_key_registry mirrors the other stores' optional-by-default
    // convention. When non-null, it becomes the authoritative source for
    // whether a presented signing_key_id is ACTIVE/GRACE_PERIOD/REVOKED/
    // EXPIRED for a given worker (see docs/signing-key-management.md);
    // when null, every signed-message verification path falls back to
    // the pre-existing single-key comparison against
    // WorkerIdentityRegistry's own cached signing_key_id, preserving
    // every existing test's behavior unchanged.
    explicit CoordinatorServiceImpl(RunManager& manager,
                                    WorkerIdentityRegistry* identity_registry = nullptr,
                                    ReplayProtectionStore* replay_store = nullptr,
                                    bool allow_unsigned_client_results = true,
                                    AccountantMonotonicityStore* monotonicity_store = nullptr,
                                    bool allow_unsigned_privacy_records = true,
                                    SigningKeyRegistry* signing_key_registry = nullptr,
                                    // Coordinator-Signed Tasks slice
                                    // (docs/signed-coordinator-tasks.md): all
                                    // three optional and nullptr by default,
                                    // same backward-compatible convention as
                                    // every store above. AcquireTask only
                                    // attaches a SignedCoordinatorTask when
                                    // coordinator_signing_identity is non-null
                                    // AND coordinator_signing_key_registry has
                                    // an ACTIVE key -- every existing test and
                                    // call site that constructs this service
                                    // without these three keeps compiling and
                                    // behaving exactly as before.
                                    CoordinatorActiveIdentityStore* coordinator_active_identity = nullptr,
                                    CoordinatorSigningKeyRegistry* coordinator_signing_key_registry = nullptr,
                                    CoordinatorTaskSequenceStore* coordinator_task_sequence_store = nullptr,
                                    // Security Administration slice
                                    // (docs/coordinator-signing-key-rotation.md):
                                    // all four optional/empty by default,
                                    // same backward-compatible convention.
                                    // RotateCoordinatorSigningKey/
                                    // RevokeCoordinatorSigningKey both
                                    // return UNIMPLEMENTED when
                                    // idempotency_store is null (there is
                                    // nothing safe to mutate without it).
                                    IdempotencyStore* idempotency_store = nullptr,
                                    std::string coordinator_signing_key_dir = "",
                                    std::string trusted_key_bundle_path = "",
                                    std::string coordinator_identity_label = "coordinator",
                                    // Security Operations and Administration
                                    // slice (docs/security-api.md): the
                                    // coordinator's own configured transport
                                    // mode, reported read-only by
                                    // GetTransportSecurityStatus. Defaults to
                                    // kInsecureDevelopment so every existing
                                    // call site that does not pass this
                                    // keeps compiling and behaving exactly
                                    // as before -- main.cpp passes the real,
                                    // already-resolved TransportMode.
                                    TransportMode transport_mode = TransportMode::kInsecureDevelopment,
                                    // Security Events, Metrics, and Durable
                                    // Audit Journal slice
                                    // (docs/security-events.md,
                                    // docs/security-audit-journal.md): both
                                    // optional/nullptr by default, same
                                    // backward-compatible convention as
                                    // every store above. Wired at a
                                    // representative subset of the most
                                    // security-sensitive mutation paths
                                    // (worker lifecycle, worker/coordinator
                                    // signing-key revocation, coordinator
                                    // signing-key rotation, and the
                                    // ADMIN_CONTROL permission-denial path
                                    // guarding all of them) rather than
                                    // every RPC -- see
                                    // docs/security-observability-inventory.md
                                    // for exactly which operations this
                                    // covers today.
                                    SecurityEventJournal* security_event_journal = nullptr,
                                    SecurityAuditJournal* security_audit_journal = nullptr,
                                    // Secure Cohort Handshake and Signed
                                    // Roster Runtime slice
                                    // (docs/secure-cohort-handshake-foundation.md):
                                    // optional/nullptr/false by default,
                                    // same backward-compatible convention.
                                    // secure_aggregation_manager is null on
                                    // every existing call site and test --
                                    // AcquireTask simply skips all secure-
                                    // aggregation logic when it is null,
                                    // and the six secure-aggregation RPCs
                                    // all return FAILED_PRECONDITION (not
                                    // UNIMPLEMENTED -- the RPC itself is
                                    // real, only this particular server
                                    // instance lacks the manager) when
                                    // called without one. secure_aggregation_enabled
                                    // is main.cpp's coordinator-wide
                                    // FL_SECURE_AGGREGATION_ENABLED opt-in
                                    // (see that doc's "coordinator-wide,
                                    // not yet per-run" scope decision) --
                                    // gates only AcquireTask's automatic
                                    // session *creation*; the five other
                                    // secure-aggregation RPCs work
                                    // regardless of this flag as long as a
                                    // session already exists.
                                    SecureAggregationSessionManager* secure_aggregation_manager = nullptr,
                                    bool secure_aggregation_enabled = false,
                                    double secure_aggregation_key_advertisement_window_seconds = 300.0,
                                    double secure_aggregation_masked_update_window_seconds = 300.0);

    grpc::Status CreateRun(grpc::ServerContext* context,
                           const fl::coordinator::v1::CreateRunRequest* request,
                           fl::coordinator::v1::CreateRunResponse* response) override;

    grpc::Status StartRun(grpc::ServerContext* context,
                          const fl::coordinator::v1::StartRunRequest* request,
                          fl::coordinator::v1::RunStateResponse* response) override;

    grpc::Status PauseRun(grpc::ServerContext* context,
                          const fl::coordinator::v1::PauseRunRequest* request,
                          fl::coordinator::v1::RunStateResponse* response) override;

    grpc::Status ResumeRun(grpc::ServerContext* context,
                           const fl::coordinator::v1::ResumeRunRequest* request,
                           fl::coordinator::v1::RunStateResponse* response) override;

    grpc::Status CancelRun(grpc::ServerContext* context,
                           const fl::coordinator::v1::CancelRunRequest* request,
                           fl::coordinator::v1::RunStateResponse* response) override;

    grpc::Status GetRun(grpc::ServerContext* context,
                        const fl::coordinator::v1::GetRunRequest* request,
                        fl::coordinator::v1::RunDetails* response) override;

    // Signed Client Results and Worker Lifecycle Enforcement slice,
    // Work Package N: both declared in the proto with no prior C++
    // handler at all (confirmed by direct inspection, not assumed --
    // see docs/rpc-security-policy.md). Neither is required by the
    // current live worker/Go/web flow, so both are resolved as
    // explicit, documented UNIMPLEMENTED overrides rather than left to
    // fall through to gRPC's own generic default -- see
    // coordinator_service.cpp.
    grpc::Status GetRound(grpc::ServerContext* context,
                          const fl::coordinator::v1::GetRoundRequest* request,
                          fl::coordinator::v1::RoundDetails* response) override;

    grpc::Status GetModelManifest(grpc::ServerContext* context,
                                  const fl::coordinator::v1::GetModelManifestRequest* request,
                                  fl::coordinator::v1::ModelManifest* response) override;

    grpc::Status RegisterWorker(grpc::ServerContext* context,
                                const fl::worker::v1::RegisterWorkerRequest* request,
                                fl::worker::v1::RegisterWorkerResponse* response) override;

    grpc::Status Heartbeat(grpc::ServerContext* context,
                           const fl::worker::v1::WorkerHeartbeatRequest* request,
                           fl::worker::v1::WorkerHeartbeatResponse* response) override;

    // Privacy Engineering phase: lets the Go control plane see which
    // registered workers actually advertise sample-level-DP support —
    // see docs/worker-privacy-capabilities.md.
    grpc::Status ListWorkers(grpc::ServerContext* context,
                             const fl::coordinator::v1::ListWorkersRequest* request,
                             fl::coordinator::v1::ListWorkersResponse* response) override;

    grpc::Status AcquireTask(grpc::ServerContext* context,
                             const fl::coordinator::v1::AcquireTaskRequest* request,
                             fl::coordinator::v1::ClientTrainingTask* response) override;

    // Work Package N: previously declared with no C++ handler at all
    // (confirmed by inspection). Not called by the current live worker
    // flow (WorkerService.run() never calls it) -- implemented for real
    // here (delegating to the pre-existing RunInstance::report_task_progress
    // domain method) with certificate identity binding, matching
    // AcquireTask/SubmitClientResult's pattern, but without a signed-
    // envelope requirement (per the specification's "implement only
    // when required by the current live worker flow" -- envelope
    // signing would be scope creep for an RPC nothing sends yet).
    grpc::Status ReportTaskProgress(grpc::ServerContext* context,
                                    const fl::coordinator::v1::TaskProgressRequest* request,
                                    fl::coordinator::v1::TaskProgressResponse* response) override;

    grpc::Status SubmitClientResult(
        grpc::ServerContext* context,
        const fl::coordinator::v1::SubmitClientResultRequest* request,
        fl::coordinator::v1::SubmitClientResultResponse* response) override;

    grpc::Status GetPersonalizationSummary(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetPersonalizationSummaryRequest* request,
        fl::coordinator::v1::PersonalizationSummaryResponse* response) override;

    // Privacy Engineering phase: read-only projections of RunInstance's
    // privacy ledgers/accountants — see docs/privacy-ledger.md. All
    // three are safe to call on a non-private run (return an empty/all-
    // false response, not an error).
    grpc::Status GetPrivacyMetrics(grpc::ServerContext* context,
                                   const fl::coordinator::v1::GetPrivacyMetricsRequest* request,
                                   fl::privacy::v1::PrivacyMetricsSnapshot* response) override;

    grpc::Status GetPrivacyLedger(grpc::ServerContext* context,
                                  const fl::coordinator::v1::GetPrivacyLedgerRequest* request,
                                  fl::coordinator::v1::GetPrivacyLedgerResponse* response) override;

    grpc::Status GetPrivacyProjection(grpc::ServerContext* context,
                                      const fl::coordinator::v1::GetPrivacyProjectionRequest* request,
                                      fl::coordinator::v1::PrivacyProjection* response) override;

    grpc::Status StreamRunEvents(
        grpc::ServerContext* context,
        const fl::coordinator::v1::StreamRunEventsRequest* request,
        grpc::ServerWriter<fl::events::v1::CoordinatorEvent>* writer) override;

    grpc::Status Health(grpc::ServerContext* context,
                        const fl::coordinator::v1::HealthRequest* request,
                        fl::coordinator::v1::HealthResponse* response) override;

    // Signed Client Results and Worker Lifecycle Enforcement slice,
    // Work Package G: ADMIN_CONTROL RPCs. Require an authenticated
    // spiffe://federated-platform/service/go-api certificate identity
    // (has_service_identity) -- a worker certificate calling any of
    // these is rejected. Require identity_registry_ != nullptr (there
    // is nothing to administer without one). See
    // docs/worker-suspension.md / docs/worker-activation.md /
    // docs/worker-revocation.md.
    grpc::Status GetWorkerIdentity(grpc::ServerContext* context,
                                   const fl::coordinator::v1::GetWorkerIdentityRequest* request,
                                   fl::coordinator::v1::WorkerIdentitySummary* response) override;

    grpc::Status ListWorkerIdentities(
        grpc::ServerContext* context,
        const fl::coordinator::v1::ListWorkerIdentitiesRequest* request,
        fl::coordinator::v1::ListWorkerIdentitiesResponse* response) override;

    grpc::Status SuspendWorker(grpc::ServerContext* context,
                               const fl::coordinator::v1::SuspendWorkerRequest* request,
                               fl::coordinator::v1::WorkerLifecycleResponse* response) override;

    grpc::Status ActivateWorker(grpc::ServerContext* context,
                                const fl::coordinator::v1::ActivateWorkerRequest* request,
                                fl::coordinator::v1::WorkerLifecycleResponse* response) override;

    grpc::Status RevokeWorker(grpc::ServerContext* context,
                              const fl::coordinator::v1::RevokeWorkerRequest* request,
                              fl::coordinator::v1::WorkerLifecycleResponse* response) override;

    // Signing-Key Lifecycle slice (docs/signing-key-management.md).
    // RotateWorkerSigningKey is a SIGNED_WORKER_MESSAGE (real mTLS +
    // signed-envelope verification against the CURRENT key); the other
    // two are ADMIN_CONTROL, gated identically to the five RPCs above.
    grpc::Status RotateWorkerSigningKey(
        grpc::ServerContext* context,
        const fl::coordinator::v1::RotateWorkerSigningKeyRequest* request,
        fl::coordinator::v1::RotateWorkerSigningKeyResponse* response) override;

    grpc::Status GetWorkerSigningKeys(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetWorkerSigningKeysRequest* request,
        fl::coordinator::v1::GetWorkerSigningKeysResponse* response) override;

    grpc::Status RevokeWorkerSigningKey(
        grpc::ServerContext* context,
        const fl::coordinator::v1::RevokeWorkerSigningKeyRequest* request,
        fl::coordinator::v1::RevokeWorkerSigningKeyResponse* response) override;

    // Coordinator-Signed Tasks slice (docs/signed-coordinator-tasks.md).
    // ADMIN_CONTROL, gated identically to the RPCs above -- operational
    // visibility only; NOT how a worker bootstraps trust (see
    // CoordinatorSigningKeyRecordSummary's proto comment).
    grpc::Status GetCoordinatorSigningKeys(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetCoordinatorSigningKeysRequest* request,
        fl::coordinator::v1::GetCoordinatorSigningKeysResponse* response) override;

    // Security Administration slice (docs/coordinator-signing-key-rotation.md,
    // docs/coordinator-signing-key-revocation.md). ADMIN_CONTROL, gated
    // identically to the RPC above.
    grpc::Status RotateCoordinatorSigningKey(
        grpc::ServerContext* context,
        const fl::coordinator::v1::RotateCoordinatorSigningKeyRequest* request,
        fl::coordinator::v1::RotateCoordinatorSigningKeyResponse* response) override;

    grpc::Status RevokeCoordinatorSigningKey(
        grpc::ServerContext* context,
        const fl::coordinator::v1::RevokeCoordinatorSigningKeyRequest* request,
        fl::coordinator::v1::RevokeCoordinatorSigningKeyResponse* response) override;

    // Security Operations and Administration slice (docs/security-api.md).
    // ADMIN_CONTROL, gated identically to the RPCs above -- both
    // read-only, intended as the backing data for the Go/web security
    // dashboard's status panels.
    grpc::Status GetTransportSecurityStatus(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetTransportSecurityStatusRequest* request,
        fl::coordinator::v1::TransportSecurityStatusResponse* response) override;

    grpc::Status GetSecurityTrustModel(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetSecurityTrustModelRequest* request,
        fl::coordinator::v1::SecurityTrustModelResponse* response) override;

    // Security Events, Metrics, and Durable Audit Journal slice
    // (docs/security-events.md). ADMIN_CONTROL, gated identically to the
    // RPCs above. Returns UNIMPLEMENTED when security_event_journal_ is
    // null (no journal configured), matching
    // RotateCoordinatorSigningKey's precedent for an optional-store RPC.
    grpc::Status ListSecurityEvents(
        grpc::ServerContext* context,
        const fl::coordinator::v1::ListSecurityEventsRequest* request,
        fl::coordinator::v1::ListSecurityEventsResponse* response) override;

    // Web Security Center, Event Centralization, and Security CI slice
    // (docs/security-event-source-health.md). ADMIN_CONTROL, gated
    // identically to the RPCs above -- read-only, aggregate-only (never
    // an individual worker's queue depth -- see the proto comment).
    grpc::Status GetSecurityEventSourceHealth(
        grpc::ServerContext* context,
        const fl::coordinator::v1::GetSecurityEventSourceHealthRequest* request,
        fl::coordinator::v1::GetSecurityEventSourceHealthResponse* response) override;

    // Web Security Center, Event Centralization, and Security CI slice
    // (docs/security-event-centralization.md). SIGNED_WORKER_MESSAGE --
    // mTLS + a valid signed envelope from the worker's own current
    // ACTIVE/GRACE_PERIOD key, same pipeline as RotateWorkerSigningKey.
    // Accepted events are journaled into security_event_journal_ tagged
    // source_service="python-worker"; a whole-batch rejection never
    // journals any of the batch's individual events.
    grpc::Status SubmitWorkerSecurityEvents(
        grpc::ServerContext* context,
        const fl::coordinator::v1::SubmitWorkerSecurityEventsRequest* request,
        fl::coordinator::v1::SubmitWorkerSecurityEventsResponse* response) override;

    // Secure Aggregation Wire Protocol and Live No-Dropout Execution
    // slice: declared in the proto (real, generated bindings), each
    // resolved as an explicit, documented UNIMPLEMENTED override --
    // same precedent as GetRound/GetModelManifest above -- rather than
    // falling through to gRPC's generic default. See
    // docs/secure-aggregation-wire-protocol-foundation.md for why a
    // live handler is Tier 2, not this pass.
    grpc::Status AdvertiseSecureAggregationKey(
        grpc::ServerContext* context,
        const fl::coordinator::v1::AdvertiseSecureAggregationKeyRequest* request,
        fl::coordinator::v1::AdvertiseSecureAggregationKeyResponse* response) override;

    grpc::Status GetFrozenCohortRoster(
        grpc::ServerContext* context, const fl::coordinator::v1::GetFrozenCohortRosterRequest* request,
        fl::coordinator::v1::GetFrozenCohortRosterResponse* response) override;

    grpc::Status SubmitMaskedClientUpdate(
        grpc::ServerContext* context, const fl::coordinator::v1::SubmitMaskedClientUpdateRequest* request,
        fl::coordinator::v1::SubmitMaskedClientUpdateResponse* response) override;

    grpc::Status GetSecureAggregationSession(
        grpc::ServerContext* context, const fl::coordinator::v1::GetSecureAggregationSessionRequest* request,
        fl::coordinator::v1::GetSecureAggregationSessionResponse* response) override;

    grpc::Status ListSecureAggregationSessions(
        grpc::ServerContext* context, const fl::coordinator::v1::ListSecureAggregationSessionsRequest* request,
        fl::coordinator::v1::ListSecureAggregationSessionsResponse* response) override;

    grpc::Status AbortSecureAggregationSession(
        grpc::ServerContext* context, const fl::coordinator::v1::AbortSecureAggregationSessionRequest* request,
        fl::coordinator::v1::AbortSecureAggregationSessionResponse* response) override;

    // Secure User-Level DP Operations, Observability, and Release
    // Evidence slice (docs/secure-user-level-operations-audit.md).
    // ADMIN_CONTROL, gated identically to the RPCs above -- read-only,
    // sourced entirely from protobuf-free RunManager/RunInstance state
    // (see run_manager.hpp's user_level_ledger()/user_level_privacy()),
    // never from the masked/decoded aggregate itself.
    grpc::Status GetSecureUserLevelPrivacyHealth(
        grpc::ServerContext* context, const fl::coordinator::v1::GetSecureUserLevelPrivacyHealthRequest* request,
        fl::coordinator::v1::SecureUserLevelPrivacyHealthResponse* response) override;

    grpc::Status GetSecureUserLevelPrivacyBudget(
        grpc::ServerContext* context, const fl::coordinator::v1::GetSecureUserLevelPrivacyBudgetRequest* request,
        fl::coordinator::v1::SecureUserLevelPrivacyBudgetResponse* response) override;

    grpc::Status ListSecureUserLevelPrivacyRounds(
        grpc::ServerContext* context, const fl::coordinator::v1::ListSecureUserLevelPrivacyRoundsRequest* request,
        fl::coordinator::v1::ListSecureUserLevelPrivacyRoundsResponse* response) override;

    grpc::Status GetSecureUserLevelPrivacyRound(
        grpc::ServerContext* context, const fl::coordinator::v1::GetSecureUserLevelPrivacyRoundRequest* request,
        fl::coordinator::v1::GetSecureUserLevelPrivacyRoundResponse* response) override;

  private:
    // Incremented by SubmitWorkerSecurityEvents (Work Package K/L) and
    // read by GetSecurityEventSourceHealth -- in-memory only (an
    // observability aggregate, not a trust decision, so it does not
    // need to survive a coordinator restart the way the journals do).
    struct WorkerEventBatchStats {
        std::uint64_t batches_accepted = 0;
        std::uint64_t batches_rejected = 0;
        std::string last_accepted_at;
        std::set<std::string> distinct_worker_ids_seen;
    };
    mutable std::mutex batch_stats_mutex_;
    WorkerEventBatchStats batch_stats_;

  private:
    RunManager* manager_;
    WorkerIdentityRegistry* identity_registry_;
    ReplayProtectionStore* replay_store_;
    bool allow_unsigned_client_results_;
    AccountantMonotonicityStore* monotonicity_store_;
    bool allow_unsigned_privacy_records_;
    SigningKeyRegistry* signing_key_registry_;
    CoordinatorActiveIdentityStore* coordinator_active_identity_;
    CoordinatorSigningKeyRegistry* coordinator_signing_key_registry_;
    CoordinatorTaskSequenceStore* coordinator_task_sequence_store_;
    IdempotencyStore* idempotency_store_;
    std::string coordinator_signing_key_dir_;
    std::string trusted_key_bundle_path_;
    std::string coordinator_identity_label_;
    TransportMode transport_mode_;
    std::chrono::steady_clock::time_point started_at_;
    SecurityEventJournal* security_event_journal_;
    SecurityAuditJournal* security_audit_journal_;
    SecureAggregationSessionManager* secure_aggregation_manager_;
    bool secure_aggregation_enabled_;
    double secure_aggregation_key_advertisement_window_seconds_;
    double secure_aggregation_masked_update_window_seconds_;

    // Regenerates the trusted-key bundle file from the registry's
    // current state -- called after every rotate/revoke. Returns false
    // (with `reason` set) on write failure; callers must not treat the
    // mutation as complete until this succeeds -- see
    // docs/coordinator-signing-key-rotation.md's "Rollback on
    // bundle-generation failure" section for the honest limit of what
    // "rollback" actually means here.
    [[nodiscard]] bool regenerate_trusted_key_bundle(std::string& reason);

    [[nodiscard]] static double now_unix_s();
};

}  // namespace fl::coordinator
