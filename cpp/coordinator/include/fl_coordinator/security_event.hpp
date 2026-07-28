#pragma once

// Shared, versioned security-event schema -- Security Events, Metrics, and
// Durable Audit Journal slice, Work Package B/C. See
// docs/security-events.md and docs/security-observability-inventory.md.
//
// Mirrored field-for-field (not byte-for-byte serialization-identical --
// see docs/canonical-security-serialization.md's scope note) by
// python/src/fl_platform/security/security_event.py and
// go/internal/observability/security_event.go. This header is
// deliberately protobuf-free and gRPC-free, matching every other
// coordinator persistence/domain type in this codebase, so it builds and
// is unit-testable on this Windows/MSVC development machine without a
// local gRPC toolchain (see idempotency_store.hpp's identical rationale).
//
// A SecurityEvent never carries: private keys, raw signatures, raw
// nonces, raw certificate PEM, full signed payloads, tensor values,
// dataset samples, privacy noise, or secret-share data -- only
// identifiers, small bounded strings, and a handful of scalars. Every
// "safe_*" field name is a reminder to the caller: put an opaque ID or
// short label there, never a secret or raw credential.

#include <cstdint>
#include <map>
#include <string>

namespace fl::coordinator {

enum class SecuritySeverity {
    kInfo,
    kWarning,
    kHigh,
    kCritical,
};

enum class SecurityOutcome {
    kAccepted,
    kRejected,
    kCompleted,
    kFailed,
    kBlocked,
    kCanceled,
};

enum class SecurityActorType {
    kUser,
    kService,
    kWorker,
    kCoordinator,
    kSystem,
};

enum class SecuritySubjectType {
    kTransport,
    kCertificate,
    kWorkerIdentity,
    kWorkerSigningKey,
    kCoordinatorSigningKey,
    kCapability,
    kHeartbeat,
    kClientResult,
    kPrivacyRecord,
    kTrainingTask,
    kReplayState,
    kTaskLease,
    kAuditQuery,
    kSecurityMutation,
    // Web Security Center, Event Centralization, and Security CI slice,
    // Work Package L.
    kWorkerEventBatch,
    // Secure Cohort Handshake and Signed Roster Runtime slice
    // (docs/secure-cohort-handshake-foundation.md).
    kSecureAggregationSession,
};

// The explicit event-type registry (Work Package C). Every enumerator's
// canonical wire string is its SCREAMING_SNAKE_CASE name, produced by
// to_string() below -- this is what actually appears in a persisted
// journal record and must stay stable once released.
enum class SecurityEventType {
    // Transport
    kTransportMtlsStarted,
    kTransportMtlsFailed,
    kTransportInsecureDevelopmentStarted,
    kPeerCertificateAccepted,
    kPeerCertificateRejected,
    kCertificateIdentityMismatch,
    kCertificateFingerprintRejected,
    kCertificateExpired,
    // Worker identity
    kWorkerRegistered,
    kWorkerRegistrationRejected,
    kWorkerSuspended,
    kWorkerActivated,
    kWorkerRevoked,
    kWorkerStatusRpcRejected,
    kActiveLeaseCanceled,
    // Worker signing keys
    kWorkerKeyMigrated,
    kWorkerKeyRegistered,
    kWorkerKeyRotationRequested,
    kWorkerKeyRotationAccepted,
    kWorkerKeyRotationRejected,
    kWorkerKeyGraceStarted,
    kWorkerKeyExpired,
    kWorkerKeyRevoked,
    kMessageRejectedByKeyState,
    // Signed worker messages
    kCapabilityAccepted,
    kCapabilityRejected,
    kHeartbeatAccepted,
    kHeartbeatRejected,
    kClientResultAccepted,
    kClientResultRejected,
    kPrivacyRecordAccepted,
    kPrivacyRecordRejected,
    kSignatureVerificationFailed,
    kPayloadHashMismatch,
    kMessageReplayRejected,
    kMessageSequenceRejected,
    kMessageExpired,
    // Coordinator tasks
    kCoordinatorSigningKeyLoaded,
    kCoordinatorSigningKeyRejected,
    kCoordinatorTaskSigned,
    kCoordinatorTaskSigningFailed,
    kCoordinatorTaskIssued,
    kCoordinatorTaskVerified,
    kCoordinatorTaskRejected,
    kCoordinatorTaskReplayRejected,
    kDuplicateTaskExecutionBlocked,
    kAcceptedTaskRecoveryStarted,
    kAcceptedTaskRecoveryCompleted,
    kTaskReissued,
    // Security administration
    kSecurityPermissionDenied,
    kSecurityMutationAccepted,
    kSecurityMutationRejected,
    kIdempotencyReplayAccepted,
    kIdempotencyConflictRejected,
    kSecurityAuditAccessed,
    // Web Security Center, Event Centralization, and Security CI slice,
    // Work Package L: whole-batch outcome for SubmitWorkerSecurityEvents
    // -- distinct from any individual worker-submitted event relayed
    // *inside* an accepted batch (those are journaled with whatever
    // event_type each one carries, source_service="python-worker"; this
    // pair describes the batch envelope itself, source_service="coordinator").
    kWorkerSecurityEventBatchAccepted,
    kWorkerSecurityEventBatchRejected,
    // Secure Cohort Handshake and Signed Roster Runtime slice
    // (docs/secure-cohort-handshake-foundation.md), Work item 17. A
    // deliberately minimal, representative set -- not the full 16-value
    // registry a live masked-update path would eventually need (that
    // remains Work item scope for a future slice; SubmitMaskedClientUpdate
    // itself stays UNIMPLEMENTED this pass, so no masked-update-specific
    // event type is added yet either).
    kSecureAggregationSessionCreated,
    kSecureAggregationCohortFrozen,
    kSecureAggregationKeyAdvertisementAccepted,
    kSecureAggregationKeyAdvertisementRejected,
    kSecureAggregationSessionAborted,
    kSecureAggregationRestartAborted,
    // Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area N/AE: the live SubmitMaskedClientUpdate path.
    kSecureAggregationMaskedUpdateAccepted,
    kSecureAggregationMaskedUpdateRejected,
    kSecureAggregationCompleteCohortReceived,
    kSecureAggregationSessionCompleted,
    kSecureAggregationAggregateValidationFailed,
    // Secure User-Level DP Operations, Observability, and Release Evidence
    // slice, Work Area B/C: a deliberately bounded, representative subset
    // of the requested ~29-name SECURE_USER_LEVEL_DP_* vocabulary -- the
    // lifecycle checkpoints an operator most needs, not every fine-grained
    // sub-step (see docs/secure-user-level-operations-audit.md's scope
    // statement). None of these payloads ever carry a clear update,
    // individual norm, clipping factor, individual weight, noise tensor/
    // state, masked bytes, shared secret, key, nonce, or dataset sample --
    // enforced by validate_security_event's existing bounded-field-only
    // shape, the same guarantee every other SecurityEvent already has.
    kSecureUserLevelDpConfigurationAccepted,
    kSecureUserLevelDpConfigurationRejected,
    kSecureUserLevelDpBudgetReserved,
    kSecureUserLevelDpBudgetExhausted,
    kSecureUserLevelDpClippingApplied,
    kSecureUserLevelDpAttestationAccepted,
    kSecureUserLevelDpAttestationRejected,
    kSecureUserLevelDpNoiseApplied,
    kSecureUserLevelDpAccountingCommitted,
    kSecureUserLevelDpRoundCompleted,
    kSecureUserLevelDpDropoutAborted,
    kSecureUserLevelDpFinalizationConflict,
    kSecureUserLevelDpCheckpointReconciled,
    kSecureUserLevelDpHealthDegraded,
    // Secure Hybrid Differential Privacy Runtime slice, Work Area V: a
    // deliberately bounded, representative subset of the requested
    // ~22-name SECURE_HYBRID_DP_* vocabulary -- the two layered
    // mechanisms' own already-real events (SecureUserLevelDp*,
    // SecureAggregationMaskedUpdate*) already cover most checkpoints;
    // these exist specifically for the checkpoints that are unique to
    // the *combination* (dual-record validation, per-round hybrid
    // completion). None of these payloads ever carry a clear update,
    // individual norm, clipping factor, individual sample epsilon,
    // noise value, masked bytes, shared secret, key, or nonce -- same
    // bounded-field-only guarantee as every other SecurityEvent.
    kSecureHybridDpConfigurationAccepted,
    kSecureHybridDpConfigurationRejected,
    kSecureHybridDpUserBudgetReserved,
    kSecureHybridDpSampleRecordAccepted,
    kSecureHybridDpSampleRecordRejected,
    kSecureHybridDpBindingAccepted,
    kSecureHybridDpRoundCompleted,
    kSecureHybridDpRoundAborted,

    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: a deliberately bounded, representative subset of the
    // requested ~21-name SECURE_ADAPTIVE_CLIPPING_* vocabulary,
    // mirroring the hybrid slice's own identical scope-bounding
    // decision immediately above. Never carries an individual norm,
    // individual indicator, raw indicator count, noise value, clipping
    // factor, masked bytes, shared secret, key, or nonce.
    kSecureAdaptiveClippingConfigurationAccepted,
    kSecureAdaptiveClippingConfigurationRejected,
    kSecureAdaptiveClippingIndicatorAccepted,
    kSecureAdaptiveClippingIndicatorRejected,
    kSecureAdaptiveClippingCompleteCohortReconstructed,
    kSecureAdaptiveClippingNextStatePublished,
    kSecureAdaptiveClippingRoundCompleted,
    kSecureAdaptiveClippingRoundAborted,
};

[[nodiscard]] std::string to_string(SecuritySeverity value);
[[nodiscard]] std::string to_string(SecurityOutcome value);
[[nodiscard]] std::string to_string(SecurityActorType value);
[[nodiscard]] std::string to_string(SecuritySubjectType value);
[[nodiscard]] std::string to_string(SecurityEventType value);

// Reverse mappings, used only by SecurityEventJournal/SecurityAuditJournal
// when reloading a persisted record from disk. Return false (leaving
// `out` unchanged) on an unrecognized string rather than throwing -- a
// journal reload must treat an unrecognized enum string as a corrupt
// line to skip, not a crash.
[[nodiscard]] bool security_severity_from_string(const std::string& value, SecuritySeverity& out);
[[nodiscard]] bool security_outcome_from_string(const std::string& value, SecurityOutcome& out);
[[nodiscard]] bool security_actor_type_from_string(const std::string& value,
                                                   SecurityActorType& out);
[[nodiscard]] bool security_subject_type_from_string(const std::string& value,
                                                     SecuritySubjectType& out);
[[nodiscard]] bool security_event_type_from_string(const std::string& value,
                                                   SecurityEventType& out);

// Default severity for a given event type, per Work Package C's "Document
// each event's default severity" requirement -- a producer may still
// override it explicitly (e.g. a CRITICAL variant of a normally-WARNING
// rejection), but this is the fallback used when none is supplied.
[[nodiscard]] SecuritySeverity default_severity(SecurityEventType type);

constexpr int kSecurityEventSchemaVersion = 1;
constexpr std::size_t kSecurityEventMaxReasonCodeLength = 128;
constexpr std::size_t kSecurityEventMaxDetailKeys = 10;
constexpr std::size_t kSecurityEventMaxDetailValueLength = 256;

// One record. Every field below is either a bounded scalar or a bounded
// short string -- see the header comment for what must never appear here.
// Unused optional fields are left at their zero/empty default, never
// omitted, matching CoordinatorEvent's fixed-shape convention (event_bus.hpp).
struct SecurityEvent {
    int schema_version = kSecurityEventSchemaVersion;
    std::string event_id;  // assigned by the journal on emit(), not the caller
    SecurityEventType event_type = SecurityEventType::kSecurityPermissionDenied;
    SecuritySeverity severity = SecuritySeverity::kInfo;
    std::string timestamp;         // ISO-8601 UTC, assigned by the journal if empty
    std::string source_service;    // e.g. "coordinator", "go-api", "python-worker"
    std::string source_component;  // e.g. "worker_registry", "signed_envelope_verifier"
    SecurityActorType actor_type = SecurityActorType::kSystem;
    std::string safe_actor_id;
    SecuritySubjectType subject_type = SecuritySubjectType::kSecurityMutation;
    std::string safe_subject_id;
    std::string worker_id;
    std::string run_id;
    std::uint64_t round_id = 0;
    std::string task_id;
    std::string safe_signing_key_id;
    std::string request_id;
    std::string trace_id;
    SecurityOutcome outcome = SecurityOutcome::kAccepted;
    std::string reason_code;  // bounded to kSecurityEventMaxReasonCodeLength
    std::map<std::string, std::string> safe_details;  // bounded, see constants above
    std::string payload_checksum;  // assigned by the journal on emit(), not the caller
};

// Result of validating a SecurityEvent against the bounds above before
// persistence (Work Package B's "Validation before persistence"
// requirement). A failing event must never be allowed to crash or block
// the caller's actual security decision -- the caller logs
// result.reason locally and drops the event instead.
struct SecurityEventValidationResult {
    bool valid = false;
    std::string reason;  // always set, even when valid == true ("ok")
};

[[nodiscard]] SecurityEventValidationResult validate_security_event(const SecurityEvent& event);

// JSON string escaping matching Python's json.dumps(ensure_ascii=True)
// for the C0 control range and '"'/'\\', with any byte >= 0x80 re-emitted
// as \u00XX (a deliberately simplified, non-UTF-8-decoding variant of
// capability_statement_verifier.cpp's escaper -- see security_event.cpp
// for why). Exposed here (not file-local) because both
// SecurityEventJournal and SecurityAuditJournal need the identical
// encoding for their own full-record JSONL serialization, not just the
// canonical payload below.
[[nodiscard]] std::string json_escape_string(const std::string& value);

// Canonical JSON encoding of every field except event_id/timestamp/
// payload_checksum (those are excluded because payload_checksum is
// computed *over* this string, and event_id/timestamp are journal-
// assigned metadata about the record, not part of what the checksum
// protects against corruption in transit). Key-sorted, matching the
// json.dumps(..., sort_keys=True, separators=(",", ":"))
// convention from docs/canonical-security-serialization.md.
[[nodiscard]] std::string canonical_security_event_payload_json(const SecurityEvent& event);

// FNV-1a 64-bit hex digest of canonical_security_event_payload_json(event)
// -- corruption/tamper-in-transit detection only, explicitly not a
// cryptographic MAC, matching every other checksum in this codebase
// (idempotency_store.cpp, replay_protection_store.cpp, etc.).
[[nodiscard]] std::string compute_security_event_checksum(const SecurityEvent& event);

}  // namespace fl::coordinator
