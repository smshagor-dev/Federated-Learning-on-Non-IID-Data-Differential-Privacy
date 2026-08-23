package application

import (
	"context"
	"testing"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type reconcileOnlyDriver struct{}

func (reconcileOnlyDriver) Create(_ context.Context, executionID string, _ executiondomain.Spec, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{
		BackendRunID: executionID,
		Status:       executiondomain.StatusCreated,
		MaxRounds:    3,
		ModelVersion: "v0",
	}, nil
}

func (reconcileOnlyDriver) Start(_ context.Context, backendRunID, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{
		BackendRunID: backendRunID,
		Status:       executiondomain.StatusRunning,
		CurrentRound: 1,
		MaxRounds:    3,
		ModelVersion: "v1",
	}, nil
}

func (reconcileOnlyDriver) Pause(_ context.Context, backendRunID, _, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusPaused}, nil
}

func (reconcileOnlyDriver) Resume(_ context.Context, backendRunID, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusRunning}, nil
}

func (reconcileOnlyDriver) Cancel(_ context.Context, backendRunID, _, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusCanceled}, nil
}

func (reconcileOnlyDriver) Get(_ context.Context, backendRunID string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{
		BackendRunID:      backendRunID,
		Status:            executiondomain.StatusRunning,
		CurrentRound:      1,
		MaxRounds:         3,
		ModelVersion:      "v2",
		RegisteredWorkers: 5,
		HealthyWorkers:    4,
	}, nil
}

func (reconcileOnlyDriver) ListWorkers(_ context.Context) ([]executiondomain.Worker, error) {
	return nil, nil
}

func TestExecutionReconcilePersistsModelAndWorkerOnlyChanges(t *testing.T) {
	service, journal := newExecutionServiceForTest(t, reconcileOnlyDriver{})
	ctx := context.Background()

	record, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.Start(ctx, record.ID, "trace")
	if err != nil {
		t.Fatal(err)
	}
	beforeRevision := record.Revision
	if record.CurrentRound != 1 || record.ModelVersion != "v1" {
		t.Fatalf("started record = %#v", record)
	}

	reconciled, err := service.Reconcile(ctx, record.ID)
	if err != nil {
		t.Fatal(err)
	}
	if reconciled.Status != executiondomain.StatusRunning || reconciled.CurrentRound != 1 {
		t.Fatalf("status/round changed unexpectedly: %#v", reconciled)
	}
	if reconciled.ModelVersion != "v2" || reconciled.RegisteredWorkers != 5 || reconciled.HealthyWorkers != 4 {
		t.Fatalf("reconciled record = %#v", reconciled)
	}
	if reconciled.Revision <= beforeRevision {
		t.Fatalf("revision did not advance: before=%d after=%d", beforeRevision, reconciled.Revision)
	}

	persisted, err := service.Get(ctx, record.ID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.ModelVersion != "v2" || persisted.RegisteredWorkers != 5 || persisted.HealthyWorkers != 4 {
		t.Fatalf("worker/model-only changes were not persisted: %#v", persisted)
	}

	events, err := journal.List(record.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) == 0 || events[len(events)-1].Type != "EXECUTION_RECONCILED" {
		t.Fatalf("missing reconcile event: %#v", events)
	}
}
