package coordinator

// Security Operations and Administration slice (docs/security-api.md):
// typed Go bindings for the coordinator's ADMIN_CONTROL RPCs. Every
// type here mirrors a wire message that already excludes secret
// material at the C++ layer (no private_key/nonce/full-envelope
// fields exist in any of these proto messages — see
// docs/rpc-security-policy.md) — this file does not perform its own
// redaction; that happens one layer up, in the HTTP handlers, which
// apply role-based projections on top of these already-safe structs
// (Work Package C/U).

import (
	"context"
	"fmt"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
)

// TransportSecurityStatus mirrors fl.coordinator.v1.TransportSecurityStatusResponse.
type TransportSecurityStatus struct {
	TransportMode     string  `json:"transport_mode"`
	MutualTLSEnforced bool    `json:"mutual_tls_enforced"`
	CheckedAtUnixS    float64 `json:"checked_at_unix_s"`
}

// SecurityTrustModel mirrors fl.coordinator.v1.SecurityTrustModelResponse
// — aggregate counts only, never a full key/worker listing (see
// ListWorkerIdentities/ListCoordinatorSigningKeys for that).
type SecurityTrustModel struct {
	ActiveCoordinatorSigningKeyID string  `json:"active_coordinator_signing_key_id"`
	TrustedCoordinatorKeyCount    uint64  `json:"trusted_coordinator_key_count"`
	TrustedKeyBundleVersion       uint64  `json:"trusted_key_bundle_version"`
	RegisteredWorkerCount         uint64  `json:"registered_worker_count"`
	WorkerSigningKeyTotalCount    uint64  `json:"worker_signing_key_total_count"`
	CheckedAtUnixS                float64 `json:"checked_at_unix_s"`
}

// WorkerIdentitySummary mirrors fl.coordinator.v1.WorkerIdentitySummary.
// Deliberately excludes signing_public_key bytes — same restriction the
// C++ RPC itself already applies (see coordinator_service.cpp).
type WorkerIdentitySummary struct {
	WorkerID               string  `json:"worker_id"`
	CertificateIdentity    string  `json:"certificate_identity"`
	CertificateFingerprint string  `json:"certificate_fingerprint"`
	SigningKeyID           string  `json:"signing_key_id"`
	RegistrationStatus     string  `json:"registration_status"`
	SoftwareVersion        string  `json:"software_version"`
	BuildID                string  `json:"build_id"`
	CreatedAtUnixS         float64 `json:"created_at_unix_s"`
	UpdatedAtUnixS         float64 `json:"updated_at_unix_s"`
	ExpiresAtUnixS         float64 `json:"expires_at_unix_s"`
	SuspendedAtUnixS       float64 `json:"suspended_at_unix_s"`
	RevokedAtUnixS         float64 `json:"revoked_at_unix_s"`
	RevocationReason       string  `json:"revocation_reason"`
}

// WorkerLifecycleRequest is shared by Suspend/Activate/RevokeWorker —
// mirrors SuspendWorkerRequest/ActivateWorkerRequest/RevokeWorkerRequest,
// which are field-for-field identical on the wire.
type WorkerLifecycleRequest struct {
	WorkerID  string
	Reason    string
	RequestID string
	TraceID   string
}

// WorkerLifecycleResult mirrors fl.coordinator.v1.WorkerLifecycleResponse.
type WorkerLifecycleResult struct {
	Identity       WorkerIdentitySummary `json:"identity"`
	Changed        bool                  `json:"changed"`
	LeasesCanceled uint32                `json:"leases_canceled"`
}

// WorkerSigningKeySummary mirrors fl.coordinator.v1.SigningKeyRecordSummary.
type WorkerSigningKeySummary struct {
	WorkerID              string  `json:"worker_id"`
	SigningKeyID          string  `json:"signing_key_id"`
	PublicKeyFingerprint  string  `json:"public_key_fingerprint"`
	Status                string  `json:"status"`
	CreatedAtUnixS        float64 `json:"created_at_unix_s"`
	ActivatedAtUnixS      float64 `json:"activated_at_unix_s"`
	ExpiresAtUnixS        float64 `json:"expires_at_unix_s"`
	GracePeriodStartUnixS float64 `json:"grace_period_start_unix_s"`
	GracePeriodEndUnixS   float64 `json:"grace_period_end_unix_s"`
	RotatedFromKeyID      string  `json:"rotated_from_key_id"`
	RotatedToKeyID        string  `json:"rotated_to_key_id"`
	RevokedAtUnixS        float64 `json:"revoked_at_unix_s"`
	RevocationReason      string  `json:"revocation_reason"`
	RegistrationSource    string  `json:"registration_source"`
}

// RevokeWorkerSigningKeyRequest mirrors fl.coordinator.v1.RevokeWorkerSigningKeyRequest.
type RevokeWorkerSigningKeyRequest struct {
	WorkerID     string
	SigningKeyID string
	Reason       string
	RequestID    string
	TraceID      string
}

// WorkerSigningKeyRevocationResult mirrors fl.coordinator.v1.RevokeWorkerSigningKeyResponse.
type WorkerSigningKeyRevocationResult struct {
	Key             WorkerSigningKeySummary `json:"key"`
	Changed         bool                    `json:"changed"`
	WorkerSuspended bool                    `json:"worker_suspended"`
}

// CoordinatorSigningKeySummary mirrors fl.coordinator.v1.CoordinatorSigningKeyRecordSummary.
type CoordinatorSigningKeySummary struct {
	SigningKeyID         string  `json:"signing_key_id"`
	PublicKeyFingerprint string  `json:"public_key_fingerprint"`
	Status               string  `json:"status"`
	CreatedAtUnixS       float64 `json:"created_at_unix_s"`
	ExpiresAtUnixS       float64 `json:"expires_at_unix_s"`
	GracePeriodEndUnixS  float64 `json:"grace_period_end_unix_s"`
	RotatedFromKeyID     string  `json:"rotated_from_key_id"`
	RotatedToKeyID       string  `json:"rotated_to_key_id"`
	RevokedAtUnixS       float64 `json:"revoked_at_unix_s"`
	RevocationReason     string  `json:"revocation_reason"`
}

// RotateCoordinatorSigningKeyRequest mirrors fl.coordinator.v1.RotateCoordinatorSigningKeyRequest.
type RotateCoordinatorSigningKeyRequest struct {
	Reason                      string
	ExpectedCurrentSigningKeyID string
	NewKeyExpiresAtUnixS        float64
	RequestedGracePeriodSeconds float64
	IdempotencyKey              string
	RequestID                   string
	TraceID                     string
}

// RotateCoordinatorSigningKeyResult mirrors fl.coordinator.v1.RotateCoordinatorSigningKeyResponse.
type RotateCoordinatorSigningKeyResult struct {
	Accepted         bool                         `json:"accepted"`
	Reason           string                       `json:"reason"`
	RejectionCode    string                       `json:"rejection_code"`
	NewKey           CoordinatorSigningKeySummary `json:"new_key"`
	PreviousKey      CoordinatorSigningKeySummary `json:"previous_key"`
	IdempotentReplay bool                         `json:"idempotent_replay"`
}

// RevokeCoordinatorSigningKeyRequest mirrors fl.coordinator.v1.RevokeCoordinatorSigningKeyRequest.
type RevokeCoordinatorSigningKeyRequest struct {
	SigningKeyID   string
	Reason         string
	RequestID      string
	TraceID        string
	IdempotencyKey string
	ExpectedStatus string
}

// RevokeCoordinatorSigningKeyResult mirrors fl.coordinator.v1.RevokeCoordinatorSigningKeyResponse.
type RevokeCoordinatorSigningKeyResult struct {
	Key                           CoordinatorSigningKeySummary `json:"key"`
	Changed                       bool                         `json:"changed"`
	ProductionTaskIssuanceStopped bool                         `json:"production_task_issuance_stopped"`
	IdempotentReplay              bool                         `json:"idempotent_replay"`
}

// ListSecurityEventsRequest mirrors fl.coordinator.v1.ListSecurityEventsRequest
// -- Security Events, Metrics, and Durable Audit Journal slice.
type ListSecurityEventsRequest struct {
	TraceID      string
	AfterEventID string
	Limit        uint32
	MinSeverity  string
	SubjectType  string
	EventType    string
}

// ListSecurityEventsResult mirrors fl.coordinator.v1.ListSecurityEventsResponse.
type ListSecurityEventsResult struct {
	Events     []observability.SecurityEvent `json:"events"`
	NextCursor string                        `json:"next_cursor"`
}

func wireSecurityEventToRecord(wire *coordinatorv1.SecurityEventRecord) observability.SecurityEvent {
	return observability.SecurityEvent{
		SchemaVersion:    int(wire.GetSchemaVersion()),
		EventID:          wire.GetEventId(),
		EventType:        wire.GetEventType(),
		Severity:         wire.GetSeverity(),
		Timestamp:        wire.GetTimestamp(),
		SourceService:    wire.GetSourceService(),
		SourceComponent:  wire.GetSourceComponent(),
		ActorType:        wire.GetActorType(),
		SafeActorID:      wire.GetSafeActorId(),
		SubjectType:      wire.GetSubjectType(),
		SafeSubjectID:    wire.GetSafeSubjectId(),
		WorkerID:         wire.GetWorkerId(),
		RunID:            wire.GetRunId(),
		RoundID:          wire.GetRoundId(),
		TaskID:           wire.GetTaskId(),
		SafeSigningKeyID: wire.GetSafeSigningKeyId(),
		RequestID:        wire.GetRequestId(),
		TraceID:          wire.GetTraceId(),
		Outcome:          wire.GetOutcome(),
		ReasonCode:       wire.GetReasonCode(),
		SafeDetails:      wire.GetSafeDetails(),
		PayloadChecksum:  wire.GetPayloadChecksum(),
	}
}

// mapSecurityGrpcError is mapGrpcError's counterpart for the
// ADMIN_CONTROL security surface. It is deliberately a separate
// function rather than an edit to mapGrpcError: that function's
// existing codes.NotFound -> ErrRunNotFound mapping is run-specific and
// still correct for every pre-existing (non-security) caller, and
// widening its default case to add PermissionDenied/FailedPrecondition
// handling would be an unrelated behavior change to code this slice
// has no reason to touch (see docs/rpc-security-policy.md's
// "structured error codes" requirement — this surface needs its own
// distinct vocabulary, not reused semantics from the run-lifecycle one).
func mapSecurityGrpcError(err error) error {
	if err == nil {
		return nil
	}
	st, ok := status.FromError(err)
	if !ok {
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	switch st.Code() {
	case codes.Unavailable, codes.DeadlineExceeded, codes.Canceled:
		return fmt.Errorf("%w: %s", ErrUnavailable, st.Message())
	case codes.PermissionDenied, codes.Unauthenticated:
		return fmt.Errorf("%w: %s", ErrPermissionDenied, st.Message())
	case codes.NotFound:
		return fmt.Errorf("%w: %s", ErrNotFound, st.Message())
	case codes.FailedPrecondition:
		return fmt.Errorf("%w: %s", ErrFailedPrecondition, st.Message())
	default:
		return &RejectedError{Reason: st.Message()}
	}
}

func wireWorkerIdentityToSummary(wire *coordinatorv1.WorkerIdentitySummary) WorkerIdentitySummary {
	return WorkerIdentitySummary{
		WorkerID:               wire.GetWorkerId(),
		CertificateIdentity:    wire.GetCertificateIdentity(),
		CertificateFingerprint: wire.GetCertificateFingerprint(),
		SigningKeyID:           wire.GetSigningKeyId(),
		RegistrationStatus:     wire.GetRegistrationStatus(),
		SoftwareVersion:        wire.GetSoftwareVersion(),
		BuildID:                wire.GetBuildId(),
		CreatedAtUnixS:         wire.GetCreatedAtUnixS(),
		UpdatedAtUnixS:         wire.GetUpdatedAtUnixS(),
		ExpiresAtUnixS:         wire.GetExpiresAtUnixS(),
		SuspendedAtUnixS:       wire.GetSuspendedAtUnixS(),
		RevokedAtUnixS:         wire.GetRevokedAtUnixS(),
		RevocationReason:       wire.GetRevocationReason(),
	}
}

func wireWorkerLifecycleToResult(wire *coordinatorv1.WorkerLifecycleResponse) WorkerLifecycleResult {
	return WorkerLifecycleResult{
		Identity:       wireWorkerIdentityToSummary(wire.GetIdentity()),
		Changed:        wire.GetChanged(),
		LeasesCanceled: wire.GetLeasesCanceled(),
	}
}

func wireWorkerSigningKeyToSummary(wire *coordinatorv1.SigningKeyRecordSummary) WorkerSigningKeySummary {
	return WorkerSigningKeySummary{
		WorkerID:              wire.GetWorkerId(),
		SigningKeyID:          wire.GetSigningKeyId(),
		PublicKeyFingerprint:  wire.GetPublicKeyFingerprint(),
		Status:                wire.GetStatus(),
		CreatedAtUnixS:        wire.GetCreatedAtUnixS(),
		ActivatedAtUnixS:      wire.GetActivatedAtUnixS(),
		ExpiresAtUnixS:        wire.GetExpiresAtUnixS(),
		GracePeriodStartUnixS: wire.GetGracePeriodStartUnixS(),
		GracePeriodEndUnixS:   wire.GetGracePeriodEndUnixS(),
		RotatedFromKeyID:      wire.GetRotatedFromKeyId(),
		RotatedToKeyID:        wire.GetRotatedToKeyId(),
		RevokedAtUnixS:        wire.GetRevokedAtUnixS(),
		RevocationReason:      wire.GetRevocationReason(),
		RegistrationSource:    wire.GetRegistrationSource(),
	}
}

func wireCoordinatorSigningKeyToSummary(wire *coordinatorv1.CoordinatorSigningKeyRecordSummary) CoordinatorSigningKeySummary {
	return CoordinatorSigningKeySummary{
		SigningKeyID:         wire.GetSigningKeyId(),
		PublicKeyFingerprint: wire.GetPublicKeyFingerprint(),
		Status:               wire.GetStatus(),
		CreatedAtUnixS:       wire.GetCreatedAtUnixS(),
		ExpiresAtUnixS:       wire.GetExpiresAtUnixS(),
		GracePeriodEndUnixS:  wire.GetGracePeriodEndUnixS(),
		RotatedFromKeyID:     wire.GetRotatedFromKeyId(),
		RotatedToKeyID:       wire.GetRotatedToKeyId(),
		RevokedAtUnixS:       wire.GetRevokedAtUnixS(),
		RevocationReason:     wire.GetRevocationReason(),
	}
}

// --- GrpcClient ------------------------------------------------------

func (c *GrpcClient) GetTransportSecurityStatus(ctx context.Context, traceID string) (TransportSecurityStatus, error) {
	response, err := c.stub.GetTransportSecurityStatus(ctx, &coordinatorv1.GetTransportSecurityStatusRequest{TraceId: traceID})
	if err != nil {
		return TransportSecurityStatus{}, mapSecurityGrpcError(err)
	}
	return TransportSecurityStatus{
		TransportMode:     response.GetTransportMode(),
		MutualTLSEnforced: response.GetMutualTlsEnforced(),
		CheckedAtUnixS:    response.GetCheckedAtUnixS(),
	}, nil
}

func (c *GrpcClient) GetSecurityTrustModel(ctx context.Context, traceID string) (SecurityTrustModel, error) {
	response, err := c.stub.GetSecurityTrustModel(ctx, &coordinatorv1.GetSecurityTrustModelRequest{TraceId: traceID})
	if err != nil {
		return SecurityTrustModel{}, mapSecurityGrpcError(err)
	}
	return SecurityTrustModel{
		ActiveCoordinatorSigningKeyID: response.GetActiveCoordinatorSigningKeyId(),
		TrustedCoordinatorKeyCount:    response.GetTrustedCoordinatorKeyCount(),
		TrustedKeyBundleVersion:       response.GetTrustedKeyBundleVersion(),
		RegisteredWorkerCount:         response.GetRegisteredWorkerCount(),
		WorkerSigningKeyTotalCount:    response.GetWorkerSigningKeyTotalCount(),
		CheckedAtUnixS:                response.GetCheckedAtUnixS(),
	}, nil
}

func (c *GrpcClient) ListWorkerIdentities(ctx context.Context, traceID string) ([]WorkerIdentitySummary, error) {
	response, err := c.stub.ListWorkerIdentities(ctx, &coordinatorv1.ListWorkerIdentitiesRequest{TraceId: traceID})
	if err != nil {
		return nil, mapSecurityGrpcError(err)
	}
	summaries := make([]WorkerIdentitySummary, 0, len(response.GetIdentities()))
	for _, wire := range response.GetIdentities() {
		summaries = append(summaries, wireWorkerIdentityToSummary(wire))
	}
	return summaries, nil
}

func (c *GrpcClient) GetWorkerIdentity(ctx context.Context, workerID, traceID string) (WorkerIdentitySummary, error) {
	response, err := c.stub.GetWorkerIdentity(ctx, &coordinatorv1.GetWorkerIdentityRequest{WorkerId: workerID, TraceId: traceID})
	if err != nil {
		return WorkerIdentitySummary{}, mapSecurityGrpcError(err)
	}
	return wireWorkerIdentityToSummary(response), nil
}

func (c *GrpcClient) SuspendWorker(ctx context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error) {
	response, err := c.stub.SuspendWorker(ctx, &coordinatorv1.SuspendWorkerRequest{
		WorkerId: request.WorkerID, Reason: request.Reason, RequestId: request.RequestID, TraceId: request.TraceID,
	})
	if err != nil {
		return WorkerLifecycleResult{}, mapSecurityGrpcError(err)
	}
	return wireWorkerLifecycleToResult(response), nil
}

func (c *GrpcClient) ActivateWorker(ctx context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error) {
	response, err := c.stub.ActivateWorker(ctx, &coordinatorv1.ActivateWorkerRequest{
		WorkerId: request.WorkerID, Reason: request.Reason, RequestId: request.RequestID, TraceId: request.TraceID,
	})
	if err != nil {
		return WorkerLifecycleResult{}, mapSecurityGrpcError(err)
	}
	return wireWorkerLifecycleToResult(response), nil
}

func (c *GrpcClient) RevokeWorker(ctx context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error) {
	response, err := c.stub.RevokeWorker(ctx, &coordinatorv1.RevokeWorkerRequest{
		WorkerId: request.WorkerID, Reason: request.Reason, RequestId: request.RequestID, TraceId: request.TraceID,
	})
	if err != nil {
		return WorkerLifecycleResult{}, mapSecurityGrpcError(err)
	}
	return wireWorkerLifecycleToResult(response), nil
}

func (c *GrpcClient) ListWorkerSigningKeys(ctx context.Context, workerID, traceID string) ([]WorkerSigningKeySummary, error) {
	response, err := c.stub.GetWorkerSigningKeys(ctx, &coordinatorv1.GetWorkerSigningKeysRequest{WorkerId: workerID, TraceId: traceID})
	if err != nil {
		return nil, mapSecurityGrpcError(err)
	}
	summaries := make([]WorkerSigningKeySummary, 0, len(response.GetKeys()))
	for _, wire := range response.GetKeys() {
		summaries = append(summaries, wireWorkerSigningKeyToSummary(wire))
	}
	return summaries, nil
}

func (c *GrpcClient) RevokeWorkerSigningKey(ctx context.Context, request RevokeWorkerSigningKeyRequest) (WorkerSigningKeyRevocationResult, error) {
	response, err := c.stub.RevokeWorkerSigningKey(ctx, &coordinatorv1.RevokeWorkerSigningKeyRequest{
		WorkerId: request.WorkerID, SigningKeyId: request.SigningKeyID, Reason: request.Reason,
		RequestId: request.RequestID, TraceId: request.TraceID,
	})
	if err != nil {
		return WorkerSigningKeyRevocationResult{}, mapSecurityGrpcError(err)
	}
	return WorkerSigningKeyRevocationResult{
		Key:             wireWorkerSigningKeyToSummary(response.GetKey()),
		Changed:         response.GetChanged(),
		WorkerSuspended: response.GetWorkerSuspended(),
	}, nil
}

func (c *GrpcClient) ListCoordinatorSigningKeys(ctx context.Context, traceID string) ([]CoordinatorSigningKeySummary, error) {
	response, err := c.stub.GetCoordinatorSigningKeys(ctx, &coordinatorv1.GetCoordinatorSigningKeysRequest{TraceId: traceID})
	if err != nil {
		return nil, mapSecurityGrpcError(err)
	}
	summaries := make([]CoordinatorSigningKeySummary, 0, len(response.GetKeys()))
	for _, wire := range response.GetKeys() {
		summaries = append(summaries, wireCoordinatorSigningKeyToSummary(wire))
	}
	return summaries, nil
}

func (c *GrpcClient) RotateCoordinatorSigningKey(ctx context.Context, request RotateCoordinatorSigningKeyRequest) (RotateCoordinatorSigningKeyResult, error) {
	response, err := c.stub.RotateCoordinatorSigningKey(ctx, &coordinatorv1.RotateCoordinatorSigningKeyRequest{
		RequestId:                   request.RequestID,
		TraceId:                     request.TraceID,
		Reason:                      request.Reason,
		ExpectedCurrentSigningKeyId: request.ExpectedCurrentSigningKeyID,
		NewKeyExpiresAtUnixS:        request.NewKeyExpiresAtUnixS,
		RequestedGracePeriodSeconds: request.RequestedGracePeriodSeconds,
		IdempotencyKey:              request.IdempotencyKey,
	})
	if err != nil {
		return RotateCoordinatorSigningKeyResult{}, mapSecurityGrpcError(err)
	}
	return RotateCoordinatorSigningKeyResult{
		Accepted:         response.GetAccepted(),
		Reason:           response.GetReason(),
		RejectionCode:    response.GetRejectionCode(),
		NewKey:           wireCoordinatorSigningKeyToSummary(response.GetNewKey()),
		PreviousKey:      wireCoordinatorSigningKeyToSummary(response.GetPreviousKey()),
		IdempotentReplay: response.GetIdempotentReplay(),
	}, nil
}

func (c *GrpcClient) ListSecurityEvents(ctx context.Context, request ListSecurityEventsRequest) (ListSecurityEventsResult, error) {
	response, err := c.stub.ListSecurityEvents(ctx, &coordinatorv1.ListSecurityEventsRequest{
		TraceId:      request.TraceID,
		AfterEventId: request.AfterEventID,
		Limit:        request.Limit,
		MinSeverity:  request.MinSeverity,
		SubjectType:  request.SubjectType,
		EventType:    request.EventType,
	})
	if err != nil {
		return ListSecurityEventsResult{}, mapSecurityGrpcError(err)
	}
	events := make([]observability.SecurityEvent, 0, len(response.GetEvents()))
	for _, wire := range response.GetEvents() {
		events = append(events, wireSecurityEventToRecord(wire))
	}
	return ListSecurityEventsResult{Events: events, NextCursor: response.GetNextCursor()}, nil
}

// SecurityEventSourceHealthEntry mirrors fl.coordinator.v1.SecurityEventSourceHealthEntry
// -- Web Security Center, Event Centralization, and Security CI slice,
// Work Package N. BatchesAccepted/BatchesRejected/DistinctWorkersSeen
// are populated only for SourceService == "python-worker".
type SecurityEventSourceHealthEntry struct {
	SourceService       string `json:"source_service"`
	LastEventAt         string `json:"last_event_at,omitempty"`
	RecordCount         uint64 `json:"record_count"`
	RecoveredLineCount  uint64 `json:"recovered_line_count"`
	Corrupted           bool   `json:"corrupted"`
	RetentionActive     bool   `json:"retention_active"`
	BatchesAccepted     uint64 `json:"batches_accepted"`
	BatchesRejected     uint64 `json:"batches_rejected"`
	DistinctWorkersSeen uint64 `json:"distinct_workers_seen"`
}

// SecurityEventSourceHealthResult mirrors fl.coordinator.v1.GetSecurityEventSourceHealthResponse.
type SecurityEventSourceHealthResult struct {
	Sources        []SecurityEventSourceHealthEntry `json:"sources"`
	CheckedAtUnixS float64                          `json:"checked_at_unix_s"`
}

func (c *GrpcClient) GetSecurityEventSourceHealth(ctx context.Context, traceID string) (SecurityEventSourceHealthResult, error) {
	response, err := c.stub.GetSecurityEventSourceHealth(ctx, &coordinatorv1.GetSecurityEventSourceHealthRequest{TraceId: traceID})
	if err != nil {
		return SecurityEventSourceHealthResult{}, mapSecurityGrpcError(err)
	}
	sources := make([]SecurityEventSourceHealthEntry, 0, len(response.GetSources()))
	for _, wire := range response.GetSources() {
		sources = append(sources, SecurityEventSourceHealthEntry{
			SourceService:       wire.GetSourceService(),
			LastEventAt:         wire.GetLastEventAt(),
			RecordCount:         wire.GetRecordCount(),
			RecoveredLineCount:  wire.GetRecoveredLineCount(),
			Corrupted:           wire.GetCorrupted(),
			RetentionActive:     wire.GetRetentionActive(),
			BatchesAccepted:     wire.GetBatchesAccepted(),
			BatchesRejected:     wire.GetBatchesRejected(),
			DistinctWorkersSeen: wire.GetDistinctWorkersSeen(),
		})
	}
	return SecurityEventSourceHealthResult{Sources: sources, CheckedAtUnixS: response.GetCheckedAtUnixS()}, nil
}

func (c *GrpcClient) RevokeCoordinatorSigningKey(ctx context.Context, request RevokeCoordinatorSigningKeyRequest) (RevokeCoordinatorSigningKeyResult, error) {
	response, err := c.stub.RevokeCoordinatorSigningKey(ctx, &coordinatorv1.RevokeCoordinatorSigningKeyRequest{
		SigningKeyId:   request.SigningKeyID,
		Reason:         request.Reason,
		RequestId:      request.RequestID,
		TraceId:        request.TraceID,
		IdempotencyKey: request.IdempotencyKey,
		ExpectedStatus: request.ExpectedStatus,
	})
	if err != nil {
		return RevokeCoordinatorSigningKeyResult{}, mapSecurityGrpcError(err)
	}
	return RevokeCoordinatorSigningKeyResult{
		Key:                           wireCoordinatorSigningKeyToSummary(response.GetKey()),
		Changed:                       response.GetChanged(),
		ProductionTaskIssuanceStopped: response.GetProductionTaskIssuanceStopped(),
		IdempotentReplay:              response.GetIdempotentReplay(),
	}, nil
}
