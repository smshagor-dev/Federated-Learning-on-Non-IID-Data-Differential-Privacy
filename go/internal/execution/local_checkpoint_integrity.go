package execution

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

func verifyLocalCheckpointDigest(checkpointPath string) error {
	digestPath := checkpointPath + ".sha256"
	encoded, err := os.ReadFile(digestPath)
	if err != nil {
		return fmt.Errorf("read checkpoint digest: %w", err)
	}
	expectedText := strings.ToLower(strings.TrimSpace(string(encoded)))
	expected, err := hex.DecodeString(expectedText)
	if err != nil || len(expected) != sha256.Size {
		return errors.New("checkpoint digest file is malformed")
	}

	file, err := os.Open(checkpointPath)
	if err != nil {
		return fmt.Errorf("open checkpoint for digest verification: %w", err)
	}
	defer file.Close()
	digest := sha256.New()
	if _, err := io.Copy(digest, file); err != nil {
		return fmt.Errorf("hash checkpoint: %w", err)
	}
	actual := digest.Sum(nil)
	if len(actual) != len(expected) {
		return errors.New("checkpoint SHA-256 digest mismatch")
	}
	var mismatch byte
	for index := range actual {
		mismatch |= actual[index] ^ expected[index]
	}
	if mismatch != 0 {
		return errors.New("checkpoint SHA-256 digest mismatch")
	}
	return nil
}
