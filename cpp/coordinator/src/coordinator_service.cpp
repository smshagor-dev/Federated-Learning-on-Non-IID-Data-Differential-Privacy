#include "fl_coordinator/coordinator_service.hpp"

#include "fl_coordinator/capability_statement_verifier.hpp"
#include "fl_coordinator/coordinator_task_signing.hpp"
#include "fl_coordinator/peer_identity.hpp"
#include "fl_coordinator/secure_aggregation_encoding.hpp"
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "fl_coordinator/trusted_key_bundle.hpp"
#include "fl_core/secure_random.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <thread>
#include <vector>

namespace fl::coordinator {

namespace {

// Duplicated from run_manager.cpp's identical (anonymous-namespace,
// therefore non-exported) helper -- matching this codebase's
// established convention of each file keeping its own small formatting
// helpers rather than a shared utility header.
std::string iso8601_from_unix_seconds(double unix_seconds) {
    const auto seconds = static_cast<std::time_t>(unix_seconds);
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &seconds);
#else
    gmtime_r(&seconds, &tm_utc);
#endif
    std::ostringstream out;
    out << std::put_time(&tm_utc, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

// A real, OS-CSPRNG-sourced nonce for an outgoing signed coordinator
// task -- the coordinator-signing counterpart to every worker-side
// nonce this codebase already generates (see docs/replay-protection.md).
// Never a counter or a timestamp: a random, unguessable value is what
// makes worker-side duplicate-nonce detection meaningful.
std::string generate_task_nonce() {
    fl::core::OsEntropySecureRandomProvider provider;
    unsigned char bytes[16];
    provider.fill_random_bytes(bytes, sizeof(bytes));
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(sizeof(bytes) * 2);
    for (unsigned char byte : bytes) {
        out += kHex[(byte >> 4) & 0xF];
        out += kHex[byte & 0xF];
    }
    return out;
}

fl::core::AggregationAlgorithm algorithm_from_wire(const std::string& value) {
    if (value == "fedavg")
        return fl::core::AggregationAlgorithm::kFedAvg;
    if (value == "fedprox")
        return fl::core::AggregationAlgorithm::kFedProx;
    if (value == "scaffold")
        return fl::core::AggregationAlgorithm::kScaffold;
    if (value == "fedadagrad")
        return fl::core::AggregationAlgorithm::kFedAdagrad;
    if (value == "fedadam")
        return fl::core::AggregationAlgorithm::kFedAdam;
    if (value == "fedyogi")
        return fl::core::AggregationAlgorithm::kFedYogi;
    if (value == "fedsam")
        return fl::core::AggregationAlgorithm::kFedSam;
    if (value == "ditto")
        return fl::core::AggregationAlgorithm::kDitto;
    if (value == "per_fedavg")
        return fl::core::AggregationAlgorithm::kPerFedAvg;
    // the Algorithm Expansion phase: do not silently accept unknown algorithm fields — a
    // typo/unsupported value must be rejected, not quietly treated as
    // FedAvg (which this fell back to before this phase).
    throw std::invalid_argument("unknown algorithm: " + value);
}

fl::core::WeightingStrategyType weighting_from_wire(const std::string& value) {
    // Privacy Engineering phase: an empty value (proto3 default for an
    // unset string field) means "caller didn't specify a weighting
    // strategy," which legitimately defaults to uniform — but any other
    // unrecognized value must be rejected, not silently coerced to
    // uniform (the previous behavior here), matching
    // algorithm_from_wire's already-strict handling above.
    if (value.empty() || value == "uniform")
        return fl::core::WeightingStrategyType::kUniform;
    if (value == "sample_count")
        return fl::core::WeightingStrategyType::kSampleCount;
    if (value == "capped_sample_count")
        return fl::core::WeightingStrategyType::kCappedSampleCount;
    if (value == "normalized_bounded")
        return fl::core::WeightingStrategyType::kNormalizedBounded;
    throw std::invalid_argument("unknown weighting strategy: " + value);
}

fl::core::PrivacyMode privacy_mode_from_wire(fl::privacy::v1::PrivacyMode value) {
    // Privacy Engineering phase: an absent privacy_config field on
    // CreateRunRequest decodes to PRIVACY_MODE_UNSPECIFIED (proto3 enum
    // default) — that and the explicit PRIVACY_MODE_NONE both mean "this
    // run is not private," matching pre-privacy-config behavior exactly.
    switch (value) {
        case fl::privacy::v1::PRIVACY_MODE_UNSPECIFIED:
        case fl::privacy::v1::PRIVACY_MODE_NONE:
            return fl::core::PrivacyMode::kNone;
        case fl::privacy::v1::PRIVACY_MODE_SAMPLE_LEVEL_DP:
            return fl::core::PrivacyMode::kSampleLevelDp;
        case fl::privacy::v1::PRIVACY_MODE_USER_LEVEL_DP:
            return fl::core::PrivacyMode::kUserLevelDp;
        case fl::privacy::v1::PRIVACY_MODE_HYBRID_DP:
            return fl::core::PrivacyMode::kHybridDp;
        default:
            throw std::invalid_argument("unknown privacy mode: " + std::to_string(value));
    }
}

// SampleLevelDPConfig/SampleLevelLedgerEntry's accountant is a plain
// domain-level string (see fl_core/privacy.hpp's comment on why: fl_core
// has no opinion on accountant names, only fl_platform.privacy.accounting
// does) — these two helpers are the sole place that string is translated
// to/from the wire AccountantType enum, mirroring Python's
// coordinator_client.py _accountant_type_to_wire/_from_wire dicts so all
// three languages agree on the mapping.
std::string accountant_type_from_wire(fl::privacy::v1::AccountantType value) {
    switch (value) {
        case fl::privacy::v1::ACCOUNTANT_TYPE_UNSPECIFIED:
        case fl::privacy::v1::ACCOUNTANT_TYPE_RDP:
            return "rdp";
        case fl::privacy::v1::ACCOUNTANT_TYPE_PRV:
            return "prv";
        case fl::privacy::v1::ACCOUNTANT_TYPE_GDP:
            return "gdp";
        default:
            throw std::invalid_argument("unknown accountant type: " + std::to_string(value));
    }
}

fl::privacy::v1::AccountantType accountant_type_to_wire(const std::string& value) {
    if (value == "rdp")
        return fl::privacy::v1::ACCOUNTANT_TYPE_RDP;
    if (value == "prv")
        return fl::privacy::v1::ACCOUNTANT_TYPE_PRV;
    if (value == "gdp")
        return fl::privacy::v1::ACCOUNTANT_TYPE_GDP;
    throw std::invalid_argument("unknown accountant: " + value);
}

// Privacy Engineering phase: unset (UNSPECIFIED) maps to kWarnOnly, the
// safe default (see fl_core/privacy.hpp's PrivacyBudgetPolicy doc
// comment) — a caller who configures an epsilon_budget without
// explicitly picking a policy gets visibility, not a surprise stoppage.
fl::core::PrivacyBudgetPolicy privacy_budget_policy_from_wire(
    fl::privacy::v1::PrivacyBudgetPolicy value) {
    switch (value) {
        case fl::privacy::v1::PRIVACY_BUDGET_POLICY_STOP_BEFORE_EXCEEDING:
            return fl::core::PrivacyBudgetPolicy::kStopBeforeExceeding;
        case fl::privacy::v1::PRIVACY_BUDGET_POLICY_STOP_AFTER_CURRENT_ROUND:
            return fl::core::PrivacyBudgetPolicy::kStopAfterCurrentRound;
        case fl::privacy::v1::PRIVACY_BUDGET_POLICY_FAIL_RUN:
            return fl::core::PrivacyBudgetPolicy::kFailRun;
        case fl::privacy::v1::PRIVACY_BUDGET_POLICY_UNSPECIFIED:
        case fl::privacy::v1::PRIVACY_BUDGET_POLICY_WARN_ONLY:
        default:
            return fl::core::PrivacyBudgetPolicy::kWarnOnly;
    }
}

fl::worker::v1::WorkerStatus worker_status_to_wire(fl::coordinator::WorkerStatus status) {
    switch (status) {
        case fl::coordinator::WorkerStatus::kRegistering:
            return fl::worker::v1::WORKER_STATUS_REGISTERING;
        case fl::coordinator::WorkerStatus::kIdle:
            return fl::worker::v1::WORKER_STATUS_IDLE;
        case fl::coordinator::WorkerStatus::kBusy:
            return fl::worker::v1::WORKER_STATUS_BUSY;
        case fl::coordinator::WorkerStatus::kUnhealthy:
            return fl::worker::v1::WORKER_STATUS_UNHEALTHY;
        case fl::coordinator::WorkerStatus::kDisconnected:
            return fl::worker::v1::WORKER_STATUS_DISCONNECTED;
        case fl::coordinator::WorkerStatus::kDraining:
            return fl::worker::v1::WORKER_STATUS_DRAINING;
        default:
            return fl::worker::v1::WORKER_STATUS_UNSPECIFIED;
    }
}

fl::core::TensorDescriptor tensor_descriptor_from_wire(
    const fl::worker::v1::TensorManifest& tensor) {
    // Only float32 is a supported domain dtype today (fl_core/tensor.hpp's
    // DType enum has a single value) — matches the same assumption already
    // made by coordinator_cli.cpp's parse_tensor_specs for the CLI-bridge
    // transport.
    return fl::core::TensorDescriptor{
        .name = tensor.name(),
        .shape = std::vector<std::uint64_t>(tensor.shape().begin(), tensor.shape().end()),
        .dtype = fl::core::DType::kFloat32,
    };
}

// Privacy Engineering phase: decodes a TensorManifest's inline `values`
// field into a real TensorBuffer — see docs/create-run-wire-mapping.md's
// "tensor transport" section for why an inline field (rather than an
// artifact-store upload/download) carries tensor payloads over the live
// gRPC wire. A TensorManifest with no values (empty) yields an empty
// TensorBuffer; callers that require a payload (e.g. a submitted client
// delta) reject that via the same validation path a missing/malformed
// tensor already goes through in submit_client_result.
fl::core::TensorBuffer tensor_buffer_from_wire(const fl::worker::v1::TensorManifest& tensor) {
    return fl::core::TensorBuffer(
        tensor_descriptor_from_wire(tensor),
        std::vector<double>(tensor.values().begin(), tensor.values().end()));
}

fl::core::TensorCollection tensor_collection_from_wire(
    const google::protobuf::RepeatedPtrField<fl::worker::v1::TensorManifest>& tensors) {
    fl::core::TensorCollection collection;
    for (const auto& tensor : tensors) {
        collection.insert(tensor_buffer_from_wire(tensor));
    }
    return collection;
}

RunConfig config_from_request(const fl::coordinator::v1::CreateRunRequest& request) {
    // Privacy Engineering phase: required-field validation — see
    // docs/create-run-wire-mapping.md. A run with no id, no clients, or a
    // per-round target exceeding the total client pool must be rejected
    // up front rather than accepted and left to fail confusingly later
    // (e.g. AcquireTask never returning a task).
    if (request.config().run_id().empty()) {
        throw std::invalid_argument("run_id is required");
    }
    if (request.total_clients() == 0) {
        throw std::invalid_argument("total_clients must be greater than zero");
    }
    if (request.target_clients_per_round() == 0 ||
        request.target_clients_per_round() > request.total_clients()) {
        throw std::invalid_argument("target_clients_per_round must be between 1 and total_clients");
    }

    RunConfig config;
    config.run_id = request.config().run_id();
    config.algorithm = algorithm_from_wire(request.optimizer().algorithm());
    config.weighting = weighting_from_wire(request.optimizer().weighting());
    config.server_lr = request.optimizer().server_lr();
    config.beta1 = request.optimizer().beta1();
    config.beta2 = request.optimizer().beta2();
    config.tau = request.optimizer().tau();
    config.contribution_cap = request.optimizer().contribution_cap();
    config.target_clients_per_round = request.target_clients_per_round();
    config.total_clients = request.total_clients();
    config.max_rounds = request.max_rounds();
    config.round_timeout_seconds = request.round_timeout_seconds();
    config.minimum_valid_results = request.minimum_valid_results();
    config.client_selection_seed = request.client_selection_seed();

    // Privacy Engineering phase: closes the CreateRun wire-mapping gap (see
    // docs/create-run-wire-mapping.md). Every field below previously had
    // no gRPC wire representation, so AcquireTask could never select a
    // real client and workers never received real training
    // hyperparameters through the live gRPC path — only the CLI-bridge
    // transport carried them (see coordinator_cli.cpp's
    // parse_run_config).
    config.client_ids.assign(request.client_ids().begin(), request.client_ids().end());
    config.local_epochs = request.local_epochs();
    config.batch_size = request.batch_size();
    config.learning_rate = request.learning_rate();
    config.momentum = request.momentum();
    config.weight_decay = request.weight_decay();
    config.fedprox_mu = request.fedprox_mu();
    config.task_lease_seconds = request.task_lease_seconds();
    config.max_task_retries = request.max_task_retries();

    const auto& wire_manifest = request.model_manifest();
    config.manifest.model_id = wire_manifest.model_id();
    config.manifest.model_version = wire_manifest.model_version();
    config.manifest.tensors.reserve(static_cast<std::size_t>(wire_manifest.tensors_size()));
    for (const auto& tensor : wire_manifest.tensors()) {
        config.manifest.tensors.push_back(tensor_descriptor_from_wire(tensor));
    }

    const auto& wire_aggregation_manifest = wire_manifest.aggregation_manifest();
    config.aggregation_manifest.shared_parameter_names.assign(
        wire_aggregation_manifest.shared_parameter_names().begin(),
        wire_aggregation_manifest.shared_parameter_names().end());
    config.aggregation_manifest.personalized_parameter_names.assign(
        wire_aggregation_manifest.personalized_parameter_names().begin(),
        wire_aggregation_manifest.personalized_parameter_names().end());
    config.aggregation_manifest.frozen_parameter_names.assign(
        wire_aggregation_manifest.frozen_parameter_names().begin(),
        wire_aggregation_manifest.frozen_parameter_names().end());
    config.aggregation_manifest.schema_hash = wire_aggregation_manifest.schema_hash();

    // Privacy Engineering phase: see docs/create-run-wire-mapping.md and
    // docs/user-level-dp.md. Unset (PRIVACY_MODE_UNSPECIFIED) and
    // PRIVACY_MODE_NONE both map to kNone — an absent privacy_config
    // means "this run is not private," unchanged pre-existing behavior.
    const auto& wire_privacy = request.privacy_config();
    config.privacy_mode = privacy_mode_from_wire(wire_privacy.mode());
    // Privacy budget policies (docs/privacy-budget-policies.md): top-
    // level PrivacyConfig fields, independent of which mechanism(s) are
    // active — mapped unconditionally, even for privacy_mode == kNone
    // (where they're simply never consulted, since finalize_round's
    // budget checks are gated on the accountants existing at all).
    config.privacy_budget_policy = privacy_budget_policy_from_wire(wire_privacy.budget_policy());
    config.warning_threshold_fraction = wire_privacy.warning_threshold_fraction();
    if (config.privacy_mode == fl::core::PrivacyMode::kSampleLevelDp ||
        config.privacy_mode == fl::core::PrivacyMode::kHybridDp) {
        // The C++ coordinator never applies sample-level DP itself — it
        // only validates and relays this config out to workers via
        // ClientTaskDescriptor (see RunInstance's make_descriptor). See
        // docs/hybrid-dp.md.
        const auto& wire_sample_level = wire_privacy.sample_level();
        config.sample_level_privacy.noise_multiplier = wire_sample_level.noise_multiplier();
        config.sample_level_privacy.max_grad_norm = wire_sample_level.max_grad_norm();
        config.sample_level_privacy.target_delta = wire_sample_level.target_delta();
        config.sample_level_privacy.accountant =
            accountant_type_from_wire(wire_sample_level.accountant());
        config.sample_level_privacy.poisson_sampling = wire_sample_level.poisson_sampling();
        config.sample_level_privacy.epsilon_budget = wire_sample_level.epsilon_budget();
        if (config.sample_level_privacy.noise_multiplier <= 0.0) {
            throw std::invalid_argument("sample-level DP requires a positive noise_multiplier");
        }
        if (config.sample_level_privacy.max_grad_norm <= 0.0) {
            throw std::invalid_argument("sample-level DP requires a positive max_grad_norm");
        }
        if (!(config.sample_level_privacy.target_delta > 0.0 &&
              config.sample_level_privacy.target_delta < 1.0)) {
            throw std::invalid_argument("sample-level DP requires target_delta in (0, 1)");
        }
    }
    if (config.privacy_mode == fl::core::PrivacyMode::kUserLevelDp ||
        config.privacy_mode == fl::core::PrivacyMode::kHybridDp) {
        const auto& wire_user_level = wire_privacy.user_level();
        config.user_level_privacy.noise_multiplier = wire_user_level.noise_multiplier();
        config.user_level_privacy.target_delta = wire_user_level.target_delta();
        config.user_level_privacy.initial_clipping_bound = wire_user_level.initial_clipping_bound();
        config.user_level_privacy.secure_random = wire_user_level.secure_random();
        config.user_level_privacy.epsilon_budget = wire_user_level.epsilon_budget();
        if (config.user_level_privacy.noise_multiplier <= 0.0) {
            throw std::invalid_argument("user-level DP requires a positive noise_multiplier");
        }
        if (config.user_level_privacy.initial_clipping_bound <= 0.0) {
            throw std::invalid_argument("user-level DP requires a positive initial_clipping_bound");
        }
        if (!(config.user_level_privacy.target_delta > 0.0 &&
              config.user_level_privacy.target_delta < 1.0)) {
            throw std::invalid_argument("user-level DP requires target_delta in (0, 1)");
        }
        // Privacy-safe weighting requirement (docs/user-level-dp.md):
        // unrestricted sample-count weighting gives unbounded per-client
        // sensitivity in the weighted sum, breaking the clipping bound's
        // guarantee. kSampleCount is the only weighting strategy this
        // rejects — uniform/capped_sample_count/normalized_bounded all
        // have a config-bounded maximum per-client weight.
        if (config.weighting == fl::core::WeightingStrategyType::kSampleCount) {
            throw std::invalid_argument(
                "weighting 'sample_count' is not privacy-safe for user-level DP; use "
                "uniform, capped_sample_count, or normalized_bounded instead");
        }

        // Adaptive clipping (docs/adaptive-clipping.md): independently
        // toggleable within user-level/hybrid DP. Left disabled (the
        // RunConfig default) when the wire message's `enabled` field is
        // false or the run isn't user-level/hybrid at all.
        const auto& wire_adaptive = wire_privacy.adaptive_clipping();
        config.adaptive_clipping_enabled = wire_adaptive.enabled();
        if (config.adaptive_clipping_enabled) {
            config.adaptive_clipping.initial_clip = wire_adaptive.initial_clip();
            config.adaptive_clipping.target_quantile = wire_adaptive.target_quantile();
            config.adaptive_clipping.clip_learning_rate = wire_adaptive.clip_learning_rate();
            config.adaptive_clipping.min_clip = wire_adaptive.min_clip();
            config.adaptive_clipping.max_clip = wire_adaptive.max_clip();
            config.adaptive_clipping.count_noise_multiplier =
                wire_adaptive.count_noise_multiplier();
            config.adaptive_clipping.target_delta = wire_adaptive.target_delta();
            config.adaptive_clipping.epsilon_budget = wire_adaptive.epsilon_budget();
        }
    }

    return config;
}

fl::coordinator::v1::RunStateResponse to_run_state_response(const RunSnapshot& snapshot) {
    fl::coordinator::v1::RunStateResponse response;
    response.set_run_id(snapshot.run_id);
    response.set_state(fl::core::to_string(snapshot.state));
    response.set_current_round(snapshot.current_round);
    response.set_model_version(snapshot.model_version);
    return response;
}

void to_run_details(const RunSnapshot& snapshot, fl::coordinator::v1::RunDetails* out) {
    out->set_run_id(snapshot.run_id);
    out->set_state(fl::core::to_string(snapshot.state));
    out->set_current_round(snapshot.current_round);
    out->set_max_rounds(snapshot.max_rounds);
    out->set_model_version(snapshot.model_version);
    out->set_algorithm(fl::core::to_string(snapshot.algorithm));
    out->set_registered_workers(static_cast<std::uint32_t>(snapshot.registered_workers));
    out->set_healthy_workers(static_cast<std::uint32_t>(snapshot.healthy_workers));
}

grpc::Status to_grpc_status(const std::exception& error) {
    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
}

// Certificate identity binding (docs/certificate-identity-binding.md),
// factored out for call sites (AcquireTask, SubmitClientResult) that
// need only the boolean accept/reject decision, not the full
// PeerIdentity -- RegisterWorker keeps its own inline version since it
// also reuses the extracted PeerIdentity afterward for signed-capability/
// identity-registry work. A null `context` (direct-call unit tests) or a
// non-mTLS-authenticated connection is a deliberate no-op, matching
// peer_identity.hpp's documented contract -- see its header comment.
std::optional<grpc::Status> reject_if_worker_identity_mismatch(grpc::ServerContext* context,
                                                               const std::string& worker_id) {
    if (context == nullptr) {
        return std::nullopt;
    }
    const auto peer_identity = extract_peer_identity(*context);
    if (peer_identity.authenticated && !has_worker_identity(peer_identity, worker_id)) {
        return grpc::Status(
            grpc::StatusCode::PERMISSION_DENIED,
            "worker_id '" + worker_id + "' does not match the authenticated certificate identity");
    }
    return std::nullopt;
}

// Work Package G: ADMIN_CONTROL RPCs require an authenticated
// spiffe://federated-platform/service/go-api certificate identity --
// unlike reject_if_worker_identity_mismatch, this is a strict
// requirement (an unauthenticated/non-mTLS connection is REJECTED, not
// a no-op) since administration RPCs must never be reachable without a
// real service certificate once mTLS is the deployed transport. A null
// `context` (direct-call unit tests) remains a no-op, matching every
// other identity check in this file, so coordinator_service_test.cpp's
// existing direct-call test pattern keeps working for these new RPCs
// too.
std::optional<grpc::Status> reject_if_not_go_api_service_identity(grpc::ServerContext* context,
                                                                  TransportMode transport_mode) {
    if (context == nullptr) {
        return std::nullopt;
    }
    const auto peer_identity = extract_peer_identity(*context);
    if (!peer_identity.authenticated) {
        if (transport_mode == TransportMode::kInsecureDevelopment) {
            return std::nullopt;
        }
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "administration RPCs require an authenticated service certificate");
    }
    if (!has_service_identity(peer_identity, "go-api")) {
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "administration RPCs require the go-api service certificate identity");
    }
    return std::nullopt;
}

// Best-effort caller identity label for a security event's safe_actor_id
// -- a URI SAN is an identifier, never a secret, so it is safe to record
// verbatim (see peer_identity.hpp). "unauthenticated" for a connection
// with no verified client certificate (INSECURE_DEVELOPMENT/TLS_SERVER)
// or a null context (direct-call unit tests).
std::string safe_actor_label(grpc::ServerContext* context) {
    if (context == nullptr) {
        return "unauthenticated";
    }
    const auto peer_identity = extract_peer_identity(*context);
    if (!peer_identity.authenticated || peer_identity.uri_sans.empty()) {
        return "unauthenticated";
    }
    return peer_identity.uri_sans.front();
}

// Security Events, Metrics, and Durable Audit Journal slice
// (docs/security-events.md): emits SECURITY_PERMISSION_DENIED for any
// ADMIN_CONTROL RPC guarded by reject_if_not_go_api_service_identity.
// No-op when no journal is configured, matching every optional-store
// convention in this file.
void emit_permission_denied_event(SecurityEventJournal* journal,
                                  grpc::ServerContext* context,
                                  const std::string& rpc_name) {
    if (journal == nullptr) {
        return;
    }
    SecurityEvent event;
    event.source_service = "coordinator";
    event.source_component = "coordinator_service";
    event.event_type = SecurityEventType::kSecurityPermissionDenied;
    event.severity = default_severity(event.event_type);
    event.actor_type = SecurityActorType::kService;
    event.safe_actor_id = safe_actor_label(context);
    event.subject_type = SecuritySubjectType::kSecurityMutation;
    event.safe_subject_id = rpc_name;
    event.outcome = SecurityOutcome::kBlocked;
    event.reason_code = "not_go_api_service_identity";
    journal->emit(std::move(event));
}

// Maps an EnvelopeVerificationResult::rejection_code (signed_envelope_verifier.cpp)
// onto the closest-matching security event type -- representative wiring
// for the Heartbeat message type; the same codes/mapping apply to every
// other SignedWorkerEnvelope-verifying RPC (SubmitClientResult, the
// privacy-record stream, RotateWorkerSigningKey), not yet wired to
// events -- see docs/security-observability-inventory.md.
SecurityEventType security_event_type_for_envelope_rejection(const std::string& rejection_code) {
    if (rejection_code == "payload_hash_mismatch") {
        return SecurityEventType::kPayloadHashMismatch;
    }
    if (rejection_code == "expired" || rejection_code == "future_issued") {
        return SecurityEventType::kMessageExpired;
    }
    return SecurityEventType::kSignatureVerificationFailed;
}

// Emits a worker-lifecycle event (WORKER_SUSPENDED/ACTIVATED/REVOKED) and
// a matching SecurityAuditRecord for the same mutation -- kept as two
// separate calls/journals per Design Decision 7 (events answer "what
// happened", audit answers "who did it").
void emit_worker_lifecycle_records(SecurityEventJournal* event_journal,
                                   SecurityAuditJournal* audit_journal,
                                   grpc::ServerContext* context,
                                   SecurityEventType event_type,
                                   const std::string& action,
                                   const std::string& worker_id,
                                   const std::string& reason,
                                   const std::string& request_id,
                                   const std::string& trace_id) {
    const std::string actor = safe_actor_label(context);
    if (event_journal != nullptr) {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = event_type;
        event.severity = default_severity(event_type);
        event.actor_type = SecurityActorType::kService;
        event.safe_actor_id = actor;
        event.subject_type = SecuritySubjectType::kWorkerIdentity;
        event.safe_subject_id = worker_id;
        event.worker_id = worker_id;
        event.outcome = SecurityOutcome::kCompleted;
        event.reason_code = reason.substr(0, kSecurityEventMaxReasonCodeLength);
        event.request_id = request_id;
        event.trace_id = trace_id;
        event_journal->emit(std::move(event));
    }
    if (audit_journal != nullptr) {
        SecurityAuditRecord record;
        record.safe_actor_id = actor;
        record.actor_role = "service";
        record.action = action;
        record.resource_type = "worker_identity";
        record.resource_id = worker_id;
        record.outcome = "ACCEPTED";
        record.reason = reason;
        record.request_id = request_id;
        record.trace_id = trace_id;
        audit_journal->append(std::move(record));
    }
}

// Privacy Record Authenticity slice, Work Package G
// (docs/signed-privacy-records.md's "Budget decision consistency"
// section): a SubmitClientResult carrying a real, accepted training
// update alongside a privacy record whose worker-side budget_decision
// says training should have been refused or should have failed is a
// contradiction -- either the worker's enforcement logic is broken, or
// something is attempting to smuggle a normal update past a budget
// stop. "stopped_after_task" is deliberately NOT contradictory: per the
// documented policy, the task that triggered STOP_AFTER_CURRENT_TASK is
// itself still allowed to submit its already-completed result -- only
// *future* task assignment for that (worker, client) pair must be
// blocked (see docs/signed-privacy-records.md's stated, disclosed gap:
// this pass does not yet wire that block into AcquireTask).
//
// Returns a non-empty reason string when the combination is
// contradictory (including when budget_decision is not one of the six
// known SampleBudgetOutcome values -- an unrecognized string is treated
// as invalid, not silently accepted).
std::string budget_decision_contradiction_reason(const std::string& budget_decision) {
    static const std::vector<std::string> kKnownValues = {
        "allowed",
        "warned",
        "stopped_before_step",
        "stopped_after_task",
        "failed_task",
        "refused_before_training",
    };
    if (std::find(kKnownValues.begin(), kKnownValues.end(), budget_decision) ==
        kKnownValues.end()) {
        return "unrecognized budget_decision value: '" + budget_decision + "'";
    }
    if (budget_decision == "stopped_before_step") {
        return "budget_decision 'stopped_before_step' means the worker's STOP_BEFORE_EXCEEDING "
               "policy should have refused this step before it was taken; a normal completed "
               "update must not accompany it";
    }
    if (budget_decision == "refused_before_training") {
        return "budget_decision 'refused_before_training' means this task should never have "
               "trained at all; a normal completed update must not accompany it";
    }
    if (budget_decision == "failed_task") {
        return "budget_decision 'failed_task' means the worker's FAIL_TASK policy triggered a "
               "hard failure; a normal successful update must not accompany a failure decision";
    }
    return "";
}

fl::coordinator::v1::WorkerIdentitySummary to_wire_identity_summary(
    const WorkerIdentityRecord& record) {
    fl::coordinator::v1::WorkerIdentitySummary summary;
    summary.set_worker_id(record.worker_id);
    summary.set_certificate_identity(record.certificate_identity);
    summary.set_certificate_fingerprint(record.certificate_fingerprint);
    summary.set_signing_key_id(record.signing_key_id);
    summary.set_registration_status(to_string(record.registration_status));
    summary.set_software_version(record.software_version);
    summary.set_build_id(record.build_id);
    summary.set_created_at_unix_s(record.created_at_unix_s);
    summary.set_updated_at_unix_s(record.updated_at_unix_s);
    summary.set_expires_at_unix_s(record.expires_at_unix_s);
    summary.set_suspended_at_unix_s(record.suspended_at_unix_s);
    summary.set_revoked_at_unix_s(record.revoked_at_unix_s);
    summary.set_revocation_reason(record.revocation_reason);
    return summary;
}

fl::coordinator::v1::SigningKeyRecordSummary to_wire_signing_key_summary(
    const SigningKeyRecord& record) {
    fl::coordinator::v1::SigningKeyRecordSummary summary;
    summary.set_worker_id(record.worker_id);
    summary.set_signing_key_id(record.signing_key_id);
    summary.set_public_key_fingerprint(record.public_key_fingerprint);
    summary.set_status(to_string(record.status));
    summary.set_created_at_unix_s(record.created_at_unix_s);
    summary.set_activated_at_unix_s(record.activated_at_unix_s);
    summary.set_expires_at_unix_s(record.expires_at_unix_s);
    summary.set_grace_period_start_unix_s(record.grace_period_start_unix_s);
    summary.set_grace_period_end_unix_s(record.grace_period_end_unix_s);
    summary.set_rotated_from_key_id(record.rotated_from_key_id);
    summary.set_rotated_to_key_id(record.rotated_to_key_id);
    summary.set_revoked_at_unix_s(record.revoked_at_unix_s);
    summary.set_revocation_reason(record.revocation_reason);
    summary.set_registration_source(record.registration_source);
    return summary;
}

fl::coordinator::v1::CoordinatorSigningKeyRecordSummary to_wire_coordinator_signing_key_summary(
    const CoordinatorSigningKeyRecord& record) {
    fl::coordinator::v1::CoordinatorSigningKeyRecordSummary summary;
    summary.set_signing_key_id(record.signing_key_id);
    summary.set_public_key_fingerprint(record.public_key_fingerprint);
    summary.set_status(to_string(record.status));
    summary.set_created_at_unix_s(record.created_at_unix_s);
    summary.set_expires_at_unix_s(record.expires_at_unix_s);
    summary.set_grace_period_end_unix_s(record.grace_period_end_unix_s);
    summary.set_rotated_from_key_id(record.rotated_from_key_id);
    summary.set_rotated_to_key_id(record.rotated_to_key_id);
    summary.set_revoked_at_unix_s(record.revoked_at_unix_s);
    summary.set_revocation_reason(record.revocation_reason);
    return summary;
}

// Signing-Key Lifecycle slice, Work Package J
// (docs/signing-key-management.md's "Enforcement across signed
// messages" section): which SigningKeyStatus values each signed
// message kind accepts. Capability refresh and key-rotation requests
// require the CURRENT preferred key (ACTIVE only); heartbeats, client
// results, and privacy records also accept a key still in its
// GRACE_PERIOD window, so a worker mid-rotation is never abruptly
// cut off from messages already in flight.
enum class SignedMessageKind {
    kCapability,
    kHeartbeat,
    kClientResult,
    kPrivacyRecord,
    kKeyRotation,
    kSecurityEventBatch,
    kSecureAggregationKeyAdvertisement,
    kSecureAggregationMaskedUpdate,
};

bool signing_key_status_permits(SigningKeyStatus status, SignedMessageKind kind) {
    switch (kind) {
        case SignedMessageKind::kCapability:
        case SignedMessageKind::kKeyRotation:
            return status == SigningKeyStatus::kActive;
        case SignedMessageKind::kHeartbeat:
        case SignedMessageKind::kClientResult:
        case SignedMessageKind::kPrivacyRecord:
        // A worker mid key-rotation must still be able to flush its
        // locally-queued security events, same reasoning as
        // heartbeat/client-result/privacy-record above.
        case SignedMessageKind::kSecurityEventBatch:
        // Same reasoning again: a worker mid key-rotation must still be
        // able to advertise its ephemeral secure-aggregation key, or
        // submit its masked update.
        case SignedMessageKind::kSecureAggregationKeyAdvertisement:
        case SignedMessageKind::kSecureAggregationMaskedUpdate:
            return status == SigningKeyStatus::kActive || status == SigningKeyStatus::kGracePeriod;
    }
    return false;
}

struct ResolvedSigningKey {
    bool ok = false;
    std::string rejection_code;  // set only when ok == false
    std::string reason;          // set only when ok == false
    std::string public_key_hex;  // valid only when ok == true
};

// The single enforcement point every signed-message verification path
// now goes through (docs/signing-key-management.md). When
// signing_key_registry is null, falls back to the pre-existing
// single-key comparison against identity_record.signing_key_id --
// preserving every test/call site written before this slice, exactly
// like every other optional store in this file. Resolves the ACTUAL
// public key bytes to verify the signature against -- critically NOT
// always identity_record.signing_public_key (WorkerIdentityRegistry's
// single "preferred" key cache, refreshed to the newest key on
// rotation): a message signed by a still-valid GRACE_PERIOD key must be
// verified against *that* key's own bytes, which SigningKeyRegistry
// retains independently of whatever the "preferred" cache currently
// points at.
ResolvedSigningKey resolve_signing_key(SigningKeyRegistry* signing_key_registry,
                                       const WorkerIdentityRecord& identity_record,
                                       const std::string& presented_signing_key_id,
                                       SignedMessageKind kind,
                                       double now_unix_s) {
    if (signing_key_registry == nullptr) {
        if (presented_signing_key_id != identity_record.signing_key_id) {
            return {false,
                    "unknown_signing_key",
                    "signing_key_id does not match the signing key on record for this worker",
                    ""};
        }
        return {true, "", "", identity_record.signing_public_key};
    }
    const auto record =
        signing_key_registry->find(identity_record.worker_id, presented_signing_key_id, now_unix_s);
    if (!record.has_value()) {
        return {false,
                "unknown_signing_key",
                "signing_key_id '" + presented_signing_key_id +
                    "' is not registered for worker_id '" + identity_record.worker_id + "'",
                ""};
    }
    if (!signing_key_status_permits(record->status, kind)) {
        return {false,
                "signing_key_" + to_string(record->status),
                "signing key '" + presented_signing_key_id + "' has status '" +
                    to_string(record->status) + "', which does not permit this message type",
                ""};
    }
    return {true, "", "", record->public_key_hex};
}

}  // namespace

CoordinatorServiceImpl::CoordinatorServiceImpl(
    RunManager& manager,
    WorkerIdentityRegistry* identity_registry,
    ReplayProtectionStore* replay_store,
    bool allow_unsigned_client_results,
    AccountantMonotonicityStore* monotonicity_store,
    bool allow_unsigned_privacy_records,
    SigningKeyRegistry* signing_key_registry,
    CoordinatorActiveIdentityStore* coordinator_active_identity,
    CoordinatorSigningKeyRegistry* coordinator_signing_key_registry,
    CoordinatorTaskSequenceStore* coordinator_task_sequence_store,
    IdempotencyStore* idempotency_store,
    std::string coordinator_signing_key_dir,
    std::string trusted_key_bundle_path,
    std::string coordinator_identity_label,
    TransportMode transport_mode,
    SecurityEventJournal* security_event_journal,
    SecurityAuditJournal* security_audit_journal,
    SecureAggregationSessionManager* secure_aggregation_manager,
    bool secure_aggregation_enabled,
    double secure_aggregation_key_advertisement_window_seconds,
    double secure_aggregation_masked_update_window_seconds)
    : manager_(&manager),
      identity_registry_(identity_registry),
      replay_store_(replay_store),
      allow_unsigned_client_results_(allow_unsigned_client_results),
      monotonicity_store_(monotonicity_store),
      allow_unsigned_privacy_records_(allow_unsigned_privacy_records),
      signing_key_registry_(signing_key_registry),
      coordinator_active_identity_(coordinator_active_identity),
      coordinator_signing_key_registry_(coordinator_signing_key_registry),
      coordinator_task_sequence_store_(coordinator_task_sequence_store),
      idempotency_store_(idempotency_store),
      coordinator_signing_key_dir_(std::move(coordinator_signing_key_dir)),
      trusted_key_bundle_path_(std::move(trusted_key_bundle_path)),
      coordinator_identity_label_(std::move(coordinator_identity_label)),
      transport_mode_(transport_mode),
      started_at_(std::chrono::steady_clock::now()),
      security_event_journal_(security_event_journal),
      security_audit_journal_(security_audit_journal),
      secure_aggregation_manager_(secure_aggregation_manager),
      secure_aggregation_enabled_(secure_aggregation_enabled),
      secure_aggregation_key_advertisement_window_seconds_(
          secure_aggregation_key_advertisement_window_seconds),
      secure_aggregation_masked_update_window_seconds_(
          secure_aggregation_masked_update_window_seconds) {}

double CoordinatorServiceImpl::now_unix_s() {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count()) /
           1000.0;
}

grpc::Status CoordinatorServiceImpl::CreateRun(grpc::ServerContext*,
                                               const fl::coordinator::v1::CreateRunRequest* request,
                                               fl::coordinator::v1::CreateRunResponse* response) {
    try {
        const auto run_id = manager_->create_run(config_from_request(*request), now_unix_s());
        response->set_run_id(run_id);
        response->set_state(fl::core::to_string(manager_->get(run_id).snapshot().state));
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::StartRun(grpc::ServerContext*,
                                              const fl::coordinator::v1::StartRunRequest* request,
                                              fl::coordinator::v1::RunStateResponse* response) {
    try {
        auto& run = manager_->get(request->run_id());
        run.start(request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::PauseRun(grpc::ServerContext*,
                                              const fl::coordinator::v1::PauseRunRequest* request,
                                              fl::coordinator::v1::RunStateResponse* response) {
    try {
        auto& run = manager_->get(request->run_id());
        run.pause(request->reason(), request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::ResumeRun(grpc::ServerContext*,
                                               const fl::coordinator::v1::ResumeRunRequest* request,
                                               fl::coordinator::v1::RunStateResponse* response) {
    try {
        auto& run = manager_->get(request->run_id());
        run.resume(request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::CancelRun(grpc::ServerContext*,
                                               const fl::coordinator::v1::CancelRunRequest* request,
                                               fl::coordinator::v1::RunStateResponse* response) {
    try {
        auto& run = manager_->get(request->run_id());
        run.cancel(request->reason(), request->trace_id(), now_unix_s());
        *response = to_run_state_response(run.snapshot());
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetRun(grpc::ServerContext*,
                                            const fl::coordinator::v1::GetRunRequest* request,
                                            fl::coordinator::v1::RunDetails* response) {
    try {
        const auto& run = manager_->get(request->run_id());
        to_run_details(run.snapshot(), response);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetRound(grpc::ServerContext*,
                                              const fl::coordinator::v1::GetRoundRequest*,
                                              fl::coordinator::v1::RoundDetails*) {
    // Work Package N (docs/rpc-security-policy.md): declared in the
    // proto with no domain-layer round-history accessor to back it
    // (RunInstance tracks the *current* round, not an indexed history
    // of every past round) and not required by the current live Go/web
    // flow. Explicit, documented UNIMPLEMENTED rather than an ambiguous
    // empty success response or a silent fall-through to gRPC's own
    // generic default -- see that doc's Work Package N section.
    return grpc::Status(grpc::StatusCode::UNIMPLEMENTED,
                        "GetRound is not implemented -- RunInstance does not retain indexed "
                        "per-round history; see docs/rpc-security-policy.md");
}

grpc::Status CoordinatorServiceImpl::GetModelManifest(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetModelManifestRequest*,
    fl::coordinator::v1::ModelManifest*) {
    // Work Package N: same reasoning as GetRound above -- declared, not
    // required by the current live flow (the manifest a run needs is
    // supplied once via CreateRunRequest.model_manifest and never
    // separately re-fetched by any current caller), explicit
    // UNIMPLEMENTED rather than silent default behavior.
    return grpc::Status(grpc::StatusCode::UNIMPLEMENTED,
                        "GetModelManifest is not implemented -- see docs/rpc-security-policy.md");
}

grpc::Status CoordinatorServiceImpl::RegisterWorker(
    grpc::ServerContext* context,
    const fl::worker::v1::RegisterWorkerRequest* request,
    fl::worker::v1::RegisterWorkerResponse* response) {
    try {
        // Certificate identity binding (Work Package C,
        // docs/certificate-identity-binding.md): enforced only when
        // this connection is actually mTLS-authenticated. Under
        // INSECURE_DEVELOPMENT or TLS_SERVER (no client certificate
        // requested) there is no peer identity to bind to, so this is
        // a deliberate no-op then -- which is also what keeps
        // coordinator_service_test.cpp's direct nullptr-context calls
        // (bypassing real gRPC dispatch entirely) unaffected. `context`
        // is never null when this method is actually invoked as a real
        // gRPC handler.
        PeerIdentity peer_identity;
        if (context != nullptr) {
            peer_identity = extract_peer_identity(*context);
            if (peer_identity.authenticated &&
                !has_worker_identity(peer_identity, request->worker_id())) {
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "worker_id '" + request->worker_id() +
                                        "' does not match the authenticated certificate identity");
            }
        }

        // Signed capability statement verification + worker identity
        // registry population (Work Package F,
        // docs/worker-identity-registry.md). A deliberate no-op when the
        // request carries no signed_capability at all (the legacy,
        // unsigned WorkerPrivacyCapabilities path below is completely
        // unaffected either way) or when this coordinator was
        // constructed without an identity_registry_ (nullptr default --
        // see coordinator_service.hpp).
        if (request->has_signed_capability()) {
            const auto& signed_statement = request->signed_capability();
            const auto verification = verify_capability_statement(signed_statement, now_unix_s());
            if (!verification.valid) {
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed capability statement rejected: " + verification.reason);
            }
            if (signed_statement.worker_id() != request->worker_id()) {
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed capability statement worker_id does not match "
                                    "the request's worker_id");
            }
            if (identity_registry_ != nullptr) {
                const auto existing = identity_registry_->find_by_worker_id(request->worker_id());
                if (existing.has_value()) {
                    if (existing->registration_status == WorkerIdentityStatus::kRevoked) {
                        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                            "worker_id '" + request->worker_id() + "' is revoked");
                    }
                }
                // Signing-Key Lifecycle slice (docs/signing-key-management.md):
                // real, multi-key-aware enforcement when signing_key_registry_
                // is wired; falls back to the pre-existing single-key
                // comparison otherwise (preserving every existing test).
                if (signing_key_registry_ != nullptr && existing.has_value()) {
                    const auto presented_key_id = signed_statement.signing_key_id();
                    const auto existing_key_record = signing_key_registry_->find(
                        request->worker_id(), presented_key_id, now_unix_s());
                    if (existing_key_record.has_value()) {
                        // A previously-known key is presenting again --
                        // capability refresh requires ACTIVE (see
                        // signing_key_status_permits).
                        if (!signing_key_status_permits(existing_key_record->status,
                                                        SignedMessageKind::kCapability)) {
                            return grpc::Status(
                                grpc::StatusCode::PERMISSION_DENIED,
                                "signed capability statement rejected: signing key '" +
                                    presented_key_id + "' has status '" +
                                    to_string(existing_key_record->status) +
                                    "', which does not permit a capability refresh");
                        }
                        if (existing_key_record->public_key_hex !=
                            signed_statement.signing_public_key()) {
                            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                                "signing_key_id '" + presented_key_id +
                                                    "' is already registered with a different "
                                                    "public key");
                        }
                    } else if (signing_key_registry_->has_any_valid_key(request->worker_id(),
                                                                        now_unix_s())) {
                        // A brand-new, never-before-seen key presented
                        // for a worker that already has a valid key on
                        // record: this must go through
                        // RotateWorkerSigningKey (which requires a
                        // signature from the CURRENT key), never a bare
                        // RegisterWorker call -- otherwise an attacker
                        // who merely reuses a valid worker_id/certificate
                        // could swap in their own signing key.
                        return grpc::Status(
                            grpc::StatusCode::PERMISSION_DENIED,
                            "worker_id '" + request->worker_id() +
                                "' already has a valid signing key on record; use "
                                "RotateWorkerSigningKey to change signing keys, not "
                                "RegisterWorker");
                    }
                } else if (existing.has_value()) {
                    if (!existing->signing_public_key.empty() &&
                        existing->signing_public_key != signed_statement.signing_public_key()) {
                        // Fallback (signing_key_registry_ == nullptr):
                        // pre-existing single-key behavior, unchanged --
                        // signing-key rotation is not evaluated at all
                        // without a real registry wired in.
                        return grpc::Status(
                            grpc::StatusCode::PERMISSION_DENIED,
                            "signed capability statement's signing key does not match the "
                            "signing key already on record for worker_id '" +
                                request->worker_id() +
                                "' (signing_key_registry_ is not configured on this "
                                "coordinator)");
                    }
                }
                // Only populate/refresh the registry when this
                // connection is actually mTLS-authenticated --
                // certificate_fingerprint is this registry's uniqueness
                // key (see worker_identity_registry.hpp), and an
                // unauthenticated (insecure/dev) connection has no real
                // certificate to derive one from.
                if (peer_identity.authenticated) {
                    const std::string certificate_identity = peer_identity.uri_sans.empty()
                                                                 ? std::string()
                                                                 : peer_identity.uri_sans.front();
                    try {
                        identity_registry_->register_identity(
                            request->worker_id(),
                            certificate_identity,
                            /*certificate_serial=*/"",
                            peer_identity.certificate_fingerprint_sha256,
                            signed_statement.signing_public_key(),
                            signed_statement.signing_key_id(),
                            signed_statement.software_version(),
                            signed_statement.build_id(),
                            now_unix_s(),
                            signed_statement.expires_at());
                    } catch (const WorkerIdentityRegistryError& error) {
                        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, error.what());
                    }
                    if (signing_key_registry_ != nullptr &&
                        !signing_key_registry_
                             ->find(request->worker_id(),
                                    signed_statement.signing_key_id(),
                                    now_unix_s())
                             .has_value()) {
                        InitialSigningKeyRegistration key_registration;
                        key_registration.worker_id = request->worker_id();
                        key_registration.signing_key_id = signed_statement.signing_key_id();
                        key_registration.public_key_hex = signed_statement.signing_public_key();
                        key_registration.public_key_fingerprint =
                            public_key_fingerprint_hex(signed_statement.signing_public_key());
                        key_registration.now_unix_s = now_unix_s();
                        key_registration.expires_at_unix_s = signed_statement.expires_at();
                        key_registration.registration_source = "registration";
                        try {
                            signing_key_registry_->register_initial_key(key_registration);
                        } catch (const SigningKeyRegistryError& error) {
                            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, error.what());
                        }
                    }
                }
            }
        }

        WorkerCapability capability;
        capability.device = request->capability().device();
        capability.cpu_count = request->capability().cpu_count();
        capability.gpu_available = request->capability().gpu_available();
        capability.gpu_count = request->capability().gpu_count();
        capability.available_memory_bytes = request->capability().available_memory_bytes();
        if (request->capability().has_privacy()) {
            const auto& wire_privacy_caps = request->capability().privacy();
            capability.privacy.supports_sample_level_dp =
                wire_privacy_caps.supports_sample_level_dp();
            capability.privacy.opacus_version = wire_privacy_caps.opacus_version();
            capability.privacy.supports_secure_random = wire_privacy_caps.supports_secure_random();
            for (const auto& accountant : wire_privacy_caps.supported_accountants()) {
                capability.privacy.supported_accountants.push_back(accountant_type_from_wire(
                    static_cast<fl::privacy::v1::AccountantType>(accountant)));
            }
        }
        const auto info = manager_->worker_registry().register_worker(
            request->worker_id(), capability, now_unix_s());
        response->set_worker_id(info.worker_id);
        response->set_status(fl::worker::v1::WORKER_STATUS_REGISTERING);
        response->set_heartbeat_interval_seconds(10);
        response->set_task_poll_interval_seconds(2);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::Heartbeat(
    grpc::ServerContext* context,
    const fl::worker::v1::WorkerHeartbeatRequest* request,
    fl::worker::v1::WorkerHeartbeatResponse* response) {
    response->set_acknowledged(false);
    response->set_should_disconnect(false);

    // Message Authenticity Enforcement and Identity Lifecycle slice,
    // Work Packages D/G (docs/signed-worker-envelopes.md): the
    // coordinator security enforcement ordering this pipeline follows
    // is: certificate identity binding -> require + decode the signed
    // envelope -> resolve the worker's identity/signing-key record ->
    // reject a REVOKED worker outright -> verify the envelope
    // (schema/message_type/payload_hash/signature/expiry) -> verify
    // replay/sequence state -> process the domain operation -> commit
    // replay state only after domain processing succeeds. No RPC in
    // this codebase reached step 1 before this slice (Heartbeat had no
    // identity check at all) -- see docs/rpc-security-policy.md.
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        response->set_rejection_code("certificate_identity_mismatch");
        return *rejection;
    }

    if (!request->has_envelope()) {
        response->set_rejection_code("envelope_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "a signed envelope is required for Heartbeat");
    }
    const auto& envelope = request->envelope();

    if (identity_registry_ == nullptr) {
        // A signature cannot be verified against any key without a
        // registry to resolve one from -- this is a coordinator
        // configuration error (main.cpp always constructs a real
        // registry for the live server), not something to silently
        // bypass. coordinator_service_test.cpp's direct-call tests
        // never exercise this branch because they never set `envelope`
        // in the first place (see the has_envelope() check above).
        response->set_rejection_code("identity_registry_unavailable");
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    const auto identity_record = identity_registry_->find_by_worker_id(request->worker_id());
    if (!identity_record.has_value()) {
        response->set_rejection_code("unknown_worker");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "unknown worker_id: " + request->worker_id());
    }
    if (identity_record->registration_status == WorkerIdentityStatus::kRevoked) {
        // Worker suspension's "signed heartbeat accepted only to report
        // suspended status" carve-out (Work Package M) is NOT
        // implemented this pass -- only this REVOKED rejection is.
        // A SUSPENDED worker's heartbeat is still verified and accepted
        // below; only revocation blocks it outright.
        response->set_rejection_code("worker_revoked");
        response->set_should_disconnect(true);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "worker_id '" + request->worker_id() + "' is revoked");
    }
    const auto resolved_key = resolve_signing_key(signing_key_registry_,
                                                  *identity_record,
                                                  envelope.signing_key_id(),
                                                  SignedMessageKind::kHeartbeat,
                                                  now_unix_s());
    if (!resolved_key.ok) {
        response->set_rejection_code(resolved_key.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, resolved_key.reason);
    }

    const auto payload_hash_input = heartbeat_payload_hash_input(*request, envelope);
    const auto verification = verify_signed_envelope(
        envelope,
        static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
        payload_hash_input,
        resolved_key.public_key_hex,
        now_unix_s(),
        /*future_issued_tolerance_seconds=*/30.0);
    if (!verification.valid) {
        response->set_rejection_code(verification.rejection_code);
        if (security_event_journal_ != nullptr) {
            SecurityEvent event;
            event.source_service = "coordinator";
            event.source_component = "coordinator_service";
            event.event_type =
                security_event_type_for_envelope_rejection(verification.rejection_code);
            event.severity = default_severity(event.event_type);
            event.actor_type = SecurityActorType::kWorker;
            event.safe_actor_id = request->worker_id();
            event.subject_type = SecuritySubjectType::kHeartbeat;
            event.safe_subject_id = request->worker_id();
            event.worker_id = request->worker_id();
            event.safe_signing_key_id = envelope.signing_key_id();
            event.outcome = SecurityOutcome::kRejected;
            event.reason_code = verification.rejection_code;
            security_event_journal_->emit(std::move(event));
        }
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "signed heartbeat rejected: " + verification.reason);
    }

    ReplayCandidate replay_candidate;
    if (replay_store_ != nullptr) {
        replay_candidate.worker_id = request->worker_id();
        replay_candidate.signing_key_id = envelope.signing_key_id();
        replay_candidate.message_stream = MessageStream::kHeartbeat;
        replay_candidate.sequence_number = envelope.sequence_number();
        replay_candidate.nonce = envelope.nonce();
        replay_candidate.now_unix_s = now_unix_s();
        // Retain this nonce for exactly as long as the envelope itself
        // was valid for -- once expired, verify_signed_envelope's own
        // expiry check already rejects a replay regardless of nonce
        // history, so retaining it any longer buys nothing. A minimum
        // of 1 second guards against a degenerate expires_at <=
        // issued_at envelope (which verify_signed_envelope would in
        // practice already have rejected as expired before this point).
        const double window = envelope.expires_at() - envelope.issued_at();
        replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;

        const auto replay_decision = replay_store_->validate(replay_candidate);
        if (!replay_decision.accepted) {
            response->set_rejection_code(to_string(replay_decision.reason));
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "coordinator_service";
                event.event_type = SecurityEventType::kMessageReplayRejected;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kWorker;
                event.safe_actor_id = request->worker_id();
                event.subject_type = SecuritySubjectType::kReplayState;
                event.safe_subject_id = request->worker_id();
                event.worker_id = request->worker_id();
                event.safe_signing_key_id = envelope.signing_key_id();
                event.outcome = SecurityOutcome::kRejected;
                event.reason_code = to_string(replay_decision.reason);
                security_event_journal_->emit(std::move(event));
            }
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "signed heartbeat rejected: " + replay_decision.detail);
        }
    }

    try {
        manager_->worker_registry().heartbeat(request->worker_id(),
                                              fl::coordinator::WorkerStatus::kIdle,
                                              request->current_task_id(),
                                              now_unix_s());
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }

    // Replay/sequence state is committed only after domain processing
    // above has actually succeeded (Work Package D's "do not update
    // replay state permanently if domain processing fails" ordering
    // requirement).
    if (replay_store_ != nullptr) {
        replay_store_->commit(replay_candidate);
    }

    if (security_event_journal_ != nullptr) {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = SecurityEventType::kHeartbeatAccepted;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = request->worker_id();
        event.subject_type = SecuritySubjectType::kHeartbeat;
        event.safe_subject_id = request->worker_id();
        event.worker_id = request->worker_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = SecurityOutcome::kAccepted;
        security_event_journal_->emit(std::move(event));
    }

    response->set_acknowledged(true);
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::ListWorkers(
    grpc::ServerContext*,
    const fl::coordinator::v1::ListWorkersRequest*,
    fl::coordinator::v1::ListWorkersResponse* response) {
    try {
        for (const auto& info : manager_->worker_registry().list()) {
            auto* wire_worker = response->add_workers();
            wire_worker->set_worker_id(info.worker_id);
            wire_worker->set_status(worker_status_to_wire(info.status));
            wire_worker->set_registered_at_unix_s(info.registered_at_unix_s);
            wire_worker->set_last_heartbeat_unix_s(info.last_heartbeat_unix_s);

            auto* wire_capability = wire_worker->mutable_capability();
            wire_capability->set_device(info.capability.device);
            wire_capability->set_cpu_count(info.capability.cpu_count);
            wire_capability->set_gpu_available(info.capability.gpu_available);
            wire_capability->set_gpu_count(info.capability.gpu_count);
            wire_capability->set_available_memory_bytes(info.capability.available_memory_bytes);
            for (const auto& format : info.capability.supported_model_formats) {
                wire_capability->add_supported_model_formats(format);
            }
            for (const auto& algorithm : info.capability.supported_algorithms) {
                wire_capability->add_supported_algorithms(algorithm);
            }
            auto* wire_privacy_caps = wire_capability->mutable_privacy();
            wire_privacy_caps->set_supports_sample_level_dp(
                info.capability.privacy.supports_sample_level_dp);
            wire_privacy_caps->set_opacus_version(info.capability.privacy.opacus_version);
            wire_privacy_caps->set_supports_secure_random(
                info.capability.privacy.supports_secure_random);
            for (const auto& accountant : info.capability.privacy.supported_accountants) {
                wire_privacy_caps->add_supported_accountants(accountant_type_to_wire(accountant));
            }
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::AcquireTask(
    grpc::ServerContext* context,
    const fl::coordinator::v1::AcquireTaskRequest* request,
    fl::coordinator::v1::ClientTrainingTask* response) {
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        return *rejection;
    }
    // Work Package H (worker status enforcement): SUSPENDED and REVOKED
    // workers both get "no new tasks" -- see docs/worker-suspension.md /
    // docs/worker-revocation.md. A worker with no identity_registry_
    // record at all (never signed a capability statement, or
    // identity_registry_ itself is null) is unaffected by this check --
    // that is the pre-existing, unchanged behavior for the legacy
    // unsigned-registration path.
    if (identity_registry_ != nullptr) {
        const auto identity_record = identity_registry_->find_by_worker_id(request->worker_id());
        if (identity_record.has_value()) {
            if (identity_record->registration_status == WorkerIdentityStatus::kRevoked) {
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "worker_id '" + request->worker_id() + "' is revoked");
            }
            if (identity_record->registration_status == WorkerIdentityStatus::kSuspended) {
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "worker_id '" + request->worker_id() + "' is suspended");
            }
            // Signing-Key Lifecycle slice (docs/signing-key-management.md's
            // "Signing-key policy": "No task assignment when the worker
            // has no valid signing key"). Only evaluated when
            // signing_key_registry_ is wired -- a worker that has never
            // gone through the registry-aware capability path (or a
            // coordinator without one configured) is unaffected.
            if (signing_key_registry_ != nullptr &&
                !signing_key_registry_->has_any_valid_key(request->worker_id(), now_unix_s())) {
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "worker_id '" + request->worker_id() +
                                        "' has no ACTIVE or GRACE_PERIOD signing key on record");
            }
        }
    }
    try {
        auto& run = manager_->get(request->run_id());
        run.advance(now_unix_s());
        const auto task = run.acquire_task(request->worker_id(), now_unix_s());
        response->set_task_available(task.has_value());
        if (task.has_value()) {
            response->set_task_id(task->task_id);
            response->set_lease_id(task->lease_id);
            // Coordinator-Signed Tasks slice, Work Package A finding:
            // lease_expires_at and attempt were both previously left at
            // their zero default -- nothing consumed them before, so
            // nothing signed them either. Fixed here as a direct
            // prerequisite for meaningful signing (task_payload_hash
            // and the signature below must bind the real lease
            // deadline and attempt count, not a placeholder), not a
            // broader unrelated rewrite. See docs/signed-coordinator-tasks.md.
            response->set_lease_expires_at(
                iso8601_from_unix_seconds(task->lease_expires_at_unix_s));
            response->set_attempt(task->attempt);
            auto* wire_task = response->mutable_task();
            wire_task->set_run_id(task->descriptor.run_id);
            wire_task->set_round_id(task->descriptor.round_id);
            wire_task->set_client_id(task->descriptor.client_id);
            wire_task->set_model_version(task->descriptor.model_version);
            wire_task->set_algorithm(fl::core::to_string(task->descriptor.algorithm));
            wire_task->set_dataset_reference(task->descriptor.dataset_reference);
            response->set_local_epochs(task->descriptor.local_epochs);
            response->set_batch_size(task->descriptor.batch_size);
            response->set_learning_rate(task->descriptor.learning_rate);
            response->set_momentum(task->descriptor.momentum);
            response->set_weight_decay(task->descriptor.weight_decay);
            response->set_fedprox_mu(task->descriptor.fedprox_mu);
            response->set_sample_level_dp_active(task->descriptor.sample_level_dp_active);
            if (task->descriptor.sample_level_dp_active) {
                auto* wire_sample_level = response->mutable_sample_level_privacy();
                wire_sample_level->set_noise_multiplier(
                    task->descriptor.sample_level_privacy.noise_multiplier);
                wire_sample_level->set_max_grad_norm(
                    task->descriptor.sample_level_privacy.max_grad_norm);
                wire_sample_level->set_target_delta(
                    task->descriptor.sample_level_privacy.target_delta);
                wire_sample_level->set_accountant(
                    accountant_type_to_wire(task->descriptor.sample_level_privacy.accountant));
                wire_sample_level->set_poisson_sampling(
                    task->descriptor.sample_level_privacy.poisson_sampling);
            }

            // Secure Cohort Handshake and Signed Roster Runtime slice
            // (docs/secure-cohort-handshake-foundation.md), work items 3/4/10.
            // Populates response->secure_aggregation *before* the signing
            // block below, since the coordinator's signature must cover
            // this binding too (secure_aggregation_configuration_hash,
            // coordinator_task_signing.cpp). A secure-aggregation session
            // is created at most once per (run_id, round_id), on the
            // first AcquireTask call that round sees -- deliberately not
            // hooked into RunInstance::begin_round() itself, since
            // run_manager.hpp/.cpp are part of the non-gRPC-gated
            // fl_coordinator library and cannot depend on
            // SecureAggregationSessionManager's generated-protobuf-typed
            // interface without breaking every local Windows build of
            // that library -- see the design doc's "mid-implementation
            // correction" section.
            if (secure_aggregation_manager_ != nullptr) {
                if (secure_aggregation_enabled_ &&
                    !secure_aggregation_manager_->has_session_for_run_round(
                        request->run_id(), task->descriptor.round_id)) {
                    const auto round = run.round_snapshot(task->descriptor.round_id);
                    if (round.has_value() && !round->selected_clients.empty()) {
                        fl::coordinator::v1::SecureAggregationSessionConfig secure_config;
                        secure_config.set_schema_version(1);
                        secure_config.set_protocol_version(1);
                        secure_config.set_provider(
                            fl::worker::v1::
                                SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL);
                        secure_config.set_session_id(request->run_id() + ":" +
                                                     std::to_string(task->descriptor.round_id));
                        secure_config.set_run_id(request->run_id());
                        secure_config.set_round_id(task->descriptor.round_id);
                        secure_config.set_model_version(task->descriptor.model_version);
                        secure_config.set_aggregation_algorithm("fedavg");
                        secure_config.set_cohort_size(round->selected_clients.size());
                        secure_config.set_minimum_cohort_size(round->selected_clients.size());
                        for (const auto& client_id : round->selected_clients) {
                            secure_config.add_ordered_participant_ids(client_id);
                        }
                        auto* fixed_point_profile = secure_config.mutable_fixed_point_profile();
                        fixed_point_profile->set_schema_version(1);
                        fixed_point_profile->set_rounding_rule("round_half_away_from_zero");
                        fixed_point_profile->set_scale_factor(1048576.0);
                        fixed_point_profile->set_max_input_magnitude(100.0);
                        fixed_point_profile->set_max_client_weight(1000000);
                        fixed_point_profile->set_max_cohort_size(10000);
                        fixed_point_profile->set_safety_margin(256);
                        secure_config.set_domain_profile("ring_mod_2_64");
                        secure_config.set_scale_factor(1048576.0);
                        // Masked Update Runtime and No-Dropout Secure
                        // FedAvg Finalization slice, Work Area C:
                        // max_absolute_update_bound/max_client_weight
                        // mirror the fixed-point profile's own bounds
                        // (the same values a worker must independently
                        // enforce before masking); max_aggregate_bound
                        // is the real proven worst-case aggregate
                        // magnitude for this exact profile
                        // (prove_domain_bounds -- the same function
                        // SecureAggregationSessionManager::create_session
                        // already calls internally to reject an unsafe
                        // profile; this is not a second, independent
                        // safety decision, only the same proof's output
                        // surfaced onto the wire for the worker to see).
                        secure_config.set_max_absolute_update_bound(
                            fixed_point_profile->max_input_magnitude());
                        secure_config.set_max_client_weight(
                            fixed_point_profile->max_client_weight());
                        const auto bounds_proof = fl::coordinator::prove_domain_bounds(
                            fl::coordinator::FixedPointEncodingProfile{});
                        secure_config.set_max_aggregate_bound(
                            bounds_proof.worst_case_aggregate_magnitude);
                        auto* crypto_profile = secure_config.mutable_cryptographic_profile();
                        crypto_profile->set_mask_generator_profile("chacha20_ietf");
                        crypto_profile->set_key_agreement_profile("x25519");
                        crypto_profile->set_key_derivation_profile("hkdf_sha256");
                        crypto_profile->set_digest_profile("sha256");
                        const double now = now_unix_s();
                        secure_config.set_session_created_at_unix_s(now);
                        secure_config.set_key_advertisement_deadline_unix_s(
                            now + secure_aggregation_key_advertisement_window_seconds_);
                        secure_config.set_masked_update_deadline_unix_s(
                            now + secure_aggregation_key_advertisement_window_seconds_ +
                            secure_aggregation_masked_update_window_seconds_);
                        secure_config.set_session_expiry_unix_s(
                            secure_config.masked_update_deadline_unix_s());
                        // Work Areas Z/AB: decided once per session,
                        // never re-derived per participant -- this
                        // run's algorithm/privacy mode do not change
                        // mid-round. Only FedAvg with no privacy mode
                        // or SAMPLE_LEVEL DP is supported under secure
                        // aggregation this slice; USER_LEVEL/HYBRID
                        // (whole-update clipping at the coordinator is
                        // structurally incompatible with hidden
                        // individual updates) and adaptive clipping
                        // (clipping indicators are not themselves
                        // securely aggregated yet) are explicitly
                        // rejected, never silently downgraded.
                        if (run.algorithm() != fl::core::AggregationAlgorithm::kFedAvg) {
                            secure_config.set_privacy_mode_compatible(false);
                            secure_config.set_privacy_incompatibility_reason(
                                "SECURE_AGGREGATION_ALGORITHM_UNSUPPORTED: only fedavg is "
                                "supported under "
                                "secure aggregation this slice");
                        } else if (run.adaptive_clipping_enabled() &&
                                   run.privacy_mode() != fl::core::PrivacyMode::kUserLevelDp &&
                                   run.privacy_mode() != fl::core::PrivacyMode::kHybridDp) {
                            // Secure Adaptive Clipping with Private
                            // Indicator Aggregation slice: adaptive
                            // clipping requires a user-level clipping
                            // layer to adapt -- it is meaningless for
                            // NONE/SAMPLE_LEVEL. When the mode IS
                            // USER_LEVEL_DP/HYBRID_DP, this branch does
                            // NOT fire and adaptive clipping is instead
                            // validated inside the shared ladder below
                            // (see docs/secure-adaptive-clipping-semantics.md
                            // section 13) -- the blanket rejection this
                            // used to be, regardless of privacy mode,
                            // is lifted.
                            secure_config.set_privacy_mode_compatible(false);
                            secure_config.set_privacy_incompatibility_reason(
                                "SECURE_ADAPTIVE_CLIPPING_UNSUPPORTED_PRIVACY_MODE: adaptive "
                                "clipping "
                                "requires USER_LEVEL_DP or HYBRID_DP as the active privacy mode");
                        } else if (run.privacy_mode() == fl::core::PrivacyMode::kUserLevelDp ||
                                   run.privacy_mode() == fl::core::PrivacyMode::kHybridDp) {
                            // Secure User-Level Differential Privacy
                            // Runtime slice (Work Areas D/E/H/N), extended
                            // by the Secure Hybrid Differential Privacy
                            // Runtime slice: kUserLevelDp and kHybridDp
                            // share the identical user-level clip/
                            // quantization/budget validation ladder below
                            // -- kHybridDp additionally validates its
                            // sample-level sub-configuration up front (a
                            // real, independent check, not a relaxed
                            // version of it) and uses SECURE_HYBRID_DP_*
                            // reason codes/events instead of
                            // SECURE_USER_LEVEL_DP_* ones. See
                            // docs/secure-hybrid-dp-semantics.md section
                            // 5 for why no new combined "hybrid
                            // configuration" message/hash is introduced
                            // here -- both sub-configurations are already
                            // independently, cryptographically bound into
                            // this same signed task via
                            // privacy_configuration_hash and
                            // secure_user_level_dp_configuration_hash.
                            const bool is_hybrid =
                                run.privacy_mode() == fl::core::PrivacyMode::kHybridDp;
                            const std::string reason_prefix =
                                is_hybrid ? "SECURE_HYBRID_DP" : "SECURE_USER_LEVEL_DP";
                            bool sample_config_ok = true;
                            if (is_hybrid) {
                                const auto& sample_level = run.sample_level_privacy();
                                if (!std::isfinite(sample_level.noise_multiplier) ||
                                    sample_level.noise_multiplier <= 0.0 ||
                                    !std::isfinite(sample_level.max_grad_norm) ||
                                    sample_level.max_grad_norm <= 0.0 ||
                                    !std::isfinite(sample_level.target_delta) ||
                                    sample_level.target_delta <= 0.0 ||
                                    sample_level.target_delta >= 1.0) {
                                    sample_config_ok = false;
                                    secure_config.set_privacy_mode_compatible(false);
                                    secure_config.set_privacy_incompatibility_reason(
                                        reason_prefix +
                                        "_INVALID_SAMPLE_CONFIGURATION: noise_multiplier, "
                                        "max_grad_norm, "
                                        "and target_delta must all be finite and positive "
                                        "(target_delta "
                                        "additionally < 1)");
                                }
                            }
                            if (sample_config_ok) {
                                if (run.weighting() != fl::core::WeightingStrategyType::kUniform) {
                                    secure_config.set_privacy_mode_compatible(false);
                                    secure_config.set_privacy_incompatibility_reason(
                                        reason_prefix +
                                        "_VARIABLE_WEIGHT_UNSUPPORTED: secure aggregation requires "
                                        "uniform "
                                        "weighting (fixed weight 1 per user) -- "
                                        "variable/sample-count "
                                        "weighting changes user-level sensitivity");
                                } else {
                                    const auto& user_level = run.user_level_privacy();
                                    // Secure Adaptive Clipping with
                                    // Private Indicator Aggregation
                                    // slice: the bound this round's
                                    // tasks are signed with is the
                                    // adaptive controller's current
                                    // value when adaptive clipping is
                                    // active for this run -- a single
                                    // substitution point that
                                    // automatically makes every
                                    // downstream quantization-margin/
                                    // sensitivity/wire-binding
                                    // computation below correctly
                                    // adaptive-aware with no further
                                    // changes (see the semantics doc
                                    // section 13).
                                    const bool adaptive_active = run.adaptive_clipping_enabled();
                                    const double clip_norm =
                                        adaptive_active ? run.current_adaptive_clip_bound()
                                                        : user_level.initial_clipping_bound;
                                    if (!std::isfinite(clip_norm) || clip_norm <= 0.0 ||
                                        !std::isfinite(user_level.noise_multiplier) ||
                                        user_level.noise_multiplier <= 0.0 ||
                                        !std::isfinite(user_level.target_delta) ||
                                        user_level.target_delta <= 0.0 ||
                                        user_level.target_delta >= 1.0) {
                                        secure_config.set_privacy_mode_compatible(false);
                                        secure_config.set_privacy_incompatibility_reason(
                                            reason_prefix +
                                            "_INVALID_CONFIGURATION: clip_norm, noise_multiplier, "
                                            "and "
                                            "target_delta must all be finite and positive "
                                            "(target_delta "
                                            "additionally < 1)");
                                    } else {
                                        std::uint64_t total_elements = 0;
                                        for (const auto& descriptor : run.manifest().tensors) {
                                            std::uint64_t element_count = 1;
                                            for (const auto dim : descriptor.shape)
                                                element_count *= dim;
                                            total_elements += element_count;
                                        }
                                        const fl::coordinator::FixedPointEncodingProfile
                                            secure_profile{};
                                        const double margin =
                                            fl::coordinator::compute_quantization_margin(
                                                total_elements, secure_profile);
                                        const double effective_sensitivity =
                                            fl::coordinator::compute_effective_sensitivity(
                                                clip_norm, margin);
                                        if (!std::isfinite(margin) ||
                                            !std::isfinite(effective_sensitivity) ||
                                            effective_sensitivity >=
                                                secure_profile.max_input_magnitude) {
                                            secure_config.set_privacy_mode_compatible(false);
                                            secure_config.set_privacy_incompatibility_reason(
                                                reason_prefix +
                                                "_UNSAFE_QUANTIZATION_MARGIN: clip_norm plus the "
                                                "proven "
                                                "worst-case quantization margin does not fit under "
                                                "this "
                                                "profile's max_input_magnitude -- lower the clip "
                                                "norm or the "
                                                "model size, or raise the profile's magnitude "
                                                "bound");
                                        } else if (
                                            user_level.epsilon_budget > 0.0 &&
                                            run.project_user_level_epsilon_after_one_more_step() >=
                                                user_level.epsilon_budget) {
                                            // Work Area N's "reserve": a
                                            // non-mutating pre-check,
                                            // refused here (before any
                                            // worker ever trains) rather
                                            // than only at finalize time.
                                            // For kHybridDp this reserves
                                            // only the user-level budget
                                            // -- the sample-level budget
                                            // is enforced entirely
                                            // worker-side (unchanged,
                                            // same as plain sample-level
                                            // DP), see the semantics doc.
                                            secure_config.set_privacy_mode_compatible(false);
                                            secure_config.set_privacy_incompatibility_reason(
                                                reason_prefix +
                                                "_BUDGET_EXHAUSTED: this round's projected epsilon "
                                                "would "
                                                "meet or exceed the configured epsilon_budget");
                                        } else if (adaptive_active &&
                                                   (!std::isfinite(
                                                        run.adaptive_clipping_config().min_clip) ||
                                                    !std::isfinite(
                                                        run.adaptive_clipping_config().max_clip) ||
                                                    run.adaptive_clipping_config().min_clip <=
                                                        0.0 ||
                                                    run.adaptive_clipping_config().min_clip >
                                                        run.adaptive_clipping_config().max_clip ||
                                                    clip_norm <
                                                        run.adaptive_clipping_config().min_clip ||
                                                    clip_norm >
                                                        run.adaptive_clipping_config().max_clip ||
                                                    !std::isfinite(run.adaptive_clipping_config()
                                                                       .target_quantile) ||
                                                    run.adaptive_clipping_config()
                                                            .target_quantile <= 0.0 ||
                                                    run.adaptive_clipping_config()
                                                            .target_quantile >= 1.0 ||
                                                    !std::isfinite(run.adaptive_clipping_config()
                                                                       .clip_learning_rate) ||
                                                    run.adaptive_clipping_config()
                                                            .clip_learning_rate <= 0.0 ||
                                                    !std::isfinite(run.adaptive_clipping_config()
                                                                       .count_noise_multiplier) ||
                                                    run.adaptive_clipping_config()
                                                            .count_noise_multiplier <= 0.0)) {
                                            // Secure Adaptive Clipping
                                            // with Private Indicator
                                            // Aggregation slice, Work
                                            // Area D: min_clip <=
                                            // current bound <= max_clip
                                            // (the current bound is
                                            // always adaptive_active's
                                            // clip_norm above, already
                                            // maintained inside
                                            // [min_clip, max_clip] by
                                            // AdaptiveClipController's
                                            // own clamp -- this is a
                                            // defensive re-check, not
                                            // the primary enforcement),
                                            // strict (0,1) target
                                            // quantile, positive finite
                                            // learning rate, positive
                                            // finite indicator noise
                                            // multiplier.
                                            secure_config.set_privacy_mode_compatible(false);
                                            secure_config.set_privacy_incompatibility_reason(
                                                "SECURE_ADAPTIVE_CLIPPING_INVALID_CONFIGURATION: "
                                                "min_clip, "
                                                "max_clip, target_quantile, learning_rate, and "
                                                "count_noise_multiplier must all be finite and "
                                                "within their "
                                                "required ranges, and the current bound must lie "
                                                "in "
                                                "[min_clip, max_clip]");
                                        } else {
                                            secure_config.set_privacy_mode_compatible(true);
                                            secure_config.set_secure_user_level_dp_active(true);
                                            secure_config.set_secure_user_level_adjacency_model(
                                                fl::coordinator::v1::
                                                    SECURE_USER_LEVEL_ADJACENCY_MODEL_ADD_REMOVE_ONE);
                                            secure_config.set_secure_user_level_clip_norm(
                                                clip_norm);
                                            secure_config.set_secure_user_level_quantization_margin(
                                                margin);
                                            secure_config
                                                .set_secure_user_level_effective_sensitivity(
                                                    effective_sensitivity);
                                            secure_config.set_secure_user_level_noise_multiplier(
                                                user_level.noise_multiplier);
                                            secure_config.set_secure_user_level_target_delta(
                                                user_level.target_delta);
                                            secure_config.set_secure_user_level_max_epsilon(
                                                user_level.epsilon_budget);
                                            secure_config.set_secure_user_level_fixed_weight(1);
                                            secure_config.set_secure_user_level_sampling_assumption(
                                                fl::coordinator::v1::
                                                    SECURE_USER_LEVEL_SAMPLING_ASSUMPTION_NO_AMPLIFICATION);
                                            if (adaptive_active) {
                                                const auto& adaptive =
                                                    run.adaptive_clipping_config();
                                                secure_config.set_secure_adaptive_clipping_active(
                                                    true);
                                                secure_config
                                                    .set_secure_adaptive_clipping_indicator_definition(
                                                        fl::privacy::v1::
                                                            SECURE_ADAPTIVE_CLIPPING_INDICATOR_DEFINITION_OVER_THRESHOLD);
                                                secure_config
                                                    .set_secure_adaptive_clipping_current_bound(
                                                        clip_norm);
                                                secure_config
                                                    .set_secure_adaptive_clipping_min_bound(
                                                        adaptive.min_clip);
                                                secure_config
                                                    .set_secure_adaptive_clipping_max_bound(
                                                        adaptive.max_clip);
                                                secure_config
                                                    .set_secure_adaptive_clipping_target_quantile(
                                                        adaptive.target_quantile);
                                                secure_config
                                                    .set_secure_adaptive_clipping_learning_rate(
                                                        adaptive.clip_learning_rate);
                                                secure_config
                                                    .set_secure_adaptive_clipping_indicator_noise_multiplier(
                                                        adaptive.count_noise_multiplier);
                                                secure_config
                                                    .set_secure_adaptive_clipping_clip_state_step_count(
                                                        run.adaptive_clip_state_step_count());
                                            }
                                        }
                                    }
                                }
                            }
                            // Secure User-Level DP Operations,
                            // Observability, and Release Evidence slice,
                            // Work Area D: distinct from the generic
                            // SECURE_AGGREGATION_SESSION_CREATED/ABORTED
                            // events emitted below for every secure
                            // session regardless of privacy mode -- these
                            // events exist specifically so a
                            // privacy-operations dashboard can separate
                            // "budget exhausted" from "configuration
                            // rejected for another reason" from "ordinary
                            // secure-aggregation session churn," which
                            // the generic pair cannot distinguish. The
                            // Secure Hybrid DP slice reuses this same
                            // block, picking SecureHybridDp* event types
                            // instead of SecureUserLevelDp* ones when
                            // is_hybrid.
                            if (security_event_journal_ != nullptr) {
                                SecurityEvent dp_event;
                                dp_event.source_service = "coordinator";
                                dp_event.source_component = "coordinator_service";
                                dp_event.actor_type = SecurityActorType::kCoordinator;
                                dp_event.subject_type =
                                    SecuritySubjectType::kSecureAggregationSession;
                                dp_event.safe_subject_id = secure_config.session_id();
                                dp_event.run_id = request->run_id();
                                dp_event.round_id = task->descriptor.round_id;
                                if (secure_config.privacy_mode_compatible()) {
                                    dp_event.event_type =
                                        is_hybrid ? SecurityEventType::
                                                        kSecureHybridDpConfigurationAccepted
                                                  : SecurityEventType::
                                                        kSecureUserLevelDpConfigurationAccepted;
                                    dp_event.severity = default_severity(dp_event.event_type);
                                    dp_event.outcome = SecurityOutcome::kAccepted;
                                    security_event_journal_->emit(dp_event);
                                    // Work Area N's "reserve": the
                                    // non-mutating budget pre-check just
                                    // above having passed is what
                                    // "reserved" means in this slice's
                                    // design (see the semantics doc
                                    // section 12) -- a second, distinct
                                    // event from configuration-accepted
                                    // because an operator dashboard needs
                                    // to count budget reservations
                                    // separately from configuration
                                    // acceptance in general.
                                    dp_event.event_type =
                                        is_hybrid
                                            ? SecurityEventType::kSecureHybridDpUserBudgetReserved
                                            : SecurityEventType::kSecureUserLevelDpBudgetReserved;
                                    security_event_journal_->emit(dp_event);
                                    // Secure Adaptive Clipping with
                                    // Private Indicator Aggregation
                                    // slice: an additional, distinct
                                    // event -- adaptive clipping is a
                                    // composition on top of user-level/
                                    // hybrid, not a replacement, so both
                                    // the (already-emitted) layer event
                                    // above AND this one fire together
                                    // when adaptive is active.
                                    if (secure_config.secure_adaptive_clipping_active()) {
                                        dp_event.event_type = SecurityEventType::
                                            kSecureAdaptiveClippingConfigurationAccepted;
                                        dp_event.severity = default_severity(dp_event.event_type);
                                        security_event_journal_->emit(std::move(dp_event));
                                    }
                                } else {
                                    const bool budget_exhausted =
                                        secure_config.privacy_incompatibility_reason().rfind(
                                            reason_prefix + "_BUDGET_EXHAUSTED", 0) == 0;
                                    // Hybrid has no separate "budget
                                    // exhausted" event type this slice
                                    // (bounded event vocabulary, see the
                                    // audit doc's scope statement) --
                                    // every hybrid rejection, including
                                    // exhaustion, uses the one
                                    // ConfigurationRejected event; the
                                    // real, structured machine-readable
                                    // reason (with the same
                                    // "_BUDGET_EXHAUSTED" prefix
                                    // budget_exhausted just checked) is
                                    // still always present in
                                    // reason_code below either way.
                                    dp_event.event_type =
                                        is_hybrid ? SecurityEventType::
                                                        kSecureHybridDpConfigurationRejected
                                        : budget_exhausted
                                            ? SecurityEventType::kSecureUserLevelDpBudgetExhausted
                                            : SecurityEventType::
                                                  kSecureUserLevelDpConfigurationRejected;
                                    dp_event.severity = default_severity(dp_event.event_type);
                                    dp_event.outcome = SecurityOutcome::kRejected;
                                    dp_event.reason_code =
                                        secure_config.privacy_incompatibility_reason();
                                    // Secure Adaptive Clipping with
                                    // Private Indicator Aggregation
                                    // slice: an additional, distinct
                                    // event for a rejection reason this
                                    // slice's own validation ladder
                                    // produced (SECURE_ADAPTIVE_CLIPPING_*
                                    // -prefixed), same "layer event AND
                                    // adaptive event both fire" reasoning
                                    // as the accepted branch above.
                                    if (dp_event.reason_code.rfind("SECURE_ADAPTIVE_CLIPPING", 0) ==
                                        0) {
                                        security_event_journal_->emit(dp_event);
                                        dp_event.event_type = SecurityEventType::
                                            kSecureAdaptiveClippingConfigurationRejected;
                                        dp_event.severity = default_severity(dp_event.event_type);
                                        security_event_journal_->emit(std::move(dp_event));
                                    } else {
                                        security_event_journal_->emit(std::move(dp_event));
                                    }
                                }
                            }
                        } else {
                            secure_config.set_privacy_mode_compatible(true);
                        }
                        try {
                            secure_aggregation_manager_->create_session(secure_config, now);
                            if (security_event_journal_ != nullptr) {
                                SecurityEvent event;
                                event.source_service = "coordinator";
                                event.source_component = "secure_aggregation_session_manager";
                                event.event_type =
                                    SecurityEventType::kSecureAggregationSessionCreated;
                                event.severity = default_severity(event.event_type);
                                event.actor_type = SecurityActorType::kCoordinator;
                                event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                                event.safe_subject_id = secure_config.session_id();
                                event.run_id = request->run_id();
                                event.round_id = task->descriptor.round_id;
                                event.outcome = SecurityOutcome::kCompleted;
                                event.safe_details["cohort_size"] =
                                    std::to_string(round->selected_clients.size());
                                security_event_journal_->emit(std::move(event));
                            }
                            // Work Areas Z/AB: a session created with an
                            // incompatible algorithm/privacy mode is
                            // never left sitting in COHORT_FORMING
                            // forever (nobody could ever complete it) --
                            // aborted immediately, with the same
                            // structured reason surfaced on every
                            // participant's task binding above.
                            if (!secure_config.privacy_mode_compatible()) {
                                secure_aggregation_manager_->abort(
                                    secure_config.session_id(),
                                    fl::coordinator::v1::
                                        SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE,
                                    now);
                                if (security_event_journal_ != nullptr) {
                                    SecurityEvent abort_event;
                                    abort_event.source_service = "coordinator";
                                    abort_event.source_component =
                                        "secure_aggregation_session_manager";
                                    abort_event.event_type =
                                        SecurityEventType::kSecureAggregationSessionAborted;
                                    abort_event.severity = default_severity(abort_event.event_type);
                                    abort_event.actor_type = SecurityActorType::kCoordinator;
                                    abort_event.subject_type =
                                        SecuritySubjectType::kSecureAggregationSession;
                                    abort_event.safe_subject_id = secure_config.session_id();
                                    abort_event.run_id = request->run_id();
                                    abort_event.round_id = task->descriptor.round_id;
                                    abort_event.outcome = SecurityOutcome::kRejected;
                                    abort_event.safe_details["abort_reason"] =
                                        secure_config.privacy_incompatibility_reason();
                                    security_event_journal_->emit(std::move(abort_event));
                                }
                            }
                        } catch (const std::exception& error) {
                            // Secure aggregation being unavailable for this
                            // round must never block ordinary task
                            // dispatch -- logged, not propagated as an RPC
                            // failure.
                            std::cerr << "secure aggregation session creation failed for run "
                                      << request->run_id() << " round " << task->descriptor.round_id
                                      << ": " << error.what() << "\n";
                        }
                    }
                }
                // Work item 15: cheap to call on every AcquireTask (this
                // RPC is already called frequently, matching
                // RunInstance::advance()'s own "safe to call repeatedly"
                // convention) -- aborts any session whose key-advertisement
                // deadline has passed with an incomplete cohort.
                for (const auto& expired_session_id :
                     secure_aggregation_manager_->sweep_expired_advertisement_deadlines(
                         now_unix_s())) {
                    if (security_event_journal_ != nullptr) {
                        SecurityEvent event;
                        event.source_service = "coordinator";
                        event.source_component = "secure_aggregation_session_manager";
                        event.event_type = SecurityEventType::kSecureAggregationSessionAborted;
                        event.severity = default_severity(event.event_type);
                        event.actor_type = SecurityActorType::kCoordinator;
                        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                        event.safe_subject_id = expired_session_id;
                        event.outcome = SecurityOutcome::kCompleted;
                        event.reason_code = "key_advertisement_deadline_exceeded";
                        security_event_journal_->emit(std::move(event));
                    }
                }
                // Work Area S: the masked-update-collection analogue of
                // the sweep immediately above -- same "cheap to call on
                // every AcquireTask" convention.
                for (const auto& expired_session_id :
                     secure_aggregation_manager_->sweep_expired_masked_update_deadlines(
                         now_unix_s())) {
                    if (security_event_journal_ != nullptr) {
                        SecurityEvent event;
                        event.source_service = "coordinator";
                        event.source_component = "secure_aggregation_session_manager";
                        event.event_type = SecurityEventType::kSecureAggregationSessionAborted;
                        event.severity = default_severity(event.event_type);
                        event.actor_type = SecurityActorType::kCoordinator;
                        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                        event.safe_subject_id = expired_session_id;
                        event.outcome = SecurityOutcome::kCompleted;
                        event.reason_code = "masked_update_deadline_exceeded";
                        security_event_journal_->emit(std::move(event));
                    }
                }
                const auto binding = secure_aggregation_manager_->find_binding_for_participant(
                    request->run_id(), task->descriptor.round_id, request->worker_id());
                if (binding.has_value()) {
                    *response->mutable_secure_aggregation() = *binding;
                }
            }

            // Coordinator-Signed Tasks slice (docs/signed-coordinator-tasks.md):
            // only attaches signed_task when the coordinator actually
            // has a signing identity AND an ACTIVE registry key --
            // optional, same backward-compatible convention as every
            // other enforcement point this session. A coordinator
            // configured with an identity but whose registry has no
            // ACTIVE key (a real misconfiguration) fails the request
            // rather than silently issuing an unsigned task, since a
            // deployment that opted into signing has no safe unsigned
            // fallback to offer.
            if (coordinator_active_identity_ != nullptr) {
                if (coordinator_signing_key_registry_ == nullptr ||
                    !coordinator_signing_key_registry_->active_key(now_unix_s()).has_value()) {
                    return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                                        "coordinator signing is enabled but has no ACTIVE signing "
                                        "key on record");
                }
                // A stable snapshot for the duration of this call --
                // Security Administration slice: a concurrent rotation
                // on another thread must not be observed mid-swap (see
                // CoordinatorActiveIdentityStore's header comment).
                const auto active_identity = coordinator_active_identity_->current();
                SignCoordinatorTaskParams params;
                params.worker_id = request->worker_id();
                params.task_id = task->task_id;
                params.lease_id = task->lease_id;
                params.attempt = task->attempt;
                params.issued_at = now_unix_s();
                params.expires_at = task->lease_expires_at_unix_s;
                params.nonce = generate_task_nonce();
                params.sequence_number = coordinator_task_sequence_store_ != nullptr
                                             ? coordinator_task_sequence_store_->next_sequence(
                                                   active_identity->key_id, request->worker_id())
                                             : 0;
                // Computed into a local message first, then copied in --
                // never passes *response (the hash input) and
                // response->mutable_signed_task() (a field of that same
                // message being written) to the same call, which would
                // make argument-evaluation order/aliasing a concern.
                fl::coordinator::v1::SignedCoordinatorTask signed_task;
                const auto sign_result =
                    sign_coordinator_task(*response, params, *active_identity, signed_task);
                if (!sign_result.ok) {
                    return grpc::Status(grpc::StatusCode::INTERNAL,
                                        "failed to sign outgoing task: " + sign_result.reason);
                }
                *response->mutable_signed_task() = std::move(signed_task);
            }
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::ReportTaskProgress(
    grpc::ServerContext* context,
    const fl::coordinator::v1::TaskProgressRequest* request,
    fl::coordinator::v1::TaskProgressResponse* response) {
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        return *rejection;
    }
    // Work Package N: real implementation (delegating to the
    // pre-existing RunInstance::report_task_progress domain method),
    // deliberately without a signed-envelope requirement -- see this
    // method's declaration comment in coordinator_service.hpp for why.
    // report_task_progress needs a run_id to find the right RunInstance,
    // but TaskProgressRequest carries no run_id field on the wire
    // (worker_id/task_id/lease_id only) -- since a worker can only ever
    // hold one active lease at a time (TaskDispatcher::acquire's "one
    // active task per worker" invariant) and every run this coordinator
    // process knows about is searched, this is unambiguous: at most one
    // run's dispatcher will actually recognize this worker_id/task_id/
    // lease_id triple.
    try {
        bool found = false;
        for (const auto& run_id : manager_->list_run_ids()) {
            auto& run = manager_->get(run_id);
            try {
                run.report_task_progress(
                    request->worker_id(), request->task_id(), request->lease_id());
                found = true;
                break;
            } catch (const std::exception&) {
                // Not this run (no active round at all, or this run's
                // dispatcher doesn't recognize the worker_id/task_id/
                // lease_id triple -- RunManagerError and
                // TaskDispatcherError respectively) -- try the next one.
                continue;
            }
        }
        response->set_acknowledged(found);
        response->set_cancel_requested(false);
        if (!found) {
            return grpc::Status(grpc::StatusCode::NOT_FOUND,
                                "no active run recognizes this worker_id/task_id/lease_id");
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::SubmitClientResult(
    grpc::ServerContext* context,
    const fl::coordinator::v1::SubmitClientResultRequest* request,
    fl::coordinator::v1::SubmitClientResultResponse* response) {
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        return *rejection;
    }

    // Signed Client Results and Worker Lifecycle Enforcement slice,
    // Work Packages D/E (docs/signed-client-results.md): identity/
    // status/signature/replay verification all happen here, before any
    // domain processing (aggregation, privacy-ledger writes) below --
    // matching Heartbeat's already-established pipeline shape.
    std::optional<WorkerIdentityRecord> identity_record;
    if (identity_registry_ != nullptr) {
        identity_record = identity_registry_->find_by_worker_id(request->worker_id());
        if (identity_record.has_value() &&
            identity_record->registration_status == WorkerIdentityStatus::kRevoked) {
            response->set_rejection_code("worker_revoked");
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "worker_id '" + request->worker_id() + "' is revoked");
        }
    }

    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area P: a worker must never be able to bypass masking
    // by simply submitting a plaintext ClientResult for a round this
    // coordinator bound to a secure aggregation session. The one
    // deliberate exception is SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE
    // (Work Areas Z/AB): that abort reason means the coordinator itself
    // decided, at session-creation time, that secure aggregation does
    // not apply to this round's algorithm/privacy configuration and the
    // round deliberately falls back to ordinary unmasked training -- see
    // AcquireTask's session-creation block. Every other terminal state
    // (COMPLETED, or ABORTED/FAILED for any other reason) still
    // forbids cleartext: a completed secure round has already advanced
    // the model via the masked path, and a non-privacy-incompatible
    // abort (deadline exceeded, dropout, cohort mismatch, ...) must
    // fail the round closed rather than silently accept an unmasked
    // substitute -- per the Threshold Secret-Sharing Restriction's
    // required frozen-cohort failure behavior (no partial-cohort
    // finalization, no fallback decode; a retry needs an entirely new
    // secure session).
    if (secure_aggregation_manager_ != nullptr) {
        const auto secure_status = secure_aggregation_manager_->find_status_for_run_round(
            request->result().run_id(), request->result().round_id());
        if (secure_status.has_value()) {
            const bool privacy_incompatible_fallback =
                secure_status->state() ==
                    fl::coordinator::v1::SECURE_AGGREGATION_SESSION_STATE_ABORTED &&
                secure_status->abort_reason() ==
                    fl::coordinator::v1::SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE;
            if (!privacy_incompatible_fallback) {
                response->set_rejection_code("cleartext_result_forbidden_for_secure_round");
                if (security_event_journal_ != nullptr) {
                    SecurityEvent event;
                    event.source_service = "coordinator";
                    event.source_component = "coordinator_service";
                    event.event_type = SecurityEventType::kClientResultRejected;
                    event.severity = default_severity(event.event_type);
                    event.actor_type = SecurityActorType::kWorker;
                    event.safe_actor_id = request->worker_id();
                    event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                    event.safe_subject_id = secure_status->session_id();
                    event.worker_id = request->worker_id();
                    event.run_id = request->result().run_id();
                    event.outcome = SecurityOutcome::kRejected;
                    event.reason_code = "cleartext_result_forbidden_for_secure_round";
                    security_event_journal_->emit(std::move(event));
                }
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "run '" + request->result().run_id() + "' round " +
                        std::to_string(request->result().round_id()) +
                        " is bound to secure aggregation session '" + secure_status->session_id() +
                        "' -- cleartext SubmitClientResult is forbidden for this round");
            }
        }
    }

    ReplayCandidate replay_candidate;
    bool have_replay_candidate = false;
    if (request->has_envelope()) {
        const auto& envelope = request->envelope();
        if (!identity_record.has_value()) {
            response->set_rejection_code("unknown_worker");
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "unknown worker_id: " + request->worker_id());
        }
        const auto resolved_key = resolve_signing_key(signing_key_registry_,
                                                      *identity_record,
                                                      envelope.signing_key_id(),
                                                      SignedMessageKind::kClientResult,
                                                      now_unix_s());
        if (!resolved_key.ok) {
            response->set_rejection_code(resolved_key.rejection_code);
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, resolved_key.reason);
        }
        const auto hash_result = client_result_payload_hash_input(*request);
        if (!hash_result.ok) {
            response->set_rejection_code("payload_hash_mismatch");
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "signed result rejected: " + hash_result.reason);
        }
        const auto verification = verify_signed_envelope(
            envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_CLIENT_RESULT),
            hash_result.hash_input,
            resolved_key.public_key_hex,
            now_unix_s(),
            /*future_issued_tolerance_seconds=*/30.0);
        if (!verification.valid) {
            response->set_rejection_code(verification.rejection_code);
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "signed result rejected: " + verification.reason);
        }
        if (replay_store_ != nullptr) {
            replay_candidate.worker_id = request->worker_id();
            replay_candidate.signing_key_id = envelope.signing_key_id();
            replay_candidate.message_stream = MessageStream::kClientResult;
            replay_candidate.sequence_number = envelope.sequence_number();
            replay_candidate.nonce = envelope.nonce();
            replay_candidate.now_unix_s = now_unix_s();
            const double window = envelope.expires_at() - envelope.issued_at();
            replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
            const auto replay_decision = replay_store_->validate(replay_candidate);
            if (!replay_decision.accepted) {
                response->set_rejection_code(to_string(replay_decision.reason));
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed result rejected: " + replay_decision.detail);
            }
            have_replay_candidate = true;
        }
    } else if (!allow_unsigned_client_results_) {
        response->set_rejection_code("envelope_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "a signed envelope is required for SubmitClientResult");
    } else {
        // Explicit, opt-in-only development compatibility path -- see
        // docs/signed-client-results.md. Emitted at WARNING severity
        // (not silent) every time it is actually exercised.
        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator level=WARNING event=unsigned_client_result_accepted "
                     "worker_id="
                  << request->worker_id()
                  << " reason=\"FL_ALLOW_UNSIGNED_CLIENT_RESULTS is set -- this coordinator is "
                     "running in an explicit development-compatibility mode that is unsafe for "
                     "private/production runs\""
                  << std::endl;
    }

    // Privacy Record Authenticity, Signing-Key Lifecycle, and
    // Coordinator-Signed Tasks slice, Work Package E
    // (docs/signed-privacy-records.md): an independently signed sample
    // privacy record is verified here -- before the ledger entry it
    // accompanies is ever appended (below, inside run.submit_client_result)
    // -- whenever this submission carries sample_level_privacy at all.
    // "Do not rely only on the outer signed client-result envelope"
    // (Work Package A) means this pipeline is separate from, and in
    // addition to, the envelope verification above.
    ReplayCandidate privacy_replay_candidate;
    bool have_privacy_replay_candidate = false;
    MonotonicityCandidate monotonicity_candidate;
    bool have_monotonicity_candidate = false;
    if (request->has_sample_level_privacy()) {
        if (request->has_privacy_record_envelope() && request->has_privacy_record_payload()) {
            const auto& privacy_envelope = request->privacy_record_envelope();
            const auto& payload = request->privacy_record_payload();
            if (!identity_record.has_value()) {
                response->set_rejection_code("unknown_worker");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "unknown worker_id: " + request->worker_id());
            }
            // Work Package K (docs/signed-privacy-records.md's "Result-
            // to-Privacy Key Consistency"): the privacy record must be
            // signed by the SAME signing key as the outer client
            // result -- a privacy record signed by a different
            // (even if independently valid) key on the same worker is
            // rejected, closing the case of a worker mixing keys
            // across the two independently-verified signatures within
            // one submission.
            if (request->has_envelope() &&
                privacy_envelope.signing_key_id() != request->envelope().signing_key_id()) {
                response->set_rejection_code("privacy_record_key_mismatch");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "privacy record signing_key_id does not match the outer client result's "
                    "signing_key_id");
            }
            const auto resolved_privacy_key = resolve_signing_key(signing_key_registry_,
                                                                  *identity_record,
                                                                  privacy_envelope.signing_key_id(),
                                                                  SignedMessageKind::kPrivacyRecord,
                                                                  now_unix_s());
            if (!resolved_privacy_key.ok) {
                response->set_rejection_code(resolved_privacy_key.rejection_code);
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    resolved_privacy_key.reason);
            }
            const auto privacy_hash_result = sample_privacy_record_payload_hash_input(payload);
            if (!privacy_hash_result.ok) {
                response->set_rejection_code("privacy_payload_hash_mismatch");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed privacy record rejected: " + privacy_hash_result.reason);
            }
            const auto privacy_verification = verify_signed_envelope(
                privacy_envelope,
                static_cast<int>(
                    fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD),
                privacy_hash_result.hash_input,
                resolved_privacy_key.public_key_hex,
                now_unix_s(),
                /*future_issued_tolerance_seconds=*/30.0);
            if (!privacy_verification.valid) {
                response->set_rejection_code(privacy_verification.rejection_code);
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed privacy record rejected: " + privacy_verification.reason);
            }

            // Work Package 6 ("bind privacy records to signed client
            // results"): the signed record's own claimed identity/values
            // must exactly match the plaintext SampleLevelLedgerEntry
            // this submission is about to append to the ledger --
            // otherwise a worker could sign one epsilon and submit a
            // different one in the field the coordinator actually
            // persists and relays.
            const auto& wire_entry = request->sample_level_privacy();
            const bool binding_ok = payload.worker_id() == request->worker_id() &&
                                    payload.run_id() == wire_entry.run_id() &&
                                    payload.round_id() == wire_entry.round_id() &&
                                    payload.client_id() == wire_entry.client_id() &&
                                    payload.task_id() == request->task_id() &&
                                    payload.epsilon() == wire_entry.epsilon() &&
                                    payload.delta() == wire_entry.delta() &&
                                    payload.noise_multiplier() == wire_entry.noise_multiplier() &&
                                    payload.sample_rate() == wire_entry.sample_rate() &&
                                    payload.accountant_step() == wire_entry.steps() &&
                                    static_cast<int>(payload.accountant_type()) ==
                                        static_cast<int>(wire_entry.accountant());
            if (!binding_ok) {
                response->set_rejection_code("privacy_record_binding_mismatch");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed privacy record does not match the plaintext "
                                    "sample_level_privacy entry submitted alongside it");
            }

            if (replay_store_ != nullptr) {
                privacy_replay_candidate.worker_id = request->worker_id();
                privacy_replay_candidate.signing_key_id = privacy_envelope.signing_key_id();
                privacy_replay_candidate.message_stream = MessageStream::kPrivacyRecord;
                privacy_replay_candidate.sequence_number = privacy_envelope.sequence_number();
                privacy_replay_candidate.nonce = privacy_envelope.nonce();
                privacy_replay_candidate.now_unix_s = now_unix_s();
                const double window = privacy_envelope.expires_at() - privacy_envelope.issued_at();
                privacy_replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
                const auto privacy_replay_decision =
                    replay_store_->validate(privacy_replay_candidate);
                if (!privacy_replay_decision.accepted) {
                    response->set_rejection_code(to_string(privacy_replay_decision.reason));
                    return grpc::Status(
                        grpc::StatusCode::PERMISSION_DENIED,
                        "signed privacy record rejected: " + privacy_replay_decision.detail);
                }
                have_privacy_replay_candidate = true;
            }

            if (monotonicity_store_ != nullptr) {
                monotonicity_candidate.run_id = payload.run_id();
                monotonicity_candidate.client_id = payload.client_id();
                monotonicity_candidate.worker_id = payload.worker_id();
                monotonicity_candidate.accountant_type =
                    static_cast<int>(payload.accountant_type());
                monotonicity_candidate.step = payload.accountant_step();
                monotonicity_candidate.epsilon = payload.epsilon();
                monotonicity_candidate.delta = payload.delta();
                monotonicity_candidate.accountant_state_hash = payload.accountant_state_hash();
                monotonicity_candidate.configuration_hash = payload.configuration_hash();
                monotonicity_candidate.round_id = payload.round_id();
                monotonicity_candidate.task_id = payload.task_id();
                monotonicity_candidate.now_unix_s = now_unix_s();
                const auto monotonicity_decision =
                    monotonicity_store_->validate(monotonicity_candidate);
                if (!monotonicity_decision.accepted) {
                    response->set_rejection_code(to_string(monotonicity_decision.reason));
                    return grpc::Status(
                        grpc::StatusCode::PERMISSION_DENIED,
                        "signed privacy record rejected: " + monotonicity_decision.detail);
                }
                have_monotonicity_candidate = true;
            }

            const auto contradiction =
                budget_decision_contradiction_reason(payload.budget_decision());
            if (!contradiction.empty()) {
                response->set_rejection_code("budget_decision_contradiction");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed privacy record rejected: " + contradiction);
            }
        } else if (!allow_unsigned_privacy_records_) {
            response->set_rejection_code("privacy_record_missing");
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "a signed sample privacy record is required alongside "
                                "sample_level_privacy on this coordinator");
        } else {
            std::cerr
                << "timestamp_unix_s=" << now_unix_s()
                << " service=coordinator level=WARNING event=unsigned_privacy_record_accepted "
                   "worker_id="
                << request->worker_id()
                << " reason=\"FL_ALLOW_UNSIGNED_PRIVACY_RECORDS is set -- this coordinator is "
                   "running in an explicit development-compatibility mode that is unsafe for "
                   "private/production runs\""
                << std::endl;
        }
    }

    try {
        auto& run = manager_->get(request->result().run_id());
        ClientResultSubmission submission;
        submission.update.run_id = request->result().run_id();
        submission.update.round_id = request->result().round_id();
        submission.update.client_id = request->result().client_id();
        submission.update.base_model_version = request->result().base_model_version();
        submission.update.sample_count = request->result().sample_count();
        submission.update.algorithm = algorithm_from_wire(request->result().algorithm());
        submission.update.worker_id = request->worker_id();
        submission.update.nonce = request->result().nonce();
        submission.update.update_id = request->task_id();
        // Privacy Engineering phase: decode the real tensor payloads —
        // see docs/create-run-wire-mapping.md's "tensor transport"
        // section. Previously these fields were never read at all, so
        // submission.update.delta stayed empty regardless of what a
        // worker actually submitted, and the coordinator could never
        // aggregate a real value through the live gRPC path.
        submission.update.delta = tensor_collection_from_wire(request->result().tensor_manifest());
        submission.update.control_delta =
            tensor_collection_from_wire(request->client_control_variate_delta());
        submission.refreshed_client_control_variate =
            tensor_collection_from_wire(request->refreshed_client_control_variate());
        if (request->has_personalization_metrics()) {
            const auto& wire_metrics = request->personalization_metrics();
            fl::coordinator::PersonalizationMetricRecord record;
            record.client_id = wire_metrics.client_id();
            record.round_id = wire_metrics.round_id();
            record.algorithm = wire_metrics.algorithm();
            record.global_local_accuracy = wire_metrics.global_local_accuracy();
            record.personalized_local_accuracy = wire_metrics.personalized_local_accuracy();
            record.global_local_loss = wire_metrics.global_local_loss();
            record.personalized_local_loss = wire_metrics.personalized_local_loss();
            record.sample_count = wire_metrics.sample_count();
            record.personalized_improvement = wire_metrics.personalized_improvement();
            record.personalized_model_version = wire_metrics.personalized_model_version();
            record.recorded_at = wire_metrics.recorded_at();
            record.has_personalized_model = wire_metrics.has_personalized_model();
            for (const auto& [key, value] : wire_metrics.algorithm_metrics()) {
                record.algorithm_metrics[key] = value;
            }
            submission.personalization_metrics = std::move(record);
        }
        if (request->has_sample_level_privacy()) {
            // Storage/relay only — see docs/privacy-ledger.md's
            // authority-split note and run_manager.cpp's
            // submit_client_result. Python already computed this value
            // via Opacus; the coordinator does not recompute it. It DOES
            // cross-check run_id/round_id/client_id against the outer
            // (already lease-validated) result fields rather than
            // trusting the entry's own embedded copies — a buggy worker
            // submitting an entry stamped for the wrong run/round/client
            // must not have it silently accepted into this run's ledger
            // (see docs/privacy-engineering-security-audit.md, section
            // 3).
            const auto& wire_entry = request->sample_level_privacy();
            if (wire_entry.run_id() != request->result().run_id() ||
                wire_entry.round_id() != request->result().round_id() ||
                wire_entry.client_id() != request->result().client_id()) {
                throw std::invalid_argument(
                    "sample_level_privacy run_id/round_id/client_id does not match this "
                    "submission's result");
            }
            fl::coordinator::SampleLevelLedgerEntry entry;
            entry.run_id = wire_entry.run_id();
            entry.round_id = wire_entry.round_id();
            entry.client_id = wire_entry.client_id();
            entry.epsilon = wire_entry.epsilon();
            entry.delta = wire_entry.delta();
            entry.noise_multiplier = wire_entry.noise_multiplier();
            entry.sample_rate = wire_entry.sample_rate();
            entry.steps = wire_entry.steps();
            entry.accountant = accountant_type_from_wire(wire_entry.accountant());
            entry.recorded_at = wire_entry.recorded_at();
            entry.entry_id = wire_entry.entry_id();
            submission.sample_level_privacy = std::move(entry);
        }
        std::string reason;
        const auto accepted = run.submit_client_result(request->worker_id(),
                                                       request->task_id(),
                                                       request->lease_id(),
                                                       std::move(submission),
                                                       now_unix_s(),
                                                       reason);
        response->set_accepted(accepted);
        response->set_reason(reason);
        if (!accepted) {
            response->set_rejection_code("domain_rejected");
        } else {
            // Replay/sequence and monotonicity state are committed only
            // now -- after domain acceptance has actually succeeded
            // (Work Package D/E's "do not update replay state
            // permanently if domain processing fails" ordering
            // requirement, identical to Heartbeat's and now applied to
            // both the client-result and privacy-record tracks).
            if (have_replay_candidate) {
                replay_store_->commit(replay_candidate);
            }
            if (have_privacy_replay_candidate) {
                replay_store_->commit(privacy_replay_candidate);
            }
            if (have_monotonicity_candidate) {
                monotonicity_store_->commit(monotonicity_candidate);
            }
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetPrivacyMetrics(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetPrivacyMetricsRequest* request,
    fl::privacy::v1::PrivacyMetricsSnapshot* response) {
    try {
        auto& run = manager_->get(request->run_id());
        const auto snapshot = run.privacy_metrics_snapshot();
        response->set_run_id(snapshot.run_id);
        response->set_round_id(snapshot.round_id);
        response->set_has_sample_level(snapshot.has_sample_level);
        response->set_sample_epsilon(snapshot.sample_epsilon);
        response->set_sample_delta(snapshot.sample_delta);
        response->set_has_user_level(snapshot.has_user_level);
        response->set_user_epsilon(snapshot.user_epsilon);
        response->set_user_delta(snapshot.user_delta);
        response->set_has_clipping(snapshot.has_clipping);
        response->set_clipping_epsilon(snapshot.clipping_epsilon);
        response->set_clipping_delta(snapshot.clipping_delta);
        response->set_current_clip_value(snapshot.current_clip_value);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetPrivacyLedger(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetPrivacyLedgerRequest* request,
    fl::coordinator::v1::GetPrivacyLedgerResponse* response) {
    try {
        auto& run = manager_->get(request->run_id());

        // Simple, documented pagination (docs/privacy-ledger.md): a
        // page_size of 0 (the default) returns every entry in all three
        // ledgers unpaginated — these histories are one-entry-per-round
        // (or one-per-client-round for sample-level), so an unpaginated
        // response is the common case. A nonzero page_size applies the
        // same [offset, offset+page_size) window to all three ledgers
        // simultaneously; next_page_token is set only if any of the
        // three still has entries beyond that window.
        std::size_t offset = 0;
        if (!request->page_token().empty()) {
            try {
                offset = static_cast<std::size_t>(std::stoull(request->page_token()));
            } catch (const std::exception&) {
                throw std::invalid_argument("invalid page_token: not a valid offset");
            }
        }
        const std::size_t page_size = request->page_size();

        const auto& sample_entries = run.sample_level_ledger();
        const auto& user_entries = run.user_level_ledger();
        const auto& clipping_entries = run.adaptive_clipping_ledger();

        const auto emit_window =
            [&](const auto& entries, std::size_t begin, std::size_t limit, auto&& append) {
                if (begin >= entries.size()) {
                    return;
                }
                const std::size_t end =
                    (limit == 0) ? entries.size() : std::min(entries.size(), begin + limit);
                for (std::size_t i = begin; i < end; ++i) {
                    append(entries[i]);
                }
            };

        emit_window(sample_entries, offset, page_size, [&](const auto& entry) {
            auto* wire_entry = response->add_sample_level_entries();
            wire_entry->set_run_id(entry.run_id);
            wire_entry->set_round_id(entry.round_id);
            wire_entry->set_client_id(entry.client_id);
            wire_entry->set_epsilon(entry.epsilon);
            wire_entry->set_delta(entry.delta);
            wire_entry->set_noise_multiplier(entry.noise_multiplier);
            wire_entry->set_sample_rate(entry.sample_rate);
            wire_entry->set_steps(entry.steps);
            wire_entry->set_accountant(accountant_type_to_wire(entry.accountant));
            wire_entry->set_recorded_at(entry.recorded_at);
            wire_entry->set_entry_id(entry.entry_id);
        });
        emit_window(user_entries, offset, page_size, [&](const auto& entry) {
            auto* wire_entry = response->add_user_level_entries();
            wire_entry->set_run_id(entry.run_id);
            wire_entry->set_round_id(entry.round_id);
            wire_entry->set_epsilon(entry.epsilon);
            wire_entry->set_delta(entry.delta);
            wire_entry->set_noise_multiplier(entry.noise_multiplier);
            wire_entry->set_clipping_bound(entry.clipping_bound);
            wire_entry->set_num_clients(entry.num_clients);
            // UserLevelAccountant only ever implements the RDP formula
            // today (see fl_core/privacy.hpp) — not a per-entry choice.
            wire_entry->set_accountant(fl::privacy::v1::ACCOUNTANT_TYPE_RDP);
        });
        emit_window(clipping_entries, offset, page_size, [&](const auto& entry) {
            auto* wire_entry = response->add_clipping_entries();
            wire_entry->set_run_id(entry.run_id);
            wire_entry->set_round_id(entry.round_id);
            wire_entry->set_epsilon(entry.epsilon);
            wire_entry->set_delta(entry.delta);
            wire_entry->set_clip_value(entry.clip_value);
            wire_entry->set_observed_over_threshold_fraction(entry.noisy_over_threshold_fraction);
        });

        const std::size_t next_offset = offset + page_size;
        const bool more_remaining = page_size > 0 && (next_offset < sample_entries.size() ||
                                                      next_offset < user_entries.size() ||
                                                      next_offset < clipping_entries.size());
        if (more_remaining) {
            response->set_next_page_token(std::to_string(next_offset));
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetPrivacyProjection(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetPrivacyProjectionRequest* request,
    fl::coordinator::v1::PrivacyProjection* response) {
    try {
        auto& run = manager_->get(request->run_id());
        const auto projection = run.privacy_projection();
        response->set_has_sample_level(projection.has_sample_level);
        response->set_sample_current_epsilon(projection.sample_current_epsilon);
        response->set_sample_projected_next_epsilon(projection.sample_projected_next_epsilon);
        response->set_sample_budget_remaining(projection.sample_budget_remaining);
        response->set_has_user_level(projection.has_user_level);
        response->set_user_current_epsilon(projection.user_current_epsilon);
        response->set_user_projected_next_epsilon(projection.user_projected_next_epsilon);
        response->set_user_budget_remaining(projection.user_budget_remaining);
        response->set_has_clipping(projection.has_clipping);
        response->set_clipping_current_epsilon(projection.clipping_current_epsilon);
        response->set_clipping_projected_next_epsilon(projection.clipping_projected_next_epsilon);
        response->set_clipping_budget_remaining(projection.clipping_budget_remaining);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::GetPersonalizationSummary(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetPersonalizationSummaryRequest* request,
    fl::coordinator::v1::PersonalizationSummaryResponse* response) {
    try {
        auto& run = manager_->get(request->run_id());
        response->set_run_id(request->run_id());
        for (const auto& record : run.personalization_summary()) {
            auto* wire_record = response->add_records();
            wire_record->set_client_id(record.client_id);
            wire_record->set_round_id(record.round_id);
            wire_record->set_algorithm(record.algorithm);
            wire_record->set_global_local_accuracy(record.global_local_accuracy);
            wire_record->set_personalized_local_accuracy(record.personalized_local_accuracy);
            wire_record->set_global_local_loss(record.global_local_loss);
            wire_record->set_personalized_local_loss(record.personalized_local_loss);
            wire_record->set_sample_count(record.sample_count);
            wire_record->set_personalized_improvement(record.personalized_improvement);
            wire_record->set_personalized_model_version(record.personalized_model_version);
            wire_record->set_recorded_at(record.recorded_at);
            wire_record->set_has_personalized_model(record.has_personalized_model);
            for (const auto& [key, value] : record.algorithm_metrics) {
                (*wire_record->mutable_algorithm_metrics())[key] = value;
            }
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::StreamRunEvents(
    grpc::ServerContext* context,
    const fl::coordinator::v1::StreamRunEventsRequest* request,
    grpc::ServerWriter<fl::events::v1::CoordinatorEvent>* writer) {
    try {
        std::string cursor = request->resume_after_event_id();
        // Simple poll loop: the domain-layer EventBus (cpp_coordinator/
        // event_bus.hpp) is itself pull-based by design (see its header
        // comment), so a real streaming handler is exactly this —
        // poll, write what's new, sleep, repeat until the client
        // disconnects (context->IsCancelled()).
        while (!context->IsCancelled()) {
            for (const auto& event : manager_->event_bus().poll(request->run_id(), cursor)) {
                fl::events::v1::CoordinatorEvent wire_event;
                wire_event.set_event_id(event.event_id);
                wire_event.set_run_id(event.run_id);
                wire_event.set_round_id(event.round_id);
                wire_event.set_event_type(fl::coordinator::to_string(event.type));
                wire_event.set_timestamp(event.timestamp);
                wire_event.set_trace_id(event.trace_id);
                wire_event.set_client_id(event.client_id);
                wire_event.set_worker_id(event.worker_id);
                wire_event.set_model_version(event.model_version);
                // Previously never relayed at all — every event's
                // metadata (e.g. a privacy-budget event's "mechanism"/
                // "policy" keys, see finalize_round's budget-policy
                // enforcement) was silently dropped between the domain
                // EventBus and the wire, even though the wire message
                // has carried a metadata field since the Coordinator
                // Runtime phase additions.
                for (const auto& [key, value] : event.metadata) {
                    (*wire_event.mutable_metadata())[key] = value;
                }
                if (!writer->Write(wire_event)) {
                    return grpc::Status::OK;  // client disconnected
                }
                cursor = event.event_id;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return to_grpc_status(error);
    }
}

grpc::Status CoordinatorServiceImpl::Health(grpc::ServerContext*,
                                            const fl::coordinator::v1::HealthRequest*,
                                            fl::coordinator::v1::HealthResponse* response) {
    response->set_status("ok");
    response->set_version("phase-3");
    const auto uptime = std::chrono::duration_cast<std::chrono::duration<double>>(
        std::chrono::steady_clock::now() - started_at_);
    response->set_uptime_seconds(uptime.count());
    response->set_active_runs(static_cast<std::uint32_t>(manager_->list_run_ids().size()));
    response->set_registered_workers(
        static_cast<std::uint32_t>(manager_->worker_registry().registered_count()));
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetWorkerIdentity(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetWorkerIdentityRequest* request,
    fl::coordinator::v1::WorkerIdentitySummary* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        return *rejection;
    }
    if (identity_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    const auto record = identity_registry_->find_by_worker_id(request->worker_id());
    if (!record.has_value()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND,
                            "unknown worker_id: " + request->worker_id());
    }
    *response = to_wire_identity_summary(*record);
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::ListWorkerIdentities(
    grpc::ServerContext* context,
    const fl::coordinator::v1::ListWorkerIdentitiesRequest*,
    fl::coordinator::v1::ListWorkerIdentitiesResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        return *rejection;
    }
    if (identity_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    for (const auto& record : identity_registry_->list()) {
        *response->add_identities() = to_wire_identity_summary(record);
    }
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::SuspendWorker(
    grpc::ServerContext* context,
    const fl::coordinator::v1::SuspendWorkerRequest* request,
    fl::coordinator::v1::WorkerLifecycleResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(security_event_journal_, context, "SuspendWorker");
        return *rejection;
    }
    if (identity_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    try {
        const auto before = identity_registry_->find_by_worker_id(request->worker_id());
        const bool was_already_suspended =
            before.has_value() && before->registration_status == WorkerIdentityStatus::kSuspended;
        const auto record =
            identity_registry_->suspend(request->worker_id(), request->reason(), now_unix_s());
        *response->mutable_identity() = to_wire_identity_summary(record);
        response->set_changed(!was_already_suspended);
        response->set_leases_canceled(0);
        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator event=WORKER_SUSPENDED worker_id="
                  << request->worker_id() << " reason=\"" << request->reason() << "\""
                  << " request_id=" << request->request_id() << std::endl;
        emit_worker_lifecycle_records(security_event_journal_,
                                      security_audit_journal_,
                                      context,
                                      SecurityEventType::kWorkerSuspended,
                                      "SuspendWorker",
                                      request->worker_id(),
                                      request->reason(),
                                      request->request_id(),
                                      request->trace_id());
        return grpc::Status::OK;
    } catch (const WorkerIdentityRegistryError& error) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
    }
}

grpc::Status CoordinatorServiceImpl::ActivateWorker(
    grpc::ServerContext* context,
    const fl::coordinator::v1::ActivateWorkerRequest* request,
    fl::coordinator::v1::WorkerLifecycleResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(security_event_journal_, context, "ActivateWorker");
        return *rejection;
    }
    if (identity_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    try {
        const auto before = identity_registry_->find_by_worker_id(request->worker_id());
        const bool was_already_active =
            before.has_value() && before->registration_status == WorkerIdentityStatus::kActive;
        const auto record = identity_registry_->activate(request->worker_id(), now_unix_s());
        *response->mutable_identity() = to_wire_identity_summary(record);
        response->set_changed(!was_already_active);
        response->set_leases_canceled(0);
        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator event=WORKER_ACTIVATED worker_id="
                  << request->worker_id() << " reason=\"" << request->reason() << "\""
                  << " request_id=" << request->request_id() << std::endl;
        emit_worker_lifecycle_records(security_event_journal_,
                                      security_audit_journal_,
                                      context,
                                      SecurityEventType::kWorkerActivated,
                                      "ActivateWorker",
                                      request->worker_id(),
                                      request->reason(),
                                      request->request_id(),
                                      request->trace_id());
        return grpc::Status::OK;
    } catch (const WorkerIdentityRegistryError& error) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
    }
}

grpc::Status CoordinatorServiceImpl::RevokeWorker(
    grpc::ServerContext* context,
    const fl::coordinator::v1::RevokeWorkerRequest* request,
    fl::coordinator::v1::WorkerLifecycleResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(security_event_journal_, context, "RevokeWorker");
        return *rejection;
    }
    if (identity_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    try {
        const auto before = identity_registry_->find_by_worker_id(request->worker_id());
        const bool was_already_revoked =
            before.has_value() && before->registration_status == WorkerIdentityStatus::kRevoked;
        const auto record =
            identity_registry_->revoke(request->worker_id(), request->reason(), now_unix_s());
        // Work Package M: cancel this worker's active lease in every run
        // it holds one in -- unconditionally, even if the registry call
        // above was itself an idempotent no-op (a worker could in
        // principle acquire a fresh lease in the narrow window between
        // an earlier revocation and this one, though AcquireTask's own
        // REVOKED check above makes that window effectively empty in
        // practice; canceling unconditionally here costs nothing and
        // closes the gap entirely).
        const auto leases_canceled = manager_->cancel_leases_for_worker(
            request->worker_id(), request->reason(), now_unix_s());
        if (replay_store_ != nullptr) {
            // Work Package E: worker-revocation cleanup policy -- this
            // worker can never send another acceptable signed message
            // under any key again, so its replay/sequence history no
            // longer serves a purpose.
            replay_store_->purge_worker(request->worker_id());
        }
        *response->mutable_identity() = to_wire_identity_summary(record);
        response->set_changed(!was_already_revoked);
        response->set_leases_canceled(leases_canceled);
        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator event=WORKER_REVOKED worker_id=" << request->worker_id()
                  << " reason=\"" << request->reason() << "\""
                  << " request_id=" << request->request_id()
                  << " leases_canceled=" << leases_canceled << std::endl;
        emit_worker_lifecycle_records(security_event_journal_,
                                      security_audit_journal_,
                                      context,
                                      SecurityEventType::kWorkerRevoked,
                                      "RevokeWorker",
                                      request->worker_id(),
                                      request->reason(),
                                      request->request_id(),
                                      request->trace_id());
        if (leases_canceled > 0) {
            SecurityEvent lease_event;
            lease_event.source_service = "coordinator";
            lease_event.source_component = "coordinator_service";
            lease_event.event_type = SecurityEventType::kActiveLeaseCanceled;
            lease_event.severity = default_severity(lease_event.event_type);
            lease_event.actor_type = SecurityActorType::kService;
            lease_event.safe_actor_id = safe_actor_label(context);
            lease_event.subject_type = SecuritySubjectType::kTaskLease;
            lease_event.safe_subject_id = request->worker_id();
            lease_event.worker_id = request->worker_id();
            lease_event.outcome = SecurityOutcome::kCanceled;
            lease_event.reason_code = "worker_revoked";
            lease_event.safe_details["leases_canceled"] = std::to_string(leases_canceled);
            lease_event.request_id = request->request_id();
            lease_event.trace_id = request->trace_id();
            if (security_event_journal_ != nullptr) {
                security_event_journal_->emit(std::move(lease_event));
            }
        }
        return grpc::Status::OK;
    } catch (const WorkerIdentityRegistryError& error) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
    }
}

grpc::Status CoordinatorServiceImpl::RotateWorkerSigningKey(
    grpc::ServerContext* context,
    const fl::coordinator::v1::RotateWorkerSigningKeyRequest* request,
    fl::coordinator::v1::RotateWorkerSigningKeyResponse* response) {
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        return *rejection;
    }
    if (identity_registry_ == nullptr || signing_key_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "worker signing-key rotation requires both a worker identity "
                            "registry and a signing-key registry to be configured");
    }
    const auto identity_record = identity_registry_->find_by_worker_id(request->worker_id());
    if (!identity_record.has_value()) {
        response->set_rejection_code("unknown_worker");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "unknown worker_id: " + request->worker_id());
    }
    if (identity_record->registration_status == WorkerIdentityStatus::kRevoked ||
        identity_record->registration_status == WorkerIdentityStatus::kSuspended ||
        identity_record->registration_status == WorkerIdentityStatus::kExpired) {
        response->set_rejection_code("worker_status_forbids_rotation");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "worker_id '" + request->worker_id() + "' has status '" +
                                to_string(identity_record->registration_status) +
                                "', which does not permit signing-key rotation");
    }

    const auto& payload = request->payload();
    if (payload.worker_id() != request->worker_id()) {
        response->set_rejection_code("payload_worker_mismatch");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "rotation payload worker_id does not match the request's worker_id");
    }
    if (!request->has_envelope()) {
        response->set_rejection_code("envelope_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "a signed envelope is required for RotateWorkerSigningKey");
    }
    const auto& envelope = request->envelope();
    if (envelope.signing_key_id() != payload.current_signing_key_id()) {
        response->set_rejection_code("envelope_key_mismatch");
        return grpc::Status(
            grpc::StatusCode::PERMISSION_DENIED,
            "envelope.signing_key_id does not match payload.current_signing_key_id");
    }

    const auto resolved_key = resolve_signing_key(signing_key_registry_,
                                                  *identity_record,
                                                  envelope.signing_key_id(),
                                                  SignedMessageKind::kKeyRotation,
                                                  now_unix_s());
    if (!resolved_key.ok) {
        response->set_rejection_code(resolved_key.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, resolved_key.reason);
    }

    const auto hash_result = rotation_payload_hash_input(payload);
    if (!hash_result.ok) {
        response->set_rejection_code("payload_hash_mismatch");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "rotation request rejected: " + hash_result.reason);
    }
    const auto verification = verify_signed_envelope(
        envelope,
        static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_KEY_ROTATION_REQUEST),
        hash_result.hash_input,
        resolved_key.public_key_hex,
        now_unix_s(),
        /*future_issued_tolerance_seconds=*/30.0);
    if (!verification.valid) {
        response->set_rejection_code(verification.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "rotation request rejected: " + verification.reason);
    }

    ReplayCandidate replay_candidate;
    bool have_replay_candidate = false;
    if (replay_store_ != nullptr) {
        replay_candidate.worker_id = request->worker_id();
        replay_candidate.signing_key_id = envelope.signing_key_id();
        replay_candidate.message_stream = MessageStream::kKeyManagement;
        replay_candidate.sequence_number = envelope.sequence_number();
        replay_candidate.nonce = envelope.nonce();
        replay_candidate.now_unix_s = now_unix_s();
        const double window = envelope.expires_at() - envelope.issued_at();
        replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
        const auto replay_decision = replay_store_->validate(replay_candidate);
        if (!replay_decision.accepted) {
            response->set_rejection_code(to_string(replay_decision.reason));
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "rotation request rejected: " + replay_decision.detail);
        }
        have_replay_candidate = true;
    }

    SigningKeyRotationRequest rotation_request;
    rotation_request.worker_id = request->worker_id();
    rotation_request.current_signing_key_id = payload.current_signing_key_id();
    rotation_request.new_signing_key_id = payload.new_signing_key_id();
    rotation_request.new_public_key_hex = payload.new_public_key_hex();
    rotation_request.new_public_key_fingerprint =
        public_key_fingerprint_hex(payload.new_public_key_hex());
    rotation_request.new_key_expires_at_unix_s = payload.new_key_expires_at_unix_s();
    rotation_request.grace_period_seconds = payload.requested_grace_period_seconds();
    rotation_request.now_unix_s = now_unix_s();

    const auto validated = signing_key_registry_->validate_rotation(rotation_request);
    if (!validated.accepted) {
        response->set_rejection_code(to_string(validated.reason));
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "rotation request rejected: " + validated.detail);
    }

    if (have_replay_candidate) {
        replay_store_->commit(replay_candidate);
    }
    const auto committed = signing_key_registry_->commit_rotation(rotation_request);

    // Keep WorkerIdentityRegistry's single "preferred key" cache pointed
    // at the new key -- every consumer that only ever reads
    // identity_record.signing_key_id/signing_public_key (e.g.
    // WorkerIdentitySummary) should see the current key without needing
    // to itself become SigningKeyRegistry-aware.
    try {
        identity_registry_->register_identity(request->worker_id(),
                                              identity_record->certificate_identity,
                                              identity_record->certificate_serial,
                                              identity_record->certificate_fingerprint,
                                              payload.new_public_key_hex(),
                                              payload.new_signing_key_id(),
                                              identity_record->software_version,
                                              identity_record->build_id,
                                              now_unix_s(),
                                              identity_record->expires_at_unix_s);
    } catch (const WorkerIdentityRegistryError& error) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
    }

    response->set_accepted(true);
    *response->mutable_new_key() = to_wire_signing_key_summary(committed.new_key);
    *response->mutable_previous_key() = to_wire_signing_key_summary(committed.previous_key);
    std::cerr << "timestamp_unix_s=" << now_unix_s()
              << " service=coordinator event=WORKER_KEY_ROTATION_ACCEPTED worker_id="
              << request->worker_id() << " previous_key=" << payload.current_signing_key_id()
              << " new_key=" << payload.new_signing_key_id() << std::endl;
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetWorkerSigningKeys(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetWorkerSigningKeysRequest* request,
    fl::coordinator::v1::GetWorkerSigningKeysResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        return *rejection;
    }
    if (signing_key_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no signing-key registry is configured");
    }
    for (const auto& record :
         signing_key_registry_->list_for_worker(request->worker_id(), now_unix_s())) {
        *response->add_keys() = to_wire_signing_key_summary(record);
    }
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::RevokeWorkerSigningKey(
    grpc::ServerContext* context,
    const fl::coordinator::v1::RevokeWorkerSigningKeyRequest* request,
    fl::coordinator::v1::RevokeWorkerSigningKeyResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(security_event_journal_, context, "RevokeWorkerSigningKey");
        return *rejection;
    }
    if (signing_key_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no signing-key registry is configured");
    }
    try {
        const auto before = signing_key_registry_->find(
            request->worker_id(), request->signing_key_id(), now_unix_s());
        const bool was_already_revoked =
            before.has_value() && before->status == SigningKeyStatus::kRevoked;
        const auto record = signing_key_registry_->revoke_key(
            request->worker_id(), request->signing_key_id(), request->reason(), now_unix_s());
        *response->mutable_key() = to_wire_signing_key_summary(record);
        response->set_changed(!was_already_revoked);

        // Work Package H: when the revoked key was the worker's only
        // valid (ACTIVE/GRACE_PERIOD) key, the worker can receive no
        // new tasks -- transition it to SUSPENDED (a controlled,
        // reversible state an operator can later ActivateWorker out of
        // once a new key is registered) rather than leaving it silently
        // stuck in an ACTIVE identity status with no usable key.
        bool worker_suspended = false;
        if (identity_registry_ != nullptr &&
            !signing_key_registry_->has_any_valid_key(request->worker_id(), now_unix_s())) {
            const auto identity = identity_registry_->find_by_worker_id(request->worker_id());
            if (identity.has_value() &&
                identity->registration_status != WorkerIdentityStatus::kRevoked &&
                identity->registration_status != WorkerIdentityStatus::kSuspended) {
                identity_registry_->suspend(request->worker_id(),
                                            "no valid signing key remains after revocation of '" +
                                                request->signing_key_id() + "'",
                                            now_unix_s());
                worker_suspended = true;
            }
        }
        response->set_worker_suspended(worker_suspended);

        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator event=WORKER_KEY_REVOKED worker_id="
                  << request->worker_id() << " signing_key_id=" << request->signing_key_id()
                  << " reason=\"" << request->reason() << "\""
                  << " request_id=" << request->request_id()
                  << " worker_suspended=" << (worker_suspended ? "true" : "false") << std::endl;
        if (security_event_journal_ != nullptr) {
            SecurityEvent event;
            event.source_service = "coordinator";
            event.source_component = "coordinator_service";
            event.event_type = SecurityEventType::kWorkerKeyRevoked;
            event.severity = default_severity(event.event_type);
            event.actor_type = SecurityActorType::kService;
            event.safe_actor_id = safe_actor_label(context);
            event.subject_type = SecuritySubjectType::kWorkerSigningKey;
            event.safe_subject_id = request->signing_key_id();
            event.worker_id = request->worker_id();
            event.safe_signing_key_id = request->signing_key_id();
            event.outcome = SecurityOutcome::kCompleted;
            event.reason_code = request->reason().substr(0, kSecurityEventMaxReasonCodeLength);
            event.request_id = request->request_id();
            event.trace_id = request->trace_id();
            event.safe_details["worker_suspended"] = worker_suspended ? "true" : "false";
            security_event_journal_->emit(std::move(event));
        }
        if (security_audit_journal_ != nullptr) {
            SecurityAuditRecord record;
            record.safe_actor_id = safe_actor_label(context);
            record.actor_role = "service";
            record.action = "RevokeWorkerSigningKey";
            record.resource_type = "worker_signing_key";
            record.resource_id = request->signing_key_id();
            record.outcome = "ACCEPTED";
            record.reason = request->reason();
            record.request_id = request->request_id();
            record.trace_id = request->trace_id();
            security_audit_journal_->append(std::move(record));
        }
        return grpc::Status::OK;
    } catch (const SigningKeyRegistryError& error) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
    }
}

grpc::Status CoordinatorServiceImpl::GetCoordinatorSigningKeys(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetCoordinatorSigningKeysRequest* request,
    fl::coordinator::v1::GetCoordinatorSigningKeysResponse* response) {
    (void)request;
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        return *rejection;
    }
    if (coordinator_signing_key_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no coordinator signing-key registry is configured");
    }
    for (const auto& record : coordinator_signing_key_registry_->list(now_unix_s())) {
        *response->add_keys() = to_wire_coordinator_signing_key_summary(record);
    }
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetTransportSecurityStatus(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetTransportSecurityStatusRequest* request,
    fl::coordinator::v1::TransportSecurityStatusResponse* response) {
    (void)request;
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        return *rejection;
    }
    response->set_transport_mode(to_string(transport_mode_));
    response->set_mutual_tls_enforced(transport_mode_ == TransportMode::kMtlsRequired);
    response->set_checked_at_unix_s(now_unix_s());
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetSecurityTrustModel(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetSecurityTrustModelRequest* request,
    fl::coordinator::v1::SecurityTrustModelResponse* response) {
    (void)request;
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        return *rejection;
    }
    const double now = now_unix_s();
    if (coordinator_signing_key_registry_ != nullptr) {
        if (const auto active = coordinator_signing_key_registry_->active_key(now)) {
            response->set_active_coordinator_signing_key_id(active->signing_key_id);
        }
        response->set_trusted_coordinator_key_count(
            coordinator_signing_key_registry_->trusted_public_keys(now).size());
    }
    if (!trusted_key_bundle_path_.empty()) {
        response->set_trusted_key_bundle_version(read_bundle_version(trusted_key_bundle_path_));
    }
    if (identity_registry_ != nullptr) {
        const auto workers = identity_registry_->list();
        response->set_registered_worker_count(workers.size());
        if (signing_key_registry_ != nullptr) {
            std::uint64_t total_keys = 0;
            for (const auto& worker : workers) {
                total_keys += signing_key_registry_->list_for_worker(worker.worker_id, now).size();
            }
            response->set_worker_signing_key_total_count(total_keys);
        }
    }
    response->set_checked_at_unix_s(now);
    return grpc::Status::OK;
}

bool CoordinatorServiceImpl::regenerate_trusted_key_bundle(std::string& reason) {
    if (coordinator_signing_key_registry_ == nullptr || trusted_key_bundle_path_.empty()) {
        reason = "no coordinator signing-key registry or trusted-key bundle path configured";
        return false;
    }
    const auto result = write_trusted_key_bundle(*coordinator_signing_key_registry_,
                                                 trusted_key_bundle_path_,
                                                 coordinator_identity_label_,
                                                 now_unix_s());
    if (!result.ok) {
        reason = result.reason;
        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator level=CRITICAL event=TRUSTED_BUNDLE_GENERATION_FAILED "
                     "reason=\""
                  << result.reason << "\"" << std::endl;
        return false;
    }
    std::cerr << "timestamp_unix_s=" << now_unix_s()
              << " service=coordinator event=TRUSTED_BUNDLE_GENERATED bundle_version="
              << result.bundle_version << std::endl;
    return true;
}

grpc::Status CoordinatorServiceImpl::RotateCoordinatorSigningKey(
    grpc::ServerContext* context,
    const fl::coordinator::v1::RotateCoordinatorSigningKeyRequest* request,
    fl::coordinator::v1::RotateCoordinatorSigningKeyResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "RotateCoordinatorSigningKey");
        return *rejection;
    }
    if (coordinator_signing_key_registry_ == nullptr || coordinator_active_identity_ == nullptr ||
        idempotency_store_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "coordinator signing-key rotation is not configured on this server");
    }

    if (!request->idempotency_key().empty()) {
        const auto existing =
            idempotency_store_->find("RotateCoordinatorSigningKey", request->idempotency_key());
        if (existing.has_value()) {
            response->set_accepted(existing->accepted);
            response->set_rejection_code(existing->rejection_code);
            response->set_idempotent_replay(true);
            response->set_reason(existing->accepted
                                     ? "ok (idempotent replay)"
                                     : "rejected (idempotent replay): " + existing->rejection_code);
            if (existing->accepted) {
                const auto new_record =
                    coordinator_signing_key_registry_->find(existing->result_key_id, now_unix_s());
                const auto previous_record = coordinator_signing_key_registry_->find(
                    existing->previous_key_id, now_unix_s());
                if (new_record.has_value()) {
                    *response->mutable_new_key() =
                        to_wire_coordinator_signing_key_summary(*new_record);
                }
                if (previous_record.has_value()) {
                    *response->mutable_previous_key() =
                        to_wire_coordinator_signing_key_summary(*previous_record);
                }
            }
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "coordinator_service";
                event.event_type = SecurityEventType::kIdempotencyReplayAccepted;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kService;
                event.safe_actor_id = safe_actor_label(context);
                event.subject_type = SecuritySubjectType::kCoordinatorSigningKey;
                event.outcome = SecurityOutcome::kAccepted;
                event.reason_code = "RotateCoordinatorSigningKey";
                event.request_id = request->request_id();
                event.trace_id = request->trace_id();
                security_event_journal_->emit(std::move(event));
            }
            return grpc::Status::OK;
        }
    }

    std::cerr << "timestamp_unix_s=" << now_unix_s()
              << " service=coordinator event=COORDINATOR_KEY_ROTATION_STARTED request_id="
              << request->request_id() << " reason=\"" << request->reason() << "\"" << std::endl;

    try {
        // Generated before any mutation -- if anything below fails, no
        // registry/bundle state has changed yet.
        const auto new_identity = generate_coordinator_signing_identity();

        CoordinatorSigningKeyRotationRequest rotation;
        rotation.current_signing_key_id = request->expected_current_signing_key_id();
        rotation.new_signing_key_id = new_identity.key_id;
        rotation.new_public_key_hex = new_identity.public_key_hex;
        rotation.new_public_key_fingerprint =
            public_key_fingerprint_hex(new_identity.public_key_hex);
        rotation.new_key_expires_at_unix_s = request->new_key_expires_at_unix_s();
        rotation.grace_period_seconds = request->requested_grace_period_seconds();
        rotation.now_unix_s = now_unix_s();

        const auto validation = coordinator_signing_key_registry_->validate_rotation(rotation);
        if (!validation.accepted) {
            response->set_accepted(false);
            response->set_rejection_code(to_string(validation.reason));
            response->set_reason(validation.detail);
            response->set_idempotent_replay(false);
            if (!request->idempotency_key().empty()) {
                IdempotentMutationRecord record;
                record.rpc_name = "RotateCoordinatorSigningKey";
                record.idempotency_key = request->idempotency_key();
                record.accepted = false;
                record.rejection_code = to_string(validation.reason);
                record.recorded_at_unix_s = now_unix_s();
                idempotency_store_->record(record);
            }
            std::cerr << "timestamp_unix_s=" << now_unix_s()
                      << " service=coordinator event=COORDINATOR_KEY_ROTATION_FAILED reason=\""
                      << validation.detail << "\"" << std::endl;
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "coordinator_service";
                event.event_type = SecurityEventType::kSecurityMutationRejected;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kService;
                event.safe_actor_id = safe_actor_label(context);
                event.subject_type = SecuritySubjectType::kCoordinatorSigningKey;
                event.outcome = SecurityOutcome::kRejected;
                event.reason_code =
                    to_string(validation.reason).substr(0, kSecurityEventMaxReasonCodeLength);
                event.request_id = request->request_id();
                event.trace_id = request->trace_id();
                event.safe_details["action"] = "RotateCoordinatorSigningKey";
                security_event_journal_->emit(std::move(event));
            }
            if (security_audit_journal_ != nullptr) {
                SecurityAuditRecord record;
                record.safe_actor_id = safe_actor_label(context);
                record.actor_role = "service";
                record.action = "RotateCoordinatorSigningKey";
                record.resource_type = "coordinator_signing_key";
                record.outcome = "REJECTED";
                record.reason = validation.detail;
                record.request_id = request->request_id();
                record.trace_id = request->trace_id();
                security_audit_journal_->append(std::move(record));
            }
            return grpc::Status::OK;
        }

        // Persisted BEFORE committing to the registry -- if this
        // throws, the registry is untouched (real rollback safety for
        // this specific failure mode).
        (void)save_keyed_coordinator_signing_identity(new_identity, coordinator_signing_key_dir_);

        const auto committed = coordinator_signing_key_registry_->commit_rotation(rotation);

        std::string bundle_reason;
        if (!regenerate_trusted_key_bundle(bundle_reason)) {
            std::cerr << "timestamp_unix_s=" << now_unix_s()
                      << " service=coordinator level=CRITICAL "
                         "event=COORDINATOR_KEY_ROTATION_FAILED reason=\"committed to the "
                         "registry but trusted-key bundle regeneration failed: "
                      << bundle_reason << "\"" << std::endl;
            return grpc::Status(
                grpc::StatusCode::INTERNAL,
                "coordinator key rotation committed to the registry but trusted-key bundle "
                "regeneration failed: " +
                    bundle_reason + " -- use the recovery tool to regenerate the bundle");
        }

        coordinator_active_identity_->set(new_identity);

        *response->mutable_new_key() = to_wire_coordinator_signing_key_summary(committed.new_key);
        *response->mutable_previous_key() =
            to_wire_coordinator_signing_key_summary(committed.previous_key);
        response->set_accepted(true);
        response->set_reason("ok");
        response->set_idempotent_replay(false);

        if (!request->idempotency_key().empty()) {
            IdempotentMutationRecord record;
            record.rpc_name = "RotateCoordinatorSigningKey";
            record.idempotency_key = request->idempotency_key();
            record.accepted = true;
            record.result_key_id = committed.new_key.signing_key_id;
            record.previous_key_id = committed.previous_key.signing_key_id;
            record.recorded_at_unix_s = now_unix_s();
            idempotency_store_->record(record);
        }

        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator event=COORDINATOR_KEY_ROTATION_COMPLETED new_key_id="
                  << committed.new_key.signing_key_id
                  << " previous_key_id=" << committed.previous_key.signing_key_id
                  << " request_id=" << request->request_id() << std::endl;
        if (security_event_journal_ != nullptr) {
            SecurityEvent event;
            event.source_service = "coordinator";
            event.source_component = "coordinator_service";
            event.event_type = SecurityEventType::kSecurityMutationAccepted;
            event.severity = default_severity(event.event_type);
            event.actor_type = SecurityActorType::kService;
            event.safe_actor_id = safe_actor_label(context);
            event.subject_type = SecuritySubjectType::kCoordinatorSigningKey;
            event.safe_subject_id = committed.new_key.signing_key_id;
            event.safe_signing_key_id = committed.new_key.signing_key_id;
            event.outcome = SecurityOutcome::kCompleted;
            event.reason_code = "RotateCoordinatorSigningKey";
            event.request_id = request->request_id();
            event.trace_id = request->trace_id();
            event.safe_details["previous_key_id"] = committed.previous_key.signing_key_id;
            security_event_journal_->emit(std::move(event));
        }
        if (security_audit_journal_ != nullptr) {
            SecurityAuditRecord record;
            record.safe_actor_id = safe_actor_label(context);
            record.actor_role = "service";
            record.action = "RotateCoordinatorSigningKey";
            record.resource_type = "coordinator_signing_key";
            record.resource_id = committed.new_key.signing_key_id;
            record.outcome = "ACCEPTED";
            record.reason = request->reason();
            record.request_id = request->request_id();
            record.trace_id = request->trace_id();
            record.safe_details["previous_key_id"] = committed.previous_key.signing_key_id;
            security_audit_journal_->append(std::move(record));
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator level=CRITICAL event=COORDINATOR_KEY_ROTATION_FAILED "
                     "reason=\""
                  << error.what() << "\"" << std::endl;
        return grpc::Status(grpc::StatusCode::INTERNAL,
                            std::string("coordinator key rotation failed: ") + error.what());
    }
}

grpc::Status CoordinatorServiceImpl::RevokeCoordinatorSigningKey(
    grpc::ServerContext* context,
    const fl::coordinator::v1::RevokeCoordinatorSigningKeyRequest* request,
    fl::coordinator::v1::RevokeCoordinatorSigningKeyResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "RevokeCoordinatorSigningKey");
        return *rejection;
    }
    if (coordinator_signing_key_registry_ == nullptr || idempotency_store_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "coordinator signing-key revocation is not configured on this server");
    }

    if (!request->idempotency_key().empty()) {
        const auto existing =
            idempotency_store_->find("RevokeCoordinatorSigningKey", request->idempotency_key());
        if (existing.has_value()) {
            const auto record =
                coordinator_signing_key_registry_->find(existing->result_key_id, now_unix_s());
            if (record.has_value()) {
                *response->mutable_key() = to_wire_coordinator_signing_key_summary(*record);
            }
            response->set_changed(existing->accepted);
            response->set_idempotent_replay(true);
            response->set_production_task_issuance_stopped(
                !coordinator_signing_key_registry_->active_key(now_unix_s()).has_value());
            return grpc::Status::OK;
        }
    }

    const auto before =
        coordinator_signing_key_registry_->find(request->signing_key_id(), now_unix_s());
    if (!before.has_value()) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND,
                            "unknown coordinator signing key '" + request->signing_key_id() + "'");
    }
    if (!request->expected_status().empty() &&
        to_string(before->status) != request->expected_status()) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "expected_status '" + request->expected_status() +
                                "' does not match current status '" + to_string(before->status) +
                                "'");
    }

    try {
        const bool was_already_revoked = before->status == CoordinatorSigningKeyStatus::kRevoked;
        const auto record = coordinator_signing_key_registry_->revoke_key(
            request->signing_key_id(), request->reason(), now_unix_s());

        std::string bundle_reason;
        // Revocation is immediate and is never rolled back on a bundle
        // write failure -- a revoked key must never be silently treated
        // as un-revoked. The failure is still surfaced (logged +
        // returned as INTERNAL) so an operator can rerun the recovery
        // tool's bundle-regeneration step.
        const bool bundle_ok = regenerate_trusted_key_bundle(bundle_reason);

        response->set_changed(!was_already_revoked);
        *response->mutable_key() = to_wire_coordinator_signing_key_summary(record);
        const bool issuance_stopped =
            !coordinator_signing_key_registry_->active_key(now_unix_s()).has_value();
        response->set_production_task_issuance_stopped(issuance_stopped);
        response->set_idempotent_replay(false);

        if (!request->idempotency_key().empty()) {
            IdempotentMutationRecord idempotency_record;
            idempotency_record.rpc_name = "RevokeCoordinatorSigningKey";
            idempotency_record.idempotency_key = request->idempotency_key();
            idempotency_record.accepted = true;
            idempotency_record.result_key_id = request->signing_key_id();
            idempotency_record.recorded_at_unix_s = now_unix_s();
            idempotency_store_->record(idempotency_record);
        }

        std::cerr << "timestamp_unix_s=" << now_unix_s()
                  << " service=coordinator event=COORDINATOR_KEY_REVOKED signing_key_id="
                  << request->signing_key_id() << " reason=\"" << request->reason() << "\""
                  << " request_id=" << request->request_id()
                  << " production_task_issuance_stopped=" << (issuance_stopped ? "true" : "false")
                  << std::endl;
        if (security_event_journal_ != nullptr) {
            SecurityEvent event;
            event.source_service = "coordinator";
            event.source_component = "coordinator_service";
            event.event_type = SecurityEventType::kSecurityMutationAccepted;
            event.severity =
                SecuritySeverity::kHigh;  // a trust-root revocation, not a routine mutation
            event.actor_type = SecurityActorType::kService;
            event.safe_actor_id = safe_actor_label(context);
            event.subject_type = SecuritySubjectType::kCoordinatorSigningKey;
            event.safe_subject_id = request->signing_key_id();
            event.safe_signing_key_id = request->signing_key_id();
            event.outcome = SecurityOutcome::kCompleted;
            event.reason_code = "RevokeCoordinatorSigningKey";
            event.request_id = request->request_id();
            event.trace_id = request->trace_id();
            event.safe_details["production_task_issuance_stopped"] =
                issuance_stopped ? "true" : "false";
            security_event_journal_->emit(std::move(event));
        }
        if (security_audit_journal_ != nullptr) {
            SecurityAuditRecord record;
            record.safe_actor_id = safe_actor_label(context);
            record.actor_role = "service";
            record.action = "RevokeCoordinatorSigningKey";
            record.resource_type = "coordinator_signing_key";
            record.resource_id = request->signing_key_id();
            record.outcome = "ACCEPTED";
            record.reason = request->reason();
            record.request_id = request->request_id();
            record.trace_id = request->trace_id();
            record.safe_details["production_task_issuance_stopped"] =
                issuance_stopped ? "true" : "false";
            security_audit_journal_->append(std::move(record));
        }

        if (!bundle_ok) {
            return grpc::Status(grpc::StatusCode::INTERNAL,
                                "coordinator key revoked but trusted-key bundle regeneration "
                                "failed: " +
                                    bundle_reason +
                                    " -- use the recovery tool to regenerate the bundle");
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return grpc::Status(grpc::StatusCode::INTERNAL,
                            std::string("coordinator key revocation failed: ") + error.what());
    }
}

namespace {
fl::coordinator::v1::SecurityEventRecord to_wire_security_event(const SecurityEvent& event) {
    fl::coordinator::v1::SecurityEventRecord wire;
    wire.set_schema_version(event.schema_version);
    wire.set_event_id(event.event_id);
    wire.set_event_type(to_string(event.event_type));
    wire.set_severity(to_string(event.severity));
    wire.set_timestamp(event.timestamp);
    wire.set_source_service(event.source_service);
    wire.set_source_component(event.source_component);
    wire.set_actor_type(to_string(event.actor_type));
    wire.set_safe_actor_id(event.safe_actor_id);
    wire.set_subject_type(to_string(event.subject_type));
    wire.set_safe_subject_id(event.safe_subject_id);
    wire.set_worker_id(event.worker_id);
    wire.set_run_id(event.run_id);
    wire.set_round_id(event.round_id);
    wire.set_task_id(event.task_id);
    wire.set_safe_signing_key_id(event.safe_signing_key_id);
    wire.set_request_id(event.request_id);
    wire.set_trace_id(event.trace_id);
    wire.set_outcome(to_string(event.outcome));
    wire.set_reason_code(event.reason_code);
    for (const auto& [key, value] : event.safe_details) {
        (*wire.mutable_safe_details())[key] = value;
    }
    wire.set_payload_checksum(event.payload_checksum);
    return wire;
}
}  // namespace

grpc::Status CoordinatorServiceImpl::ListSecurityEvents(
    grpc::ServerContext* context,
    const fl::coordinator::v1::ListSecurityEventsRequest* request,
    fl::coordinator::v1::ListSecurityEventsResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(security_event_journal_, context, "ListSecurityEvents");
        return *rejection;
    }
    if (security_event_journal_ == nullptr) {
        return grpc::Status(grpc::StatusCode::UNIMPLEMENTED,
                            "no security event journal is configured on this server");
    }
    SecurityEventJournal::ListFilters filters;
    filters.after_event_id = request->after_event_id();
    filters.limit = request->limit() > 0 ? request->limit() : 100;
    if (!request->min_severity().empty()) {
        SecuritySeverity severity{};
        if (!security_severity_from_string(request->min_severity(), severity)) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                                "unrecognized min_severity '" + request->min_severity() + "'");
        }
        filters.min_severity = severity;
    }
    if (!request->subject_type().empty()) {
        SecuritySubjectType subject_type{};
        if (!security_subject_type_from_string(request->subject_type(), subject_type)) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                                "unrecognized subject_type '" + request->subject_type() + "'");
        }
        filters.subject_type = subject_type;
    }
    if (!request->event_type().empty()) {
        SecurityEventType event_type{};
        if (!security_event_type_from_string(request->event_type(), event_type)) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT,
                                "unrecognized event_type '" + request->event_type() + "'");
        }
        filters.event_type = event_type;
    }
    const auto result = security_event_journal_->list(filters);
    for (const auto& event : result.events) {
        *response->add_events() = to_wire_security_event(event);
    }
    response->set_next_cursor(result.next_cursor);
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetSecurityEventSourceHealth(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetSecurityEventSourceHealthRequest* request,
    fl::coordinator::v1::GetSecurityEventSourceHealthResponse* response) {
    (void)request;
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "GetSecurityEventSourceHealth");
        return *rejection;
    }
    response->set_checked_at_unix_s(now_unix_s());

    if (security_event_journal_ != nullptr) {
        auto* coordinator_source = response->add_sources();
        coordinator_source->set_source_service("coordinator");
        coordinator_source->set_last_event_at(security_event_journal_->last_record_timestamp());
        coordinator_source->set_record_count(security_event_journal_->size());
        coordinator_source->set_recovered_line_count(
            security_event_journal_->recovered_line_count());
        coordinator_source->set_corrupted(security_event_journal_->recovered_line_count() > 0);
        coordinator_source->set_retention_active(security_event_journal_->has_rotated());
    }

    {
        std::lock_guard<std::mutex> lock(batch_stats_mutex_);
        auto* worker_source = response->add_sources();
        worker_source->set_source_service("python-worker");
        worker_source->set_last_event_at(batch_stats_.last_accepted_at);
        worker_source->set_batches_accepted(batch_stats_.batches_accepted);
        worker_source->set_batches_rejected(batch_stats_.batches_rejected);
        worker_source->set_distinct_workers_seen(batch_stats_.distinct_worker_ids_seen.size());
    }
    return grpc::Status::OK;
}

namespace {

// Work Package L's "bounded batch/event size" requirement -- a batch
// exceeding this many events is rejected wholesale (accepted_event_count
// stays 0), never silently truncated. Per-event field bounds (reason_code
// length, safe_details key/value counts) are enforced by
// validate_security_event, the same shared bound every other event
// producer in this process already goes through.
constexpr int kMaxSecurityEventBatchSize = 200;

// Converts one worker-submitted event into the shared SecurityEvent
// schema, or returns std::nullopt if any enum-vocabulary field the
// worker sent does not match this process's shared registry (see
// security_event.hpp's from_string functions) -- an unrecognized vocabulary
// value means the sending worker is running a schema this coordinator
// does not understand, so that one event is skipped (bumping
// rejected_event_count) rather than failing the whole, already-signature-
// verified batch.
std::optional<SecurityEvent> security_event_from_worker_payload(
    const fl::worker::v1::WorkerSecurityEventPayload& payload, const std::string& worker_id) {
    SecurityEvent event;
    if (!security_event_type_from_string(payload.event_type(), event.event_type)) {
        return std::nullopt;
    }
    if (!security_severity_from_string(payload.severity(), event.severity)) {
        return std::nullopt;
    }
    if (!security_actor_type_from_string(payload.actor_type(), event.actor_type)) {
        return std::nullopt;
    }
    if (!security_subject_type_from_string(payload.subject_type(), event.subject_type)) {
        return std::nullopt;
    }
    if (!security_outcome_from_string(payload.outcome(), event.outcome)) {
        return std::nullopt;
    }
    event.schema_version = static_cast<int>(payload.schema_version());
    event.timestamp = payload.timestamp();
    event.source_service = "python-worker";
    event.source_component = payload.source_component();
    event.safe_actor_id = payload.safe_actor_id();
    event.safe_subject_id = payload.safe_subject_id();
    event.worker_id = worker_id;
    event.run_id = payload.run_id();
    event.round_id = payload.round_id();
    event.task_id = payload.task_id();
    event.safe_signing_key_id = payload.safe_signing_key_id();
    event.request_id = payload.request_id();
    event.trace_id = payload.trace_id();
    event.reason_code = payload.reason_code();
    for (const auto& [key, value] : payload.safe_details()) {
        event.safe_details[key] = value;
    }
    return event;
}

}  // namespace

grpc::Status CoordinatorServiceImpl::SubmitWorkerSecurityEvents(
    grpc::ServerContext* context,
    const fl::coordinator::v1::SubmitWorkerSecurityEventsRequest* request,
    fl::coordinator::v1::SubmitWorkerSecurityEventsResponse* response) {
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        return *rejection;
    }
    auto record_rejection = [&]() {
        std::lock_guard<std::mutex> lock(batch_stats_mutex_);
        ++batch_stats_.batches_rejected;
    };
    if (identity_registry_ == nullptr || signing_key_registry_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "worker security-event submission requires both a worker identity "
                            "registry and a signing-key registry to be configured");
    }
    if (security_event_journal_ == nullptr) {
        return grpc::Status(grpc::StatusCode::UNIMPLEMENTED,
                            "no security event journal is configured on this server");
    }
    const auto identity_record = identity_registry_->find_by_worker_id(request->worker_id());
    if (!identity_record.has_value()) {
        response->set_rejection_code("unknown_worker");
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "unknown worker_id: " + request->worker_id());
    }
    if (identity_record->registration_status == WorkerIdentityStatus::kRevoked ||
        identity_record->registration_status == WorkerIdentityStatus::kSuspended ||
        identity_record->registration_status == WorkerIdentityStatus::kExpired) {
        response->set_rejection_code("worker_status_forbids_submission");
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "worker_id '" + request->worker_id() + "' has status '" +
                                to_string(identity_record->registration_status) +
                                "', which does not permit security-event submission");
    }

    const auto& batch = request->batch();
    if (batch.worker_id() != request->worker_id()) {
        response->set_rejection_code("payload_worker_mismatch");
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "batch payload worker_id does not match the request's worker_id");
    }
    if (batch.events_size() > kMaxSecurityEventBatchSize) {
        response->set_rejection_code("batch_too_large");
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "batch exceeds the maximum of " +
                                std::to_string(kMaxSecurityEventBatchSize) + " events");
    }
    if (!request->has_envelope()) {
        response->set_rejection_code("envelope_missing");
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "a signed envelope is required for SubmitWorkerSecurityEvents");
    }
    const auto& envelope = request->envelope();

    const auto resolved_key = resolve_signing_key(signing_key_registry_,
                                                  *identity_record,
                                                  envelope.signing_key_id(),
                                                  SignedMessageKind::kSecurityEventBatch,
                                                  now_unix_s());
    if (!resolved_key.ok) {
        response->set_rejection_code(resolved_key.rejection_code);
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, resolved_key.reason);
    }

    const auto hash_result = security_event_batch_payload_hash_input(batch);
    if (!hash_result.ok) {
        response->set_rejection_code("payload_hash_mismatch");
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "security-event batch rejected: " + hash_result.reason);
    }
    const auto verification = verify_signed_envelope(
        envelope,
        static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURITY_EVENT_BATCH),
        hash_result.hash_input,
        resolved_key.public_key_hex,
        now_unix_s(),
        /*future_issued_tolerance_seconds=*/30.0);
    if (!verification.valid) {
        response->set_rejection_code(verification.rejection_code);
        record_rejection();
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "security-event batch rejected: " + verification.reason);
    }

    ReplayCandidate replay_candidate;
    bool have_replay_candidate = false;
    if (replay_store_ != nullptr) {
        replay_candidate.worker_id = request->worker_id();
        replay_candidate.signing_key_id = envelope.signing_key_id();
        replay_candidate.message_stream = MessageStream::kSecurityEvents;
        replay_candidate.sequence_number = envelope.sequence_number();
        replay_candidate.nonce = envelope.nonce();
        replay_candidate.now_unix_s = now_unix_s();
        const double window = envelope.expires_at() - envelope.issued_at();
        replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
        const auto replay_decision = replay_store_->validate(replay_candidate);
        if (!replay_decision.accepted) {
            response->set_rejection_code(to_string(replay_decision.reason));
            record_rejection();
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "security-event batch rejected: " + replay_decision.detail);
        }
        have_replay_candidate = true;
    }
    if (have_replay_candidate) {
        replay_store_->commit(replay_candidate);
    }

    // From here, the batch's signature/replay checks have passed --
    // individual malformed events are skipped, never fatal to the whole
    // (already-authenticated) batch.
    std::uint32_t accepted_count = 0;
    std::uint32_t rejected_count = 0;
    std::string last_accepted_event_id;
    for (const auto& payload : batch.events()) {
        auto converted = security_event_from_worker_payload(payload, request->worker_id());
        if (!converted.has_value()) {
            ++rejected_count;
            continue;
        }
        const auto validation = validate_security_event(*converted);
        if (!validation.valid) {
            ++rejected_count;
            continue;
        }
        const auto assigned_id = security_event_journal_->emit(std::move(*converted));
        if (assigned_id.empty()) {
            ++rejected_count;
            continue;
        }
        last_accepted_event_id = assigned_id;
        ++accepted_count;
    }

    {
        std::lock_guard<std::mutex> lock(batch_stats_mutex_);
        ++batch_stats_.batches_accepted;
        batch_stats_.last_accepted_at = iso8601_from_unix_seconds(now_unix_s());
        batch_stats_.distinct_worker_ids_seen.insert(request->worker_id());
    }

    SecurityEvent batch_event;
    batch_event.source_service = "coordinator";
    batch_event.source_component = "coordinator_service";
    batch_event.event_type = SecurityEventType::kWorkerSecurityEventBatchAccepted;
    batch_event.severity = default_severity(batch_event.event_type);
    batch_event.actor_type = SecurityActorType::kWorker;
    batch_event.safe_actor_id = request->worker_id();
    batch_event.subject_type = SecuritySubjectType::kWorkerEventBatch;
    batch_event.safe_subject_id = request->worker_id();
    batch_event.worker_id = request->worker_id();
    batch_event.outcome = SecurityOutcome::kAccepted;
    batch_event.safe_details["accepted_event_count"] = std::to_string(accepted_count);
    batch_event.safe_details["rejected_event_count"] = std::to_string(rejected_count);
    security_event_journal_->emit(std::move(batch_event));

    response->set_accepted(true);
    response->set_accepted_event_count(accepted_count);
    response->set_rejected_event_count(rejected_count);
    response->set_last_accepted_event_id(last_accepted_event_id);
    return grpc::Status::OK;
}

// Secure Cohort Handshake and Signed Roster Runtime slice
// (docs/secure-cohort-handshake-foundation.md): five of the six
// secure-aggregation RPCs are now real, live handlers. SubmitMaskedClientUpdate
// deliberately stays an explicit UNIMPLEMENTED override -- see this
// task's own scope boundary ("do not implement a partial or unsafe
// handler" for masked-update submission) -- until the entire
// masked-update execution boundary is separately completed and
// validated.
grpc::Status CoordinatorServiceImpl::AdvertiseSecureAggregationKey(
    grpc::ServerContext* context,
    const fl::coordinator::v1::AdvertiseSecureAggregationKeyRequest* request,
    fl::coordinator::v1::AdvertiseSecureAggregationKeyResponse* response) {
    response->set_accepted(false);
    response->set_rejection_reason(
        fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_UNSPECIFIED);

    if (secure_aggregation_manager_ == nullptr) {
        response->set_reason("secure_aggregation_unavailable");
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "this coordinator has no secure aggregation session manager configured");
    }
    if (!request->has_key_advertisement()) {
        response->set_reason("key_advertisement_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, "key_advertisement is required");
    }
    const auto& advertisement = request->key_advertisement();

    // Same pipeline ordering as Heartbeat/RotateWorkerSigningKey: mTLS
    // identity binding -> signed envelope required -> identity/status ->
    // signing-key resolve -> payload-hash recompute + signature verify
    // -> replay/sequence validate -> domain call -> replay commit only
    // after domain success -> auto-freeze if this advertisement
    // completes the cohort.
    if (const auto rejection =
            reject_if_worker_identity_mismatch(context, advertisement.worker_id())) {
        response->set_reason("certificate_identity_mismatch");
        return *rejection;
    }
    if (!request->has_envelope()) {
        response->set_reason("envelope_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "a signed envelope is required for AdvertiseSecureAggregationKey");
    }
    const auto& envelope = request->envelope();

    if (identity_registry_ == nullptr) {
        response->set_reason("identity_registry_unavailable");
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    const auto identity_record = identity_registry_->find_by_worker_id(advertisement.worker_id());
    if (!identity_record.has_value()) {
        response->set_reason("unknown_worker");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "unknown worker_id: " + advertisement.worker_id());
    }
    if (identity_record->registration_status == WorkerIdentityStatus::kRevoked) {
        response->set_reason("worker_revoked");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "worker_id '" + advertisement.worker_id() + "' is revoked");
    }

    const auto resolved_key =
        resolve_signing_key(signing_key_registry_,
                            *identity_record,
                            envelope.signing_key_id(),
                            SignedMessageKind::kSecureAggregationKeyAdvertisement,
                            now_unix_s());
    if (!resolved_key.ok) {
        response->set_reason(resolved_key.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, resolved_key.reason);
    }

    const auto hash_result = secure_aggregation_key_advertisement_payload_hash_input(advertisement);
    if (!hash_result.ok) {
        response->set_reason("payload_hash_computation_failed");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, hash_result.reason);
    }
    const auto verification = verify_signed_envelope(
        envelope,
        static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::
                             MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT),
        hash_result.hash_input,
        resolved_key.public_key_hex,
        now_unix_s(),
        /*future_issued_tolerance_seconds=*/30.0);
    auto emit_rejection_event = [&](const std::string& code) {
        if (security_event_journal_ == nullptr)
            return;
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = SecurityEventType::kSecureAggregationKeyAdvertisementRejected;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = advertisement.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = advertisement.session_id();
        event.worker_id = advertisement.worker_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = SecurityOutcome::kRejected;
        event.reason_code = code;
        security_event_journal_->emit(std::move(event));
    };
    if (!verification.valid) {
        response->set_reason(verification.rejection_code);
        response->set_rejection_reason(
            fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_INVALID_SIGNATURE);
        emit_rejection_event(verification.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "signed key advertisement rejected: " + verification.reason);
    }

    ReplayCandidate replay_candidate;
    if (replay_store_ != nullptr) {
        replay_candidate.worker_id = advertisement.worker_id();
        replay_candidate.signing_key_id = envelope.signing_key_id();
        replay_candidate.message_stream = MessageStream::kSecureAggregation;
        replay_candidate.sequence_number = envelope.sequence_number();
        replay_candidate.nonce = envelope.nonce();
        replay_candidate.now_unix_s = now_unix_s();
        const double window = envelope.expires_at() - envelope.issued_at();
        replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
        const auto replay_decision = replay_store_->validate(replay_candidate);
        if (!replay_decision.accepted) {
            response->set_reason(to_string(replay_decision.reason));
            emit_rejection_event(to_string(replay_decision.reason));
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "signed key advertisement rejected: " + replay_decision.detail);
        }
    }

    fl::coordinator::v1::SecureAggregationSessionStatus status;
    try {
        status = secure_aggregation_manager_->advertise_key(advertisement, now_unix_s());
    } catch (const std::exception& error) {
        response->set_reason("advertise_key_rejected");
        emit_rejection_event("advertise_key_rejected");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, error.what());
    }

    // Replay/sequence state is committed only after domain processing
    // above has actually succeeded -- same ordering discipline as
    // every other signed-message RPC in this file.
    if (replay_store_ != nullptr) {
        replay_store_->commit(replay_candidate);
    }
    if (security_event_journal_ != nullptr) {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = SecurityEventType::kSecureAggregationKeyAdvertisementAccepted;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = advertisement.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = advertisement.session_id();
        event.worker_id = advertisement.worker_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = SecurityOutcome::kAccepted;
        security_event_journal_->emit(std::move(event));
    }

    // Work items 9/10: auto-freeze the cohort the moment this
    // advertisement completes it -- no dedicated "freeze" RPC exists in
    // this protocol's surface (see the design doc's audit finding).
    if (status.key_advertisement_count() == status.cohort_size()) {
        try {
            const auto active_identity = coordinator_active_identity_ != nullptr
                                             ? coordinator_active_identity_->current()
                                             : nullptr;
            secure_aggregation_manager_->freeze_cohort(
                advertisement.session_id(), now_unix_s(), active_identity.get());
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "secure_aggregation_session_manager";
                event.event_type = SecurityEventType::kSecureAggregationCohortFrozen;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kCoordinator;
                event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                event.safe_subject_id = advertisement.session_id();
                event.outcome = SecurityOutcome::kCompleted;
                event.safe_details["signed"] = active_identity != nullptr ? "true" : "false";
                security_event_journal_->emit(std::move(event));
            }
        } catch (const std::exception& error) {
            // A freeze failure must not un-accept an already-accepted
            // advertisement -- logged, not propagated as an RPC failure
            // for this call (the session is left in whatever state
            // freeze_cohort left it in; a subsequent GetSecureAggregationSession
            // call reveals the real state).
            std::cerr << "secure aggregation cohort freeze failed for session "
                      << advertisement.session_id() << ": " << error.what() << "\n";
        }
    }

    response->set_accepted(true);
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetFrozenCohortRoster(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetFrozenCohortRosterRequest* request,
    fl::coordinator::v1::GetFrozenCohortRosterResponse* response) {
    response->set_available(false);
    if (secure_aggregation_manager_ == nullptr) {
        response->set_reason("secure_aggregation_unavailable");
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "this coordinator has no secure aggregation session manager configured");
    }
    if (const auto rejection = reject_if_worker_identity_mismatch(context, request->worker_id())) {
        response->set_reason("certificate_identity_mismatch");
        return *rejection;
    }
    const auto roster = secure_aggregation_manager_->get_frozen_roster(request->session_id());
    if (!roster.has_value()) {
        // Work Package K: "do not expose the roster until cohort
        // freeze" -- an ordinary, expected outcome for a worker polling
        // ahead of freeze, or for an unknown session_id, not an RPC
        // error. Workers must poll with their own bounded backoff (this
        // RPC itself has no server-side blocking/streaming wait).
        response->set_reason("cohort_not_yet_frozen_or_unknown_session");
        return grpc::Status::OK;
    }
    const bool is_participant = std::any_of(
        roster->participants().begin(), roster->participants().end(), [&](const auto& participant) {
            return participant.worker_id() == request->worker_id();
        });
    if (!is_participant) {
        response->set_reason("not_a_participant");
        return grpc::Status(
            grpc::StatusCode::PERMISSION_DENIED,
            "worker_id '" + request->worker_id() + "' is not a participant in this cohort");
    }
    response->set_available(true);
    *response->mutable_roster() = *roster;
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::SubmitMaskedClientUpdate(
    grpc::ServerContext* context,
    const fl::coordinator::v1::SubmitMaskedClientUpdateRequest* request,
    fl::coordinator::v1::SubmitMaskedClientUpdateResponse* response) {
    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area N: real, live handler -- the exact same
    // SIGNED_WORKER_MESSAGE pipeline AdvertiseSecureAggregationKey
    // already established (mTLS -> envelope required -> identity/
    // status -> signing-key resolve -> payload-hash recompute ->
    // Ed25519 verify -> replay/sequence validate -> domain call ->
    // replay commit only after domain success), then: attempt
    // finalization only when the complete frozen cohort has now
    // submitted, and only then bridge into RunInstance to actually
    // advance the model version. Never decodes or exposes an
    // individual accepted contribution.
    response->set_accepted(false);
    response->set_rejection_reason(
        fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_UNSPECIFIED);

    if (secure_aggregation_manager_ == nullptr) {
        response->set_reason("secure_aggregation_unavailable");
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "this coordinator has no secure aggregation session manager configured");
    }
    if (!request->has_masked_update()) {
        response->set_reason("masked_update_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, "masked_update is required");
    }
    const auto& update = request->masked_update();

    if (const auto rejection = reject_if_worker_identity_mismatch(context, update.worker_id())) {
        response->set_reason("certificate_identity_mismatch");
        return *rejection;
    }
    if (!request->has_envelope()) {
        response->set_reason("envelope_missing");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "a signed envelope is required for SubmitMaskedClientUpdate");
    }
    const auto& envelope = request->envelope();

    if (identity_registry_ == nullptr) {
        response->set_reason("identity_registry_unavailable");
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                            "no worker identity registry is configured");
    }
    const auto identity_record = identity_registry_->find_by_worker_id(update.worker_id());
    if (!identity_record.has_value()) {
        response->set_reason("unknown_worker");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "unknown worker_id: " + update.worker_id());
    }
    if (identity_record->registration_status == WorkerIdentityStatus::kRevoked) {
        response->set_reason("worker_revoked");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "worker_id '" + update.worker_id() + "' is revoked");
    }

    const auto resolved_key = resolve_signing_key(signing_key_registry_,
                                                  *identity_record,
                                                  envelope.signing_key_id(),
                                                  SignedMessageKind::kSecureAggregationMaskedUpdate,
                                                  now_unix_s());
    if (!resolved_key.ok) {
        response->set_reason(resolved_key.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, resolved_key.reason);
    }

    const auto hash_result = masked_client_update_payload_hash_input(update);
    if (!hash_result.ok) {
        response->set_reason("payload_hash_computation_failed");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, hash_result.reason);
    }
    const auto verification = verify_signed_envelope(
        envelope,
        static_cast<int>(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE),
        hash_result.hash_input,
        resolved_key.public_key_hex,
        now_unix_s(),
        /*future_issued_tolerance_seconds=*/30.0);
    auto emit_rejection_event = [&](const std::string& code) {
        if (security_event_journal_ == nullptr)
            return;
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = SecurityEventType::kSecureAggregationMaskedUpdateRejected;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = update.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = update.session_id();
        event.worker_id = update.worker_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = SecurityOutcome::kRejected;
        event.reason_code = code;
        security_event_journal_->emit(std::move(event));
    };
    // Secure User-Level DP Operations, Observability, and Release
    // Evidence slice, Work Area D: distinct from
    // emit_rejection_event's generic kSecureAggregationMaskedUpdateRejected
    // (emitted for every rejection reason on this RPC, privacy-attested
    // or not) -- this second, privacy-specific event exists so an
    // operator can isolate "how many attestation-specific rejections
    // happened" without inferring it from reason_code string matching
    // on the generic event.
    auto emit_attestation_event = [&](bool accepted, const std::string& code) {
        if (security_event_journal_ == nullptr)
            return;
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = accepted ? SecurityEventType::kSecureUserLevelDpAttestationAccepted
                                    : SecurityEventType::kSecureUserLevelDpAttestationRejected;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = update.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = update.session_id();
        event.worker_id = update.worker_id();
        event.run_id = update.run_id();
        event.round_id = update.round_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = accepted ? SecurityOutcome::kAccepted : SecurityOutcome::kRejected;
        event.reason_code = code;
        security_event_journal_->emit(std::move(event));
    };
    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: distinct from emit_attestation_event -- isolates
    // indicator-binding rejections from user-level-attestation
    // rejections on the same submission, same reasoning as
    // emit_sample_record_event below.
    auto emit_indicator_event = [&](bool accepted, const std::string& code) {
        if (security_event_journal_ == nullptr)
            return;
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = accepted ? SecurityEventType::kSecureAdaptiveClippingIndicatorAccepted
                                    : SecurityEventType::kSecureAdaptiveClippingIndicatorRejected;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = update.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = update.session_id();
        event.worker_id = update.worker_id();
        event.run_id = update.run_id();
        event.round_id = update.round_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = accepted ? SecurityOutcome::kAccepted : SecurityOutcome::kRejected;
        event.reason_code = code;
        security_event_journal_->emit(std::move(event));
    };
    if (!verification.valid) {
        response->set_reason(verification.rejection_code);
        response->set_rejection_reason(
            fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_INVALID_SIGNATURE);
        emit_rejection_event(verification.rejection_code);
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                            "signed masked update rejected: " + verification.reason);
    }

    ReplayCandidate replay_candidate;
    if (replay_store_ != nullptr) {
        replay_candidate.worker_id = update.worker_id();
        replay_candidate.signing_key_id = envelope.signing_key_id();
        // A distinct stream from AdvertiseSecureAggregationKey's
        // kSecureAggregation -- see MessageStream::kSecureAggregationMaskedUpdate's
        // own doc comment for the real collision this fixes (found live
        // via this slice's own 3-worker Docker validation).
        replay_candidate.message_stream = MessageStream::kSecureAggregationMaskedUpdate;
        replay_candidate.sequence_number = envelope.sequence_number();
        replay_candidate.nonce = envelope.nonce();
        replay_candidate.now_unix_s = now_unix_s();
        const double window = envelope.expires_at() - envelope.issued_at();
        replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
        const auto replay_decision = replay_store_->validate(replay_candidate);
        if (!replay_decision.accepted) {
            response->set_reason(to_string(replay_decision.reason));
            emit_rejection_event(to_string(replay_decision.reason));
            return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                "signed masked update rejected: " + replay_decision.detail);
        }
    }

    // Secure Hybrid Differential Privacy Runtime slice: validated inside
    // the attestation try-block below (where sample_payload is in
    // scope), but -- matching replay_candidate's own "commit only after
    // domain processing has actually succeeded" discipline just above --
    // actually committed/appended only after
    // secure_aggregation_manager_->submit_masked_update() durably
    // accepts this submission, never eagerly. A masked update that
    // fails domain submission must never leave behind a phantom
    // sample-level ledger entry or a burned replay/monotonicity slot.
    ReplayCandidate sample_replay_candidate;
    bool have_sample_replay_candidate = false;
    MonotonicityCandidate sample_monotonicity_candidate;
    bool have_sample_monotonicity_candidate = false;
    std::optional<fl::coordinator::SampleLevelLedgerEntry> pending_sample_ledger_entry;

    // Secure Hybrid Differential Privacy Runtime slice: distinct from
    // emit_attestation_event -- this event pair is specifically about
    // the SAMPLE-level record's own verification outcome, so an
    // operator can isolate sample-record rejections from user-
    // attestation rejections on the same submission.
    auto emit_sample_record_event = [&](bool accepted, const std::string& code) {
        if (security_event_journal_ == nullptr)
            return;
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = accepted ? SecurityEventType::kSecureHybridDpSampleRecordAccepted
                                    : SecurityEventType::kSecureHybridDpSampleRecordRejected;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = update.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = update.session_id();
        event.worker_id = update.worker_id();
        event.run_id = update.run_id();
        event.round_id = update.round_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = accepted ? SecurityOutcome::kAccepted : SecurityOutcome::kRejected;
        event.reason_code = code;
        security_event_journal_->emit(std::move(event));
    };
    // Secure User-Level Differential Privacy Runtime slice, Work Area
    // L, extended by the Secure Hybrid Differential Privacy Runtime
    // slice: required for a run whose privacy_mode is kUserLevelDp OR
    // kHybridDp (NONE/SAMPLE_LEVEL-alone secure updates never carry or
    // require a user-level attestation, unchanged). The coordinator
    // never decodes or inspects the masked tensors themselves here --
    // only the independently signed attestation/record and their
    // structural binding to this exact update.
    try {
        auto& privacy_run = manager_->get(update.run_id());
        const bool is_hybrid_update =
            privacy_run.privacy_mode() == fl::core::PrivacyMode::kHybridDp;
        if (privacy_run.privacy_mode() == fl::core::PrivacyMode::kUserLevelDp || is_hybrid_update) {
            if (!update.has_user_level_attestation()) {
                response->set_reason("user_level_attestation_missing");
                response->set_rejection_reason(
                    fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_ATTESTATION_MISSING);
                emit_rejection_event("user_level_attestation_missing");
                emit_attestation_event(false, "user_level_attestation_missing");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "a signed user-level privacy attestation is required for this "
                                    "secure user-level-DP round");
            }
            const auto& attestation = update.user_level_attestation();
            // Structural binding (Work Area J): every identifying field
            // on the attestation must match the outer update it rides
            // alongside -- a validly-signed attestation for a
            // *different* worker/client/session/task/round/model
            // version must never be accepted here.
            const bool binding_ok = attestation.worker_id() == update.worker_id() &&
                                    attestation.client_id() == update.client_id() &&
                                    attestation.run_id() == update.run_id() &&
                                    attestation.round_id() == update.round_id() &&
                                    attestation.task_id() == update.task_id() &&
                                    attestation.session_id() == update.session_id() &&
                                    attestation.model_version() == update.model_version() &&
                                    attestation.operation_completed();
            if (!binding_ok) {
                response->set_reason("user_level_attestation_binding_mismatch");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_ATTESTATION_BINDING_MISMATCH);
                emit_rejection_event("user_level_attestation_binding_mismatch");
                emit_attestation_event(false, "user_level_attestation_binding_mismatch");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed user-level privacy attestation does not match this "
                                    "submission's worker/client/session/task/round/model_version");
            }
            // Work Area J: "wrong worker" is rejected by requiring the
            // attestation to be signed by the SAME signing key already
            // resolved and verified for the outer envelope -- not by a
            // second, independent key lookup.
            if (attestation.signing_key_id() != envelope.signing_key_id()) {
                response->set_reason("user_level_attestation_wrong_signing_key");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_ATTESTATION_INVALID_SIGNATURE);
                emit_rejection_event("user_level_attestation_wrong_signing_key");
                emit_attestation_event(false, "user_level_attestation_wrong_signing_key");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed user-level privacy attestation was not signed by the same "
                    "key as this submission's outer envelope");
            }
            const auto attestation_verification = verify_user_level_privacy_attestation(
                attestation, resolved_key.public_key_hex, now_unix_s());
            if (!attestation_verification.valid) {
                response->set_reason(attestation_verification.rejection_code);
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_ATTESTATION_INVALID_SIGNATURE);
                emit_rejection_event(attestation_verification.rejection_code);
                emit_attestation_event(false, attestation_verification.rejection_code);
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed user-level privacy attestation rejected: " +
                                        attestation_verification.reason);
            }
            emit_attestation_event(true, "ok");
        }
        // Secure Adaptive Clipping with Private Indicator Aggregation
        // slice: inserted here (after the user-level attestation block,
        // before the hybrid sample-record block), following the exact
        // same staged-then-committed discipline every other check in
        // this function uses -- see
        // docs/secure-adaptive-clipping-semantics.md section 16.
        if (privacy_run.secure_adaptive_clipping_active()) {
            if (!update.has_adaptive_clipping_binding()) {
                response->set_reason("adaptive_clipping_binding_missing");
                response->set_rejection_reason(
                    fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_INDICATOR_MISSING);
                emit_rejection_event("adaptive_clipping_binding_missing");
                emit_indicator_event(false, "adaptive_clipping_binding_missing");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "a signed adaptive clipping binding is required for this secure "
                    "adaptive-clipping round");
            }
            const auto& adaptive_binding = update.adaptive_clipping_binding();
            const bool adaptive_binding_ok =
                adaptive_binding.worker_id() == update.worker_id() &&
                adaptive_binding.client_id() == update.client_id() &&
                adaptive_binding.run_id() == update.run_id() &&
                adaptive_binding.round_id() == update.round_id() &&
                adaptive_binding.task_id() == update.task_id() &&
                adaptive_binding.session_id() == update.session_id() &&
                adaptive_binding.model_version() == update.model_version() &&
                adaptive_binding.operation_completed();
            if (!adaptive_binding_ok) {
                response->set_reason("adaptive_clipping_binding_mismatch");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_INDICATOR_BINDING_MISMATCH);
                emit_rejection_event("adaptive_clipping_binding_mismatch");
                emit_indicator_event(false, "adaptive_clipping_binding_mismatch");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed adaptive clipping binding does not match this submission's "
                    "worker/client/session/task/round/model_version");
            }
            if (adaptive_binding.signing_key_id() != envelope.signing_key_id()) {
                response->set_reason("adaptive_clipping_binding_wrong_signing_key");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_INDICATOR_INVALID_SIGNATURE);
                emit_rejection_event("adaptive_clipping_binding_wrong_signing_key");
                emit_indicator_event(false, "adaptive_clipping_binding_wrong_signing_key");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed adaptive clipping binding was not signed by the same key as "
                    "this submission's outer envelope");
            }
            const auto adaptive_binding_verification = verify_adaptive_clipping_binding(
                adaptive_binding, resolved_key.public_key_hex, now_unix_s());
            if (!adaptive_binding_verification.valid) {
                response->set_reason(adaptive_binding_verification.rejection_code);
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_INDICATOR_INVALID_SIGNATURE);
                emit_rejection_event(adaptive_binding_verification.rejection_code);
                emit_indicator_event(false, adaptive_binding_verification.rejection_code);
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed adaptive clipping binding rejected: " +
                                        adaptive_binding_verification.reason);
            }
            // Stale clip-state rejection (Work Area L): a worker holding
            // a task signed against an OLDER clip-state step count than
            // the one currently live for this run must be rejected, not
            // silently accepted against a mismatched bound -- the
            // current step count only ever advances at finalize time
            // (see run_manager.cpp's apply_secure_aggregate_and_advance),
            // so it is unchanged since this round's tasks were signed
            // unless the round has already finalized underneath this
            // submission.
            if (adaptive_binding.clip_state_step_count() !=
                privacy_run.adaptive_clip_state_step_count()) {
                response->set_reason("adaptive_clipping_stale_clip_state");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_INDICATOR_STALE_CLIP_STATE);
                emit_rejection_event("adaptive_clipping_stale_clip_state");
                emit_indicator_event(false, "adaptive_clipping_stale_clip_state");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed adaptive clipping binding was signed against a clip-state step "
                    "count that is no longer current for this run");
            }
            emit_indicator_event(true, "ok");
        }
        // Secure Hybrid Differential Privacy Runtime slice, Work Areas
        // K/L/M: closes the pre-existing sample_privacy_record_hash gap
        // (docs/secure-hybrid-dp-runtime-audit.md) -- reuses the exact
        // cleartext-path verification sequence (SubmitClientResult's
        // own signature/binding/replay/monotonicity/budget-contradiction
        // block) against the sample record carried alongside this
        // masked update, rather than a new parallel implementation.
        if (is_hybrid_update) {
            if (!request->has_sample_privacy_record_envelope() ||
                !request->has_sample_privacy_record_payload()) {
                response->set_reason("sample_privacy_record_missing");
                response->set_rejection_reason(
                    fl::coordinator::v1::SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_MISSING);
                emit_rejection_event("sample_privacy_record_missing");
                emit_sample_record_event(false, "sample_privacy_record_missing");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "a signed sample-level privacy record is required for this "
                                    "secure hybrid-DP round");
            }
            const auto& sample_envelope = request->sample_privacy_record_envelope();
            const auto& sample_payload = request->sample_privacy_record_payload();
            // Work Package K's "Result-to-Privacy Key Consistency"
            // rule, reused verbatim: the sample record must be signed
            // by the SAME signing key as the outer masked update.
            if (sample_envelope.signing_key_id() != envelope.signing_key_id()) {
                response->set_reason("sample_privacy_record_key_mismatch");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_INVALID_SIGNATURE);
                emit_rejection_event("sample_privacy_record_key_mismatch");
                emit_sample_record_event(false, "sample_privacy_record_key_mismatch");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "sample privacy record signing_key_id does not match the outer "
                                    "masked update's signing_key_id");
            }
            const auto sample_hash_result =
                sample_privacy_record_payload_hash_input(sample_payload);
            if (!sample_hash_result.ok) {
                response->set_reason("sample_privacy_payload_hash_computation_failed");
                emit_rejection_event("sample_privacy_payload_hash_computation_failed");
                emit_sample_record_event(false, "sample_privacy_payload_hash_computation_failed");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed sample privacy record rejected: " + sample_hash_result.reason);
            }
            const auto sample_verification = verify_signed_envelope(
                sample_envelope,
                static_cast<int>(
                    fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD),
                sample_hash_result.hash_input,
                resolved_key.public_key_hex,
                now_unix_s(),
                /*future_issued_tolerance_seconds=*/30.0);
            if (!sample_verification.valid) {
                response->set_reason(sample_verification.rejection_code);
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_INVALID_SIGNATURE);
                emit_rejection_event(sample_verification.rejection_code);
                emit_sample_record_event(false, sample_verification.rejection_code);
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed sample privacy record rejected: " + sample_verification.reason);
            }
            // Binds sample_privacy_record_hash (already covered by the
            // outer masked update's own signature, see
            // signed_envelope_verifier.cpp's
            // masked_client_update_payload_hash_input) to the actual
            // record just verified above -- the wrapping envelope's own
            // payload_hash IS the canonical hash of this record (see
            // the semantics doc section 5), so no separate hash
            // function is introduced.
            const bool binding_ok =
                sample_payload.worker_id() == update.worker_id() &&
                sample_payload.run_id() == update.run_id() &&
                sample_payload.round_id() == update.round_id() &&
                sample_payload.client_id() == update.client_id() &&
                sample_payload.task_id() == update.task_id() &&
                update.sample_privacy_record_hash() == sample_envelope.payload_hash();
            if (!binding_ok) {
                response->set_reason("sample_privacy_record_binding_mismatch");
                response->set_rejection_reason(
                    fl::coordinator::v1::
                        SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_BINDING_MISMATCH);
                emit_rejection_event("sample_privacy_record_binding_mismatch");
                emit_sample_record_event(false, "sample_privacy_record_binding_mismatch");
                return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                    "signed sample privacy record does not match this submission's "
                                    "worker/client/run/round/task or its own declared hash");
            }
            if (replay_store_ != nullptr) {
                sample_replay_candidate.worker_id = update.worker_id();
                sample_replay_candidate.signing_key_id = sample_envelope.signing_key_id();
                sample_replay_candidate.message_stream = MessageStream::kPrivacyRecord;
                sample_replay_candidate.sequence_number = sample_envelope.sequence_number();
                sample_replay_candidate.nonce = sample_envelope.nonce();
                sample_replay_candidate.now_unix_s = now_unix_s();
                const double window = sample_envelope.expires_at() - sample_envelope.issued_at();
                sample_replay_candidate.nonce_retention_seconds = window > 1.0 ? window : 1.0;
                const auto sample_replay_decision =
                    replay_store_->validate(sample_replay_candidate);
                if (!sample_replay_decision.accepted) {
                    response->set_reason(to_string(sample_replay_decision.reason));
                    emit_rejection_event(to_string(sample_replay_decision.reason));
                    emit_sample_record_event(false, to_string(sample_replay_decision.reason));
                    return grpc::Status(
                        grpc::StatusCode::PERMISSION_DENIED,
                        "signed sample privacy record rejected: " + sample_replay_decision.detail);
                }
                have_sample_replay_candidate = true;
            }
            if (monotonicity_store_ != nullptr) {
                sample_monotonicity_candidate.run_id = sample_payload.run_id();
                sample_monotonicity_candidate.client_id = sample_payload.client_id();
                sample_monotonicity_candidate.worker_id = sample_payload.worker_id();
                sample_monotonicity_candidate.accountant_type =
                    static_cast<int>(sample_payload.accountant_type());
                sample_monotonicity_candidate.step = sample_payload.accountant_step();
                sample_monotonicity_candidate.epsilon = sample_payload.epsilon();
                sample_monotonicity_candidate.delta = sample_payload.delta();
                sample_monotonicity_candidate.accountant_state_hash =
                    sample_payload.accountant_state_hash();
                sample_monotonicity_candidate.configuration_hash =
                    sample_payload.configuration_hash();
                sample_monotonicity_candidate.round_id = sample_payload.round_id();
                sample_monotonicity_candidate.task_id = sample_payload.task_id();
                sample_monotonicity_candidate.now_unix_s = now_unix_s();
                const auto sample_monotonicity_decision =
                    monotonicity_store_->validate(sample_monotonicity_candidate);
                if (!sample_monotonicity_decision.accepted) {
                    response->set_reason(to_string(sample_monotonicity_decision.reason));
                    emit_rejection_event(to_string(sample_monotonicity_decision.reason));
                    emit_sample_record_event(false, to_string(sample_monotonicity_decision.reason));
                    return grpc::Status(grpc::StatusCode::PERMISSION_DENIED,
                                        "signed sample privacy record rejected: " +
                                            sample_monotonicity_decision.detail);
                }
                have_sample_monotonicity_candidate = true;
            }
            const auto sample_contradiction =
                budget_decision_contradiction_reason(sample_payload.budget_decision());
            if (!sample_contradiction.empty()) {
                response->set_reason("sample_budget_decision_contradiction");
                emit_rejection_event("sample_budget_decision_contradiction");
                emit_sample_record_event(false, "sample_budget_decision_contradiction");
                return grpc::Status(
                    grpc::StatusCode::PERMISSION_DENIED,
                    "signed sample privacy record rejected: " + sample_contradiction);
            }
            // Work Area O's sample-level publication boundary: staged
            // here, actually committed/appended only after
            // secure_aggregation_manager_->submit_masked_update()
            // durably accepts this submission below -- strictly earlier
            // than (and independent of) whether the cohort ever reaches
            // complete-cohort finalization. See
            // docs/secure-hybrid-dp-semantics.md section 7.
            fl::coordinator::SampleLevelLedgerEntry ledger_entry;
            ledger_entry.run_id = sample_payload.run_id();
            ledger_entry.round_id = sample_payload.round_id();
            ledger_entry.client_id = sample_payload.client_id();
            ledger_entry.epsilon = sample_payload.epsilon();
            ledger_entry.delta = sample_payload.delta();
            ledger_entry.noise_multiplier = sample_payload.noise_multiplier();
            ledger_entry.sample_rate = sample_payload.sample_rate();
            ledger_entry.steps = sample_payload.accountant_step();
            ledger_entry.accountant = accountant_type_from_wire(sample_payload.accountant_type());
            pending_sample_ledger_entry = std::move(ledger_entry);
            // Signals "verified OK" (matching emit_attestation_event's
            // identical timing/meaning) -- not a claim of durable
            // commit, which happens later, only once
            // secure_aggregation_manager_->submit_masked_update()
            // itself succeeds.
            emit_sample_record_event(true, "ok");
        }
    } catch (const std::exception& error) {
        response->set_reason("user_level_attestation_verification_failed");
        emit_rejection_event("user_level_attestation_verification_failed");
        emit_attestation_event(false, "user_level_attestation_verification_failed");
        return grpc::Status(
            grpc::StatusCode::PERMISSION_DENIED,
            std::string("user-level privacy attestation verification failed: ") + error.what());
    }

    fl::coordinator::v1::SecureAggregationSessionStatus status;
    try {
        status = secure_aggregation_manager_->submit_masked_update(update, now_unix_s());
    } catch (const std::exception& error) {
        response->set_reason("masked_update_rejected");
        emit_rejection_event("masked_update_rejected");
        return grpc::Status(grpc::StatusCode::PERMISSION_DENIED, error.what());
    }

    // Replay/sequence state is committed only after domain processing
    // above has actually succeeded, and the masked contribution has
    // been durably accepted into the session's in-memory record --
    // same ordering discipline as every other signed-message RPC in
    // this file.
    if (replay_store_ != nullptr) {
        replay_store_->commit(replay_candidate);
    }
    // Secure Hybrid Differential Privacy Runtime slice: the sample-
    // level half's replay/monotonicity commit and ledger append, staged
    // during attestation verification above, now applied -- exactly
    // here, alongside the outer masked-update replay commit, because
    // this is the actual "durably accepted" point (§7 of
    // docs/secure-hybrid-dp-semantics.md): submit_masked_update() has
    // now genuinely succeeded.
    if (have_sample_replay_candidate && replay_store_ != nullptr) {
        replay_store_->commit(sample_replay_candidate);
    }
    if (have_sample_monotonicity_candidate && monotonicity_store_ != nullptr) {
        monotonicity_store_->commit(sample_monotonicity_candidate);
    }
    if (pending_sample_ledger_entry.has_value()) {
        try {
            manager_->get(update.run_id())
                .append_sample_level_ledger_entry(std::move(*pending_sample_ledger_entry));
        } catch (const std::exception&) {
            // The run existing is already guaranteed by everything
            // above having succeeded against it -- this catch exists
            // only so a ledger-append failure can never turn an
            // already-accepted masked update into a failed RPC this
            // late (the same "an aggregate-level failure must not
            // un-accept an individual accepted contribution" principle
            // the finalization block below already follows).
        }
    }
    if (security_event_journal_ != nullptr) {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = SecurityEventType::kSecureAggregationMaskedUpdateAccepted;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kWorker;
        event.safe_actor_id = update.worker_id();
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.safe_subject_id = update.session_id();
        event.worker_id = update.worker_id();
        event.safe_signing_key_id = envelope.signing_key_id();
        event.outcome = SecurityOutcome::kAccepted;
        security_event_journal_->emit(event);
        // Secure Hybrid Differential Privacy Runtime slice: fires only
        // when a sample-level record was actually staged above (i.e.
        // this is a real hybrid submission, not merely a hybrid-mode
        // run's plain accepted-update event) -- marks that BOTH
        // independently signed records (sample-level record,
        // user-level attestation) validated and are now bound into this
        // one accepted contribution.
        if (pending_sample_ledger_entry.has_value()) {
            event.event_type = SecurityEventType::kSecureHybridDpBindingAccepted;
            event.severity = default_severity(event.event_type);
            event.run_id = update.run_id();
            event.round_id = update.round_id();
            security_event_journal_->emit(std::move(event));
        }
    }
    response->set_accepted(true);

    // Work Areas U/V/X/Y: attempt finalization only when the complete
    // frozen cohort has now submitted -- never a partial cohort (the
    // manager's own finalize() already refuses that; this is the live
    // trigger point, not a second safety check). A finalization
    // failure (including the RunInstance bridge failing) is logged,
    // never propagated as a failure of *this* worker's own already-
    // accepted submission -- the same "an aggregate-level failure must
    // not un-accept an individual accepted contribution" pattern
    // AdvertiseSecureAggregationKey's auto-freeze already established.
    if (status.masked_contribution_count() == status.cohort_size()) {
        try {
            // Secure User-Level Differential Privacy Runtime slice,
            // Work Areas P/Q: fetched before finalize() (not just
            // before apply_secure_aggregate_and_advance, as this call
            // site previously did) so the noise provider/std_dev can
            // be computed and passed into finalize() itself -- noise
            // must be added inside finalize(), before its own
            // divide-by-weight-sum step, not after. Recomputes
            // effective_sensitivity the same deterministic way
            // AcquireTask's session-creation gate did (clip_norm +
            // quantization_margin over the run's own manifest) rather
            // than fetching it back from stored session state -- see
            // this file's AcquireTask block for the identical
            // computation this mirrors.
            auto& run = manager_->get(update.run_id());
            fl::core::NoiseProvider* noise_provider = nullptr;
            double noise_std_dev = 0.0;
            double expected_weight_sum = 0.0;
            // Secure Hybrid Differential Privacy Runtime slice: hybrid's
            // user-level half is byte-for-byte the same central-noise
            // computation as plain kUserLevelDp -- see
            // docs/secure-hybrid-dp-semantics.md section 8. Missing
            // kHybridDp here would have meant a hybrid round's aggregate
            // was finalized with NO noise at all, a real correctness gap
            // found and fixed during this slice's own implementation
            // (not live-discovered -- caught by re-reading this exact
            // gate while wiring the hybrid finalize path).
            if (run.privacy_mode() == fl::core::PrivacyMode::kUserLevelDp ||
                run.privacy_mode() == fl::core::PrivacyMode::kHybridDp) {
                const auto& user_level = run.user_level_privacy();
                std::uint64_t total_elements = 0;
                for (const auto& descriptor : run.manifest().tensors) {
                    std::uint64_t element_count = 1;
                    for (const auto dim : descriptor.shape) element_count *= dim;
                    total_elements += element_count;
                }
                const fl::coordinator::FixedPointEncodingProfile secure_profile{};
                const double margin =
                    fl::coordinator::compute_quantization_margin(total_elements, secure_profile);
                // Secure Adaptive Clipping with Private Indicator
                // Aggregation slice: the bound to calibrate model noise
                // against is the adaptive controller's current (this-
                // round) value when adaptive clipping is active for
                // this run -- config_.user_level_privacy.initial_clipping_bound
                // was a real latent under-calibration bug for the
                // secure path (never reachable before this slice, since
                // AcquireTask unconditionally rejected adaptive clipping
                // under secure aggregation).
                const double clip_bound_this_round = run.secure_adaptive_clipping_active()
                                                         ? run.current_adaptive_clip_bound()
                                                         : user_level.initial_clipping_bound;
                const double effective_sensitivity =
                    fl::coordinator::compute_effective_sensitivity(clip_bound_this_round, margin);
                noise_provider = run.user_level_noise_provider();
                noise_std_dev = user_level.noise_multiplier * effective_sensitivity;
                // Work Area L: the fixed-weight-1 integrity check (see
                // finalize()'s own doc comment) -- cohort_size is the
                // frozen participant count this exact session was
                // created with.
                expected_weight_sum = static_cast<double>(status.cohort_size());
            }
            // Secure Adaptive Clipping with Private Indicator
            // Aggregation slice: the indicator sum is decoded BEFORE
            // finalize() (while the session is still in
            // MASKED_UPDATE_COLLECTION with the complete cohort's
            // contributions present) -- see
            // docs/secure-adaptive-clipping-semantics.md section 17.
            // Never decodes or exposes an individual indicator; only
            // the final aggregate count ever crosses this boundary.
            std::optional<std::uint64_t> indicator_over_threshold_count;
            if (run.secure_adaptive_clipping_active()) {
                indicator_over_threshold_count =
                    secure_aggregation_manager_->decode_secure_adaptive_clipping_indicator_count(
                        update.session_id());
                if (security_event_journal_ != nullptr) {
                    SecurityEvent event;
                    event.source_service = "coordinator";
                    event.source_component = "secure_aggregation_session_manager";
                    event.event_type =
                        SecurityEventType::kSecureAdaptiveClippingCompleteCohortReconstructed;
                    event.severity = default_severity(event.event_type);
                    event.actor_type = SecurityActorType::kCoordinator;
                    event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                    event.safe_subject_id = update.session_id();
                    event.run_id = update.run_id();
                    event.round_id = update.round_id();
                    event.outcome = SecurityOutcome::kCompleted;
                    security_event_journal_->emit(std::move(event));
                }
            }
            const auto aggregate = secure_aggregation_manager_->finalize(update.session_id(),
                                                                         now_unix_s(),
                                                                         noise_provider,
                                                                         noise_std_dev,
                                                                         expected_weight_sum);
            const bool is_hybrid_dp = run.privacy_mode() == fl::core::PrivacyMode::kHybridDp;
            const bool is_user_level_dp =
                run.privacy_mode() == fl::core::PrivacyMode::kUserLevelDp || is_hybrid_dp;
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "secure_aggregation_session_manager";
                event.event_type = SecurityEventType::kSecureAggregationCompleteCohortReceived;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kCoordinator;
                event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                event.safe_subject_id = update.session_id();
                event.outcome = SecurityOutcome::kCompleted;
                security_event_journal_->emit(event);
                // Work Area D: fired only when noise was actually
                // requested for this session (noise_provider != nullptr
                // implies is_user_level_dp) -- never claims noise was
                // applied to a NONE/SAMPLE_LEVEL secure session, which
                // never carries a noise_provider here. Never carries the
                // noise value itself, only that the step ran.
                if (noise_provider != nullptr) {
                    event.event_type = SecurityEventType::kSecureUserLevelDpNoiseApplied;
                    event.severity = default_severity(event.event_type);
                    event.run_id = update.run_id();
                    event.round_id = update.round_id();
                    security_event_journal_->emit(std::move(event));
                }
            }
            bool advanced = false;
            std::string advance_error;
            try {
                advanced = run.apply_secure_aggregate_and_advance(
                    update.round_id(), aggregate, now_unix_s(), indicator_over_threshold_count);
            } catch (const std::exception& error) {
                advance_error = error.what();
            }
            if (advanced && run.secure_adaptive_clipping_active() &&
                security_event_journal_ != nullptr) {
                // One atomic transaction with the model mechanism's own
                // kSecureAggregationSessionCompleted event just below --
                // see docs/secure-adaptive-clipping-semantics.md section
                // 18. NEXT_STATE_PUBLISHED and ROUND_COMPLETED are
                // distinct events (mirroring the hybrid slice's own
                // BINDING_ACCEPTED/ROUND_COMPLETED split) so an operator
                // dashboard can count "a next clip bound was durably
                // published" separately from "a full adaptive round
                // completed."
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "run_manager";
                event.actor_type = SecurityActorType::kCoordinator;
                event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                event.safe_subject_id = update.session_id();
                event.run_id = update.run_id();
                event.round_id = update.round_id();
                event.outcome = SecurityOutcome::kCompleted;
                event.event_type = SecurityEventType::kSecureAdaptiveClippingNextStatePublished;
                event.severity = default_severity(event.event_type);
                security_event_journal_->emit(event);
                event.event_type = SecurityEventType::kSecureAdaptiveClippingRoundCompleted;
                event.severity = default_severity(event.event_type);
                security_event_journal_->emit(std::move(event));
            }
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "run_manager";
                event.event_type =
                    advanced ? SecurityEventType::kSecureAggregationSessionCompleted
                             : SecurityEventType::kSecureAggregationAggregateValidationFailed;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kCoordinator;
                event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                event.safe_subject_id = update.session_id();
                event.outcome = advanced ? SecurityOutcome::kCompleted : SecurityOutcome::kRejected;
                if (!advanced) {
                    event.safe_details["advance_error"] =
                        advance_error.empty() ? "round_id_or_state_mismatch" : "internal_error";
                }
                security_event_journal_->emit(event);
                // Work Area D/R: the user-level-DP-specific pair.
                // "Accounting committed" + "round completed" only when
                // the round-progression idempotency guard actually let
                // the accountant/ledger commit through (see
                // RunInstance::apply_secure_aggregate_and_advance);
                // "finalization conflict" when that same guard is what
                // caused `advanced == false` -- the exactly-once commit
                // protection firing, not a real error (see
                // docs/secure-user-level-dp-publication-boundary.md).
                if (is_user_level_dp) {
                    if (advanced) {
                        event.event_type = SecurityEventType::kSecureUserLevelDpAccountingCommitted;
                        event.severity = default_severity(event.event_type);
                        event.run_id = update.run_id();
                        event.round_id = update.round_id();
                        security_event_journal_->emit(event);
                        event.event_type = SecurityEventType::kSecureUserLevelDpRoundCompleted;
                        security_event_journal_->emit(event);
                        // Secure Hybrid Differential Privacy Runtime
                        // slice: one further event marking this round
                        // completed under BOTH mechanisms together --
                        // distinct from the user-level-only event above,
                        // which fires for plain kUserLevelDp rounds too.
                        if (is_hybrid_dp) {
                            event.event_type = SecurityEventType::kSecureHybridDpRoundCompleted;
                            security_event_journal_->emit(std::move(event));
                        }
                    } else {
                        event.event_type =
                            SecurityEventType::kSecureUserLevelDpFinalizationConflict;
                        event.severity = default_severity(event.event_type);
                        event.run_id = update.run_id();
                        event.round_id = update.round_id();
                        security_event_journal_->emit(event);
                        if (is_hybrid_dp) {
                            event.event_type = SecurityEventType::kSecureHybridDpRoundAborted;
                            event.severity = default_severity(event.event_type);
                            security_event_journal_->emit(event);
                        }
                        if (run.secure_adaptive_clipping_active()) {
                            // Secure Adaptive Clipping with Private
                            // Indicator Aggregation slice: the same
                            // finalization-conflict guard that prevents
                            // the model mechanism from double-committing
                            // also prevents the clip-state update from
                            // re-applying -- see the semantics doc
                            // section 18. Never a real privacy
                            // double-spend; the guard firing IS the
                            // protection working.
                            event.event_type =
                                SecurityEventType::kSecureAdaptiveClippingRoundAborted;
                            event.severity = default_severity(event.event_type);
                            security_event_journal_->emit(std::move(event));
                        }
                    }
                }
            }
            if (!advanced) {
                // A known, narrow, disclosed residual-inconsistency
                // window (see apply_secure_aggregate_and_advance's own
                // header comment and docs/known-limitations.md): the
                // secure session is already COMPLETED at this point
                // (finalize() succeeded above), but the model version
                // did not actually advance. Logged loudly -- never
                // silently reported as a successful secure round.
                std::cerr
                    << "secure aggregate for session " << update.session_id()
                    << " decoded successfully but RunInstance::apply_secure_aggregate_and_advance "
                    << "did not advance the model (run_id=" << update.run_id()
                    << " round_id=" << update.round_id()
                    << (advance_error.empty() ? "" : ": " + advance_error)
                    << ") -- the secure session reports COMPLETED but the model version is "
                       "unchanged\n";
            }
        } catch (const std::exception& error) {
            if (security_event_journal_ != nullptr) {
                SecurityEvent event;
                event.source_service = "coordinator";
                event.source_component = "secure_aggregation_session_manager";
                event.event_type = SecurityEventType::kSecureAggregationAggregateValidationFailed;
                event.severity = default_severity(event.event_type);
                event.actor_type = SecurityActorType::kCoordinator;
                event.subject_type = SecuritySubjectType::kSecureAggregationSession;
                event.safe_subject_id = update.session_id();
                event.outcome = SecurityOutcome::kRejected;
                security_event_journal_->emit(std::move(event));
            }
            std::cerr << "secure aggregation finalization failed for session "
                      << update.session_id() << ": " << error.what() << "\n";
        }
    }

    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetSecureAggregationSession(
    grpc::ServerContext*,
    const fl::coordinator::v1::GetSecureAggregationSessionRequest* request,
    fl::coordinator::v1::GetSecureAggregationSessionResponse* response) {
    response->set_found(false);
    if (secure_aggregation_manager_ == nullptr) {
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "this coordinator has no secure aggregation session manager configured");
    }
    const auto status = secure_aggregation_manager_->find(request->session_id());
    if (!status.has_value()) {
        return grpc::Status::OK;
    }
    response->set_found(true);
    *response->mutable_status() = *status;
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::ListSecureAggregationSessions(
    grpc::ServerContext*,
    const fl::coordinator::v1::ListSecureAggregationSessionsRequest* request,
    fl::coordinator::v1::ListSecureAggregationSessionsResponse* response) {
    if (secure_aggregation_manager_ == nullptr) {
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "this coordinator has no secure aggregation session manager configured");
    }
    // Work item 13: pagination fields (page_size/page_token) are
    // accepted on the wire but not yet implemented -- this pass's
    // session volume never warrants it. next_page_token stays empty,
    // meaning "this is the complete result set," never a silently
    // truncated one.
    for (const auto& summary : secure_aggregation_manager_->list()) {
        if (!request->run_id().empty() && summary.run_id() != request->run_id()) {
            continue;
        }
        if (request->state_filter() !=
                fl::coordinator::v1::SECURE_AGGREGATION_SESSION_STATE_UNSPECIFIED &&
            summary.state() != request->state_filter()) {
            continue;
        }
        *response->add_sessions() = summary;
    }
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::AbortSecureAggregationSession(
    grpc::ServerContext* context,
    const fl::coordinator::v1::AbortSecureAggregationSessionRequest* request,
    fl::coordinator::v1::AbortSecureAggregationSessionResponse* response) {
    response->set_accepted(false);
    // ADMIN_CONTROL, same gate as SuspendWorker/RevokeWorker -- manual
    // abort is an administrative action, not a worker-initiated one.
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "AbortSecureAggregationSession");
        return *rejection;
    }
    if (secure_aggregation_manager_ == nullptr) {
        response->set_reason("secure_aggregation_unavailable");
        return grpc::Status(
            grpc::StatusCode::FAILED_PRECONDITION,
            "this coordinator has no secure aggregation session manager configured");
    }
    try {
        const auto status = secure_aggregation_manager_->abort(
            request->session_id(),
            fl::coordinator::v1::SECURE_AGGREGATION_ABORT_REASON_MANUAL_ABORT,
            now_unix_s());
        (void)status;
        if (security_event_journal_ != nullptr) {
            SecurityEvent event;
            event.source_service = "coordinator";
            event.source_component = "coordinator_service";
            event.event_type = SecurityEventType::kSecureAggregationSessionAborted;
            event.severity = default_severity(event.event_type);
            event.actor_type = SecurityActorType::kService;
            event.safe_actor_id = "go-api";
            event.subject_type = SecuritySubjectType::kSecureAggregationSession;
            event.safe_subject_id = request->session_id();
            event.outcome = SecurityOutcome::kCompleted;
            event.reason_code = "manual_abort";
            event.safe_details["reason"] = request->reason();
            security_event_journal_->emit(std::move(event));
        }
        response->set_accepted(true);
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        response->set_reason(error.what());
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, error.what());
    }
}

namespace {

// Static mechanism description -- see docs/secure-user-level-dp-semantics.md.
// Every field here is a fixed design fact this slice's own mechanism uses
// unconditionally, not something read back from a run (a run either uses
// this mechanism or doesn't; there is no per-run variant of it).
void fill_secure_user_level_privacy_capability(
    fl::coordinator::v1::SecureUserLevelPrivacyCapabilityInfo* info) {
    info->set_available(true);
    info->set_provider("SECAGG_NO_DROPOUT_EXPERIMENTAL");
    info->set_adjacency_model("ADD_REMOVE_ONE");
    info->set_sampling_assumption("NO_AMPLIFICATION");
    info->set_sensitivity_formula(
        "clip_norm + sqrt(total_elements) * (0.5 / fixed_point_scale_factor)");
    info->set_noise_placement("sum_before_divide");
    info->set_fixed_weight(1.0);
    info->add_trust_limitations("honest_client_dependent_clipping_not_cryptographically_verified");
    info->add_trust_limitations("not_malicious_client_secure");
    info->add_trust_limitations("no_privacy_amplification_claimed");
    info->add_trust_limitations("attestation_is_signed_evidence_not_a_correctness_proof");
    info->add_trust_limitations("independent_privacy_and_cryptographic_review_not_completed");
    info->add_trust_limitations("not_production_privacy_ready");
}

}  // namespace

grpc::Status CoordinatorServiceImpl::GetSecureUserLevelPrivacyHealth(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetSecureUserLevelPrivacyHealthRequest* request,
    fl::coordinator::v1::SecureUserLevelPrivacyHealthResponse* response) {
    (void)request;
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "GetSecureUserLevelPrivacyHealth");
        return *rejection;
    }
    fill_secure_user_level_privacy_capability(response->mutable_capability());
    response->set_checked_at_unix_s(now_unix_s());

    response->set_provider_status(secure_aggregation_manager_ != nullptr ? "ok" : "unavailable");
    response->set_noise_provider_status("ok");
    response->set_accountant_status("ok");
    response->set_ledger_status("ok");
    response->set_event_journal_status(security_event_journal_ != nullptr ? "ok" : "unavailable");

    std::string last_successful_round_at;
    double last_successful_round_unix_s = 0.0;
    std::uint64_t active_runs = 0;
    bool degraded = secure_aggregation_manager_ == nullptr;
    std::string degraded_reason = degraded ? "secure_aggregation_unavailable" : "";
    if (manager_ != nullptr) {
        for (const auto& run_id : manager_->list_run_ids()) {
            try {
                const auto& run = manager_->get(run_id);
                // Secure Hybrid Differential Privacy Runtime slice: a
                // hybrid run has a real UserLevelAccountant/ledger too
                // (identical construction/commit gate as plain
                // kUserLevelDp, see run_manager.cpp) -- excluding it
                // here would make this existing health surface under-
                // report active runs for every hybrid round, a real gap
                // this one-line fix closes without a new RPC or route.
                if (run.privacy_mode() != fl::core::PrivacyMode::kUserLevelDp &&
                    run.privacy_mode() != fl::core::PrivacyMode::kHybridDp) {
                    continue;
                }
                ++active_runs;
                const auto& ledger = run.user_level_ledger();
                if (!ledger.empty() &&
                    ledger.back().committed_at_unix_s > last_successful_round_unix_s) {
                    last_successful_round_unix_s = ledger.back().committed_at_unix_s;
                }
            } catch (const std::exception&) {
                // A run that vanished between list_run_ids() and get() (should
                // not happen under RunManager's own invariants, but this RPC
                // must never fail the whole health check over one run).
                continue;
            }
        }
    }
    if (last_successful_round_unix_s > 0.0) {
        last_successful_round_at = iso8601_from_unix_seconds(last_successful_round_unix_s);
    }
    response->set_last_successful_round_at(last_successful_round_at);
    response->set_active_runs_with_user_level_dp(active_runs);
    response->set_reconciliation_required(false);
    response->set_degraded_reason(degraded_reason);
    if (degraded && security_event_journal_ != nullptr) {
        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "coordinator_service";
        event.event_type = SecurityEventType::kSecureUserLevelDpHealthDegraded;
        event.severity = default_severity(event.event_type);
        event.actor_type = SecurityActorType::kCoordinator;
        event.subject_type = SecuritySubjectType::kSecureAggregationSession;
        event.outcome = SecurityOutcome::kFailed;
        event.reason_code = degraded_reason;
        security_event_journal_->emit(std::move(event));
    }
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetSecureUserLevelPrivacyBudget(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetSecureUserLevelPrivacyBudgetRequest* request,
    fl::coordinator::v1::SecureUserLevelPrivacyBudgetResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "GetSecureUserLevelPrivacyBudget");
        return *rejection;
    }
    if (manager_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "no run manager configured");
    }
    response->set_run_id(request->run_id());
    try {
        const auto& run = manager_->get(request->run_id());
        // Secure Hybrid Differential Privacy Runtime slice: reports the
        // USER-LEVEL half of a hybrid run's budget -- the sample-level
        // half is a separate mechanism with its own separate ledger
        // (run.sample_level_ledger()), deliberately not exposed through
        // this same field set (see docs/secure-hybrid-dp-semantics.md
        // section 4's "never combined" rule -- a new Go/web surface for
        // the sample-level half is out of this slice's scope, see the
        // audit doc's scope statement).
        if (run.privacy_mode() != fl::core::PrivacyMode::kUserLevelDp &&
            run.privacy_mode() != fl::core::PrivacyMode::kHybridDp) {
            return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION,
                                "run_id '" + request->run_id() +
                                    "' is not a user-level-DP or "
                                    "hybrid-DP run");
        }
        const auto& user_level = run.user_level_privacy();
        const auto& ledger = run.user_level_ledger();
        const double epsilon_spent = ledger.empty() ? 0.0 : ledger.back().epsilon;
        const bool budget_configured = user_level.epsilon_budget > 0.0;
        response->set_budget_configured(budget_configured);
        response->set_epsilon_spent(epsilon_spent);
        response->set_epsilon_budget(user_level.epsilon_budget);
        response->set_epsilon_remaining(
            budget_configured ? std::max(0.0, user_level.epsilon_budget - epsilon_spent) : 0.0);
        response->set_target_delta(user_level.target_delta);
        response->set_rounds_committed(static_cast<std::uint64_t>(ledger.size()));
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, error.what());
    }
}

namespace {
fl::coordinator::v1::SecureUserLevelPrivacyRoundSummary to_round_summary(
    const std::string& run_id, const fl::coordinator::UserLevelLedgerEntry& entry) {
    fl::coordinator::v1::SecureUserLevelPrivacyRoundSummary summary;
    summary.set_run_id(run_id);
    summary.set_round_id(entry.round_id);
    summary.set_epsilon_after_round(entry.epsilon);
    summary.set_target_delta(entry.delta);
    summary.set_noise_multiplier(entry.noise_multiplier);
    summary.set_clipping_bound(entry.clipping_bound);
    summary.set_num_clients(entry.num_clients);
    summary.set_committed_at_unix_s(entry.committed_at_unix_s);
    return summary;
}
}  // namespace

grpc::Status CoordinatorServiceImpl::ListSecureUserLevelPrivacyRounds(
    grpc::ServerContext* context,
    const fl::coordinator::v1::ListSecureUserLevelPrivacyRoundsRequest* request,
    fl::coordinator::v1::ListSecureUserLevelPrivacyRoundsResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "ListSecureUserLevelPrivacyRounds");
        return *rejection;
    }
    if (manager_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "no run manager configured");
    }
    // Work item: pagination is a cursor over round_id (ascending), scoped to
    // exactly one run_id -- this pass's session volume never warrants a
    // cross-run cursor. after_cursor is the last round_id already returned;
    // "" starts from the beginning.
    std::uint64_t after_round_id = 0;
    if (!request->after_cursor().empty()) {
        try {
            after_round_id = std::stoull(request->after_cursor());
        } catch (const std::exception&) {
            return grpc::Status(grpc::StatusCode::INVALID_ARGUMENT, "invalid after_cursor");
        }
    }
    const std::uint32_t limit = request->limit() > 0 ? request->limit() : 50;
    // A LIST endpoint for an unknown/not-yet-existent run_id returns an
    // empty page (OK), not NOT_FOUND -- "no rounds found" is exactly
    // what an empty list already means, matching this codebase's other
    // list endpoints (e.g. ListSecurityEvents for a filter that matches
    // nothing). Real bug found live by this slice's own Playwright
    // suite: the web Round Explorer's "empty" vs. "error" states are
    // meaningfully different UI states, and treating an unknown run_id
    // as an error made every "search for a run with no rounds yet"
    // query show a scary error banner instead of a plain empty result.
    // GetSecureUserLevelPrivacyRound (the single-round DETAIL endpoint)
    // deliberately keeps its own 404-for-truly-missing behavior --only
    // this LIST endpoint's semantics changed.
    try {
        const auto& run = manager_->get(request->run_id());
        std::uint64_t last_round_id = 0;
        std::uint32_t emitted = 0;
        for (const auto& entry : run.user_level_ledger()) {
            if (entry.round_id <= after_round_id)
                continue;
            if (emitted >= limit)
                break;
            *response->add_rounds() = to_round_summary(request->run_id(), entry);
            last_round_id = entry.round_id;
            ++emitted;
        }
        response->set_next_cursor(emitted == limit ? std::to_string(last_round_id) : "");
    } catch (const std::exception&) {
        // Unknown run_id: leave response.rounds empty, next_cursor "".
    }
    return grpc::Status::OK;
}

grpc::Status CoordinatorServiceImpl::GetSecureUserLevelPrivacyRound(
    grpc::ServerContext* context,
    const fl::coordinator::v1::GetSecureUserLevelPrivacyRoundRequest* request,
    fl::coordinator::v1::GetSecureUserLevelPrivacyRoundResponse* response) {
    if (const auto rejection = reject_if_not_go_api_service_identity(context, transport_mode_)) {
        emit_permission_denied_event(
            security_event_journal_, context, "GetSecureUserLevelPrivacyRound");
        return *rejection;
    }
    if (manager_ == nullptr) {
        return grpc::Status(grpc::StatusCode::FAILED_PRECONDITION, "no run manager configured");
    }
    response->set_found(false);
    try {
        const auto& run = manager_->get(request->run_id());
        for (const auto& entry : run.user_level_ledger()) {
            if (entry.round_id == request->round_id()) {
                response->set_found(true);
                *response->mutable_round() = to_round_summary(request->run_id(), entry);
                break;
            }
        }
        return grpc::Status::OK;
    } catch (const std::exception& error) {
        return grpc::Status(grpc::StatusCode::NOT_FOUND, error.what());
    }
}

}  // namespace fl::coordinator
