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

func checkpointHelperCommand(_ string, _ string, specPath string) *exec.Cmd {
	command := exec.Command(os.Args[0], "-test.run=TestCheckpointLocalDriverHelperProcess", "--", specPath)
	command.Env = append(os.Environ(), "FL_CHECKPOINT_LOCAL_HELPER=1")
	return command
}

func TestCheckpointLocalDriverPauseRestartResume(t *testing.T) {
	root := localTestRepository(t)
	stateRoot := filepath.Join(root, "control-state")
	artifactRoot := filepath.Join(root, "artifacts", "checkpoint-run")
	spec := localDriverSpec(artifactRoot)
	base, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		StateRoot:      stateRoot,
		CommandFactory: checkpointHelperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	driver := EnableCheckpointLifecycle(base)
	created, err := driver.Create(context.Background(), "exec-checkpoint", spec, "")
	if err != nil || created.Status != StatusCreated {
		t.Fatalf("create snapshot=%#v err=%v", created, err)
	}
	started, err := driver.Start(context.Background(), "exec-checkpoint", "")
	if err != nil || started.Status != StatusRunning {
		t.Fatalf("start snapshot=%#v err=%v", started, err)
	}
	paused, err := driver.Pause(context.Background(), "exec-checkpoint", "operator", "")
	if err != nil {
		t.Fatal(err)
	}
	if paused.Status != StatusPaused || paused.CurrentRound != 1 {
		t.Fatalf("paused snapshot=%#v", paused)
	}

	// A fresh driver instance must recover the durable PAUSED state rather than
	// converting it to FAILED simply because the original child is gone.
	restartedBase, err := NewLocalDriver(LocalDriverConfig{
		RepositoryRoot: root,
		StateRoot:      stateRoot,
		CommandFactory: checkpointHelperCommand,
	})
	if err != nil {
		t.Fatal(err)
	}
	restarted := EnableCheckpointLifecycle(restartedBase)
	recovered, err := restarted.Get(context.Background(), "exec-checkpoint")
	if err != nil {
		t.Fatal(err)
	}
	if recovered.Status != StatusPaused || recovered.CurrentRound != 1 {
		t.Fatalf("recovered snapshot=%#v", recovered)
	}

	resumed, err := restarted.Resume(context.Background(), "exec-checkpoint", "")
	if err != nil || resumed.Status != StatusRunning || resumed.CurrentRound != 1 {
		t.Fatalf("resume snapshot=%#v err=%v", resumed, err)
	}
	deadline := time.Now().Add(3 * time.Second)
	for {
		snapshot, getErr := restarted.Get(context.Background(), "exec-checkpoint")
		if getErr != nil {
			t.Fatal(getErr)
		}
		if snapshot.Status == StatusCompleted {
			if snapshot.CurrentRound != uint64(spec.Federation.Rounds) {
				t.Fatalf("completed round=%d", snapshot.CurrentRound)
			}
			break
		}
		if snapshot.Status == StatusFailed {
			t.Fatalf("resumed execution failed: %#v", snapshot)
		}
		if time.Now().After(deadline) {
			t.Fatalf("resumed execution did not complete, status=%s", snapshot.Status)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestCheckpointLocalDriverHelperProcess(t *testing.T) {
	if os.Getenv("FL_CHECKPOINT_LOCAL_HELPER") == "" {
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
	encoded, err := os.ReadFile(os.Args[separator+1])
	if err != nil {
		os.Exit(3)
	}
	var spec Spec
	if err := json.Unmarshal(encoded, &spec); err != nil {
		os.Exit(4)
	}
	controlDir := filepath.Join(spec.Artifacts.Root, "execution-control")
	if err := os.MkdirAll(controlDir, 0o755); err != nil {
		os.Exit(5)
	}
	if _, err := os.Stat(filepath.Join(controlDir, "resume.request")); err == nil {
		if err := os.WriteFile(filepath.Join(spec.Artifacts.Root, "summary.json"), []byte("{}\n"), 0o600); err != nil {
			os.Exit(6)
		}
		os.Exit(0)
	}

	deadline := time.Now().Add(2 * time.Second)
	pausePath := filepath.Join(controlDir, "pause.request")
	for {
		if _, err := os.Stat(pausePath); err == nil {
			checkpointPath := filepath.Join(controlDir, "runtime-checkpoint.pt")
			if err := os.WriteFile(checkpointPath, []byte("checkpoint\n"), 0o600); err != nil {
				os.Exit(7)
			}
			marker := localPausedMarker{
				SchemaVersion:   1,
				Status:          "PAUSED",
				Algorithm:       spec.Algorithm.Name,
				RoundsCompleted: 1,
				CheckpointPath:  checkpointPath,
			}
			markerBytes, marshalErr := json.Marshal(marker)
			if marshalErr != nil {
				os.Exit(8)
			}
			if err := os.WriteFile(filepath.Join(controlDir, "paused.json"), append(markerBytes, '\n'), 0o600); err != nil {
				os.Exit(9)
			}
			os.Exit(localPausedExitCode)
		}
		if time.Now().After(deadline) {
			os.Exit(10)
		}
		time.Sleep(10 * time.Millisecond)
	}
}
