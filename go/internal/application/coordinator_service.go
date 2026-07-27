package application

import (
	"context"
	"fmt"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
)

// ErrCoordinatorNotConfigured is returned when no coordinator.Client was
// wired in — e.g. local development without a running C++ coordinator
// process or CLI bridge. Distinct from coordinator.ErrUnavailable, which
// means a client exists but couldn't reach the coordinator.
var ErrCoordinatorNotConfigured = fmt.Errorf("coordinator client not configured")

// CoordinatorService is the application-layer boundary between HTTP
// handlers and the coordinator.Client interface. Per
// docs/go-coordinator-integration.md, HTTP handlers must not call
// coordinator.Client directly — they go through this service, which adds
// audit logging and derives read-only views (current round, progress
// metrics) from the coordinator's RunSnapshot rather than exposing wire
// types to the transport layer.
type CoordinatorService struct {
	client  coordinator.Client
	clock   Clock
	audit   *AuditService
	metrics *observability.MetricsRecorder

	// Security Events, Metrics, and Durable Audit Journal slice
	// (docs/security-events.md): both nil by default (see SetSecurityJournals) --
	// every existing constructor/test call site keeps compiling and
	// behaving exactly as before (a mutation simply skips the new
	// journal writes when these are unset).
	securityEvents *observability.SecurityEventJournal
	securityAudit  *observability.SecurityAuditJournal
}

func (s *CoordinatorService) Configured() bool {
	return s != nil && s.client != nil
}

// SetSecurityJournals wires the Go-local security event journal and the
// durable security-specific audit journal into this service after
// construction -- called once by httpapi.NewServer (test-friendly,
// temp-file-backed defaults) or explicitly by cmd/api/main.go
// (production, env-var-configured paths). Either argument may be nil to
// leave that journal unset.
func (s *CoordinatorService) SetSecurityJournals(events *observability.SecurityEventJournal, audit *observability.SecurityAuditJournal) {
	if s == nil {
		return
	}
	s.securityEvents = events
	s.securityAudit = audit
}

// emitSecurityEvent is a small helper so every security mutation below
// can emit consistently without repeating the nil-check. Never returns
// an error -- an event-emission failure must not affect the caller's
// actual security decision (see SecurityEventJournal.Emit's contract).
func (s *CoordinatorService) emitSecurityEvent(event observability.SecurityEvent) {
	if s == nil || s.securityEvents == nil {
		return
	}
	if event.SchemaVersion == 0 {
		event.SchemaVersion = observability.SecurityEventSchemaVersion
	}
	if event.SourceService == "" {
		event.SourceService = "go-api"
	}
	if event.Severity == "" {
		event.Severity = observability.DefaultSeverity(event.EventType)
	}
	s.securityEvents.Emit(event)
	if s.metrics != nil {
		s.metrics.RecordSecurityEvent(event.SourceService, event.EventType, event.Severity, event.Outcome)
	}
}

// appendSecurityAudit mirrors emitSecurityEvent for the durable audit
// journal -- additive alongside the existing, unchanged
// AuditService.Record call at every mutation site (Design Decision 7:
// the general-purpose repository keeps being written to for backward
// compatibility; this is the new, richer, security-specific record).
func (s *CoordinatorService) appendSecurityAudit(record observability.SecurityAuditRecord) {
	if s == nil || s.securityAudit == nil {
		return
	}
	s.securityAudit.Append(record)
}

// EmitPermissionDenied is exported so the HTTP layer's requirePermission
// (which has no other access to CoordinatorService's unexported
// journals) can emit SECURITY_PERMISSION_DENIED for any
// /api/v1/security/... route -- one call site covers every permission
// check in security_handlers.go.
func (s *CoordinatorService) EmitPermissionDenied(actor Actor, permission string) {
	s.emitSecurityEvent(observability.SecurityEvent{
		EventType:     observability.EventSecurityPermissionDenied,
		ActorType:     observability.ActorTypeUser,
		SafeActorID:   actor.ID,
		SubjectType:   observability.SubjectTypeSecurityMutation,
		SafeSubjectID: permission,
		Outcome:       observability.OutcomeBlocked,
		ReasonCode:    permission,
	})
}

// EmitAuditAccessed is exported so handleSecurityAudit can record
// requirement 12 ("audit access to detailed security records") --
// emitted only when a detailed (ADMIN) read actually occurs, into the
// events journal, not recursively into the audit journal itself.
func (s *CoordinatorService) EmitAuditAccessed(actor Actor) {
	s.emitSecurityEvent(observability.SecurityEvent{
		EventType:   observability.EventSecurityAuditAccessed,
		ActorType:   observability.ActorTypeUser,
		SafeActorID: actor.ID,
		SubjectType: observability.SubjectTypeAuditQuery,
		Outcome:     observability.OutcomeCompleted,
	})
}

// recordRPC feeds fl_coordinator_rpc_total{method,outcome} (see
// telemetry.go's WritePrometheus) — the one place every coordinator.Client
// call passes through, regardless of which HTTP route triggered it.
func (s *CoordinatorService) recordRPC(method string, err error) {
	if s.metrics == nil {
		return
	}
	outcome := "success"
	if err != nil {
		outcome = "error"
	}
	s.metrics.RecordCoordinatorRPC(method, outcome)
}

func (s *CoordinatorService) Health(ctx context.Context) (string, error) {
	if !s.Configured() {
		return "", ErrCoordinatorNotConfigured
	}
	status, err := s.client.Health(ctx)
	s.recordRPC("Health", err)
	return status, err
}

type CreateCoordinatorRunRequest struct {
	RunID                 string
	Algorithm             string
	Weighting             string
	TotalClients          uint32
	TargetClientsPerRound uint32
	MaxRounds             uint32
	MinimumValidResults   uint32
	ClientSelectionSeed   uint64
	RoundTimeoutSeconds   uint32
	ServerLR              float64
	// Fields below close the CreateRun wire-mapping gap — see
	// docs/create-run-wire-mapping.md.
	ClientIDs        []string
	LocalEpochs      uint32
	BatchSize        uint32
	LearningRate     float64
	Momentum         float64
	WeightDecay      float64
	FedProxMu        float64
	TaskLeaseSeconds uint32
	MaxTaskRetries   uint32
	ModelManifest    coordinator.ModelManifest
	RequestID        string
	Privacy          coordinator.PrivacyConfig
}

func (s *CoordinatorService) CreateRun(ctx context.Context, req CreateCoordinatorRunRequest) (coordinator.RunSnapshot, error) {
	if !s.Configured() {
		return coordinator.RunSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.CreateRun(ctx, coordinator.CreateRunRequest{
		RunID:                 req.RunID,
		Algorithm:             req.Algorithm,
		Weighting:             req.Weighting,
		TotalClients:          req.TotalClients,
		TargetClientsPerRound: req.TargetClientsPerRound,
		MaxRounds:             req.MaxRounds,
		MinimumValidResults:   req.MinimumValidResults,
		ClientSelectionSeed:   req.ClientSelectionSeed,
		RoundTimeoutSeconds:   req.RoundTimeoutSeconds,
		ServerLR:              req.ServerLR,
		ClientIDs:             req.ClientIDs,
		LocalEpochs:           req.LocalEpochs,
		BatchSize:             req.BatchSize,
		LearningRate:          req.LearningRate,
		Momentum:              req.Momentum,
		WeightDecay:           req.WeightDecay,
		FedProxMu:             req.FedProxMu,
		TaskLeaseSeconds:      req.TaskLeaseSeconds,
		MaxTaskRetries:        req.MaxTaskRetries,
		ModelManifest:         req.ModelManifest,
		RequestID:             req.RequestID,
		Privacy:               req.Privacy,
	})
	s.recordRPC("CreateRun", err)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "coordinator.run.create", "coordinator_run", snapshot.RunID, "success", map[string]any{"algorithm": req.Algorithm, "max_rounds": req.MaxRounds, "privacy_mode": string(req.Privacy.Mode)})
	}
	return snapshot, err
}

func (s *CoordinatorService) StartRun(ctx context.Context, runID, traceID string) (coordinator.RunSnapshot, error) {
	if !s.Configured() {
		return coordinator.RunSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.StartRun(ctx, runID, traceID)
	s.recordRPC("StartRun", err)
	s.recordLifecycle(ctx, "coordinator.run.start", runID, err)
	return snapshot, err
}

func (s *CoordinatorService) PauseRun(ctx context.Context, runID, reason, traceID string) (coordinator.RunSnapshot, error) {
	if !s.Configured() {
		return coordinator.RunSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.PauseRun(ctx, runID, reason, traceID)
	s.recordRPC("PauseRun", err)
	s.recordLifecycle(ctx, "coordinator.run.pause", runID, err)
	return snapshot, err
}

func (s *CoordinatorService) ResumeRun(ctx context.Context, runID, traceID string) (coordinator.RunSnapshot, error) {
	if !s.Configured() {
		return coordinator.RunSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.ResumeRun(ctx, runID, traceID)
	s.recordRPC("ResumeRun", err)
	s.recordLifecycle(ctx, "coordinator.run.resume", runID, err)
	return snapshot, err
}

func (s *CoordinatorService) CancelRun(ctx context.Context, runID, reason, traceID string) (coordinator.RunSnapshot, error) {
	if !s.Configured() {
		return coordinator.RunSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.CancelRun(ctx, runID, reason, traceID)
	s.recordRPC("CancelRun", err)
	s.recordLifecycle(ctx, "coordinator.run.cancel", runID, err)
	return snapshot, err
}

func (s *CoordinatorService) GetRun(ctx context.Context, runID string) (coordinator.RunSnapshot, error) {
	if !s.Configured() {
		return coordinator.RunSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.GetRun(ctx, runID)
	s.recordRPC("GetRun", err)
	return snapshot, err
}

// CurrentRound is a read-only projection of GetRun for the
// GET /api/v1/coordinator/runs/{runId}/rounds/current endpoint.
type CurrentRound struct {
	RunID        string `json:"run_id"`
	Round        uint64 `json:"round"`
	MaxRounds    uint32 `json:"max_rounds"`
	ModelVersion string `json:"model_version"`
	State        string `json:"state"`
}

func (s *CoordinatorService) CurrentRound(ctx context.Context, runID string) (CurrentRound, error) {
	snapshot, err := s.GetRun(ctx, runID)
	if err != nil {
		return CurrentRound{}, err
	}
	return CurrentRound{
		RunID:        snapshot.RunID,
		Round:        snapshot.CurrentRound,
		MaxRounds:    snapshot.MaxRounds,
		ModelVersion: snapshot.ModelVersion,
		State:        string(snapshot.State),
	}, nil
}

// RunMetrics is a read-only projection of GetRun for the
// GET /api/v1/coordinator/runs/{runId}/metrics endpoint. It reports only
// what the coordinator's RunDetails actually carries (round/worker
// counts) — it does not fabricate accuracy or loss figures, unlike the
// pre-existing the Foundation phase dashboard demo endpoints.
type RunMetrics struct {
	RunID             string `json:"run_id"`
	State             string `json:"state"`
	CurrentRound      uint64 `json:"current_round"`
	MaxRounds         uint32 `json:"max_rounds"`
	ProgressPercent   int    `json:"progress_percent"`
	RegisteredWorkers uint32 `json:"registered_workers"`
	HealthyWorkers    uint32 `json:"healthy_workers"`
}

func (s *CoordinatorService) Metrics(ctx context.Context, runID string) (RunMetrics, error) {
	snapshot, err := s.GetRun(ctx, runID)
	if err != nil {
		return RunMetrics{}, err
	}
	progress := 0
	if snapshot.MaxRounds > 0 {
		progress = int((snapshot.CurrentRound * 100) / uint64(snapshot.MaxRounds))
		if progress > 100 {
			progress = 100
		}
	}
	return RunMetrics{
		RunID:             snapshot.RunID,
		State:             string(snapshot.State),
		CurrentRound:      snapshot.CurrentRound,
		MaxRounds:         snapshot.MaxRounds,
		ProgressPercent:   progress,
		RegisteredWorkers: snapshot.RegisteredWorkers,
		HealthyWorkers:    snapshot.HealthyWorkers,
	}, nil
}

func (s *CoordinatorService) PollEvents(ctx context.Context, runID, afterEventID string) ([]coordinator.Event, error) {
	if !s.Configured() {
		return nil, ErrCoordinatorNotConfigured
	}
	events, err := s.client.PollEvents(ctx, runID, afterEventID)
	s.recordRPC("PollEvents", err)
	return events, err
}

// PersonalizationRecords returns the raw per-client personalization
// metric records the coordinator has received for runID (empty, not an
// error, for runs where no client submitted one — e.g. FedAvg/FedProx/
// SCAFFOLD/FedSAM runs never do).
func (s *CoordinatorService) PersonalizationRecords(ctx context.Context, runID string) ([]coordinator.PersonalizationMetricRecord, error) {
	if !s.Configured() {
		return nil, ErrCoordinatorNotConfigured
	}
	records, err := s.client.GetPersonalizationSummary(ctx, runID)
	s.recordRPC("GetPersonalizationSummary", err)
	return records, err
}

// GetPrivacyMetrics/GetPrivacyLedger/GetPrivacyProjection expose the
// coordinator's privacy ledgers/accountants read-only — see
// docs/privacy-ledger.md. Safe to call for any run, private or not.
func (s *CoordinatorService) GetPrivacyMetrics(ctx context.Context, runID string) (coordinator.PrivacyMetricsSnapshot, error) {
	if !s.Configured() {
		return coordinator.PrivacyMetricsSnapshot{}, ErrCoordinatorNotConfigured
	}
	snapshot, err := s.client.GetPrivacyMetrics(ctx, runID)
	s.recordRPC("GetPrivacyMetrics", err)
	return snapshot, err
}

func (s *CoordinatorService) GetPrivacyLedger(ctx context.Context, runID, pageToken string, pageSize uint32) (coordinator.PrivacyLedger, error) {
	if !s.Configured() {
		return coordinator.PrivacyLedger{}, ErrCoordinatorNotConfigured
	}
	ledger, err := s.client.GetPrivacyLedger(ctx, runID, pageToken, pageSize)
	s.recordRPC("GetPrivacyLedger", err)
	return ledger, err
}

func (s *CoordinatorService) GetPrivacyProjection(ctx context.Context, runID string) (coordinator.PrivacyProjection, error) {
	if !s.Configured() {
		return coordinator.PrivacyProjection{}, ErrCoordinatorNotConfigured
	}
	projection, err := s.client.GetPrivacyProjection(ctx, runID)
	s.recordRPC("GetPrivacyProjection", err)
	return projection, err
}

// ListWorkers returns every registered worker with its privacy
// capabilities — see docs/worker-privacy-capabilities.md.
func (s *CoordinatorService) ListWorkers(ctx context.Context) ([]coordinator.WorkerSummary, error) {
	if !s.Configured() {
		return nil, ErrCoordinatorNotConfigured
	}
	workers, err := s.client.ListWorkers(ctx)
	s.recordRPC("ListWorkers", err)
	return workers, err
}

// ErrClientNotFound is returned by ClientPersonalization when runID has
// no personalization record for clientID (either the client never
// submitted one, or it does not exist).
var ErrClientNotFound = fmt.Errorf("client has no personalization record for this run")

func (s *CoordinatorService) ClientPersonalization(ctx context.Context, runID, clientID string) (coordinator.PersonalizationMetricRecord, error) {
	records, err := s.PersonalizationRecords(ctx, runID)
	if err != nil {
		return coordinator.PersonalizationMetricRecord{}, err
	}
	for _, record := range records {
		if record.ClientID == clientID {
			return record, nil
		}
	}
	return coordinator.PersonalizationMetricRecord{}, ErrClientNotFound
}

func personalizationRecordsToEvaluationRecords(records []coordinator.PersonalizationMetricRecord) []PerClientEvaluationRecord {
	converted := make([]PerClientEvaluationRecord, 0, len(records))
	for _, record := range records {
		evalRecord := PerClientEvaluationRecord{
			ClientID:            record.ClientID,
			GlobalLocalAccuracy: record.GlobalLocalAccuracy,
			SampleCount:         int64(record.SampleCount),
		}
		if record.HasPersonalizedModel {
			accuracy := record.PersonalizedLocalAccuracy
			evalRecord.PersonalizedLocalAccuracy = &accuracy
		}
		converted = append(converted, evalRecord)
	}
	return converted
}

// Fairness computes personalization fairness statistics for runID from
// the coordinator's raw personalization records (see fairness.go /
// docs/fairness-metrics.md). Returns a zero-value, all-excluded
// PersonalizationMetrics (not an error) when the run has no clients that
// have reported anything yet — see EmptyPersonalizationMetrics.
func (s *CoordinatorService) Fairness(ctx context.Context, runID string) (PersonalizationMetrics, error) {
	records, err := s.PersonalizationRecords(ctx, runID)
	if err != nil {
		return PersonalizationMetrics{}, err
	}
	if len(records) == 0 {
		return PersonalizationMetrics{ExcludedReasons: []string{}}, nil
	}
	return ComputeAggregatedPersonalizationMetrics(personalizationRecordsToEvaluationRecords(records))
}

// AlgorithmSummary is a per-run projection combining the run's algorithm
// with its fairness statistics, for the algorithm-comparison dashboard
// view (docs/algorithm-expansion-architecture.md).
type AlgorithmSummary struct {
	RunID       string                 `json:"run_id"`
	Algorithm   string                 `json:"algorithm"`
	ClientCount int                    `json:"reporting_client_count"`
	Fairness    PersonalizationMetrics `json:"fairness"`
}

func (s *CoordinatorService) AlgorithmSummary(ctx context.Context, runID string) (AlgorithmSummary, error) {
	snapshot, err := s.GetRun(ctx, runID)
	if err != nil {
		return AlgorithmSummary{}, err
	}
	fairness, err := s.Fairness(ctx, runID)
	if err != nil {
		return AlgorithmSummary{}, err
	}
	records, err := s.PersonalizationRecords(ctx, runID)
	if err != nil {
		return AlgorithmSummary{}, err
	}
	return AlgorithmSummary{
		RunID:       runID,
		Algorithm:   snapshot.Algorithm,
		ClientCount: len(records),
		Fairness:    fairness,
	}, nil
}

func (s *CoordinatorService) recordLifecycle(ctx context.Context, action, runID string, err error) {
	outcome := "success"
	details := map[string]any{}
	if err != nil {
		outcome = "error"
		details["error"] = err.Error()
	}
	_ = s.audit.Record(ctx, actorFromContext(ctx), action, "coordinator_run", runID, outcome, details)
}
