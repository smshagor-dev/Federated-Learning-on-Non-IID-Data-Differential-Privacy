package application

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type backendEventTestDriver struct {
	snapshot executiondomain.Snapshot
	events   []executiondomain.BackendEvent
}

func (d *backendEventTestDriver) Create(context.Context, string, executiondomain.Spec, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *backendEventTestDriver) Start(context.Context, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *backendEventTestDriver) Pause(context.Context, string, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *backendEventTestDriver) Resume(context.Context, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *backendEventTestDriver) Cancel(context.Context, string, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *backendEventTestDriver) Get(context.Context, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *backendEventTestDriver) ListWorkers(context.Context) ([]executiondomain.Worker, error) {
	return nil, nil
}

func (d *backendEventTestDriver) PollEvents(_ context.Context, _ string, afterEventID string) ([]executiondomain.BackendEvent, error) {
	if afterEventID == "" {
		return append([]executiondomain.BackendEvent(nil), d.events...), nil
	}
	for index, event := range d.events {
		if event.EventID == afterEventID {
			return append([]executiondomain.BackendEvent(nil), d.events[index+1:]...), nil
		}
	}
	return append([]executiondomain.BackendEvent(nil), d.events...), nil
}

func TestExecutionBackendEventsAreDurableAndIdempotent(t *testing.T) {
	ctx := context.Background()
	clockTime := time.Date(2026, 8, 23, 15, 0, 0, 0, time.UTC)
	repo := executiondomain.NewInMemoryRepository()
	journal, err := executiondomain.NewJournal(filepath.Join(t.TempDir(), "execution-events.jsonl"))
	if err != nil {
		t.Fatalf("new journal: %v", err)
	}
	driver := &backendEventTestDriver{
		events: []executiondomain.BackendEvent{
			{
				EventID:   "event-41",
				Type:      "ROUND_STARTED",
				Round:     7,
				Timestamp: clockTime.Add(-2 * time.Second),
			},
			{
				EventID: "event-42",
				Type:    "TASK_FAILED",
				Round:   7,
				Reason:  "round deadline exceeded",
				Metadata: map[string]string{
					"failure_kind": "round_timeout",
					"client_id":    "client-3",
					"worker_id":    "worker-2",
				},
			},
		},
	}
	service := NewExecutionService(
		repo,
		executiondomain.DriverRegistry{executiondomain.BackendDistributed: driver},
		journal,
		nil,
		func() time.Time { return clockTime },
		nil,
	)
	record, err := repo.Create(ctx, executiondomain.Record{
		ID:           "exec-events",
		Backend:      executiondomain.BackendDistributed,
		Status:       executiondomain.StatusRunning,
		BackendRunID: "run-events",
		Revision:     1,
		CreatedAt:    clockTime.Add(-time.Minute),
		UpdatedAt:    clockTime.Add(-time.Minute),
	})
	if err != nil {
		t.Fatalf("create execution: %v", err)
	}

	updated, ingested, err := service.IngestBackendEvents(ctx, record.ID)
	if err != nil {
		t.Fatalf("ingest backend events: %v", err)
	}
	if ingested != 2 {
		t.Fatalf("ingested=%d, want 2", ingested)
	}
	if updated.BackendEventCursor != "event-42" {
		t.Fatalf("cursor=%q, want event-42", updated.BackendEventCursor)
	}
	events, err := service.Events(ctx, record.ID, 0)
	if err != nil {
		t.Fatalf("list events: %v", err)
	}
	if len(events) != 2 {
		t.Fatalf("events=%d, want 2", len(events))
	}
	if events[1].Type != "COORDINATOR_TASK_FAILED" {
		t.Fatalf("event type=%q", events[1].Type)
	}
	if events[1].Metadata["failure_kind"] != "round_timeout" {
		t.Fatalf("failure_kind=%q", events[1].Metadata["failure_kind"])
	}
	if events[1].Metadata["client_id"] != "client-3" {
		t.Fatalf("client_id=%q", events[1].Metadata["client_id"])
	}

	// Simulate a crash after journal append but before cursor persistence by
	// deliberately rolling the cursor back. Replaying the backend batch must
	// not duplicate the already-durable journal entries.
	rolledBack := updated
	rolledBack.BackendEventCursor = ""
	rolledBack, err = repo.Update(ctx, rolledBack, updated.Revision)
	if err != nil {
		t.Fatalf("roll back cursor: %v", err)
	}
	replayed, replayCount, err := service.IngestBackendEvents(ctx, record.ID)
	if err != nil {
		t.Fatalf("replay backend events: %v", err)
	}
	if replayCount != 0 {
		t.Fatalf("replay ingested=%d, want 0", replayCount)
	}
	if replayed.BackendEventCursor != "event-42" {
		t.Fatalf("replayed cursor=%q, want event-42", replayed.BackendEventCursor)
	}
	events, err = service.Events(ctx, record.ID, 0)
	if err != nil {
		t.Fatalf("list replayed events: %v", err)
	}
	if len(events) != 2 {
		t.Fatalf("events after replay=%d, want 2", len(events))
	}
	_ = rolledBack
}

func TestRuntimeReconcileIngestsBackendEventsWhenSnapshotChanges(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 15, 5, 0, 0, time.UTC)
	repo := executiondomain.NewInMemoryRepository()
	journal, err := executiondomain.NewJournal(filepath.Join(t.TempDir(), "execution-events.jsonl"))
	if err != nil {
		t.Fatalf("new journal: %v", err)
	}
	driver := &backendEventTestDriver{
		snapshot: executiondomain.Snapshot{
			BackendRunID: "run-reconcile-events",
			Status:       executiondomain.StatusRunning,
			CurrentRound: 3,
			ModelVersion: "v2",
		},
		events: []executiondomain.BackendEvent{
			{
				EventID: "event-99",
				Type:    "MODEL_VERSION_UPDATED",
				Round:   2,
				Metadata: map[string]string{
					"model_version": "v2",
				},
			},
		},
	}
	service := NewExecutionService(
		repo,
		executiondomain.DriverRegistry{executiondomain.BackendDistributed: driver},
		journal,
		nil,
		func() time.Time { return now },
		nil,
	)
	_, err = repo.Create(ctx, executiondomain.Record{
		ID:           "exec-reconcile-events",
		Backend:      executiondomain.BackendDistributed,
		Status:       executiondomain.StatusRunning,
		BackendRunID: "run-reconcile-events",
		CurrentRound: 2,
		ModelVersion: "v1",
		Revision:     1,
		CreatedAt:    now.Add(-time.Minute),
		UpdatedAt:    now.Add(-time.Minute),
	})
	if err != nil {
		t.Fatalf("create execution: %v", err)
	}

	summary, err := service.ReconcileRuntimeBackend(ctx, executiondomain.BackendDistributed)
	if err != nil {
		t.Fatalf("runtime reconcile: %v", err)
	}
	if len(summary.Failures) != 0 {
		t.Fatalf("reconcile failures: %+v", summary.Failures)
	}
	if summary.Updated != 1 {
		t.Fatalf("updated=%d, want 1", summary.Updated)
	}
	updated, err := service.Get(ctx, "exec-reconcile-events")
	if err != nil {
		t.Fatalf("get execution: %v", err)
	}
	if updated.BackendEventCursor != "event-99" {
		t.Fatalf("cursor=%q, want event-99", updated.BackendEventCursor)
	}
	events, err := service.Events(ctx, updated.ID, 0)
	if err != nil {
		t.Fatalf("events: %v", err)
	}
	if len(events) != 2 {
		// Reconcile itself records EXECUTION_RECONCILED, then event ingestion
		// records the coordinator event.
		t.Fatalf("events=%d, want 2", len(events))
	}
	if events[1].Type != "COORDINATOR_MODEL_VERSION_UPDATED" {
		t.Fatalf("last event type=%q", events[1].Type)
	}
}
