package coordinator

import (
	"context"
	"fmt"
	"sort"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
)

// MockClient is a real, deterministic in-memory stand-in for a live
// coordinator, used by Go-side tests that need to exercise the
// application service layer's coordinator-calling code paths without a
// running C++ (or CLI-bridge) coordinator process. It implements the
// same idempotency/rejection rules the real coordinator does (see
// cpp/coordinator/src/run_manager.cpp) at a level of detail sufficient
// for Go-side HTTP-to-gRPC mapping and error-mapping tests — it does not
// re-implement aggregation, checkpointing, or SCAFFOLD state.
type MockClient struct {
	mu              sync.Mutex
	healthy         bool
	runs            map[string]*RunSnapshot
	events          map[string][]Event
	personalization map[string]map[string]PersonalizationMetricRecord // runID -> clientID -> latest record
	failNext        error

	// Privacy Engineering phase: what CreateRun was called with, plus
	// seedable read-side state for GetPrivacyMetrics/GetPrivacyLedger/
	// GetPrivacyProjection — see SeedPrivacy*. Zero-value entries (the
	// default for a run nobody seeded) correctly report an all-false/
	// empty response, matching the real coordinator's behavior for a
	// non-private run.
	privacyConfig     map[string]PrivacyConfig
	privacyLedger     map[string]PrivacyLedger
	privacyMetrics    map[string]PrivacyMetricsSnapshot
	privacyProjection map[string]PrivacyProjection

	// workerID -> registered summary, in registration order (see
	// workerOrder) — mirrors WorkerRegistry's role for ListWorkers tests.
	workers     map[string]WorkerSummary
	workerOrder []string

	// Security Operations and Administration slice (docs/security-api.md):
	// deterministic in-memory state sufficient for HTTP-handler/
	// permission tests, not a re-implementation of the C++ registries'
	// full validation rules (grace periods, lifetime caps, etc. — those
	// are exercised against the real coordinator in Docker, same
	// division of labor as every other MockClient state above).
	transportStatus        TransportSecurityStatus
	trustModel             SecurityTrustModel
	workerIdentities       map[string]WorkerIdentitySummary
	workerIdentityOrder    []string
	workerSigningKeys      map[string][]WorkerSigningKeySummary
	coordinatorSigningKeys []CoordinatorSigningKeySummary
	nextCoordinatorKeyID   int
	rotateIdempotency      map[string]RotateCoordinatorSigningKeyResult
	revokeIdempotency      map[string]RevokeCoordinatorSigningKeyResult

	// Security Events, Metrics, and Durable Audit Journal slice
	// (docs/security-events.md): seedable, as if the coordinator's own
	// ListSecurityEvents RPC had returned these -- see SeedSecurityEvent.
	securityEvents []observability.SecurityEvent

	// Web Security Center, Event Centralization, and Security CI slice:
	// seedable, as if GetSecurityEventSourceHealth had returned it.
	securityEventSourceHealth SecurityEventSourceHealthResult

	// Secure User-Level DP Operations, Observability, and Release
	// Evidence slice: seedable, as if the corresponding coordinator RPCs
	// had returned these -- see Seed* helpers in
	// secure_user_level_privacy_mock_client.go.
	secureUserLevelPrivacyHealth SecureUserLevelPrivacyHealth
	secureUserLevelPrivacyBudget map[string]SecureUserLevelPrivacyBudget
	secureUserLevelPrivacyRounds map[string][]SecureUserLevelPrivacyRound
}

func NewMockClient() *MockClient {
	return &MockClient{
		healthy:           true,
		runs:              make(map[string]*RunSnapshot),
		events:            make(map[string][]Event),
		personalization:   make(map[string]map[string]PersonalizationMetricRecord),
		privacyConfig:     make(map[string]PrivacyConfig),
		privacyLedger:     make(map[string]PrivacyLedger),
		privacyMetrics:    make(map[string]PrivacyMetricsSnapshot),
		privacyProjection: make(map[string]PrivacyProjection),
		workers:           make(map[string]WorkerSummary),
		workerIdentities:  make(map[string]WorkerIdentitySummary),
		workerSigningKeys: make(map[string][]WorkerSigningKeySummary),
		rotateIdempotency: make(map[string]RotateCoordinatorSigningKeyResult),
		revokeIdempotency: make(map[string]RevokeCoordinatorSigningKeyResult),
		transportStatus: TransportSecurityStatus{
			TransportMode:     "insecure_development",
			MutualTLSEnforced: false,
		},
		secureUserLevelPrivacyBudget: make(map[string]SecureUserLevelPrivacyBudget),
		secureUserLevelPrivacyRounds: make(map[string][]SecureUserLevelPrivacyRound),
	}
}

// SeedWorker registers a worker summary (including privacy
// capabilities) as if RegisterWorker had actually been called — for
// tests that need ListWorkers to return data without a live
// coordinator. Re-seeding the same WorkerID overwrites in place without
// changing its position in ListWorkers' registration-order output.
func (m *MockClient) SeedWorker(summary WorkerSummary) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if _, exists := m.workers[summary.WorkerID]; !exists {
		m.workerOrder = append(m.workerOrder, summary.WorkerID)
	}
	m.workers[summary.WorkerID] = summary
}

// SeedPrivacyMetrics/SeedPrivacyLedger/SeedPrivacyProjection let a test
// populate what GetPrivacyMetrics/GetPrivacyLedger/GetPrivacyProjection
// return for runID, as if the coordinator had actually run private
// rounds — without needing a live C++ coordinator. Mirrors
// SeedPersonalizationMetric's role for personalization data.
func (m *MockClient) SeedPrivacyMetrics(runID string, snapshot PrivacyMetricsSnapshot) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.privacyMetrics[runID] = snapshot
}

func (m *MockClient) SeedPrivacyLedger(runID string, ledger PrivacyLedger) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.privacyLedger[runID] = ledger
}

func (m *MockClient) SeedPrivacyProjection(runID string, projection PrivacyProjection) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.privacyProjection[runID] = projection
}

// PrivacyConfigFor returns whatever PrivacyConfig CreateRun was called
// with for runID (the zero value if none, or if runID doesn't exist) —
// for tests asserting the CreateRun -> coordinator.Client wire-mapping
// path actually carries privacy settings through.
func (m *MockClient) PrivacyConfigFor(runID string) PrivacyConfig {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.privacyConfig[runID]
}

// SeedPersonalizationMetric records a personalization metric for runID/
// record.ClientID as if a worker had submitted it via SubmitClientResult,
// for tests that need GetPersonalizationSummary to return data without a
// live coordinator. Later calls for the same client overwrite the
// previous record (mirrors the real coordinator's "latest per client"
// semantics — see run_manager.cpp's personalization_metrics_by_client_).
func (m *MockClient) SeedPersonalizationMetric(runID string, record PersonalizationMetricRecord) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.personalization[runID] == nil {
		m.personalization[runID] = make(map[string]PersonalizationMetricRecord)
	}
	m.personalization[runID][record.ClientID] = record
}

// SetUnavailable makes the next call fail with ErrUnavailable, for
// testing "coordinator unavailable" handling paths. Automatically resets
// after one call.
func (m *MockClient) SetUnavailable() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.failNext = ErrUnavailable
}

func (m *MockClient) consumeFailure() error {
	if m.failNext != nil {
		err := m.failNext
		m.failNext = nil
		return err
	}
	return nil
}

func (m *MockClient) Health(_ context.Context) (string, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return "", err
	}
	if !m.healthy {
		return "", ErrUnavailable
	}
	return "ok", nil
}

func (m *MockClient) CreateRun(_ context.Context, request CreateRunRequest) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	if _, exists := m.runs[request.RunID]; exists {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("duplicate run_id: %s", request.RunID)}
	}
	snapshot := &RunSnapshot{
		RunID:        request.RunID,
		State:        "CREATED",
		MaxRounds:    request.MaxRounds,
		Algorithm:    request.Algorithm,
		ModelVersion: "v0",
	}
	m.runs[request.RunID] = snapshot
	m.privacyConfig[request.RunID] = request.Privacy
	m.publish(request.RunID, "RUN_CREATED")
	return *snapshot, nil
}

func (m *MockClient) get(runID string) (*RunSnapshot, error) {
	run, ok := m.runs[runID]
	if !ok {
		return nil, ErrRunNotFound
	}
	return run, nil
}

func (m *MockClient) StartRun(_ context.Context, runID, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	switch run.State {
	case "RUNNING", "WAITING_FOR_CLIENTS":
		return *run, nil // idempotent
	case "COMPLETED", "FAILED", "CANCELED", "PAUSED":
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot start a run in state %s", run.State)}
	}
	run.State = "RUNNING"
	m.publish(runID, "RUN_STARTED")
	return *run, nil
}

func (m *MockClient) PauseRun(_ context.Context, runID, _, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	if run.State == "PAUSED" {
		return *run, nil // idempotent
	}
	if run.State != "RUNNING" && run.State != "WAITING_FOR_CLIENTS" {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot pause a run in state %s", run.State)}
	}
	run.State = "PAUSED"
	m.publish(runID, "RUN_PAUSED")
	return *run, nil
}

func (m *MockClient) ResumeRun(_ context.Context, runID, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	if run.State == "RUNNING" {
		return *run, nil // idempotent
	}
	if run.State != "PAUSED" {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot resume a run in state %s", run.State)}
	}
	run.State = "RUNNING"
	m.publish(runID, "RUN_RESUMED")
	return *run, nil
}

func (m *MockClient) CancelRun(_ context.Context, runID, _, _ string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	if run.State == "CANCELED" {
		return *run, nil // idempotent
	}
	if run.State == "COMPLETED" || run.State == "FAILED" {
		return RunSnapshot{}, &RejectedError{Reason: fmt.Sprintf("cannot cancel a run in terminal state %s", run.State)}
	}
	run.State = "CANCELED"
	m.publish(runID, "RUN_CANCELED")
	return *run, nil
}

func (m *MockClient) GetRun(_ context.Context, runID string) (RunSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return RunSnapshot{}, err
	}
	run, err := m.get(runID)
	if err != nil {
		return RunSnapshot{}, err
	}
	return *run, nil
}

func (m *MockClient) PollEvents(_ context.Context, runID, afterEventID string) ([]Event, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	all := m.events[runID]
	if afterEventID == "" {
		return append([]Event(nil), all...), nil
	}
	for index, event := range all {
		if event.EventID == afterEventID {
			return append([]Event(nil), all[index+1:]...), nil
		}
	}
	return append([]Event(nil), all...), nil
}

func (m *MockClient) GetPersonalizationSummary(_ context.Context, runID string) ([]PersonalizationMetricRecord, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	byClient := m.personalization[runID]
	records := make([]PersonalizationMetricRecord, 0, len(byClient))
	for _, record := range byClient {
		records = append(records, record)
	}
	sort.Slice(records, func(i, j int) bool { return records[i].ClientID < records[j].ClientID })
	return records, nil
}

func (m *MockClient) GetPrivacyMetrics(_ context.Context, runID string) (PrivacyMetricsSnapshot, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return PrivacyMetricsSnapshot{}, err
	}
	if _, err := m.get(runID); err != nil {
		return PrivacyMetricsSnapshot{}, err
	}
	// Zero-value default (no SeedPrivacyMetrics call for this runID)
	// correctly reports Has*=false everywhere, matching a non-private
	// run's real response.
	return m.privacyMetrics[runID], nil
}

func (m *MockClient) GetPrivacyLedger(_ context.Context, runID, _ string, _ uint32) (PrivacyLedger, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return PrivacyLedger{}, err
	}
	if _, err := m.get(runID); err != nil {
		return PrivacyLedger{}, err
	}
	// Pagination is a real-coordinator concern (see
	// coordinator_service.cpp's GetPrivacyLedger) not re-implemented
	// here — the mock always returns everything seeded for runID.
	return m.privacyLedger[runID], nil
}

func (m *MockClient) GetPrivacyProjection(_ context.Context, runID string) (PrivacyProjection, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return PrivacyProjection{}, err
	}
	if _, err := m.get(runID); err != nil {
		return PrivacyProjection{}, err
	}
	return m.privacyProjection[runID], nil
}

func (m *MockClient) ListWorkers(_ context.Context) ([]WorkerSummary, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if err := m.consumeFailure(); err != nil {
		return nil, err
	}
	summaries := make([]WorkerSummary, 0, len(m.workerOrder))
	for _, workerID := range m.workerOrder {
		summaries = append(summaries, m.workers[workerID])
	}
	return summaries, nil
}

func (m *MockClient) publish(runID, eventType string) {
	sequence := len(m.events[runID]) + 1
	m.events[runID] = append(m.events[runID], Event{
		EventID: fmt.Sprintf("%s:%d", runID, sequence),
		RunID:   runID,
		Type:    eventType,
	})
}
