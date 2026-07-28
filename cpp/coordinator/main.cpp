// Real gRPC coordinator server entry point. Only built when gRPC is
// found (see cpp/CMakeLists.txt's fl_coordinator_grpc_server target) —
// see docs/coordinator-runtime.md for why that's CI-only in this
// environment, and cpp/coordinator/tools/coordinator_cli.cpp for the
// local-development substitute that actually runs here.
#include "fl_coordinator/coordinator_service.hpp"
#include "fl_coordinator/coordinator_signing_identity.hpp"
#include "fl_coordinator/coordinator_signing_key_registry.hpp"
#include "fl_coordinator/coordinator_task_sequence_store.hpp"
#include "fl_coordinator/idempotency_store.hpp"
#include "fl_coordinator/secure_aggregation_session_manager.hpp"
#include "fl_coordinator/secure_aggregation_session_store.hpp"
#include "fl_coordinator/security_audit_journal.hpp"
#include "fl_coordinator/security_event_journal.hpp"
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "fl_coordinator/transport_credentials.hpp"
#include "fl_coordinator/trusted_key_bundle.hpp"

#include <chrono>
#include <cstdlib>
#include <iostream>
#include <memory>
#include <string>

#include <grpcpp/grpcpp.h>

namespace {
double now_unix_s() {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count()) /
           1000.0;
}
}  // namespace

int main(int argc, char** argv) {
    std::string bind_address = "0.0.0.0:50051";
    if (argc > 1) {
        bind_address = argv[1];
    }

    fl::coordinator::CoordinatorConfig config;
    fl::coordinator::RunManager manager(config, "checkpoints", "scaffold_state");

    // Coordinator Transport Verification and Message Authenticity
    // slice, Work Package E/F (docs/worker-identity-registry.md):
    // FL_WORKER_IDENTITY_REGISTRY_PATH lets deployments point this at a
    // persistent volume; the default is a sensible relative path for
    // local/container runs where the whole working directory is
    // already treated as the coordinator's persistent state (see
    // "checkpoints"/"scaffold_state" above, same convention). A
    // pre-existing, corrupt registry file fails the process at startup
    // (see WorkerIdentityRegistry's constructor) rather than silently
    // starting from an empty, no-longer-revoked-anyone registry.
    const char* identity_registry_path_env = std::getenv("FL_WORKER_IDENTITY_REGISTRY_PATH");
    const std::string identity_registry_path = identity_registry_path_env != nullptr
                                                   ? identity_registry_path_env
                                                   : "worker_identity_registry.dat";
    std::unique_ptr<fl::coordinator::WorkerIdentityRegistry> identity_registry;
    try {
        identity_registry =
            std::make_unique<fl::coordinator::WorkerIdentityRegistry>(identity_registry_path);
    } catch (const fl::coordinator::WorkerIdentityRegistryError& error) {
        std::cerr << "coordinator worker identity registry error: " << error.what() << "\n";
        return 1;
    }

    // Message Authenticity Enforcement and Identity Lifecycle slice,
    // Work Package E (docs/replay-protection.md): same
    // env-var-with-sensible-default convention as the identity registry
    // above.
    const char* replay_store_path_env = std::getenv("FL_REPLAY_PROTECTION_STORE_PATH");
    const std::string replay_store_path =
        replay_store_path_env != nullptr ? replay_store_path_env : "replay_protection_store.dat";
    std::unique_ptr<fl::coordinator::ReplayProtectionStore> replay_store;
    try {
        replay_store = std::make_unique<fl::coordinator::ReplayProtectionStore>(replay_store_path);
    } catch (const fl::coordinator::ReplayProtectionStoreError& error) {
        std::cerr << "coordinator replay protection store error: " << error.what() << "\n";
        return 1;
    }

    // Signed Client Results and Worker Lifecycle Enforcement slice,
    // Work Package C (docs/signed-client-results.md): fail-closed by
    // default (unsigned results rejected) unless explicitly opted into
    // -- same pattern as FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT. Unlike
    // that flag, this one is not additionally gated on transport mode
    // here (an operator setting this on an MTLS_REQUIRED deployment is
    // still a deliberate, logged choice -- the per-request WARNING log
    // in coordinator_service.cpp's SubmitClientResult is what actually
    // surfaces every time this path is exercised).
    const char* allow_unsigned_env = std::getenv("FL_ALLOW_UNSIGNED_CLIENT_RESULTS");
    const bool allow_unsigned_client_results =
        allow_unsigned_env != nullptr && std::string(allow_unsigned_env) == "true";

    // Privacy Record Authenticity, Signing-Key Lifecycle, and
    // Coordinator-Signed Tasks slice, Work Package F
    // (docs/privacy-accountant-monotonicity.md): same env-var-with-
    // sensible-default persistence convention as identity_registry/
    // replay_store above.
    const char* monotonicity_store_path_env = std::getenv("FL_ACCOUNTANT_MONOTONICITY_STORE_PATH");
    const std::string monotonicity_store_path = monotonicity_store_path_env != nullptr
                                                    ? monotonicity_store_path_env
                                                    : "accountant_monotonicity_store.dat";
    std::unique_ptr<fl::coordinator::AccountantMonotonicityStore> monotonicity_store;
    try {
        monotonicity_store =
            std::make_unique<fl::coordinator::AccountantMonotonicityStore>(monotonicity_store_path);
    } catch (const fl::coordinator::AccountantMonotonicityStoreError& error) {
        std::cerr << "coordinator accountant monotonicity store error: " << error.what() << "\n";
        return 1;
    }

    // Same fail-closed-by-default, explicit-opt-in-for-dev pattern as
    // FL_ALLOW_UNSIGNED_CLIENT_RESULTS above -- see
    // docs/signed-privacy-records.md.
    const char* allow_unsigned_privacy_env = std::getenv("FL_ALLOW_UNSIGNED_PRIVACY_RECORDS");
    const bool allow_unsigned_privacy_records =
        allow_unsigned_privacy_env != nullptr && std::string(allow_unsigned_privacy_env) == "true";

    // Signing-Key Lifecycle slice, Work Package B/C
    // (docs/signing-key-management.md, docs/signing-key-migration.md):
    // same env-var-with-sensible-default persistence convention as the
    // other stores above.
    const char* signing_key_registry_path_env = std::getenv("FL_SIGNING_KEY_REGISTRY_PATH");
    const std::string signing_key_registry_path = signing_key_registry_path_env != nullptr
                                                      ? signing_key_registry_path_env
                                                      : "signing_key_registry.dat";
    std::unique_ptr<fl::coordinator::SigningKeyRegistry> signing_key_registry;
    try {
        signing_key_registry =
            std::make_unique<fl::coordinator::SigningKeyRegistry>(signing_key_registry_path);
    } catch (const fl::coordinator::SigningKeyRegistryError& error) {
        std::cerr << "coordinator signing-key registry error: " << error.what() << "\n";
        return 1;
    }

    // Legacy identity migration (Work Package C,
    // docs/signing-key-migration.md): every existing
    // WorkerIdentityRegistry record with a non-empty signing key that
    // has no corresponding SigningKeyRegistry entry yet is migrated as
    // that worker's initial ACTIVE key, preserving the exact same
    // key_id and public-key bytes. Idempotent -- a worker already
    // migrated (or freshly registered through the registry-aware
    // RegisterWorker path) is skipped on every subsequent restart, so
    // this loop is safe to run unconditionally on every startup rather
    // than needing a separate "has migration run" flag file.
    for (const auto& record : identity_registry->list()) {
        if (record.signing_public_key.empty() || record.signing_key_id.empty()) {
            continue;  // never registered a signing key at all -- nothing to migrate
        }
        if (signing_key_registry->find(record.worker_id, record.signing_key_id, now_unix_s())
                .has_value()) {
            continue;  // already migrated, or already registered fresh -- idempotent
        }
        fl::coordinator::InitialSigningKeyRegistration migration;
        migration.worker_id = record.worker_id;
        migration.signing_key_id = record.signing_key_id;
        migration.public_key_hex = record.signing_public_key;
        migration.public_key_fingerprint =
            fl::coordinator::public_key_fingerprint_hex(record.signing_public_key);
        migration.now_unix_s = record.created_at_unix_s;
        migration.expires_at_unix_s = record.expires_at_unix_s;
        migration.registration_source = "migration";
        try {
            signing_key_registry->register_initial_key(migration);
            std::cerr << "timestamp_unix_s=" << now_unix_s()
                      << " service=coordinator event=SIGNING_KEY_MIGRATED worker_id="
                      << record.worker_id << " signing_key_id=" << record.signing_key_id
                      << std::endl;
        } catch (const fl::coordinator::SigningKeyRegistryError& error) {
            std::cerr << "coordinator signing-key migration error for worker_id='"
                      << record.worker_id << "': " << error.what() << "\n";
            return 1;
        }
    }

    // Coordinator-Signed Tasks slice, Work Packages B/C
    // (docs/coordinator-signing-identity.md,
    // docs/coordinator-signing-key-management.md): a persistent Ed25519
    // signing identity, deliberately a *different* file from the TLS
    // server key configured below (FL_COORDINATOR_SERVER_KEY) -- see
    // coordinator_signing_identity.hpp's header comment. Always active
    // for the live server (not opt-in): every production task AcquireTask
    // returns is signed, matching the Primary Objective's "sign every
    // production task returned by AcquireTask." A pre-existing,
    // malformed identity file fails the process at startup, same
    // fail-closed convention as every other persistence class above.
    const char* signing_identity_path_env = std::getenv("FL_COORDINATOR_SIGNING_KEY_PATH");
    const std::string signing_identity_path = signing_identity_path_env != nullptr
                                                  ? signing_identity_path_env
                                                  : "coordinator_signing_key.pem";
    fl::coordinator::CoordinatorSigningIdentity genesis_identity;
    try {
        genesis_identity =
            fl::coordinator::load_or_create_coordinator_signing_identity(signing_identity_path);
    } catch (const fl::coordinator::CoordinatorSigningIdentityError& error) {
        std::cerr << "coordinator signing-identity error: " << error.what() << "\n";
        return 1;
    }

    const char* coordinator_signing_key_registry_path_env =
        std::getenv("FL_COORDINATOR_SIGNING_KEY_REGISTRY_PATH");
    const std::string coordinator_signing_key_registry_path =
        coordinator_signing_key_registry_path_env != nullptr
            ? coordinator_signing_key_registry_path_env
            : "coordinator_signing_key_registry.dat";
    std::unique_ptr<fl::coordinator::CoordinatorSigningKeyRegistry>
        coordinator_signing_key_registry;
    try {
        coordinator_signing_key_registry =
            std::make_unique<fl::coordinator::CoordinatorSigningKeyRegistry>(
                coordinator_signing_key_registry_path);
    } catch (const fl::coordinator::CoordinatorSigningKeyRegistryError& error) {
        std::cerr << "coordinator signing-key registry error: " << error.what() << "\n";
        return 1;
    }
    // Idempotent -- a coordinator identity already registered (every
    // restart after the first) is a no-op refresh, matching every other
    // "register on startup" loop in this file.
    {
        fl::coordinator::InitialCoordinatorSigningKeyRegistration registration;
        registration.signing_key_id = genesis_identity.key_id;
        registration.public_key_hex = genesis_identity.public_key_hex;
        registration.public_key_fingerprint =
            fl::coordinator::public_key_fingerprint_hex(genesis_identity.public_key_hex);
        registration.now_unix_s = now_unix_s();
        registration.expires_at_unix_s = 0.0;  // never expires by default
        try {
            coordinator_signing_key_registry->register_initial_key(registration);
        } catch (const fl::coordinator::CoordinatorSigningKeyRegistryError& error) {
            std::cerr << "coordinator signing-key registration error: " << error.what() << "\n";
            return 1;
        }
    }

    // Security Administration slice, Work Package A/B
    // (docs/coordinator-signing-key-rotation.md): a rotation performed
    // on a prior run may have made a *different* key ACTIVE. The
    // genesis identity above always loads from the single fixed path
    // (unchanged behavior for a coordinator that has never rotated);
    // if the registry's current ACTIVE key is not the genesis key, the
    // real active identity must be loaded from the keyed directory a
    // prior rotation wrote it to instead.
    const char* signing_key_dir_env = std::getenv("FL_COORDINATOR_SIGNING_KEY_DIR");
    const std::string coordinator_signing_key_dir =
        signing_key_dir_env != nullptr ? signing_key_dir_env : "coordinator_signing_keys";
    fl::coordinator::CoordinatorSigningIdentity active_identity = genesis_identity;
    {
        const auto active_record = coordinator_signing_key_registry->active_key(now_unix_s());
        if (active_record.has_value() && active_record->signing_key_id != genesis_identity.key_id) {
            try {
                active_identity = fl::coordinator::load_keyed_coordinator_signing_identity(
                    active_record->signing_key_id, coordinator_signing_key_dir);
            } catch (const fl::coordinator::CoordinatorSigningIdentityError& error) {
                std::cerr << "coordinator active signing-key load error: " << error.what() << "\n";
                return 1;
            }
        }
    }
    auto coordinator_active_identity =
        std::make_unique<fl::coordinator::CoordinatorActiveIdentityStore>(active_identity);

    const char* idempotency_store_path_env = std::getenv("FL_COORDINATOR_IDEMPOTENCY_STORE_PATH");
    const std::string idempotency_store_path = idempotency_store_path_env != nullptr
                                                   ? idempotency_store_path_env
                                                   : "coordinator_idempotency_store.dat";
    std::unique_ptr<fl::coordinator::IdempotencyStore> idempotency_store;
    try {
        idempotency_store =
            std::make_unique<fl::coordinator::IdempotencyStore>(idempotency_store_path);
    } catch (const fl::coordinator::IdempotencyStoreError& error) {
        std::cerr << "coordinator idempotency store error: " << error.what() << "\n";
        return 1;
    }

    const char* coordinator_task_sequence_store_path_env =
        std::getenv("FL_COORDINATOR_TASK_SEQUENCE_STORE_PATH");
    const std::string coordinator_task_sequence_store_path =
        coordinator_task_sequence_store_path_env != nullptr
            ? coordinator_task_sequence_store_path_env
            : "coordinator_task_sequence_store.dat";
    std::unique_ptr<fl::coordinator::CoordinatorTaskSequenceStore> coordinator_task_sequence_store;
    try {
        coordinator_task_sequence_store =
            std::make_unique<fl::coordinator::CoordinatorTaskSequenceStore>(
                coordinator_task_sequence_store_path);
    } catch (const fl::coordinator::CoordinatorTaskSequenceStoreError& error) {
        std::cerr << "coordinator task sequence store error: " << error.what() << "\n";
        return 1;
    }

    // Trusted coordinator key bundle (docs/trusted-coordinator-key-bundle.md):
    // written to a path workers mount/read directly, out of band from
    // any gRPC connection -- a worker must never bootstrap trust in a
    // coordinator key from the very task whose authenticity is in
    // question. Rewritten (a real, versioned, checksummed, atomic
    // write -- see trusted_key_bundle.hpp) on every startup and after
    // every rotation/revocation so it always reflects the registry's
    // current ACTIVE/GRACE_PERIOD key set.
    const char* signing_key_bundle_path_env = std::getenv("FL_COORDINATOR_SIGNING_KEY_BUNDLE_PATH");
    const std::string trusted_key_bundle_path =
        signing_key_bundle_path_env != nullptr ? signing_key_bundle_path_env : "";
    const char* coordinator_identity_label_env = std::getenv("FL_COORDINATOR_IDENTITY_LABEL");
    const std::string coordinator_identity_label =
        coordinator_identity_label_env != nullptr ? coordinator_identity_label_env : "coordinator";
    if (!trusted_key_bundle_path.empty()) {
        const auto bundle_result =
            fl::coordinator::write_trusted_key_bundle(*coordinator_signing_key_registry,
                                                      trusted_key_bundle_path,
                                                      coordinator_identity_label,
                                                      now_unix_s());
        if (!bundle_result.ok) {
            std::cerr << "failed to write coordinator trusted-key bundle: " << bundle_result.reason
                      << "\n";
            return 1;
        }
    }

    // Secure Transport and Worker Identity Hardening slice
    // (docs/mtls.md): transport mode and certificate paths come from
    // the environment (FL_TRANSPORT_MODE, FL_COORDINATOR_SERVER_CERT/
    // _KEY, FL_COORDINATOR_CLIENT_CA, FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT)
    // — mirrors the Go and Python worker sides of this same
    // environment-variable contract. A misconfigured or explicitly
    // insecure-without-opt-in environment fails the process at startup
    // (structured error to stderr, non-zero exit) rather than silently
    // falling back to insecure credentials. Resolved before constructing
    // CoordinatorServiceImpl below because the service itself now
    // reports this same transport_mode via GetTransportSecurityStatus
    // (Security Operations and Administration slice).
    std::shared_ptr<grpc::ServerCredentials> credentials;
    fl::coordinator::TransportMode transport_mode;
    try {
        const auto transport_config = fl::coordinator::transport_config_from_environment();
        transport_mode = transport_config.mode;
        credentials = fl::coordinator::build_server_credentials(transport_config);
    } catch (const fl::coordinator::TransportConfigurationError& error) {
        std::cerr << "coordinator transport configuration error: " << error.what() << "\n";
        return 1;
    }

    // Security Events, Metrics, and Durable Audit Journal slice
    // (docs/security-events.md, docs/security-audit-journal.md): same
    // env-var-with-sensible-default persistence convention as every store
    // above. Unlike those stores, a corrupt/unreadable journal does not
    // fail coordinator startup (see security_event_journal.hpp's
    // corruption-recovery policy) -- only a filesystem-level failure
    // (can't open the path at all) does.
    const char* security_event_journal_path_env = std::getenv("FL_SECURITY_EVENT_JOURNAL_PATH");
    const std::string security_event_journal_path = security_event_journal_path_env != nullptr
                                                        ? security_event_journal_path_env
                                                        : "security_events.jsonl";
    std::unique_ptr<fl::coordinator::SecurityEventJournal> security_event_journal;
    try {
        security_event_journal =
            std::make_unique<fl::coordinator::SecurityEventJournal>(security_event_journal_path);
    } catch (const fl::coordinator::SecurityEventJournalError& error) {
        std::cerr << "coordinator security event journal error: " << error.what() << "\n";
        return 1;
    }

    const char* security_audit_journal_path_env = std::getenv("FL_SECURITY_AUDIT_JOURNAL_PATH");
    const std::string security_audit_journal_path = security_audit_journal_path_env != nullptr
                                                        ? security_audit_journal_path_env
                                                        : "security_audit.jsonl";
    std::unique_ptr<fl::coordinator::SecurityAuditJournal> security_audit_journal;
    try {
        security_audit_journal =
            std::make_unique<fl::coordinator::SecurityAuditJournal>(security_audit_journal_path);
    } catch (const fl::coordinator::SecurityAuditJournalError& error) {
        std::cerr << "coordinator security audit journal error: " << error.what() << "\n";
        return 1;
    }

    {
        fl::coordinator::SecurityEvent startup_event;
        startup_event.source_service = "coordinator";
        startup_event.source_component = "main";
        startup_event.event_type =
            transport_mode == fl::coordinator::TransportMode::kInsecureDevelopment
                ? fl::coordinator::SecurityEventType::kTransportInsecureDevelopmentStarted
                : fl::coordinator::SecurityEventType::kTransportMtlsStarted;
        startup_event.severity = fl::coordinator::default_severity(startup_event.event_type);
        startup_event.actor_type = fl::coordinator::SecurityActorType::kSystem;
        startup_event.subject_type = fl::coordinator::SecuritySubjectType::kTransport;
        startup_event.outcome = fl::coordinator::SecurityOutcome::kCompleted;
        startup_event.reason_code = fl::coordinator::to_string(transport_mode);
        security_event_journal->emit(std::move(startup_event));
    }

    // Secure Cohort Handshake and Signed Roster Runtime slice
    // (docs/secure-cohort-handshake-foundation.md). The session store
    // is optional/file-backed (same fail-closed-on-corruption
    // convention as every other store above); the manager itself is
    // in-memory only (no path -- see its own header comment) and is
    // always constructed, but only actually reachable by the RPC
    // handlers when FL_SECURE_AGGREGATION_ENABLED opts a run's session
    // *auto-creation* in (AdvertiseSecureAggregationKey/GetFrozenCohortRoster/
    // etc. all work regardless of this flag once a session already
    // exists -- see coordinator_service.hpp's constructor comment).
    const char* secure_aggregation_session_store_path_env =
        std::getenv("FL_SECURE_AGGREGATION_SESSION_STORE_PATH");
    const std::string secure_aggregation_session_store_path =
        secure_aggregation_session_store_path_env != nullptr
            ? secure_aggregation_session_store_path_env
            : "secure_aggregation_sessions.dat";
    std::unique_ptr<fl::coordinator::SecureAggregationSessionStore>
        secure_aggregation_session_store;
    try {
        secure_aggregation_session_store =
            std::make_unique<fl::coordinator::SecureAggregationSessionStore>(
                secure_aggregation_session_store_path);
    } catch (const fl::coordinator::SecureAggregationSessionStoreError& error) {
        std::cerr << "coordinator secure aggregation session store error: " << error.what() << "\n";
        return 1;
    }

    // Work item 16 (restart abort): reconcile any session left
    // non-terminal by a prior process's unclean exit -- there is
    // nothing live to abort against (the in-memory manager below
    // starts empty), so this is a log-level reconciliation of the
    // persisted record itself, done once, before any RPC can reach the
    // manager.
    for (const auto& reconciled_session_id :
         secure_aggregation_session_store->reconcile_after_restart(now_unix_s())) {
        fl::coordinator::SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "main";
        event.event_type = fl::coordinator::SecurityEventType::kSecureAggregationRestartAborted;
        event.severity = fl::coordinator::default_severity(event.event_type);
        event.actor_type = fl::coordinator::SecurityActorType::kSystem;
        event.subject_type = fl::coordinator::SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = reconciled_session_id;
        event.outcome = fl::coordinator::SecurityOutcome::kCompleted;
        event.reason_code = "coordinator_restart";
        security_event_journal->emit(std::move(event));
    }

    fl::coordinator::SecureAggregationSessionManager secure_aggregation_manager(
        secure_aggregation_session_store.get());

    const bool secure_aggregation_enabled = [] {
        const char* value = std::getenv("FL_SECURE_AGGREGATION_ENABLED");
        return value != nullptr && std::string(value) == "true";
    }();
    const double secure_aggregation_key_advertisement_window_seconds = [] {
        const char* value = std::getenv("FL_SECURE_AGGREGATION_KEY_ADVERTISEMENT_WINDOW_SECONDS");
        return value != nullptr ? std::stod(value) : 300.0;
    }();
    const double secure_aggregation_masked_update_window_seconds = [] {
        const char* value = std::getenv("FL_SECURE_AGGREGATION_MASKED_UPDATE_WINDOW_SECONDS");
        return value != nullptr ? std::stod(value) : 300.0;
    }();

    fl::coordinator::CoordinatorServiceImpl service(
        manager,
        identity_registry.get(),
        replay_store.get(),
        allow_unsigned_client_results,
        monotonicity_store.get(),
        allow_unsigned_privacy_records,
        signing_key_registry.get(),
        coordinator_active_identity.get(),
        coordinator_signing_key_registry.get(),
        coordinator_task_sequence_store.get(),
        idempotency_store.get(),
        coordinator_signing_key_dir,
        trusted_key_bundle_path,
        coordinator_identity_label,
        transport_mode,
        security_event_journal.get(),
        security_audit_journal.get(),
        &secure_aggregation_manager,
        secure_aggregation_enabled,
        secure_aggregation_key_advertisement_window_seconds,
        secure_aggregation_masked_update_window_seconds);

    grpc::ServerBuilder builder;
    builder.AddListeningPort(bind_address, credentials);
    builder.RegisterService(&service);
    builder.SetMaxReceiveMessageSize(64 * 1024 * 1024);
    builder.SetMaxSendMessageSize(64 * 1024 * 1024);

    const auto server = builder.BuildAndStart();
    if (!server) {
        std::cerr << "failed to start coordinator gRPC server on " << bind_address << "\n";
        return 1;
    }
    // std::cout is fully buffered when stdout isn't a TTY (true for
    // `docker logs` and CI) — without an explicit flush, this line (and
    // therefore the container's only startup confirmation) never actually
    // reaches the log until the buffer fills or the process exits, making
    // a healthy server look silent/hung. See structured_log.hpp's comment
    // for why per-event logging uses std::cerr instead.
    std::cout << "fl coordinator gRPC server listening on " << bind_address
              << " (transport_mode=" << fl::coordinator::to_string(transport_mode) << ")"
              << std::endl;
    server->Wait();
    return 0;
}
