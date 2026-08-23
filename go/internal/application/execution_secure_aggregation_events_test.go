package application

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type secureAbortTestDriver struct {
	snapshot       executiondomain.Snapshot
	securityEvents []executiondomain.BackendSecurityEvent
	getErr         error
	cancelErr      error
	getCalls       int
	cancelCalls    int
}

func (d *secureAbortTestDriver) Create(context.Context, string, executiondomain.Spec, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureAbortTestDriver) Start(context.Context, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureAbortTestDriver) Pause(context.Context, string, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureAbortTestDriver) Resume(context.Context, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureAbortTestDriver) Cancel(_ context.Context, backendRunID, _ string, _ string) (executiondomain.Snapshot, error) {
	d.cancelCalls++
	if d.cancelErr != nil {
		return executiondomain.Snapshot{}, d.cancelErr
	}
	result := d.snapshot
	result.BackendRunID = backendRunID
	result.Status = executiondomain.StatusCanceled
	return result, nil
}

func (d *secureAbortTestDriver) Get(context.Context, string) (executiondomain.Snapshot, error) {
	d.getCalls++
	if d.getErr != nil {
		return executiondomain.Snapshot{}, d.getErr
	}
	return d.snapshot, nil
}

func (d *secureAbortTestDriver) ListWorkers(context.Context) ([]executiondomain.Worker, error) {
	return nil, nil
}

func (d *secureAbortTestDriver) PollSecurityEvents(
	_ context.Context,
	afterEventID string,
	_ uint32,
) (executiondomain.SecurityEventPage, error) {
	start := 0
	if afterEventID != "" {
		for index, event := range d.securityEvents {
			if event.EventID == afterEventID {
				start = index + 1
				break
			}
		}
	}
	events := append([]executiondomain.BackendSecurityEvent(nil), d.securityEvents[start:]...)
	cursor := afterEventID
	if len(events) > 0 {
		cursor = events[len(events)-1].EventID
	}
	return executiondomain.SecurityEventPage{Events: events, NextCursor: cursor}, nil
}

func newSecureAbortExecutionService(
	t *testing.T,
	driver *secureAbortTestDriver,
	now time.Time,
) (*ExecutionService, executiondomain.Repository) {
	t.Helper()
	repo := executiondomain.NewInMemoryRepository()
	journal, err := executiondomain.NewJournal(filepath.Join(t.TempDir(), "execution-events.jsonl"))
	if err != nil {
		t.Fatalf("new journal: %v", err)
	}
	service := NewExecutionService(
		repo,
		executiondomain.DriverRegistry{executiondomain.BackendDistributed: driver},
		journal,
		nil,
		func() time.Time { return now },
		nil,
	)
	return service, repo
}

func createSecureAbortExecution(
	t *testing.T,
	repo executiondomain.Repository,
	now time.Time,
	id string,
) executiondomain.Record {
	t.Helper()
	record, err := repo.Create(context.Background(), executiondomain.Record{
		ID:           id,
		Backend:      executiondomain.BackendDistributed,
		Status:       executiondomain.StatusRunning,
		BackendRunID: "run-secure",
		CurrentRound: 1,
		MaxRounds:    10,
		Revision:     1,
		CreatedAt:    now.Add(-time.Minute),
		UpdatedAt:    now.Add(-time.Minute),
	})
	if err != nil {
		t.Fatalf("create execution: %v", err)
	}
	return record
}

func TestSecureAggregationDeadlineAbortCancelsLiveExecution(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 15, 30, 0, 0, time.UTC)
	driver := &secureAbortTestDriver{
		snapshot: executiondomain.Snapshot{
			BackendRunID: "run-secure",
			Status:       executiondomain.StatusRunning,
			CurrentRound: 1,
			MaxRounds:    10,
		},
		securityEvents: []executiondomain.BackendSecurityEvent{
			{
				EventID:       "security-7",
				EventType:     secureAggregationSessionAborted,
				SafeSubjectID: "run-secure:1",
				ReasonCode:    "masked_update_deadline_exceeded",
				Outcome:       "COMPLETED",
				TraceID:       "trace-secure-timeout",
			},
		},
	}
	service, repo := newSecureAbortExecutionService(t, driver, now)
	record := createSecureAbortExecution(t, repo, now, "exec-secure-timeout")

	updated, processed, err := service.ReconcileSecureAggregationSecurityEvents(ctx, record.ID)
	if err != nil {
		t.Fatalf("reconcile secure events: %v", err)
	}
	if processed != 1 {
		t.Fatalf("processed=%d, want 1", processed)
	}
	if driver.cancelCalls != 1 {
		t.Fatalf("cancel calls=%d, want 1", driver.cancelCalls)
	}
	if updated.Status != executiondomain.StatusCanceled {
		t.Fatalf("status=%s, want CANCELED", updated.Status)
	}
	if updated.SecurityEventCursor != "security-7" {
		t.Fatalf("security cursor=%q, want security-7", updated.SecurityEventCursor)
	}
	events, err := service.Events(ctx, record.ID, 0)
	if err != nil {
		t.Fatalf("list events: %v", err)
	}
	if len(events) == 0 || events[len(events)-1].Type != "SECURE_AGGREGATION_ABORT_PROPAGATED" {
		t.Fatalf("last event=%+v", events)
	}
	if events[len(events)-1].Metadata["security_reason_code"] != "masked_update_deadline_exceeded" {
		t.Fatalf("security reason metadata=%q", events[len(events)-1].Metadata["security_reason_code"])
	}
}

func TestSecureAggregationManualAbortAlsoCancelsLiveExecution(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 15, 31, 0, 0, time.UTC)
	driver := &secureAbortTestDriver{
		securityEvents: []executiondomain.BackendSecurityEvent{
			{
				EventID:       "security-manual",
				EventType:     secureAggregationSessionAborted,
				SafeSubjectID: "run-secure:1",
				ReasonCode:    "manual_abort",
				Outcome:       "COMPLETED",
			},
		},
	}
	service, repo := newSecureAbortExecutionService(t, driver, now)
	record := createSecureAbortExecution(t, repo, now, "exec-secure-manual")

	updated, _, err := service.ReconcileSecureAggregationSecurityEvents(ctx, record.ID)
	if err != nil {
		t.Fatalf("reconcile secure events: %v", err)
	}
	if updated.Status != executiondomain.StatusCanceled || driver.cancelCalls != 1 {
		t.Fatalf("updated=%+v cancel_calls=%d", updated, driver.cancelCalls)
	}
}

func TestSecureAggregationConfigurationFallbackDoesNotCancel(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 15, 32, 0, 0, time.UTC)
	driver := &secureAbortTestDriver{
		securityEvents: []executiondomain.BackendSecurityEvent{
			{
				EventID:       "security-fallback",
				EventType:     secureAggregationSessionAborted,
				SafeSubjectID: "run-secure:1",
				Outcome:       "REJECTED",
				SafeDetails: map[string]string{
					"abort_reason": "SECURE_AGGREGATION_ALGORITHM_UNSUPPORTED: only fedavg is supported",
				},
			},
		},
	}
	service, repo := newSecureAbortExecutionService(t, driver, now)
	record := createSecureAbortExecution(t, repo, now, "exec-secure-fallback")

	updated, processed, err := service.ReconcileSecureAggregationSecurityEvents(ctx, record.ID)
	if err != nil {
		t.Fatalf("reconcile secure events: %v", err)
	}
	if processed != 1 {
		t.Fatalf("processed=%d, want 1", processed)
	}
	if driver.cancelCalls != 0 {
		t.Fatalf("cancel calls=%d, want 0", driver.cancelCalls)
	}
	if updated.Status != executiondomain.StatusRunning {
		t.Fatalf("status=%s, want RUNNING", updated.Status)
	}
	if updated.SecurityEventCursor != "security-fallback" {
		t.Fatalf("security cursor=%q", updated.SecurityEventCursor)
	}
}

func TestSecureAggregationRestartAbortFailsExecutionWithoutBackendCall(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 15, 33, 0, 0, time.UTC)
	driver := &secureAbortTestDriver{
		getErr: errors.New("old coordinator run no longer exists"),
		securityEvents: []executiondomain.BackendSecurityEvent{
			{
				EventID:       "security-restart",
				EventType:     secureAggregationRestartAborted,
				SafeSubjectID: "run-secure:1",
				ReasonCode:    "coordinator_restart",
				Outcome:       "COMPLETED",
			},
		},
	}
	service, repo := newSecureAbortExecutionService(t, driver, now)
	record := createSecureAbortExecution(t, repo, now, "exec-secure-restart")

	updated, _, err := service.ReconcileSecureAggregationSecurityEvents(ctx, record.ID)
	if err != nil {
		t.Fatalf("reconcile secure events: %v", err)
	}
	if driver.getCalls != 0 || driver.cancelCalls != 0 {
		t.Fatalf("backend calls get=%d cancel=%d, want 0/0", driver.getCalls, driver.cancelCalls)
	}
	if updated.Status != executiondomain.StatusFailed {
		t.Fatalf("status=%s, want FAILED", updated.Status)
	}
	if updated.CompletedAt == nil {
		t.Fatal("completed_at must be set")
	}
	if updated.SecurityEventCursor != "security-restart" {
		t.Fatalf("security cursor=%q", updated.SecurityEventCursor)
	}
	if updated.LastError == "" {
		t.Fatal("last_error must explain secure restart failure")
	}
	events, err := service.Events(ctx, record.ID, 0)
	if err != nil {
		t.Fatalf("list events: %v", err)
	}
	if len(events) != 1 || events[0].Type != "EXECUTION_FAILED_SECURE_AGGREGATION" {
		t.Fatalf("events=%+v", events)
	}
}

func TestRuntimeReconcileHandlesSecureRestartBeforeGetRun(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 15, 34, 0, 0, time.UTC)
	driver := &secureAbortTestDriver{
		getErr: errors.New("backend run missing after restart"),
		securityEvents: []executiondomain.BackendSecurityEvent{
			{
				EventID:       "security-restart-loop",
				EventType:     secureAggregationRestartAborted,
				SafeSubjectID: "run-secure:1",
				ReasonCode:    "coordinator_restart",
				Outcome:       "COMPLETED",
			},
		},
	}
	service, repo := newSecureAbortExecutionService(t, driver, now)
	createSecureAbortExecution(t, repo, now, "exec-secure-restart-loop")

	summary, err := service.ReconcileRuntimeBackend(ctx, executiondomain.BackendDistributed)
	if err != nil {
		t.Fatalf("runtime reconcile: %v", err)
	}
	if len(summary.Failures) != 0 {
		t.Fatalf("failures=%+v", summary.Failures)
	}
	if summary.Checked != 1 || summary.Updated != 1 {
		t.Fatalf("summary=%+v", summary)
	}
	if driver.getCalls != 0 {
		t.Fatalf("Get called %d times; secure restart must resolve first", driver.getCalls)
	}
	updated, err := service.Get(ctx, "exec-secure-restart-loop")
	if err != nil {
		t.Fatalf("get execution: %v", err)
	}
	if updated.Status != executiondomain.StatusFailed {
		t.Fatalf("status=%s, want FAILED", updated.Status)
	}
}
