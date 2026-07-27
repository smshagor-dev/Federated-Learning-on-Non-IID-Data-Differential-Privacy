package application

// Security Operations and Administration slice (docs/security-api.md):
// these methods extend the existing CoordinatorService (not a new
// service type) — every one of them is just another
// coordinator.Client operation, so it belongs alongside Health/CreateRun/
// etc. rather than duplicating the Configured()/recordRPC() plumbing in
// a parallel type. Every mutation additionally writes a real
// AuditService.Record entry (ResourceType "security") — the existing,
// general-purpose Go audit repository (go/internal/observability), not
// a new security-specific store; see docs/known-limitations.md for why
// a durable, security-specific audit journal is a separate, deferred
// work package.

import (
	"context"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
)

func (s *CoordinatorService) GetTransportSecurityStatus(ctx context.Context, traceID string) (coordinator.TransportSecurityStatus, error) {
	if !s.Configured() {
		return coordinator.TransportSecurityStatus{}, ErrCoordinatorNotConfigured
	}
	status, err := s.client.GetTransportSecurityStatus(ctx, traceID)
	s.recordRPC("GetTransportSecurityStatus", err)
	return status, err
}

func (s *CoordinatorService) GetSecurityTrustModel(ctx context.Context, traceID string) (coordinator.SecurityTrustModel, error) {
	if !s.Configured() {
		return coordinator.SecurityTrustModel{}, ErrCoordinatorNotConfigured
	}
	model, err := s.client.GetSecurityTrustModel(ctx, traceID)
	s.recordRPC("GetSecurityTrustModel", err)
	return model, err
}

// ListSecurityEvents mirrors coordinator.SecurityClient.ListSecurityEvents
// -- read-only, no audit/event emission of its own (the HTTP layer emits
// SECURITY_AUDIT_ACCESSED for the audit endpoint, not this one).
func (s *CoordinatorService) ListSecurityEvents(ctx context.Context, request coordinator.ListSecurityEventsRequest) (coordinator.ListSecurityEventsResult, error) {
	if !s.Configured() {
		return coordinator.ListSecurityEventsResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.ListSecurityEvents(ctx, request)
	s.recordRPC("ListSecurityEvents", err)
	return result, err
}

// GetSecurityEventSourceHealth mirrors coordinator.SecurityClient.GetSecurityEventSourceHealth.
func (s *CoordinatorService) GetSecurityEventSourceHealth(ctx context.Context, traceID string) (coordinator.SecurityEventSourceHealthResult, error) {
	if !s.Configured() {
		return coordinator.SecurityEventSourceHealthResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.GetSecurityEventSourceHealth(ctx, traceID)
	s.recordRPC("GetSecurityEventSourceHealth", err)
	return result, err
}

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice (docs/secure-user-level-operations-audit.md): these five mirror
// coordinator.SecurityClient's new secure-user-level-DP methods exactly,
// same Configured()/recordRPC() plumbing as every method above.

func (s *CoordinatorService) GetSecureUserLevelPrivacyStatus(ctx context.Context, traceID string) (coordinator.SecureUserLevelPrivacyCapability, error) {
	if !s.Configured() {
		return coordinator.SecureUserLevelPrivacyCapability{}, ErrCoordinatorNotConfigured
	}
	status, err := s.client.GetSecureUserLevelPrivacyStatus(ctx, traceID)
	s.recordRPC("GetSecureUserLevelPrivacyStatus", err)
	return status, err
}

func (s *CoordinatorService) GetSecureUserLevelPrivacyHealth(ctx context.Context, traceID string) (coordinator.SecureUserLevelPrivacyHealth, error) {
	if !s.Configured() {
		return coordinator.SecureUserLevelPrivacyHealth{}, ErrCoordinatorNotConfigured
	}
	health, err := s.client.GetSecureUserLevelPrivacyHealth(ctx, traceID)
	s.recordRPC("GetSecureUserLevelPrivacyHealth", err)
	return health, err
}

func (s *CoordinatorService) GetSecureUserLevelPrivacyBudget(ctx context.Context, runID, traceID string) (coordinator.SecureUserLevelPrivacyBudget, error) {
	if !s.Configured() {
		return coordinator.SecureUserLevelPrivacyBudget{}, ErrCoordinatorNotConfigured
	}
	budget, err := s.client.GetSecureUserLevelPrivacyBudget(ctx, runID, traceID)
	s.recordRPC("GetSecureUserLevelPrivacyBudget", err)
	return budget, err
}

func (s *CoordinatorService) ListSecureUserLevelPrivacyRounds(ctx context.Context, request coordinator.ListSecureUserLevelPrivacyRoundsRequest) (coordinator.ListSecureUserLevelPrivacyRoundsResult, error) {
	if !s.Configured() {
		return coordinator.ListSecureUserLevelPrivacyRoundsResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.ListSecureUserLevelPrivacyRounds(ctx, request)
	s.recordRPC("ListSecureUserLevelPrivacyRounds", err)
	return result, err
}

func (s *CoordinatorService) GetSecureUserLevelPrivacyRound(ctx context.Context, runID string, roundID uint64, traceID string) (coordinator.SecureUserLevelPrivacyRound, bool, error) {
	if !s.Configured() {
		return coordinator.SecureUserLevelPrivacyRound{}, false, ErrCoordinatorNotConfigured
	}
	round, found, err := s.client.GetSecureUserLevelPrivacyRound(ctx, runID, roundID, traceID)
	s.recordRPC("GetSecureUserLevelPrivacyRound", err)
	return round, found, err
}

func (s *CoordinatorService) ListWorkerIdentities(ctx context.Context, traceID string) ([]coordinator.WorkerIdentitySummary, error) {
	if !s.Configured() {
		return nil, ErrCoordinatorNotConfigured
	}
	identities, err := s.client.ListWorkerIdentities(ctx, traceID)
	s.recordRPC("ListWorkerIdentities", err)
	return identities, err
}

func (s *CoordinatorService) GetWorkerIdentity(ctx context.Context, workerID, traceID string) (coordinator.WorkerIdentitySummary, error) {
	if !s.Configured() {
		return coordinator.WorkerIdentitySummary{}, ErrCoordinatorNotConfigured
	}
	identity, err := s.client.GetWorkerIdentity(ctx, workerID, traceID)
	s.recordRPC("GetWorkerIdentity", err)
	return identity, err
}

// auditWorkerMutation records one Suspend/Activate/RevokeWorker call.
// actor is the authenticated caller (Work Package K: "audit actor,
// timestamp... for all security mutations").
func (s *CoordinatorService) auditWorkerMutation(ctx context.Context, actor Actor, action, workerID string, request coordinator.WorkerLifecycleRequest, result coordinator.WorkerLifecycleResult, err error) {
	outcome := "success"
	if err != nil {
		outcome = "error"
	}
	_ = s.audit.Record(ctx, actor, action, "security.worker", workerID, outcome, map[string]any{
		"reason":     request.Reason,
		"request_id": request.RequestID,
		"trace_id":   request.TraceID,
		"changed":    result.Changed,
	})

	auditOutcome := observability.OutcomeAccepted
	eventType := workerLifecycleEventType(action)
	if err != nil {
		auditOutcome = observability.OutcomeRejected
		eventType = observability.EventWorkerStatusRPCRejected
	}
	s.appendSecurityAudit(observability.SecurityAuditRecord{
		SafeActorID:  actor.ID,
		ActorRole:    actor.Role,
		Action:       action,
		ResourceType: "worker_identity",
		ResourceID:   workerID,
		Outcome:      auditOutcome,
		Reason:       request.Reason,
		RequestID:    request.RequestID,
		TraceID:      request.TraceID,
	})
	s.emitSecurityEvent(observability.SecurityEvent{
		EventType:     eventType,
		ActorType:     observability.ActorTypeUser,
		SafeActorID:   actor.ID,
		SubjectType:   observability.SubjectTypeWorkerIdentity,
		SafeSubjectID: workerID,
		WorkerID:      workerID,
		Outcome:       auditOutcome,
		ReasonCode:    request.Reason,
		RequestID:     request.RequestID,
		TraceID:       request.TraceID,
	})
	if result.LeasesCanceled > 0 {
		s.emitSecurityEvent(observability.SecurityEvent{
			EventType:     observability.EventActiveLeaseCanceled,
			ActorType:     observability.ActorTypeUser,
			SafeActorID:   actor.ID,
			SubjectType:   observability.SubjectTypeTaskLease,
			SafeSubjectID: workerID,
			WorkerID:      workerID,
			Outcome:       observability.OutcomeCanceled,
			ReasonCode:    "worker_revoked",
			RequestID:     request.RequestID,
			TraceID:       request.TraceID,
		})
	}
}

func workerLifecycleEventType(action string) string {
	switch action {
	case "security.workers.suspend":
		return observability.EventWorkerSuspended
	case "security.workers.activate":
		return observability.EventWorkerActivated
	case "security.workers.revoke":
		return observability.EventWorkerRevoked
	default:
		return observability.EventSecurityMutationAccepted
	}
}

func (s *CoordinatorService) SuspendWorker(ctx context.Context, actor Actor, request coordinator.WorkerLifecycleRequest) (coordinator.WorkerLifecycleResult, error) {
	if !s.Configured() {
		return coordinator.WorkerLifecycleResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.SuspendWorker(ctx, request)
	s.recordRPC("SuspendWorker", err)
	s.auditWorkerMutation(ctx, actor, "security.workers.suspend", request.WorkerID, request, result, err)
	return result, err
}

func (s *CoordinatorService) ActivateWorker(ctx context.Context, actor Actor, request coordinator.WorkerLifecycleRequest) (coordinator.WorkerLifecycleResult, error) {
	if !s.Configured() {
		return coordinator.WorkerLifecycleResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.ActivateWorker(ctx, request)
	s.recordRPC("ActivateWorker", err)
	s.auditWorkerMutation(ctx, actor, "security.workers.activate", request.WorkerID, request, result, err)
	return result, err
}

func (s *CoordinatorService) RevokeWorker(ctx context.Context, actor Actor, request coordinator.WorkerLifecycleRequest) (coordinator.WorkerLifecycleResult, error) {
	if !s.Configured() {
		return coordinator.WorkerLifecycleResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.RevokeWorker(ctx, request)
	s.recordRPC("RevokeWorker", err)
	s.auditWorkerMutation(ctx, actor, "security.workers.revoke", request.WorkerID, request, result, err)
	return result, err
}

func (s *CoordinatorService) ListWorkerSigningKeys(ctx context.Context, workerID, traceID string) ([]coordinator.WorkerSigningKeySummary, error) {
	if !s.Configured() {
		return nil, ErrCoordinatorNotConfigured
	}
	keys, err := s.client.ListWorkerSigningKeys(ctx, workerID, traceID)
	s.recordRPC("ListWorkerSigningKeys", err)
	return keys, err
}

func (s *CoordinatorService) RevokeWorkerSigningKey(ctx context.Context, actor Actor, request coordinator.RevokeWorkerSigningKeyRequest) (coordinator.WorkerSigningKeyRevocationResult, error) {
	if !s.Configured() {
		return coordinator.WorkerSigningKeyRevocationResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.RevokeWorkerSigningKey(ctx, request)
	s.recordRPC("RevokeWorkerSigningKey", err)
	outcome := "success"
	if err != nil {
		outcome = "error"
	}
	_ = s.audit.Record(ctx, actor, "security.worker_keys.revoke", "security.worker_signing_key", request.WorkerID+"/"+request.SigningKeyID, outcome, map[string]any{
		"reason": request.Reason, "request_id": request.RequestID, "trace_id": request.TraceID,
		"changed": result.Changed, "worker_suspended": result.WorkerSuspended,
	})
	auditOutcome := observability.OutcomeAccepted
	if err != nil {
		auditOutcome = observability.OutcomeRejected
	}
	s.appendSecurityAudit(observability.SecurityAuditRecord{
		SafeActorID: actor.ID, ActorRole: actor.Role, Action: "RevokeWorkerSigningKey",
		ResourceType: "worker_signing_key", ResourceID: request.SigningKeyID, Outcome: auditOutcome,
		Reason: request.Reason, RequestID: request.RequestID, TraceID: request.TraceID,
		SafeDetails: map[string]string{"worker_id": request.WorkerID},
	})
	s.emitSecurityEvent(observability.SecurityEvent{
		EventType: observability.EventWorkerKeyRevoked, ActorType: observability.ActorTypeUser,
		SafeActorID: actor.ID, SubjectType: observability.SubjectTypeWorkerSigningKey,
		SafeSubjectID: request.SigningKeyID, WorkerID: request.WorkerID,
		SafeSigningKeyID: request.SigningKeyID, Outcome: auditOutcome, ReasonCode: request.Reason,
		RequestID: request.RequestID, TraceID: request.TraceID,
	})
	return result, err
}

func (s *CoordinatorService) ListCoordinatorSigningKeys(ctx context.Context, traceID string) ([]coordinator.CoordinatorSigningKeySummary, error) {
	if !s.Configured() {
		return nil, ErrCoordinatorNotConfigured
	}
	keys, err := s.client.ListCoordinatorSigningKeys(ctx, traceID)
	s.recordRPC("ListCoordinatorSigningKeys", err)
	return keys, err
}

func (s *CoordinatorService) RotateCoordinatorSigningKey(ctx context.Context, actor Actor, request coordinator.RotateCoordinatorSigningKeyRequest) (coordinator.RotateCoordinatorSigningKeyResult, error) {
	if !s.Configured() {
		return coordinator.RotateCoordinatorSigningKeyResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.RotateCoordinatorSigningKey(ctx, request)
	s.recordRPC("RotateCoordinatorSigningKey", err)
	outcome := "success"
	if err != nil || !result.Accepted {
		outcome = "error"
	}
	_ = s.audit.Record(ctx, actor, "security.coordinator_keys.rotate", "security.coordinator_signing_key", result.NewKey.SigningKeyID, outcome, map[string]any{
		"reason": request.Reason, "request_id": request.RequestID, "trace_id": request.TraceID,
		"idempotency_key": request.IdempotencyKey, "accepted": result.Accepted,
		"rejection_code": result.RejectionCode, "idempotent_replay": result.IdempotentReplay,
	})
	eventType := observability.EventSecurityMutationAccepted
	auditOutcome := observability.OutcomeAccepted
	if result.IdempotentReplay {
		eventType = observability.EventIdempotencyReplayAccepted
	} else if err != nil || !result.Accepted {
		eventType = observability.EventSecurityMutationRejected
		auditOutcome = observability.OutcomeRejected
	}
	s.appendSecurityAudit(observability.SecurityAuditRecord{
		SafeActorID: actor.ID, ActorRole: actor.Role, Action: "RotateCoordinatorSigningKey",
		ResourceType: "coordinator_signing_key", ResourceID: result.NewKey.SigningKeyID, Outcome: auditOutcome,
		Reason: request.Reason, RequestID: request.RequestID, TraceID: request.TraceID,
		SafeDetails: map[string]string{"previous_key_id": result.PreviousKey.SigningKeyID},
	})
	s.emitSecurityEvent(observability.SecurityEvent{
		EventType: eventType, ActorType: observability.ActorTypeUser, SafeActorID: actor.ID,
		SubjectType: observability.SubjectTypeCoordinatorSigningKey, SafeSubjectID: result.NewKey.SigningKeyID,
		SafeSigningKeyID: result.NewKey.SigningKeyID, Outcome: auditOutcome, ReasonCode: result.RejectionCode,
		RequestID: request.RequestID, TraceID: request.TraceID,
	})
	return result, err
}

func (s *CoordinatorService) RevokeCoordinatorSigningKey(ctx context.Context, actor Actor, request coordinator.RevokeCoordinatorSigningKeyRequest) (coordinator.RevokeCoordinatorSigningKeyResult, error) {
	if !s.Configured() {
		return coordinator.RevokeCoordinatorSigningKeyResult{}, ErrCoordinatorNotConfigured
	}
	result, err := s.client.RevokeCoordinatorSigningKey(ctx, request)
	s.recordRPC("RevokeCoordinatorSigningKey", err)
	outcome := "success"
	if err != nil {
		outcome = "error"
	}
	_ = s.audit.Record(ctx, actor, "security.coordinator_keys.revoke", "security.coordinator_signing_key", request.SigningKeyID, outcome, map[string]any{
		"reason": request.Reason, "request_id": request.RequestID, "trace_id": request.TraceID,
		"idempotency_key": request.IdempotencyKey, "changed": result.Changed,
		"production_task_issuance_stopped": result.ProductionTaskIssuanceStopped,
		"idempotent_replay":                result.IdempotentReplay,
	})
	eventType := observability.EventSecurityMutationAccepted
	auditOutcome := observability.OutcomeAccepted
	severity := observability.SeverityHigh // a trust-root revocation, not a routine mutation
	if result.IdempotentReplay {
		eventType = observability.EventIdempotencyReplayAccepted
	} else if err != nil {
		eventType = observability.EventSecurityMutationRejected
		auditOutcome = observability.OutcomeRejected
	}
	s.appendSecurityAudit(observability.SecurityAuditRecord{
		SafeActorID: actor.ID, ActorRole: actor.Role, Action: "RevokeCoordinatorSigningKey",
		ResourceType: "coordinator_signing_key", ResourceID: request.SigningKeyID, Outcome: auditOutcome,
		Reason: request.Reason, RequestID: request.RequestID, TraceID: request.TraceID,
		SafeDetails: map[string]string{
			"production_task_issuance_stopped": formatBool(result.ProductionTaskIssuanceStopped),
		},
	})
	s.emitSecurityEvent(observability.SecurityEvent{
		EventType: eventType, Severity: severity, ActorType: observability.ActorTypeUser, SafeActorID: actor.ID,
		SubjectType: observability.SubjectTypeCoordinatorSigningKey, SafeSubjectID: request.SigningKeyID,
		SafeSigningKeyID: request.SigningKeyID, Outcome: auditOutcome, RequestID: request.RequestID,
		TraceID: request.TraceID,
	})
	return result, err
}

func formatBool(value bool) string {
	if value {
		return "true"
	}
	return "false"
}
