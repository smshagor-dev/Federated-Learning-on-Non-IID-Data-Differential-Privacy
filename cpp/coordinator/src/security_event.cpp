#include "fl_coordinator/security_event.hpp"

#include <iomanip>
#include <sstream>

namespace fl::coordinator {

namespace {

// -- JSON string escaping, matching Python's json.dumps(ensure_ascii=True) --
// Deliberately duplicated from capability_statement_verifier.cpp rather
// than shared: that file is only compiled into the gRPC-gated build (it
// depends on generated protobuf headers), while this file must stay
// buildable in the protobuf-free local fl_coordinator library -- see
// this header's file comment.
void append_u_escape(std::string& out, std::uint32_t code_unit) {
    static constexpr char kHex[] = "0123456789abcdef";
    out += "\\u";
    out += kHex[(code_unit >> 12) & 0xF];
    out += kHex[(code_unit >> 8) & 0xF];
    out += kHex[(code_unit >> 4) & 0xF];
    out += kHex[code_unit & 0xF];
}

std::string json_uint(std::uint64_t value) { return std::to_string(value); }

std::uint64_t fnv1a_hash(const std::string& data) {
    std::uint64_t hash = 1469598103934665603ULL;
    for (const unsigned char byte : data) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string hash_to_hex(std::uint64_t hash) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

}  // namespace

std::string json_escape_string(const std::string& utf8) {
    std::string out;
    out.reserve(utf8.size() + 2);
    out += '"';
    std::size_t i = 0;
    while (i < utf8.size()) {
        const unsigned char byte0 = static_cast<unsigned char>(utf8[i]);
        if (byte0 == '"') {
            out += "\\\"";
            ++i;
        } else if (byte0 == '\\') {
            out += "\\\\";
            ++i;
        } else if (byte0 == '\n') {
            out += "\\n";
            ++i;
        } else if (byte0 == '\r') {
            out += "\\r";
            ++i;
        } else if (byte0 == '\t') {
            out += "\\t";
            ++i;
        } else if (byte0 == '\b') {
            out += "\\b";
            ++i;
        } else if (byte0 == '\f') {
            out += "\\f";
            ++i;
        } else if (byte0 < 0x20) {
            append_u_escape(out, byte0);
            ++i;
        } else if (byte0 < 0x80) {
            out += static_cast<char>(byte0);
            ++i;
        } else {
            // Non-ASCII bytes: this schema's string fields are all
            // identifiers/reason codes expected to be ASCII in practice;
            // re-emit byte-for-byte as \u00XX rather than attempting full
            // UTF-8 decoding (unlike capability_statement_verifier.cpp,
            // which signs cross-language payloads and must match Python's
            // decoder exactly). This function must never crash on
            // attacker-controlled input.
            append_u_escape(out, byte0);
            ++i;
        }
    }
    out += '"';
    return out;
}

std::string to_string(SecuritySeverity value) {
    switch (value) {
        case SecuritySeverity::kInfo:
            return "INFO";
        case SecuritySeverity::kWarning:
            return "WARNING";
        case SecuritySeverity::kHigh:
            return "HIGH";
        case SecuritySeverity::kCritical:
            return "CRITICAL";
    }
    return "INFO";
}

std::string to_string(SecurityOutcome value) {
    switch (value) {
        case SecurityOutcome::kAccepted:
            return "ACCEPTED";
        case SecurityOutcome::kRejected:
            return "REJECTED";
        case SecurityOutcome::kCompleted:
            return "COMPLETED";
        case SecurityOutcome::kFailed:
            return "FAILED";
        case SecurityOutcome::kBlocked:
            return "BLOCKED";
        case SecurityOutcome::kCanceled:
            return "CANCELED";
    }
    return "REJECTED";
}

std::string to_string(SecurityActorType value) {
    switch (value) {
        case SecurityActorType::kUser:
            return "USER";
        case SecurityActorType::kService:
            return "SERVICE";
        case SecurityActorType::kWorker:
            return "WORKER";
        case SecurityActorType::kCoordinator:
            return "COORDINATOR";
        case SecurityActorType::kSystem:
            return "SYSTEM";
    }
    return "SYSTEM";
}

std::string to_string(SecuritySubjectType value) {
    switch (value) {
        case SecuritySubjectType::kTransport:
            return "TRANSPORT";
        case SecuritySubjectType::kCertificate:
            return "CERTIFICATE";
        case SecuritySubjectType::kWorkerIdentity:
            return "WORKER_IDENTITY";
        case SecuritySubjectType::kWorkerSigningKey:
            return "WORKER_SIGNING_KEY";
        case SecuritySubjectType::kCoordinatorSigningKey:
            return "COORDINATOR_SIGNING_KEY";
        case SecuritySubjectType::kCapability:
            return "CAPABILITY";
        case SecuritySubjectType::kHeartbeat:
            return "HEARTBEAT";
        case SecuritySubjectType::kClientResult:
            return "CLIENT_RESULT";
        case SecuritySubjectType::kPrivacyRecord:
            return "PRIVACY_RECORD";
        case SecuritySubjectType::kTrainingTask:
            return "TRAINING_TASK";
        case SecuritySubjectType::kReplayState:
            return "REPLAY_STATE";
        case SecuritySubjectType::kTaskLease:
            return "TASK_LEASE";
        case SecuritySubjectType::kAuditQuery:
            return "AUDIT_QUERY";
        case SecuritySubjectType::kSecurityMutation:
            return "SECURITY_MUTATION";
        case SecuritySubjectType::kWorkerEventBatch:
            return "WORKER_EVENT_BATCH";
        case SecuritySubjectType::kSecureAggregationSession:
            return "SECURE_AGGREGATION_SESSION";
    }
    return "SECURITY_MUTATION";
}

std::string to_string(SecurityEventType value) {
    switch (value) {
        case SecurityEventType::kTransportMtlsStarted:
            return "TRANSPORT_MTLS_STARTED";
        case SecurityEventType::kTransportMtlsFailed:
            return "TRANSPORT_MTLS_FAILED";
        case SecurityEventType::kTransportInsecureDevelopmentStarted:
            return "TRANSPORT_INSECURE_DEVELOPMENT_STARTED";
        case SecurityEventType::kPeerCertificateAccepted:
            return "PEER_CERTIFICATE_ACCEPTED";
        case SecurityEventType::kPeerCertificateRejected:
            return "PEER_CERTIFICATE_REJECTED";
        case SecurityEventType::kCertificateIdentityMismatch:
            return "CERTIFICATE_IDENTITY_MISMATCH";
        case SecurityEventType::kCertificateFingerprintRejected:
            return "CERTIFICATE_FINGERPRINT_REJECTED";
        case SecurityEventType::kCertificateExpired:
            return "CERTIFICATE_EXPIRED";
        case SecurityEventType::kWorkerRegistered:
            return "WORKER_REGISTERED";
        case SecurityEventType::kWorkerRegistrationRejected:
            return "WORKER_REGISTRATION_REJECTED";
        case SecurityEventType::kWorkerSuspended:
            return "WORKER_SUSPENDED";
        case SecurityEventType::kWorkerActivated:
            return "WORKER_ACTIVATED";
        case SecurityEventType::kWorkerRevoked:
            return "WORKER_REVOKED";
        case SecurityEventType::kWorkerStatusRpcRejected:
            return "WORKER_STATUS_RPC_REJECTED";
        case SecurityEventType::kActiveLeaseCanceled:
            return "ACTIVE_LEASE_CANCELED";
        case SecurityEventType::kWorkerKeyMigrated:
            return "WORKER_KEY_MIGRATED";
        case SecurityEventType::kWorkerKeyRegistered:
            return "WORKER_KEY_REGISTERED";
        case SecurityEventType::kWorkerKeyRotationRequested:
            return "WORKER_KEY_ROTATION_REQUESTED";
        case SecurityEventType::kWorkerKeyRotationAccepted:
            return "WORKER_KEY_ROTATION_ACCEPTED";
        case SecurityEventType::kWorkerKeyRotationRejected:
            return "WORKER_KEY_ROTATION_REJECTED";
        case SecurityEventType::kWorkerKeyGraceStarted:
            return "WORKER_KEY_GRACE_STARTED";
        case SecurityEventType::kWorkerKeyExpired:
            return "WORKER_KEY_EXPIRED";
        case SecurityEventType::kWorkerKeyRevoked:
            return "WORKER_KEY_REVOKED";
        case SecurityEventType::kMessageRejectedByKeyState:
            return "MESSAGE_REJECTED_BY_KEY_STATE";
        case SecurityEventType::kCapabilityAccepted:
            return "CAPABILITY_ACCEPTED";
        case SecurityEventType::kCapabilityRejected:
            return "CAPABILITY_REJECTED";
        case SecurityEventType::kHeartbeatAccepted:
            return "HEARTBEAT_ACCEPTED";
        case SecurityEventType::kHeartbeatRejected:
            return "HEARTBEAT_REJECTED";
        case SecurityEventType::kClientResultAccepted:
            return "CLIENT_RESULT_ACCEPTED";
        case SecurityEventType::kClientResultRejected:
            return "CLIENT_RESULT_REJECTED";
        case SecurityEventType::kPrivacyRecordAccepted:
            return "PRIVACY_RECORD_ACCEPTED";
        case SecurityEventType::kPrivacyRecordRejected:
            return "PRIVACY_RECORD_REJECTED";
        case SecurityEventType::kSignatureVerificationFailed:
            return "SIGNATURE_VERIFICATION_FAILED";
        case SecurityEventType::kPayloadHashMismatch:
            return "PAYLOAD_HASH_MISMATCH";
        case SecurityEventType::kMessageReplayRejected:
            return "MESSAGE_REPLAY_REJECTED";
        case SecurityEventType::kMessageSequenceRejected:
            return "MESSAGE_SEQUENCE_REJECTED";
        case SecurityEventType::kMessageExpired:
            return "MESSAGE_EXPIRED";
        case SecurityEventType::kCoordinatorSigningKeyLoaded:
            return "COORDINATOR_SIGNING_KEY_LOADED";
        case SecurityEventType::kCoordinatorSigningKeyRejected:
            return "COORDINATOR_SIGNING_KEY_REJECTED";
        case SecurityEventType::kCoordinatorTaskSigned:
            return "COORDINATOR_TASK_SIGNED";
        case SecurityEventType::kCoordinatorTaskSigningFailed:
            return "COORDINATOR_TASK_SIGNING_FAILED";
        case SecurityEventType::kCoordinatorTaskIssued:
            return "COORDINATOR_TASK_ISSUED";
        case SecurityEventType::kCoordinatorTaskVerified:
            return "COORDINATOR_TASK_VERIFIED";
        case SecurityEventType::kCoordinatorTaskRejected:
            return "COORDINATOR_TASK_REJECTED";
        case SecurityEventType::kCoordinatorTaskReplayRejected:
            return "COORDINATOR_TASK_REPLAY_REJECTED";
        case SecurityEventType::kDuplicateTaskExecutionBlocked:
            return "DUPLICATE_TASK_EXECUTION_BLOCKED";
        case SecurityEventType::kAcceptedTaskRecoveryStarted:
            return "ACCEPTED_TASK_RECOVERY_STARTED";
        case SecurityEventType::kAcceptedTaskRecoveryCompleted:
            return "ACCEPTED_TASK_RECOVERY_COMPLETED";
        case SecurityEventType::kTaskReissued:
            return "TASK_REISSUED";
        case SecurityEventType::kSecurityPermissionDenied:
            return "SECURITY_PERMISSION_DENIED";
        case SecurityEventType::kSecurityMutationAccepted:
            return "SECURITY_MUTATION_ACCEPTED";
        case SecurityEventType::kSecurityMutationRejected:
            return "SECURITY_MUTATION_REJECTED";
        case SecurityEventType::kIdempotencyReplayAccepted:
            return "IDEMPOTENCY_REPLAY_ACCEPTED";
        case SecurityEventType::kIdempotencyConflictRejected:
            return "IDEMPOTENCY_CONFLICT_REJECTED";
        case SecurityEventType::kSecurityAuditAccessed:
            return "SECURITY_AUDIT_ACCESSED";
        case SecurityEventType::kWorkerSecurityEventBatchAccepted:
            return "WORKER_SECURITY_EVENT_BATCH_ACCEPTED";
        case SecurityEventType::kWorkerSecurityEventBatchRejected:
            return "WORKER_SECURITY_EVENT_BATCH_REJECTED";
        case SecurityEventType::kSecureAggregationSessionCreated:
            return "SECURE_AGGREGATION_SESSION_CREATED";
        case SecurityEventType::kSecureAggregationCohortFrozen:
            return "SECURE_AGGREGATION_COHORT_FROZEN";
        case SecurityEventType::kSecureAggregationKeyAdvertisementAccepted:
            return "SECURE_AGGREGATION_KEY_ADVERTISEMENT_ACCEPTED";
        case SecurityEventType::kSecureAggregationKeyAdvertisementRejected:
            return "SECURE_AGGREGATION_KEY_ADVERTISEMENT_REJECTED";
        case SecurityEventType::kSecureAggregationSessionAborted:
            return "SECURE_AGGREGATION_SESSION_ABORTED";
        case SecurityEventType::kSecureAggregationRestartAborted:
            return "SECURE_AGGREGATION_RESTART_ABORTED";
        case SecurityEventType::kSecureAggregationMaskedUpdateAccepted:
            return "SECURE_AGGREGATION_MASKED_UPDATE_ACCEPTED";
        case SecurityEventType::kSecureAggregationMaskedUpdateRejected:
            return "SECURE_AGGREGATION_MASKED_UPDATE_REJECTED";
        case SecurityEventType::kSecureAggregationCompleteCohortReceived:
            return "SECURE_AGGREGATION_COMPLETE_COHORT_RECEIVED";
        case SecurityEventType::kSecureAggregationSessionCompleted:
            return "SECURE_AGGREGATION_SESSION_COMPLETED";
        case SecurityEventType::kSecureAggregationAggregateValidationFailed:
            return "SECURE_AGGREGATION_AGGREGATE_VALIDATION_FAILED";
        case SecurityEventType::kSecureUserLevelDpConfigurationAccepted:
            return "SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED";
        case SecurityEventType::kSecureUserLevelDpConfigurationRejected:
            return "SECURE_USER_LEVEL_DP_CONFIGURATION_REJECTED";
        case SecurityEventType::kSecureUserLevelDpBudgetReserved:
            return "SECURE_USER_LEVEL_DP_BUDGET_RESERVED";
        case SecurityEventType::kSecureUserLevelDpBudgetExhausted:
            return "SECURE_USER_LEVEL_DP_BUDGET_EXHAUSTED";
        case SecurityEventType::kSecureUserLevelDpClippingApplied:
            return "SECURE_USER_LEVEL_DP_CLIPPING_APPLIED";
        case SecurityEventType::kSecureUserLevelDpAttestationAccepted:
            return "SECURE_USER_LEVEL_DP_ATTESTATION_ACCEPTED";
        case SecurityEventType::kSecureUserLevelDpAttestationRejected:
            return "SECURE_USER_LEVEL_DP_ATTESTATION_REJECTED";
        case SecurityEventType::kSecureUserLevelDpNoiseApplied:
            return "SECURE_USER_LEVEL_DP_NOISE_APPLIED";
        case SecurityEventType::kSecureUserLevelDpAccountingCommitted:
            return "SECURE_USER_LEVEL_DP_ACCOUNTING_COMMITTED";
        case SecurityEventType::kSecureUserLevelDpRoundCompleted:
            return "SECURE_USER_LEVEL_DP_ROUND_COMPLETED";
        case SecurityEventType::kSecureUserLevelDpDropoutAborted:
            return "SECURE_USER_LEVEL_DP_DROPOUT_ABORTED";
        case SecurityEventType::kSecureUserLevelDpFinalizationConflict:
            return "SECURE_USER_LEVEL_DP_FINALIZATION_CONFLICT";
        case SecurityEventType::kSecureUserLevelDpCheckpointReconciled:
            return "SECURE_USER_LEVEL_DP_CHECKPOINT_RECONCILED";
        case SecurityEventType::kSecureUserLevelDpHealthDegraded:
            return "SECURE_USER_LEVEL_DP_HEALTH_DEGRADED";
        case SecurityEventType::kSecureHybridDpConfigurationAccepted:
            return "SECURE_HYBRID_DP_CONFIGURATION_ACCEPTED";
        case SecurityEventType::kSecureHybridDpConfigurationRejected:
            return "SECURE_HYBRID_DP_CONFIGURATION_REJECTED";
        case SecurityEventType::kSecureHybridDpUserBudgetReserved:
            return "SECURE_HYBRID_DP_USER_BUDGET_RESERVED";
        case SecurityEventType::kSecureHybridDpSampleRecordAccepted:
            return "SECURE_HYBRID_DP_SAMPLE_RECORD_ACCEPTED";
        case SecurityEventType::kSecureHybridDpSampleRecordRejected:
            return "SECURE_HYBRID_DP_SAMPLE_RECORD_REJECTED";
        case SecurityEventType::kSecureHybridDpBindingAccepted:
            return "SECURE_HYBRID_DP_BINDING_ACCEPTED";
        case SecurityEventType::kSecureHybridDpRoundCompleted:
            return "SECURE_HYBRID_DP_ROUND_COMPLETED";
        case SecurityEventType::kSecureHybridDpRoundAborted:
            return "SECURE_HYBRID_DP_ROUND_ABORTED";
    }
    return "UNKNOWN";
}

bool security_severity_from_string(const std::string& value, SecuritySeverity& out) {
    if (value == "INFO") { out = SecuritySeverity::kInfo; return true; }
    if (value == "WARNING") { out = SecuritySeverity::kWarning; return true; }
    if (value == "HIGH") { out = SecuritySeverity::kHigh; return true; }
    if (value == "CRITICAL") { out = SecuritySeverity::kCritical; return true; }
    return false;
}

bool security_outcome_from_string(const std::string& value, SecurityOutcome& out) {
    if (value == "ACCEPTED") { out = SecurityOutcome::kAccepted; return true; }
    if (value == "REJECTED") { out = SecurityOutcome::kRejected; return true; }
    if (value == "COMPLETED") { out = SecurityOutcome::kCompleted; return true; }
    if (value == "FAILED") { out = SecurityOutcome::kFailed; return true; }
    if (value == "BLOCKED") { out = SecurityOutcome::kBlocked; return true; }
    if (value == "CANCELED") { out = SecurityOutcome::kCanceled; return true; }
    return false;
}

bool security_actor_type_from_string(const std::string& value, SecurityActorType& out) {
    if (value == "USER") { out = SecurityActorType::kUser; return true; }
    if (value == "SERVICE") { out = SecurityActorType::kService; return true; }
    if (value == "WORKER") { out = SecurityActorType::kWorker; return true; }
    if (value == "COORDINATOR") { out = SecurityActorType::kCoordinator; return true; }
    if (value == "SYSTEM") { out = SecurityActorType::kSystem; return true; }
    return false;
}

bool security_subject_type_from_string(const std::string& value, SecuritySubjectType& out) {
    if (value == "TRANSPORT") { out = SecuritySubjectType::kTransport; return true; }
    if (value == "CERTIFICATE") { out = SecuritySubjectType::kCertificate; return true; }
    if (value == "WORKER_IDENTITY") { out = SecuritySubjectType::kWorkerIdentity; return true; }
    if (value == "WORKER_SIGNING_KEY") { out = SecuritySubjectType::kWorkerSigningKey; return true; }
    if (value == "COORDINATOR_SIGNING_KEY") { out = SecuritySubjectType::kCoordinatorSigningKey; return true; }
    if (value == "CAPABILITY") { out = SecuritySubjectType::kCapability; return true; }
    if (value == "HEARTBEAT") { out = SecuritySubjectType::kHeartbeat; return true; }
    if (value == "CLIENT_RESULT") { out = SecuritySubjectType::kClientResult; return true; }
    if (value == "PRIVACY_RECORD") { out = SecuritySubjectType::kPrivacyRecord; return true; }
    if (value == "TRAINING_TASK") { out = SecuritySubjectType::kTrainingTask; return true; }
    if (value == "REPLAY_STATE") { out = SecuritySubjectType::kReplayState; return true; }
    if (value == "TASK_LEASE") { out = SecuritySubjectType::kTaskLease; return true; }
    if (value == "AUDIT_QUERY") { out = SecuritySubjectType::kAuditQuery; return true; }
    if (value == "SECURITY_MUTATION") { out = SecuritySubjectType::kSecurityMutation; return true; }
    if (value == "WORKER_EVENT_BATCH") { out = SecuritySubjectType::kWorkerEventBatch; return true; }
    if (value == "SECURE_AGGREGATION_SESSION") { out = SecuritySubjectType::kSecureAggregationSession; return true; }
    return false;
}

bool security_event_type_from_string(const std::string& value, SecurityEventType& out) {
    static const std::map<std::string, SecurityEventType> kByName = {
        {"TRANSPORT_MTLS_STARTED", SecurityEventType::kTransportMtlsStarted},
        {"TRANSPORT_MTLS_FAILED", SecurityEventType::kTransportMtlsFailed},
        {"TRANSPORT_INSECURE_DEVELOPMENT_STARTED", SecurityEventType::kTransportInsecureDevelopmentStarted},
        {"PEER_CERTIFICATE_ACCEPTED", SecurityEventType::kPeerCertificateAccepted},
        {"PEER_CERTIFICATE_REJECTED", SecurityEventType::kPeerCertificateRejected},
        {"CERTIFICATE_IDENTITY_MISMATCH", SecurityEventType::kCertificateIdentityMismatch},
        {"CERTIFICATE_FINGERPRINT_REJECTED", SecurityEventType::kCertificateFingerprintRejected},
        {"CERTIFICATE_EXPIRED", SecurityEventType::kCertificateExpired},
        {"WORKER_REGISTERED", SecurityEventType::kWorkerRegistered},
        {"WORKER_REGISTRATION_REJECTED", SecurityEventType::kWorkerRegistrationRejected},
        {"WORKER_SUSPENDED", SecurityEventType::kWorkerSuspended},
        {"WORKER_ACTIVATED", SecurityEventType::kWorkerActivated},
        {"WORKER_REVOKED", SecurityEventType::kWorkerRevoked},
        {"WORKER_STATUS_RPC_REJECTED", SecurityEventType::kWorkerStatusRpcRejected},
        {"ACTIVE_LEASE_CANCELED", SecurityEventType::kActiveLeaseCanceled},
        {"WORKER_KEY_MIGRATED", SecurityEventType::kWorkerKeyMigrated},
        {"WORKER_KEY_REGISTERED", SecurityEventType::kWorkerKeyRegistered},
        {"WORKER_KEY_ROTATION_REQUESTED", SecurityEventType::kWorkerKeyRotationRequested},
        {"WORKER_KEY_ROTATION_ACCEPTED", SecurityEventType::kWorkerKeyRotationAccepted},
        {"WORKER_KEY_ROTATION_REJECTED", SecurityEventType::kWorkerKeyRotationRejected},
        {"WORKER_KEY_GRACE_STARTED", SecurityEventType::kWorkerKeyGraceStarted},
        {"WORKER_KEY_EXPIRED", SecurityEventType::kWorkerKeyExpired},
        {"WORKER_KEY_REVOKED", SecurityEventType::kWorkerKeyRevoked},
        {"MESSAGE_REJECTED_BY_KEY_STATE", SecurityEventType::kMessageRejectedByKeyState},
        {"CAPABILITY_ACCEPTED", SecurityEventType::kCapabilityAccepted},
        {"CAPABILITY_REJECTED", SecurityEventType::kCapabilityRejected},
        {"HEARTBEAT_ACCEPTED", SecurityEventType::kHeartbeatAccepted},
        {"HEARTBEAT_REJECTED", SecurityEventType::kHeartbeatRejected},
        {"CLIENT_RESULT_ACCEPTED", SecurityEventType::kClientResultAccepted},
        {"CLIENT_RESULT_REJECTED", SecurityEventType::kClientResultRejected},
        {"PRIVACY_RECORD_ACCEPTED", SecurityEventType::kPrivacyRecordAccepted},
        {"PRIVACY_RECORD_REJECTED", SecurityEventType::kPrivacyRecordRejected},
        {"SIGNATURE_VERIFICATION_FAILED", SecurityEventType::kSignatureVerificationFailed},
        {"PAYLOAD_HASH_MISMATCH", SecurityEventType::kPayloadHashMismatch},
        {"MESSAGE_REPLAY_REJECTED", SecurityEventType::kMessageReplayRejected},
        {"MESSAGE_SEQUENCE_REJECTED", SecurityEventType::kMessageSequenceRejected},
        {"MESSAGE_EXPIRED", SecurityEventType::kMessageExpired},
        {"COORDINATOR_SIGNING_KEY_LOADED", SecurityEventType::kCoordinatorSigningKeyLoaded},
        {"COORDINATOR_SIGNING_KEY_REJECTED", SecurityEventType::kCoordinatorSigningKeyRejected},
        {"COORDINATOR_TASK_SIGNED", SecurityEventType::kCoordinatorTaskSigned},
        {"COORDINATOR_TASK_SIGNING_FAILED", SecurityEventType::kCoordinatorTaskSigningFailed},
        {"COORDINATOR_TASK_ISSUED", SecurityEventType::kCoordinatorTaskIssued},
        {"COORDINATOR_TASK_VERIFIED", SecurityEventType::kCoordinatorTaskVerified},
        {"COORDINATOR_TASK_REJECTED", SecurityEventType::kCoordinatorTaskRejected},
        {"COORDINATOR_TASK_REPLAY_REJECTED", SecurityEventType::kCoordinatorTaskReplayRejected},
        {"DUPLICATE_TASK_EXECUTION_BLOCKED", SecurityEventType::kDuplicateTaskExecutionBlocked},
        {"ACCEPTED_TASK_RECOVERY_STARTED", SecurityEventType::kAcceptedTaskRecoveryStarted},
        {"ACCEPTED_TASK_RECOVERY_COMPLETED", SecurityEventType::kAcceptedTaskRecoveryCompleted},
        {"TASK_REISSUED", SecurityEventType::kTaskReissued},
        {"SECURITY_PERMISSION_DENIED", SecurityEventType::kSecurityPermissionDenied},
        {"SECURITY_MUTATION_ACCEPTED", SecurityEventType::kSecurityMutationAccepted},
        {"SECURITY_MUTATION_REJECTED", SecurityEventType::kSecurityMutationRejected},
        {"IDEMPOTENCY_REPLAY_ACCEPTED", SecurityEventType::kIdempotencyReplayAccepted},
        {"IDEMPOTENCY_CONFLICT_REJECTED", SecurityEventType::kIdempotencyConflictRejected},
        {"SECURITY_AUDIT_ACCESSED", SecurityEventType::kSecurityAuditAccessed},
        {"WORKER_SECURITY_EVENT_BATCH_ACCEPTED", SecurityEventType::kWorkerSecurityEventBatchAccepted},
        {"WORKER_SECURITY_EVENT_BATCH_REJECTED", SecurityEventType::kWorkerSecurityEventBatchRejected},
        {"SECURE_AGGREGATION_SESSION_CREATED", SecurityEventType::kSecureAggregationSessionCreated},
        {"SECURE_AGGREGATION_COHORT_FROZEN", SecurityEventType::kSecureAggregationCohortFrozen},
        {"SECURE_AGGREGATION_KEY_ADVERTISEMENT_ACCEPTED",
         SecurityEventType::kSecureAggregationKeyAdvertisementAccepted},
        {"SECURE_AGGREGATION_KEY_ADVERTISEMENT_REJECTED",
         SecurityEventType::kSecureAggregationKeyAdvertisementRejected},
        {"SECURE_AGGREGATION_SESSION_ABORTED", SecurityEventType::kSecureAggregationSessionAborted},
        {"SECURE_AGGREGATION_RESTART_ABORTED", SecurityEventType::kSecureAggregationRestartAborted},
        {"SECURE_AGGREGATION_MASKED_UPDATE_ACCEPTED", SecurityEventType::kSecureAggregationMaskedUpdateAccepted},
        {"SECURE_AGGREGATION_MASKED_UPDATE_REJECTED", SecurityEventType::kSecureAggregationMaskedUpdateRejected},
        {"SECURE_AGGREGATION_COMPLETE_COHORT_RECEIVED",
         SecurityEventType::kSecureAggregationCompleteCohortReceived},
        {"SECURE_AGGREGATION_SESSION_COMPLETED", SecurityEventType::kSecureAggregationSessionCompleted},
        {"SECURE_AGGREGATION_AGGREGATE_VALIDATION_FAILED",
         SecurityEventType::kSecureAggregationAggregateValidationFailed},
        {"SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED",
         SecurityEventType::kSecureUserLevelDpConfigurationAccepted},
        {"SECURE_USER_LEVEL_DP_CONFIGURATION_REJECTED",
         SecurityEventType::kSecureUserLevelDpConfigurationRejected},
        {"SECURE_USER_LEVEL_DP_BUDGET_RESERVED", SecurityEventType::kSecureUserLevelDpBudgetReserved},
        {"SECURE_USER_LEVEL_DP_BUDGET_EXHAUSTED", SecurityEventType::kSecureUserLevelDpBudgetExhausted},
        {"SECURE_USER_LEVEL_DP_CLIPPING_APPLIED", SecurityEventType::kSecureUserLevelDpClippingApplied},
        {"SECURE_USER_LEVEL_DP_ATTESTATION_ACCEPTED",
         SecurityEventType::kSecureUserLevelDpAttestationAccepted},
        {"SECURE_USER_LEVEL_DP_ATTESTATION_REJECTED",
         SecurityEventType::kSecureUserLevelDpAttestationRejected},
        {"SECURE_USER_LEVEL_DP_NOISE_APPLIED", SecurityEventType::kSecureUserLevelDpNoiseApplied},
        {"SECURE_USER_LEVEL_DP_ACCOUNTING_COMMITTED",
         SecurityEventType::kSecureUserLevelDpAccountingCommitted},
        {"SECURE_USER_LEVEL_DP_ROUND_COMPLETED", SecurityEventType::kSecureUserLevelDpRoundCompleted},
        {"SECURE_USER_LEVEL_DP_DROPOUT_ABORTED", SecurityEventType::kSecureUserLevelDpDropoutAborted},
        {"SECURE_USER_LEVEL_DP_FINALIZATION_CONFLICT",
         SecurityEventType::kSecureUserLevelDpFinalizationConflict},
        {"SECURE_USER_LEVEL_DP_CHECKPOINT_RECONCILED",
         SecurityEventType::kSecureUserLevelDpCheckpointReconciled},
        {"SECURE_USER_LEVEL_DP_HEALTH_DEGRADED", SecurityEventType::kSecureUserLevelDpHealthDegraded},
        {"SECURE_HYBRID_DP_CONFIGURATION_ACCEPTED", SecurityEventType::kSecureHybridDpConfigurationAccepted},
        {"SECURE_HYBRID_DP_CONFIGURATION_REJECTED", SecurityEventType::kSecureHybridDpConfigurationRejected},
        {"SECURE_HYBRID_DP_USER_BUDGET_RESERVED", SecurityEventType::kSecureHybridDpUserBudgetReserved},
        {"SECURE_HYBRID_DP_SAMPLE_RECORD_ACCEPTED", SecurityEventType::kSecureHybridDpSampleRecordAccepted},
        {"SECURE_HYBRID_DP_SAMPLE_RECORD_REJECTED", SecurityEventType::kSecureHybridDpSampleRecordRejected},
        {"SECURE_HYBRID_DP_BINDING_ACCEPTED", SecurityEventType::kSecureHybridDpBindingAccepted},
        {"SECURE_HYBRID_DP_ROUND_COMPLETED", SecurityEventType::kSecureHybridDpRoundCompleted},
        {"SECURE_HYBRID_DP_ROUND_ABORTED", SecurityEventType::kSecureHybridDpRoundAborted},
    };
    const auto it = kByName.find(value);
    if (it == kByName.end()) {
        return false;
    }
    out = it->second;
    return true;
}

SecuritySeverity default_severity(SecurityEventType type) {
    switch (type) {
        // CRITICAL: signing/issuance failures on the coordinator's own trust root.
        case SecurityEventType::kCoordinatorTaskSigningFailed:
        case SecurityEventType::kCoordinatorSigningKeyRejected:
            return SecuritySeverity::kCritical;
        // HIGH: real attack-shaped or trust-boundary rejections.
        case SecurityEventType::kPeerCertificateRejected:
        case SecurityEventType::kCertificateIdentityMismatch:
        case SecurityEventType::kCertificateFingerprintRejected:
        case SecurityEventType::kWorkerRevoked:
        case SecurityEventType::kWorkerKeyRevoked:
        case SecurityEventType::kSignatureVerificationFailed:
        case SecurityEventType::kPayloadHashMismatch:
        case SecurityEventType::kMessageReplayRejected:
        case SecurityEventType::kCoordinatorTaskReplayRejected:
        case SecurityEventType::kTransportMtlsFailed:
            return SecuritySeverity::kHigh;
        // WARNING: policy-shaped rejections, expected under normal churn.
        case SecurityEventType::kWorkerRegistrationRejected:
        case SecurityEventType::kWorkerSuspended:
        case SecurityEventType::kWorkerStatusRpcRejected:
        case SecurityEventType::kWorkerKeyRotationRejected:
        case SecurityEventType::kWorkerKeyExpired:
        case SecurityEventType::kMessageRejectedByKeyState:
        case SecurityEventType::kCapabilityRejected:
        case SecurityEventType::kHeartbeatRejected:
        case SecurityEventType::kClientResultRejected:
        case SecurityEventType::kPrivacyRecordRejected:
        case SecurityEventType::kMessageSequenceRejected:
        case SecurityEventType::kMessageExpired:
        case SecurityEventType::kCoordinatorTaskRejected:
        case SecurityEventType::kDuplicateTaskExecutionBlocked:
        case SecurityEventType::kAcceptedTaskRecoveryStarted:
        case SecurityEventType::kTransportInsecureDevelopmentStarted:
        case SecurityEventType::kSecurityPermissionDenied:
        case SecurityEventType::kSecurityMutationRejected:
        case SecurityEventType::kIdempotencyConflictRejected:
        case SecurityEventType::kWorkerSecurityEventBatchRejected:
        case SecurityEventType::kSecureAggregationKeyAdvertisementRejected:
        case SecurityEventType::kSecureAggregationSessionAborted:
        case SecurityEventType::kSecureAggregationRestartAborted:
        case SecurityEventType::kSecureAggregationMaskedUpdateRejected:
        case SecurityEventType::kSecureUserLevelDpConfigurationRejected:
        case SecurityEventType::kSecureUserLevelDpBudgetExhausted:
        case SecurityEventType::kSecureUserLevelDpAttestationRejected:
        case SecurityEventType::kSecureUserLevelDpDropoutAborted:
        case SecurityEventType::kSecureHybridDpConfigurationRejected:
        case SecurityEventType::kSecureHybridDpSampleRecordRejected:
        case SecurityEventType::kSecureHybridDpRoundAborted:
            return SecuritySeverity::kWarning;
        // HIGH: an aggregate that decoded but failed to actually advance
        // the model -- the disclosed residual-inconsistency window
        // (see RunInstance::apply_secure_aggregate_and_advance). Also a
        // duplicate-finalization/reconciliation-required signal, since
        // both indicate the exactly-once accounting guarantee needs
        // operator attention even though no privacy budget was actually
        // double-spent (the guard that prevents double-spend is what
        // fired -- see docs/secure-user-level-dp-publication-boundary.md).
        case SecurityEventType::kSecureAggregationAggregateValidationFailed:
        case SecurityEventType::kSecureUserLevelDpFinalizationConflict:
        case SecurityEventType::kSecureUserLevelDpHealthDegraded:
            return SecuritySeverity::kHigh;
        // INFO: everything else -- normal accepted/completed operation.
        default:
            return SecuritySeverity::kInfo;
    }
}

SecurityEventValidationResult validate_security_event(const SecurityEvent& event) {
    if (event.schema_version != kSecurityEventSchemaVersion) {
        return {false, "unsupported schema_version"};
    }
    if (event.source_service.empty()) {
        return {false, "source_service is required"};
    }
    if (event.reason_code.size() > kSecurityEventMaxReasonCodeLength) {
        return {false, "reason_code exceeds maximum length"};
    }
    if (event.safe_details.size() > kSecurityEventMaxDetailKeys) {
        return {false, "safe_details has too many keys"};
    }
    for (const auto& [key, value] : event.safe_details) {
        if (value.size() > kSecurityEventMaxDetailValueLength) {
            return {false, "safe_details value exceeds maximum length for key '" + key + "'"};
        }
    }
    return {true, "ok"};
}

std::string canonical_security_event_payload_json(const SecurityEvent& event) {
    // Field order is the alphabetical sort of field names, matching the
    // json.dumps(sort_keys=True) convention documented in
    // docs/canonical-security-serialization.md and mirrored by
    // python/src/fl_platform/security/security_event.py and
    // go/internal/observability/security_event.go.
    std::ostringstream out;
    out << "{";
    out << "\"actor_type\":" << json_escape_string(to_string(event.actor_type)) << ",";
    out << "\"event_type\":" << json_escape_string(to_string(event.event_type)) << ",";
    out << "\"outcome\":" << json_escape_string(to_string(event.outcome)) << ",";
    out << "\"reason_code\":" << json_escape_string(event.reason_code) << ",";
    out << "\"request_id\":" << json_escape_string(event.request_id) << ",";
    out << "\"round_id\":" << json_uint(event.round_id) << ",";
    out << "\"run_id\":" << json_escape_string(event.run_id) << ",";
    out << "\"safe_actor_id\":" << json_escape_string(event.safe_actor_id) << ",";
    out << "\"safe_details\":{";
    bool first_detail = true;
    for (const auto& [key, value] : event.safe_details) {
        if (!first_detail) {
            out << ",";
        }
        first_detail = false;
        out << json_escape_string(key) << ":" << json_escape_string(value);
    }
    out << "},";
    out << "\"safe_signing_key_id\":" << json_escape_string(event.safe_signing_key_id) << ",";
    out << "\"safe_subject_id\":" << json_escape_string(event.safe_subject_id) << ",";
    out << "\"schema_version\":" << event.schema_version << ",";
    out << "\"severity\":" << json_escape_string(to_string(event.severity)) << ",";
    out << "\"source_component\":" << json_escape_string(event.source_component) << ",";
    out << "\"source_service\":" << json_escape_string(event.source_service) << ",";
    out << "\"subject_type\":" << json_escape_string(to_string(event.subject_type)) << ",";
    out << "\"task_id\":" << json_escape_string(event.task_id) << ",";
    out << "\"trace_id\":" << json_escape_string(event.trace_id) << ",";
    out << "\"worker_id\":" << json_escape_string(event.worker_id);
    out << "}";
    return out.str();
}

std::string compute_security_event_checksum(const SecurityEvent& event) {
    return hash_to_hex(fnv1a_hash(canonical_security_event_payload_json(event)));
}

}  // namespace fl::coordinator
