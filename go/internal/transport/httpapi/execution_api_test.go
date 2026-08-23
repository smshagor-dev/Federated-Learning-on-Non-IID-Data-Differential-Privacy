package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

type executionAPIDriver struct{}

func (executionAPIDriver) Create(_ context.Context, executionID string, _ executiondomain.Spec, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: executionID, Status: executiondomain.StatusCreated, MaxRounds: 2, ModelVersion: "v0"}, nil
}
func (executionAPIDriver) Start(_ context.Context, backendRunID, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusRunning, CurrentRound: 1, MaxRounds: 2, ModelVersion: "v1"}, nil
}
func (executionAPIDriver) Pause(_ context.Context, backendRunID, _, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusPaused, CurrentRound: 1, MaxRounds: 2, ModelVersion: "v1"}, nil
}
func (executionAPIDriver) Resume(_ context.Context, backendRunID, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusRunning, CurrentRound: 1, MaxRounds: 2, ModelVersion: "v1"}, nil
}
func (executionAPIDriver) Cancel(_ context.Context, backendRunID, _, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusCanceled, CurrentRound: 1, MaxRounds: 2, ModelVersion: "v1"}, nil
}
func (executionAPIDriver) Get(_ context.Context, backendRunID string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{BackendRunID: backendRunID, Status: executiondomain.StatusRunning, CurrentRound: 1, MaxRounds: 2, ModelVersion: "v1", RegisteredWorkers: 2, HealthyWorkers: 2}, nil
}
func (executionAPIDriver) ListWorkers(_ context.Context) ([]executiondomain.Worker, error) {
	return []executiondomain.Worker{{WorkerID: "worker-1", Status: "IDLE"}}, nil
}

func executionAPISpec() executiondomain.Spec {
	return executiondomain.Spec{
		SchemaVersion: executiondomain.CurrentSchemaVersion,
		Name:          "api-smoke",
		Backend:       executiondomain.BackendDistributed,
		Dataset: executiondomain.DatasetSpec{
			Name:      "MNIST",
			Partition: executiondomain.PartitionSpec{Strategy: "iid", MinimumClientSize: 1},
		},
		Model: executiondomain.ModelSpec{
			Name:         "tiny",
			Version:      "v1",
			UpdateFormat: "state_dict_delta",
			Tensors:      []executiondomain.TensorSpec{{Name: "weight", Shape: []uint64{2, 2}}},
			Aggregation: executiondomain.AggregationManifest{
				SharedParameterNames: []string{"weight"},
			},
		},
		Algorithm: executiondomain.AlgorithmSpec{Name: "fedavg"},
		Optimizer: executiondomain.OptimizerSpec{LearningRate: 0.01, ServerLR: 1},
		Federation: executiondomain.FederationSpec{
			TotalClients:          2,
			ClientIDs:             []string{"client-1", "client-2"},
			TargetClientsPerRound: 2,
			MinimumValidResults:   1,
			Rounds:                2,
			LocalEpochs:           1,
			BatchSize:             8,
			Weighting:             "uniform",
			SchedulingMode:        executiondomain.SchedulingSynchronous,
			RoundTimeoutSeconds:   30,
			TaskLeaseSeconds:      15,
			MaxTaskRetries:        1,
		},
		Privacy: executiondomain.PrivacySpec{Mode: executiondomain.PrivacyNone},
		Evaluation: executiondomain.EvaluationSpec{
			EvaluateGlobal:      true,
			EvaluatePerClient:   true,
			EvaluateFairness:    true,
			EvaluationBatchSize: 32,
		},
		Artifacts: executiondomain.ArtifactSpec{Root: "artifacts/api-smoke", PersistEvents: true},
	}
}

func TestExecutionAPIAuthenticatedCreateStartAndEvents(t *testing.T) {
	services := application.NewServices(
		projects.NewInMemoryRepository(),
		experiments.NewInMemoryRepository(),
		runs.NewInMemoryRepository(),
		testClock,
	)
	services.Auth.SetTokenSourceForTesting(func() (string, error) { return "execution-token", nil })
	session, err := services.Auth.Login(context.Background(), "admin@fl-platform.dev", "admin-demo")
	if err != nil {
		t.Fatal(err)
	}
	journal, err := executiondomain.NewJournal(filepath.Join(t.TempDir(), "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	engine := application.NewExecutionService(
		executiondomain.NewInMemoryRepository(),
		executiondomain.DriverRegistry{executiondomain.BackendDistributed: executionAPIDriver{}},
		journal,
		services.Experiments,
		testClock,
		services.Audit,
	)
	application.SetExecutionEngineForTests(services, engine)
	defer application.ClearExecutionEngineForTests(services)

	handler := WithExecutionAPI(NewServer(services).Handler(), services)
	body, err := json.Marshal(map[string]any{"spec": executionAPISpec()})
	if err != nil {
		t.Fatal(err)
	}
	createRequest := httptest.NewRequest(http.MethodPost, executionPrefix, bytes.NewReader(body))
	createRequest.Header.Set("Authorization", "Bearer "+session.Token)
	createRequest.Header.Set("Content-Type", "application/json")
	createRecorder := httptest.NewRecorder()
	handler.ServeHTTP(createRecorder, createRequest)
	if createRecorder.Code != http.StatusCreated {
		t.Fatalf("create status=%d body=%s", createRecorder.Code, createRecorder.Body.String())
	}
	var created executiondomain.Record
	if err := json.Unmarshal(createRecorder.Body.Bytes(), &created); err != nil {
		t.Fatal(err)
	}
	if created.ID == "" || created.Status != executiondomain.StatusCreated {
		t.Fatalf("created=%#v", created)
	}

	startRequest := httptest.NewRequest(http.MethodPost, executionPrefix+"/"+created.ID+"/start", nil)
	startRequest.Header.Set("Authorization", "Bearer "+session.Token)
	startRequest.Header.Set("X-Trace-Id", "trace-api-start")
	startRecorder := httptest.NewRecorder()
	handler.ServeHTTP(startRecorder, startRequest)
	if startRecorder.Code != http.StatusOK {
		t.Fatalf("start status=%d body=%s", startRecorder.Code, startRecorder.Body.String())
	}
	var started executiondomain.Record
	if err := json.Unmarshal(startRecorder.Body.Bytes(), &started); err != nil {
		t.Fatal(err)
	}
	if started.Status != executiondomain.StatusRunning || started.BackendRunID == "" {
		t.Fatalf("started=%#v", started)
	}

	eventsRequest := httptest.NewRequest(http.MethodGet, executionPrefix+"/"+created.ID+"/events", nil)
	eventsRequest.Header.Set("Authorization", "Bearer "+session.Token)
	eventsRecorder := httptest.NewRecorder()
	handler.ServeHTTP(eventsRecorder, eventsRequest)
	if eventsRecorder.Code != http.StatusOK {
		t.Fatalf("events status=%d body=%s", eventsRecorder.Code, eventsRecorder.Body.String())
	}
	var events []executiondomain.Event
	if err := json.Unmarshal(eventsRecorder.Body.Bytes(), &events); err != nil {
		t.Fatal(err)
	}
	if len(events) < 4 {
		t.Fatalf("events=%#v", events)
	}
}

func TestExecutionAPIDelegatesExistingRoutes(t *testing.T) {
	server := testServer()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	WithExecutionAPI(server.Handler(), server.services).ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", recorder.Code, recorder.Body.String())
	}
}
