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
}

func (s *CoordinatorService) Configured() bool {
	return s != nil && s.client != nil
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
	})
	s.recordRPC("CreateRun", err)
	if err == nil {
		_ = s.audit.Record(ctx, actorFromContext(ctx), "coordinator.run.create", "coordinator_run", snapshot.RunID, "success", map[string]any{"algorithm": req.Algorithm, "max_rounds": req.MaxRounds})
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
// pre-existing Milestone 1 dashboard demo endpoints.
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

func (s *CoordinatorService) recordLifecycle(ctx context.Context, action, runID string, err error) {
	outcome := "success"
	details := map[string]any{}
	if err != nil {
		outcome = "error"
		details["error"] = err.Error()
	}
	_ = s.audit.Record(ctx, actorFromContext(ctx), action, "coordinator_run", runID, outcome, details)
}
