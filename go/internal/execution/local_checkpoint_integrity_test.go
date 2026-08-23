package execution

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"testing"
)

func TestVerifyLocalCheckpointDigestRejectsTampering(t *testing.T) {
	checkpointPath := filepath.Join(t.TempDir(), "runtime-checkpoint.pt")
	original := []byte("checkpoint-bytes\n")
	if err := os.WriteFile(checkpointPath, original, 0o600); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(original)
	if err := os.WriteFile(checkpointPath+".sha256", []byte(hex.EncodeToString(digest[:])+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := verifyLocalCheckpointDigest(checkpointPath); err != nil {
		t.Fatalf("valid digest rejected: %v", err)
	}
	if err := os.WriteFile(checkpointPath, []byte("tampered\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := verifyLocalCheckpointDigest(checkpointPath); err == nil {
		t.Fatal("expected tampered checkpoint to fail digest verification")
	}
}
