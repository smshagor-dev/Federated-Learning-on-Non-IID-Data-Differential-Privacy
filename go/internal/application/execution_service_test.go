package application

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type fakeExecutionDriver struct {
	createErr error
	startErr  error
	status    executiondomain.Status
	created   bool
}

func (d *fakeExecutionDriver) Create(_ context.Context, executionID string, _ executiondomain.Spec, _ string) (executiondomain.Snapshot, error) {
	if d.createErr != nil {
		return executiondomain.Snapshot{}, d.createErr
	}
	d.created = true
	return executiondomain.Snapshot{
		BackendRunID: executionID,
		Status:       executiondomain.StatusCreated,
		MaxRounds:    3,
		ModelVersion: "v0",
	}, nil
}

func (d *fakeExecutionDriver) Start(_ context.Context, backendRunID, _ string) (executiondomain.Snapshot, error) {
	if d.startErr != nil {
		return executiondomain.Snapshot{}, d.startErr
	}
	d.status = executiondomain.StatusRunning
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: d.status, CurrentRound: 1, MaxRounds: 3, ModelVersion: "v1"}, nil
}

func (d *fakeExecutionDriver) Pause(_ context.Context, backendRunID, _, _ string) (executiondomain.Snapshot, error) {
	d.status = executiondomain.StatusPaused
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: d.status, CurrentRound: 1, MaxRounds: 3, ModelVersion: "v1"}, nil
}

func (d *fakeExecutionDriver) Resume(_ context.Context, backendRunID, _ string) (executiondomain.Snapshot, error) {
	d.status = executiondomain.StatusRunning
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: d.status, CurrentRound: 2, MaxRounds: 3, ModelVersion: "v2"}, nil
}

func (d *fakeExecutionDriver) Cancel(_ context.Context, backendRunID, _, _ string) (executiondomain.Snapshot, error) {
	d.status = executiondomain.StatusCanceled
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: d.status, CurrentRound: 2, MaxRounds: 3, ModelVersion: "v2"}, nil
}

func (d *fakeExecutionDriver) Get(_ context.Context, backendRunID string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: d.status, CurrentRound: 2, MaxRounds: 3, ModelVersion: "v2", RegisteredWorkers: 4, HealthyWorkers: 3}, nil
}

func (d *fakeExecutionDriver) ListWorkers(_ context.Context) ([]executiondomain.Worker, error) {
	return []executiondomain.Worker{{WorkerID: "worker-1", Status: "IDLE"}}, nil
}

func validExecutionSpec() executiondomain.Spec {
	return executiondomain.Spec{
		SchemaVersion: executiondomain.CurrentSchemaVersion,
		Name:          "integration-smoke",
		Backend:       executiondomain.BackendDistributed,
		Dataset: executiondomain.DatasetSpec{
			Name: "MNIST",
			Partition: executiondomain.PartitionSpec{
				Strategy:          "iid",
				MinimumClientSize: 1,
			},
		},
		Model: executiondomain.ModelSpec{
			Name:         "tiny",
			Version:      "v1",
			UpdateFormat: "state_dict_delta",
			Tensors: []executiondomain.TensorSpec{
				{Name: "weight", Shape: []uint64{2, 2}},
			},
			Aggregation: executiondomain.AggregationManifest{
				SharedParameterNames: []string{"weight"},
			},
		},
		Algorithm: executiondomain.AlgorithmSpec{Name: "fedavg"},
		Optimizer: executiondomain.OptimizerSpec{
			LearningRate: 0.01,
			ServerLR:     1,
		},
		Federation: executiondomain.FederationSpec{
			TotalClients:          2,
			ClientIDs:             []string{"client-1", "client-2"},
			TargetClientsPerRound: 2,
			MinimumValidResults:   1,
			Rounds:                3,
			LocalEpochs:           1,
			BatchSize:             8,
			Weighting:             "uniform",
			ClientSelectionSeed:   7,
			SchedulingMode:        executiondomain.SchedulingSynchronous,
			RoundTimeoutSeconds:   30,
			TaskLeaseSeconds:      15,
			MaxTaskRetries:        2,
		},
		Privacy: executiondomain.PrivacySpec{Mode: executiondomain.PrivacyNone},
		Evaluation: executiondomain.EvaluationSpec{
			EvaluateGlobal:      true,
			EvaluatePerClient:   true,
			EvaluateFairness:    true,
			EvaluationBatchSize: 32,
		},
		Artifacts: executiondomain.ArtifactSpec{Root: "artifacts/integration-smoke", PersistCheckpoints: true, PersistEvents: true},
	}
}

func newExecutionServiceForTest(t *testing.T, driver executiondomain.Driver) (*ExecutionService, *executiondomain.Journal) {
	t.Helper()
	journal, err := executiondomain.NewJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	clock := func() time.Time { return time.Unix(100, 0).UTC() }
	return NewExecutionService(
		executiondomain.NewInMemoryRepository(),
		executiondomain.DriverRegistry{executiondomain.BackendDistributed: driver},
		journal,
		nil,
		clock,
		nil,
	), journal
}

func TestExecutionServiceFullLifecycle(t *testing.T) {
	driver := &fakeExecutionDriver{}
	service, journal := newExecutionServiceForTest(t, driver)
	ctx := context.Background()

	record, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	if record.Status != executiondomain.StatusCreated || record.SpecHash == "" {
		t.Fatalf("created record = %#v", record)
	}

	record, err = service.Start(ctx, record.ID, "trace-start")
	if err != nil {
		t.Fatal(err)
	}
	if record.Status != executiondomain.StatusRunning || record.BackendRunID == "" || record.CurrentRound != 1 {
		t.Fatalf("started record = %#v", record)
	}

	record, err = service.Pause(ctx, record.ID, "operator", "trace-pause")
	if err != nil || record.Status != executiondomain.StatusPaused {
		t.Fatalf("pause record=%#v err=%v", record, err)
	}
	record, err = service.Resume(ctx, record.ID, "trace-resume")
	if err != nil || record.Status != executiondomain.StatusRunning || record.CurrentRound != 2 {
		t.Fatalf("resume record=%#v err=%v", record, err)
	}
	record, err = service.Cancel(ctx, record.ID, "operator", "trace-cancel")
	if err != nil || record.Status != executiondomain.StatusCanceled || record.CompletedAt == nil {
		t.Fatalf("cancel record=%#v err=%v", record, err)
	}

	events, err := journal.List(record.ID, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(events) < 8 {
		t.Fatalf("event count = %d, want lifecycle journal", len(events))
	}
}

func TestExecutionStartFailureRemainsRetryable(t *testing.T) {
	backendErr := errors.New("coordinator temporarily unavailable")
	driver := &fakeExecutionDriver{createErr: backendErr}
	service, _ := newExecutionServiceForTest(t, driver)
	ctx := context.Background()

	record, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.Start(ctx, record.ID, "trace")
	if !errors.Is(err, backendErr) {
		t.Fatalf("start error = %v", err)
	}
	if record.Status != executiondomain.StatusCreated || record.LastError == "" || record.Terminal() {
		t.Fatalf("failed start record = %#v", record)
	}
}

func TestExecutionReconcileRefreshesWorkerCounts(t *testing.T) {
	driver := &fakeExecutionDriver{}
	service, _ := newExecutionServiceForTest(t, driver)
	ctx := context.Background()
	record, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.Start(ctx, record.ID, "trace")
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.Reconcile(ctx, record.ID)
	if err != nil {
		t.Fatal(err)
	}
	if record.RegisteredWorkers != 4 || record.HealthyWorkers != 3 || record.CurrentRound != 2 {
		t.Fatalf("reconciled record = %#v", record)
	}
}
