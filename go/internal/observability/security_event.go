package observability

// Shared, versioned security-event schema -- Security Events, Metrics,
// and Durable Audit Journal slice, Work Package B/C. See
// docs/security-events.md and docs/security-observability-inventory.md.
//
// Mirrors, field-for-field,
// cpp/coordinator/include/fl_coordinator/security_event.hpp and
// python/src/fl_platform/security/security_event.py. Enum values are
// plain strings (not Go iota constants), matching
// go/internal/security.Permission's existing string-typed convention --
// "string-typed (not iota) so it round-trips cleanly through logs/audit
// records without a lookup table."
//
// A SecurityEvent never carries: private keys, raw signatures, raw
// nonces, raw certificate PEM, full signed payloads, tensor values,
// dataset samples, privacy noise, or secret-share data.

const SecurityEventSchemaVersion = 1

const (
	SeverityInfo     = "INFO"
	SeverityWarning  = "WARNING"
	SeverityHigh     = "HIGH"
	SeverityCritical = "CRITICAL"
)

const (
	OutcomeAccepted  = "ACCEPTED"
	OutcomeRejected  = "REJECTED"
	OutcomeCompleted = "COMPLETED"
	OutcomeFailed    = "FAILED"
	OutcomeBlocked   = "BLOCKED"
	OutcomeCanceled  = "CANCELED"
)

const (
	ActorTypeUser        = "USER"
	ActorTypeService     = "SERVICE"
	ActorTypeWorker      = "WORKER"
	ActorTypeCoordinator = "COORDINATOR"
	ActorTypeSystem      = "SYSTEM"
)

const (
	SubjectTypeTransport             = "TRANSPORT"
	SubjectTypeCertificate           = "CERTIFICATE"
	SubjectTypeWorkerIdentity        = "WORKER_IDENTITY"
	SubjectTypeWorkerSigningKey      = "WORKER_SIGNING_KEY"
	SubjectTypeCoordinatorSigningKey = "COORDINATOR_SIGNING_KEY"
	SubjectTypeCapability            = "CAPABILITY"
	SubjectTypeHeartbeat             = "HEARTBEAT"
	SubjectTypeClientResult          = "CLIENT_RESULT"
	SubjectTypePrivacyRecord         = "PRIVACY_RECORD"
	SubjectTypeTrainingTask          = "TRAINING_TASK"
	SubjectTypeReplayState           = "REPLAY_STATE"
	SubjectTypeTaskLease             = "TASK_LEASE"
	SubjectTypeAuditQuery            = "AUDIT_QUERY"
	SubjectTypeSecurityMutation      = "SECURITY_MUTATION"
)

// Event type registry (Work Package C). Only the subset Go itself
// originates is exercised by this package's own emission call sites
// (security_handlers.go) -- the rest are recognized here so events
// relayed from the C++ coordinator's ListSecurityEvents RPC round-trip
// through the same Go types without a separate parallel enum.
const (
	EventTransportMtlsStarted                = "TRANSPORT_MTLS_STARTED"
	EventTransportMtlsFailed                 = "TRANSPORT_MTLS_FAILED"
	EventTransportInsecureDevelopmentStarted = "TRANSPORT_INSECURE_DEVELOPMENT_STARTED"
	EventPeerCertificateAccepted             = "PEER_CERTIFICATE_ACCEPTED"
	EventPeerCertificateRejected             = "PEER_CERTIFICATE_REJECTED"
	EventCertificateIdentityMismatch         = "CERTIFICATE_IDENTITY_MISMATCH"
	EventCertificateFingerprintRejected      = "CERTIFICATE_FINGERPRINT_REJECTED"
	EventCertificateExpired                  = "CERTIFICATE_EXPIRED"

	EventWorkerRegistered           = "WORKER_REGISTERED"
	EventWorkerRegistrationRejected = "WORKER_REGISTRATION_REJECTED"
	EventWorkerSuspended            = "WORKER_SUSPENDED"
	EventWorkerActivated            = "WORKER_ACTIVATED"
	EventWorkerRevoked              = "WORKER_REVOKED"
	EventWorkerStatusRPCRejected    = "WORKER_STATUS_RPC_REJECTED"
	EventActiveLeaseCanceled        = "ACTIVE_LEASE_CANCELED"

	EventWorkerKeyMigrated          = "WORKER_KEY_MIGRATED"
	EventWorkerKeyRegistered        = "WORKER_KEY_REGISTERED"
	EventWorkerKeyRotationRequested = "WORKER_KEY_ROTATION_REQUESTED"
	EventWorkerKeyRotationAccepted  = "WORKER_KEY_ROTATION_ACCEPTED"
	EventWorkerKeyRotationRejected  = "WORKER_KEY_ROTATION_REJECTED"
	EventWorkerKeyGraceStarted      = "WORKER_KEY_GRACE_STARTED"
	EventWorkerKeyExpired           = "WORKER_KEY_EXPIRED"
	EventWorkerKeyRevoked           = "WORKER_KEY_REVOKED"
	EventMessageRejectedByKeyState  = "MESSAGE_REJECTED_BY_KEY_STATE"

	EventCapabilityAccepted          = "CAPABILITY_ACCEPTED"
	EventCapabilityRejected          = "CAPABILITY_REJECTED"
	EventHeartbeatAccepted           = "HEARTBEAT_ACCEPTED"
	EventHeartbeatRejected           = "HEARTBEAT_REJECTED"
	EventClientResultAccepted        = "CLIENT_RESULT_ACCEPTED"
	EventClientResultRejected        = "CLIENT_RESULT_REJECTED"
	EventPrivacyRecordAccepted       = "PRIVACY_RECORD_ACCEPTED"
	EventPrivacyRecordRejected       = "PRIVACY_RECORD_REJECTED"
	EventSignatureVerificationFailed = "SIGNATURE_VERIFICATION_FAILED"
	EventPayloadHashMismatch         = "PAYLOAD_HASH_MISMATCH"
	EventMessageReplayRejected       = "MESSAGE_REPLAY_REJECTED"
	EventMessageSequenceRejected     = "MESSAGE_SEQUENCE_REJECTED"
	EventMessageExpired              = "MESSAGE_EXPIRED"

	EventCoordinatorSigningKeyLoaded   = "COORDINATOR_SIGNING_KEY_LOADED"
	EventCoordinatorSigningKeyRejected = "COORDINATOR_SIGNING_KEY_REJECTED"
	EventCoordinatorTaskSigned         = "COORDINATOR_TASK_SIGNED"
	EventCoordinatorTaskSigningFailed  = "COORDINATOR_TASK_SIGNING_FAILED"
	EventCoordinatorTaskIssued         = "COORDINATOR_TASK_ISSUED"
	EventCoordinatorTaskVerified       = "COORDINATOR_TASK_VERIFIED"
	EventCoordinatorTaskRejected       = "COORDINATOR_TASK_REJECTED"
	EventCoordinatorTaskReplayRejected = "COORDINATOR_TASK_REPLAY_REJECTED"
	EventDuplicateTaskExecutionBlocked = "DUPLICATE_TASK_EXECUTION_BLOCKED"
	EventAcceptedTaskRecoveryStarted   = "ACCEPTED_TASK_RECOVERY_STARTED"
	EventAcceptedTaskRecoveryCompleted = "ACCEPTED_TASK_RECOVERY_COMPLETED"
	EventTaskReissued                  = "TASK_REISSUED"

	EventSecurityPermissionDenied    = "SECURITY_PERMISSION_DENIED"
	EventSecurityMutationAccepted    = "SECURITY_MUTATION_ACCEPTED"
	EventSecurityMutationRejected    = "SECURITY_MUTATION_REJECTED"
	EventIdempotencyReplayAccepted   = "IDEMPOTENCY_REPLAY_ACCEPTED"
	EventIdempotencyConflictRejected = "IDEMPOTENCY_CONFLICT_REJECTED"
	EventSecurityAuditAccessed       = "SECURITY_AUDIT_ACCESSED"
)

const (
	MaxReasonCodeLength  = 128
	MaxDetailKeys        = 10
	MaxDetailValueLength = 256
)

// SecurityEvent is the shared record shape. JSON tags match the wire
// field names used by the C++ ListSecurityEvents RPC
// (SecurityEventRecord) and the Python journal, so a single struct can
// be marshaled straight to HTTP JSON without a second projection type.
type SecurityEvent struct {
	SchemaVersion    int               `json:"schema_version"`
	EventID          string            `json:"event_id"`
	EventType        string            `json:"event_type"`
	Severity         string            `json:"severity"`
	Timestamp        string            `json:"timestamp"`
	SourceService    string            `json:"source_service"`
	SourceComponent  string            `json:"source_component"`
	ActorType        string            `json:"actor_type"`
	SafeActorID      string            `json:"safe_actor_id"`
	SubjectType      string            `json:"subject_type"`
	SafeSubjectID    string            `json:"safe_subject_id"`
	WorkerID         string            `json:"worker_id"`
	RunID            string            `json:"run_id"`
	RoundID          uint64            `json:"round_id"`
	TaskID           string            `json:"task_id"`
	SafeSigningKeyID string            `json:"safe_signing_key_id"`
	RequestID        string            `json:"request_id"`
	TraceID          string            `json:"trace_id"`
	Outcome          string            `json:"outcome"`
	ReasonCode       string            `json:"reason_code"`
	SafeDetails      map[string]string `json:"safe_details"`
	PayloadChecksum  string            `json:"payload_checksum"`
}

var validSeverities = map[string]bool{
	SeverityInfo: true, SeverityWarning: true, SeverityHigh: true, SeverityCritical: true,
}

var validOutcomes = map[string]bool{
	OutcomeAccepted: true, OutcomeRejected: true, OutcomeCompleted: true,
	OutcomeFailed: true, OutcomeBlocked: true, OutcomeCanceled: true,
}

// ValidateSecurityEvent enforces the same bounds as the C++/Python
// implementations (Work Package B's "Validation before persistence").
// A failing event must be logged and dropped by the caller, never
// allowed to crash a security decision path.
func ValidateSecurityEvent(event SecurityEvent) (bool, string) {
	if event.SchemaVersion != SecurityEventSchemaVersion {
		return false, "unsupported schema_version"
	}
	if event.SourceService == "" {
		return false, "source_service is required"
	}
	if event.Severity != "" && !validSeverities[event.Severity] {
		return false, "unrecognized severity"
	}
	if event.Outcome != "" && !validOutcomes[event.Outcome] {
		return false, "unrecognized outcome"
	}
	if len(event.ReasonCode) > MaxReasonCodeLength {
		return false, "reason_code exceeds maximum length"
	}
	if len(event.SafeDetails) > MaxDetailKeys {
		return false, "safe_details has too many keys"
	}
	for key, value := range event.SafeDetails {
		if len(value) > MaxDetailValueLength {
			return false, "safe_details value exceeds maximum length for key '" + key + "'"
		}
	}
	return true, "ok"
}

// DefaultSeverity mirrors cpp/coordinator/src/security_event.cpp's
// default_severity switch -- kept in sync by hand (see that file's
// comment for the same disclosed limitation).
func DefaultSeverity(eventType string) string {
	switch eventType {
	case EventCoordinatorTaskSigningFailed, EventCoordinatorSigningKeyRejected:
		return SeverityCritical
	case EventPeerCertificateRejected, EventCertificateIdentityMismatch,
		EventCertificateFingerprintRejected, EventWorkerRevoked, EventWorkerKeyRevoked,
		EventSignatureVerificationFailed, EventPayloadHashMismatch, EventMessageReplayRejected,
		EventCoordinatorTaskReplayRejected, EventTransportMtlsFailed:
		return SeverityHigh
	case EventWorkerRegistrationRejected, EventWorkerSuspended, EventWorkerStatusRPCRejected,
		EventWorkerKeyRotationRejected, EventWorkerKeyExpired, EventMessageRejectedByKeyState,
		EventCapabilityRejected, EventHeartbeatRejected, EventClientResultRejected,
		EventPrivacyRecordRejected, EventMessageSequenceRejected, EventMessageExpired,
		EventCoordinatorTaskRejected, EventDuplicateTaskExecutionBlocked,
		EventAcceptedTaskRecoveryStarted, EventTransportInsecureDevelopmentStarted,
		EventSecurityPermissionDenied, EventSecurityMutationRejected, EventIdempotencyConflictRejected:
		return SeverityWarning
	default:
		return SeverityInfo
	}
}
