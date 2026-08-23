package execution

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func localDriverSpec(artifactRoot string) Spec {
	return Spec{
		SchemaVersion: CurrentSchemaVersion,
		Name:          "local-driver-smoke",
		Backend:       BackendLocal,
		Dataset: DatasetSpec{
			Name:      "MNIST",
			Partition: PartitionSpec{Strategy: "iid", MinimumClientSize: 1},
		},
		Model: ModelSpec{
			Name:             "root-cnn",
			Version:          "v1",
			ArchitectureName: "cnn",
			UpdateFormat:     "state_dict_delta",
			Tensors:          []TensorSpec{{Name: "weight", Shape: []uint64{2, 2}}},
			Aggregation: AggregationManifest{
				SharedParameterNames: []string{"weight"},
			},
		},
		Algorithm: AlgorithmSpec{Name: "fedavg"},
		Optimizer: OptimizerSpec{LearningRate: 0.01, ServerLR: 1},
		Federation: FederationSpec{
			TotalClients:          4,
			TargetClientsPerRound: 2,
			MinimumValidResults:   1,
			Rounds:                2,
			LocalEpochs:           1,
			BatchSize:             8,
			Weighting:             "uniform",
			SamplingStrategy:      SamplingFixedWithoutReplacement,
			ClientSelectionSeed:   17,
			SchedulingMode:        SchedulingSynchronous,
			RoundTimeoutSeconds:   30,
			TaskLeaseSeconds:      15,
			MaxTaskRetries:        1,
		},
		Privacy: PrivacySpec{Mode: PrivacyNone},
		Evaluation: EvaluationSpec{
			EvaluateGlobal:      true,
			EvaluatePerClient:   true,
			EvaluateFairness:    true,
			EvaluationBatchSize: 32,
		},
		Artifacts: ArtifactSpec{Root: artifactRoot, PersistCheckpoints: true, PersistEvents: true},
	}
}

func localTestRepository(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	if err := os.WriteFile(filepath.Join(root, "main.py"), []byte("# test placeholder\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "scripts", "run_local_execution.py"), []byte("# test placeholder\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return root
}

func helperCommand(_ string, _ string, specPath string) *exec.Cmd {
	command := exec.Command(os.Args[0], "-test.run=TestLocalDriverHelperProcess", "--", specPath)
	command.Env = append(os.Environ(), "FL_LOCAL_DRIVER_HELPER=success")
	return command
}

func waitForLocalDriverStatus(t *testing.T, driver *LocalDriver, executionID string, expected Status) Snapshot {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for {
		snapshot, err := driver.Get(context.Background(), executionID)
		if err != nil {
			t.Fatal(err)
		}
		if snapshot.Status == expected {
			return snapshot
		}
		if snapshot.Status == StatusFailed && expected != StatusFailed {
			t.Fatalf("local driver reported failed while waiting for %s", expected)
		}
		if time.Now().After(deadline) {
			t.Fatalf("local driver did not reach %s, status=%s", expected, snapshot.Status)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestLocalDriverRunsSubprocessAndRequiresSummary(t *testing.T) {
	root := localTestRepository(t)
	driver, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		CommandFactory: helperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	artifactRoot := filepath.Join(root, "artifacts", "run-1")
	spec := localDriverSpec(artifactRoot)
	created, err := driver.Create(context.Background(), "exec-local-1", spec, "")
	if err != nil {
		t.Fatal(err)
	}
	if created.Status != StatusCreated {
		t.Fatalf("create status = %s", created.Status)
	}
	started, err := driver.Start(context.Background(), "exec-local-1", "")
	if err != nil {
		t.Fatal(err)
	}
	if started.Status != StatusRunning {
		t.Fatalf("start status = %s", started.Status)
	}

	snapshot := waitForLocalDriverStatus(t, driver, "exec-local-1", StatusCompleted)
	if snapshot.CurrentRound != uint64(spec.Federation.Rounds) {
		t.Fatalf("completed round = %d", snapshot.CurrentRound)
	}
	if _, err := os.Stat(filepath.Join(artifactRoot, "execution-control", "execution-spec.json")); err != nil {
		t.Fatalf("canonical spec was not persisted: %v", err)
	}
	if _, err := os.Stat(filepath.Join(artifactRoot, "summary.json")); err != nil {
		t.Fatalf("helper summary was not persisted: %v", err)
	}
}

func TestLocalDriverRestoresCompletedRunAfterRestart(t *testing.T) {
	root := localTestRepository(t)
	stateRoot := filepath.Join(t.TempDir(), "local-state")
	artifactRoot := filepath.Join(root, "artifacts", "restart-complete")
	driver, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		StateRoot:      stateRoot,
		CommandFactory: helperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := driver.Create(context.Background(), "exec-restart-complete", localDriverSpec(artifactRoot), ""); err != nil {
		t.Fatal(err)
	}
	if _, err := driver.Start(context.Background(), "exec-restart-complete", ""); err != nil {
		t.Fatal(err)
	}
	waitForLocalDriverStatus(t, driver, "exec-restart-complete", StatusCompleted)

	restarted, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		StateRoot:      stateRoot,
		CommandFactory: helperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := restarted.Get(context.Background(), "exec-restart-complete")
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Status != StatusCompleted || snapshot.CurrentRound != 2 {
		t.Fatalf("recovered snapshot = %#v", snapshot)
	}
}

func TestLocalDriverFailsUnreattachableRunningStateAfterRestart(t *testing.T) {
	root := localTestRepository(t)
	stateRoot := filepath.Join(t.TempDir(), "local-state")
	driver, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		StateRoot:      stateRoot,
		CommandFactory: helperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	executionID := "exec-restart-running"
	artifactRoot := filepath.Join(root, "artifacts", "restart-running")
	if _, err := driver.Create(context.Background(), executionID, localDriverSpec(artifactRoot), ""); err != nil {
		t.Fatal(err)
	}
	driver.mu.Lock()
	run := driver.runs[executionID]
	run.status = StatusRunning
	run.pid = 424242
	if err := driver.persistRunLocked(executionID, run); err != nil {
		driver.mu.Unlock()
		t.Fatal(err)
	}
	driver.mu.Unlock()

	restarted, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		StateRoot:      stateRoot,
		CommandFactory: helperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	snapshot, err := restarted.Get(context.Background(), executionID)
	if err != nil {
		t.Fatal(err)
	}
	if snapshot.Status != StatusFailed {
		t.Fatalf("recovered status = %s, want FAILED", snapshot.Status)
	}
	restarted.mu.RLock()
	lastError := restarted.runs[executionID].lastError
	restarted.mu.RUnlock()
	if lastError == nil || !strings.Contains(lastError.Error(), "could not be safely reattached") {
		t.Fatalf("recovery error = %v", lastError)
	}
}

func TestLocalDriverRejectsDPWithFixedSampling(t *testing.T) {
	root := localTestRepository(t)
	driver, err := NewLocalDriver(LocalDriverConfig{RepositoryRoot: root, CommandFactory: helperCommand})
	if err != nil {
		t.Fatal(err)
	}
	spec := localDriverSpec(filepath.Join(root, "artifacts", "bad"))
	spec.Privacy = PrivacySpec{
		Mode: PrivacyUserLevel,
		UserLevel: UserLevelPrivacySpec{
			NoiseMultiplier:      1.2,
			TargetDelta:          1e-5,
			Accountant:           "rdp",
			InitialClippingBound: 1,
			WeightingStrategy:    "uniform",
		},
	}
	if _, err := driver.Create(context.Background(), "exec-local-bad", spec, ""); err == nil {
		t.Fatal("expected local DP with fixed sampling to be rejected")
	}
}

func TestLocalDriverRejectsUnsupportedPrivacyBudgetSemantics(t *testing.T) {
	root := localTestRepository(t)
	driver, err := NewLocalDriver(LocalDriverConfig{RepositoryRoot: root, CommandFactory: helperCommand})
	if err != nil {
		t.Fatal(err)
	}
	spec := localDriverSpec(filepath.Join(root, "artifacts", "budget"))
	spec.Federation.SamplingStrategy = SamplingPoisson
	spec.Privacy = PrivacySpec{
		Mode: PrivacyUserLevel,
		UserLevel: UserLevelPrivacySpec{
			NoiseMultiplier:      1.2,
			TargetDelta:          1e-5,
			Accountant:           "rdp",
			InitialClippingBound: 1,
			WeightingStrategy:    "uniform",
			EpsilonBudget:        4,
		},
	}
	_, err = driver.Create(context.Background(), "exec-local-budget", spec, "")
	if err == nil || !strings.Contains(err.Error(), "epsilon_budget") {
		t.Fatalf("expected epsilon_budget rejection, got %v", err)
	}
}

func TestLocalDriverHelperProcess(t *testing.T) {
	mode := os.Getenv("FL_LOCAL_DRIVER_HELPER")
	if mode == "" {
		return
	}
	separator := -1
	for index, value := range os.Args {
		if value == "--" {
			separator = index
			break
		}
	}
	if separator < 0 || separator+1 >= len(os.Args) {
		os.Exit(2)
	}
	specPath := os.Args[separator+1]
	encoded, err := os.ReadFile(specPath)
	if err != nil {
		os.Exit(3)
	}
	var spec Spec
	if err := json.Unmarshal(encoded, &spec); err != nil {
		os.Exit(4)
	}
	if err := os.MkdirAll(spec.Artifacts.Root, 0o755); err != nil {
		os.Exit(5)
	}
	if mode == "success" {
		if err := os.WriteFile(filepath.Join(spec.Artifacts.Root, "summary.json"), []byte("{}\n"), 0o600); err != nil {
			os.Exit(6)
		}
		os.Exit(0)
	}
	os.Exit(7)
}
