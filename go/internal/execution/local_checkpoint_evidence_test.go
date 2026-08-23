package execution

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestPausedEvidenceAllowsFinalTrainingBoundary(t *testing.T) {
	artifactRoot := t.TempDir()
	run := &localRun{
		spec:   localDriverSpec(artifactRoot),
		status: StatusPaused,
	}
	controlDir := filepath.Join(artifactRoot, "execution-control")
	if err := os.MkdirAll(controlDir, 0o755); err != nil {
		t.Fatal(err)
	}
	checkpointPath := filepath.Join(controlDir, "runtime-checkpoint.pt")
	checkpointBytes := []byte("checkpoint\n")
	if err := os.WriteFile(checkpointPath, checkpointBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(checkpointBytes)
	if err := os.WriteFile(
		checkpointPath+".sha256",
		[]byte(hex.EncodeToString(digest[:])+"\n"),
		0o600,
	); err != nil {
		t.Fatal(err)
	}
	marker := localPausedMarker{
		SchemaVersion:   1,
		Status:          "PAUSED",
		Algorithm:       run.spec.Algorithm.Name,
		RoundsCompleted: uint64(run.spec.Federation.Rounds),
		CheckpointPath:  checkpointPath,
	}
	encoded, err := json.Marshal(marker)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(controlDir, "paused.json"), append(encoded, '\n'), 0o600); err != nil {
		t.Fatal(err)
	}

	evidence, err := readLocalPauseEvidence(run)
	if err != nil {
		t.Fatal(err)
	}
	if evidence.RoundsCompleted != uint64(run.spec.Federation.Rounds) {
		t.Fatalf("rounds_completed=%d", evidence.RoundsCompleted)
	}
}
