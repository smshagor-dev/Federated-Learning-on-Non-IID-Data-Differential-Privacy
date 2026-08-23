package execution

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
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

	deadline := time.Now().Add(3 * time.Second)
	for {
		snapshot, getErr := driver.Get(context.Background(), "exec-local-1")
		if getErr != nil {
			t.Fatal(getErr)
		}
		if snapshot.Status == StatusCompleted {
			if snapshot.CurrentRound != uint64(spec.Federation.Rounds) {
				t.Fatalf("completed round = %d", snapshot.CurrentRound)
			}
			break
		}
		if snapshot.Status == StatusFailed {
			t.Fatal("local driver reported failed subprocess")
		}
		if time.Now().After(deadline) {
			t.Fatalf("local subprocess did not complete, status=%s", snapshot.Status)
		}
		time.Sleep(10 * time.Millisecond)
	}
	if _, err := os.Stat(filepath.Join(artifactRoot, "execution-control", "execution-spec.json")); err != nil {
		t.Fatalf("canonical spec was not persisted: %v", err)
	}
	if _, err := os.Stat(filepath.Join(artifactRoot, "summary.json")); err != nil {
		t.Fatalf("helper summary was not persisted: %v", err)
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
