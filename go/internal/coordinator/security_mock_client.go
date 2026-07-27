package coordinator

// Security Operations and Administration slice (docs/security-api.md):
// MockClient's implementation of SecurityClient. Deterministic and
// in-memory, sufficient for Go HTTP-handler/permission/idempotency
// tests — it deliberately does not re-implement the C++ registries'
// full validation rules (grace-period math, max-lifetime caps,
// current-key compare-and-set nuance beyond a simple string match).
// Those are exercised against the real coordinator in Docker, the same
// division of labor MockClient already uses for run lifecycle.

import (
	"context"
	"fmt"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
)

// SeedTransportSecurityStatus/SeedSecurityTrustModel let a test set
// exactly what GetTransportSecurityStatus/GetSecurityTrustModel return,
// as if a real coordinator had reported them.
func (m *MockClient) SeedTransportSecurityStatus(status TransportSecurityStatus) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.transportStatus = status
}

func (m *MockClient) SeedSecurityTrustModel(model SecurityTrustModel) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.trustModel = model
}

// SeedWorkerIdentity registers a worker identity summary, as if
// RegisterWorker had run through the identity-registry path — for
// ListWorkerIdentities/GetWorkerIdentity/Suspend/Activate/Revoke tests.
func (m *MockClient) SeedWorkerIdentity(identity WorkerIdentitySummary) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.workerIdentities[identity.WorkerID]; !exists {
		m.workerIdentityOrder = append(m.workerIdentityOrder, identity.WorkerID)
	}
	m.workerIdentities[identity.WorkerID] = identity
}

// SeedWorkerSigningKey adds one signing-key record for a worker — for
// ListWorkerSigningKeys/RevokeWorkerSigningKey tests.
func (m *MockClient) SeedWorkerSigningKey(key WorkerSigningKeySummary) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.workerSigningKeys[key.WorkerID] = append(m.workerSigningKeys[key.WorkerID], key)
}

// SeedCoordinatorSigningKey adds one coordinator signing-key record —
// for ListCoordinatorSigningKeys/Rotate/Revoke tests.
func (m *MockClient) SeedCoordinatorSigningKey(key CoordinatorSigningKeySummary) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.coordinatorSigningKeys = append(m.coordinatorSigningKeys, key)
}

// SeedSecurityEvent appends one event, as if the coordinator's own
// ListSecurityEvents RPC had returned it -- for handleSecurityEvents
// tests.
func (m *MockClient) SeedSecurityEvent(event observability.SecurityEvent) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.securityEvents = append(m.securityEvents, event)
}

func (m *MockClient) ListSecurityEvents(_ context.Context, request ListSecurityEventsRequest) (ListSecurityEventsResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return ListSecurityEventsResult{}, err
	}
	limit := int(request.Limit)
	if limit <= 0 {
		limit = 100
	}
	pastCursor := request.AfterEventID == ""
	result := ListSecurityEventsResult{Events: []observability.SecurityEvent{}}
	for _, event := range m.securityEvents {
		if !pastCursor {
			if event.EventID == request.AfterEventID {
				pastCursor = true
			}
			continue
		}
		if request.SubjectType != "" && event.SubjectType != request.SubjectType {
			continue
		}
		if request.EventType != "" && event.EventType != request.EventType {
			continue
		}
		if len(result.Events) >= limit {
			result.NextCursor = result.Events[len(result.Events)-1].EventID
			return result, nil
		}
		result.Events = append(result.Events, event)
	}
	return result, nil
}

// SeedSecurityEventSourceHealth lets a test set exactly what
// GetSecurityEventSourceHealth returns.
func (m *MockClient) SeedSecurityEventSourceHealth(result SecurityEventSourceHealthResult) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.securityEventSourceHealth = result
}

func (m *MockClient) GetSecurityEventSourceHealth(_ context.Context, _ string) (SecurityEventSourceHealthResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return SecurityEventSourceHealthResult{}, err
	}
	return m.securityEventSourceHealth, nil
}

func (m *MockClient) GetTransportSecurityStatus(_ context.Context, _ string) (TransportSecurityStatus, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return TransportSecurityStatus{}, err
	}
	return m.transportStatus, nil
}

func (m *MockClient) GetSecurityTrustModel(_ context.Context, _ string) (SecurityTrustModel, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return SecurityTrustModel{}, err
	}
	return m.trustModel, nil
}

func (m *MockClient) ListWorkerIdentities(_ context.Context, _ string) ([]WorkerIdentitySummary, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	identities := make([]WorkerIdentitySummary, 0, len(m.workerIdentityOrder))
	for _, workerID := range m.workerIdentityOrder {
		identities = append(identities, m.workerIdentities[workerID])
	}
	return identities, nil
}

func (m *MockClient) GetWorkerIdentity(_ context.Context, workerID, _ string) (WorkerIdentitySummary, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return WorkerIdentitySummary{}, err
	}
	identity, ok := m.workerIdentities[workerID]
	if !ok {
		return WorkerIdentitySummary{}, fmt.Errorf("%w: worker %q", ErrNotFound, workerID)
	}
	return identity, nil
}

func (m *MockClient) transitionWorker(workerID, targetStatus string, leasesCanceled uint32) (WorkerLifecycleResult, error) {
	identity, ok := m.workerIdentities[workerID]
	if !ok {
		return WorkerLifecycleResult{}, fmt.Errorf("%w: worker %q", ErrNotFound, workerID)
	}
	changed := identity.RegistrationStatus != targetStatus
	identity.RegistrationStatus = targetStatus
	m.workerIdentities[workerID] = identity
	result := WorkerLifecycleResult{Identity: identity, Changed: changed}
	if changed && targetStatus == "revoked" {
		result.LeasesCanceled = leasesCanceled
	}
	return result, nil
}

func (m *MockClient) SuspendWorker(_ context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return WorkerLifecycleResult{}, err
	}
	return m.transitionWorker(request.WorkerID, "suspended", 0)
}

func (m *MockClient) ActivateWorker(_ context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return WorkerLifecycleResult{}, err
	}
	return m.transitionWorker(request.WorkerID, "active", 0)
}

func (m *MockClient) RevokeWorker(_ context.Context, request WorkerLifecycleRequest) (WorkerLifecycleResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return WorkerLifecycleResult{}, err
	}
	return m.transitionWorker(request.WorkerID, "revoked", 1)
}

func (m *MockClient) ListWorkerSigningKeys(_ context.Context, workerID, _ string) ([]WorkerSigningKeySummary, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	return m.workerSigningKeys[workerID], nil
}

func (m *MockClient) RevokeWorkerSigningKey(_ context.Context, request RevokeWorkerSigningKeyRequest) (WorkerSigningKeyRevocationResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return WorkerSigningKeyRevocationResult{}, err
	}
	keys := m.workerSigningKeys[request.WorkerID]
	for index, key := range keys {
		if key.SigningKeyID != request.SigningKeyID {
			continue
		}
		changed := key.Status != "revoked"
		key.Status = "revoked"
		key.RevocationReason = request.Reason
		keys[index] = key
		m.workerSigningKeys[request.WorkerID] = keys
		anyValid := false
		for _, remaining := range keys {
			if remaining.Status == "active" || remaining.Status == "grace_period" {
				anyValid = true
				break
			}
		}
		result := WorkerSigningKeyRevocationResult{Key: key, Changed: changed, WorkerSuspended: changed && !anyValid}
		if result.WorkerSuspended {
			_, _ = m.transitionWorker(request.WorkerID, "suspended", 0)
		}
		return result, nil
	}
	return WorkerSigningKeyRevocationResult{}, fmt.Errorf("%w: signing key %q for worker %q", ErrNotFound, request.SigningKeyID, request.WorkerID)
}

func (m *MockClient) ListCoordinatorSigningKeys(_ context.Context, _ string) ([]CoordinatorSigningKeySummary, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	return m.coordinatorSigningKeys, nil
}

func (m *MockClient) findActiveCoordinatorKeyIndex() int {
	for index, key := range m.coordinatorSigningKeys {
		if key.Status == "active" {
			return index
		}
	}
	return -1
}

func (m *MockClient) RotateCoordinatorSigningKey(_ context.Context, request RotateCoordinatorSigningKeyRequest) (RotateCoordinatorSigningKeyResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RotateCoordinatorSigningKeyResult{}, err
	}
	if request.IdempotencyKey != "" {
		if cached, ok := m.rotateIdempotency[request.IdempotencyKey]; ok {
			cached.IdempotentReplay = true
			return cached, nil
		}
	}
	activeIndex := m.findActiveCoordinatorKeyIndex()
	if activeIndex == -1 {
		return RotateCoordinatorSigningKeyResult{
			Accepted: false, Reason: "no ACTIVE coordinator signing key to rotate from",
			RejectionCode: "unknown_current_key",
		}, nil
	}
	current := m.coordinatorSigningKeys[activeIndex]
	if request.ExpectedCurrentSigningKeyID != "" && request.ExpectedCurrentSigningKeyID != current.SigningKeyID {
		return RotateCoordinatorSigningKeyResult{
			Accepted: false, Reason: "expected_current_signing_key_id does not match the current ACTIVE key",
			RejectionCode: "key_mismatch",
		}, nil
	}
	current.Status = "grace_period"
	current.GracePeriodEndUnixS = request.RequestedGracePeriodSeconds
	m.nextCoordinatorKeyID++
	newKey := CoordinatorSigningKeySummary{
		SigningKeyID:     fmt.Sprintf("mock-coordinator-key-%d", m.nextCoordinatorKeyID),
		Status:           "active",
		ExpiresAtUnixS:   request.NewKeyExpiresAtUnixS,
		RotatedFromKeyID: current.SigningKeyID,
	}
	current.RotatedToKeyID = newKey.SigningKeyID
	m.coordinatorSigningKeys[activeIndex] = current
	m.coordinatorSigningKeys = append(m.coordinatorSigningKeys, newKey)
	result := RotateCoordinatorSigningKeyResult{Accepted: true, NewKey: newKey, PreviousKey: current}
	if request.IdempotencyKey != "" {
		m.rotateIdempotency[request.IdempotencyKey] = result
	}
	return result, nil
}

func (m *MockClient) RevokeCoordinatorSigningKey(_ context.Context, request RevokeCoordinatorSigningKeyRequest) (RevokeCoordinatorSigningKeyResult, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RevokeCoordinatorSigningKeyResult{}, err
	}
	if request.IdempotencyKey != "" {
		if cached, ok := m.revokeIdempotency[request.IdempotencyKey]; ok {
			cached.IdempotentReplay = true
			return cached, nil
		}
	}
	for index, key := range m.coordinatorSigningKeys {
		if key.SigningKeyID != request.SigningKeyID {
			continue
		}
		if request.ExpectedStatus != "" && request.ExpectedStatus != key.Status {
			return RevokeCoordinatorSigningKeyResult{}, fmt.Errorf(
				"%w: key %q has status %q, expected %q", ErrFailedPrecondition, key.SigningKeyID, key.Status, request.ExpectedStatus)
		}
		changed := key.Status != "revoked"
		key.Status = "revoked"
		key.RevocationReason = request.Reason
		m.coordinatorSigningKeys[index] = key
		result := RevokeCoordinatorSigningKeyResult{
			Key: key, Changed: changed, ProductionTaskIssuanceStopped: m.findActiveCoordinatorKeyIndex() == -1,
		}
		if request.IdempotencyKey != "" {
			m.revokeIdempotency[request.IdempotencyKey] = result
		}
		return result, nil
	}
	return RevokeCoordinatorSigningKeyResult{}, fmt.Errorf("%w: coordinator signing key %q", ErrNotFound, request.SigningKeyID)
}
