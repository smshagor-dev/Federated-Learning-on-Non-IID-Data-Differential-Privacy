package execution

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type localRunState struct {
	ExecutionID     string `json:"execution_id"`
	Spec            Spec   `json:"spec"`
	SpecPath        string `json:"spec_path"`
	LogPath         string `json:"log_path"`
	Status          Status `json:"status"`
	ModelVersion    string `json:"model_version"`
	PID             int    `json:"pid,omitempty"`
	LastError       string `json:"last_error,omitempty"`
	CancelRequested bool   `json:"cancel_requested,omitempty"`
}

func (d *LocalDriver) statePath(executionID string) string {
	digest := sha256.Sum256([]byte(executionID))
	return filepath.Join(d.stateRoot, hex.EncodeToString(digest[:])+".json")
}

func (d *LocalDriver) persistRunLocked(executionID string, run *localRun) error {
	if d == nil || run == nil {
		return errors.New("local execution state is unavailable")
	}
	state := localRunState{
		ExecutionID:     executionID,
		Spec:            run.spec,
		SpecPath:        run.specPath,
		LogPath:         run.logPath,
		Status:          run.status,
		ModelVersion:    run.modelVersion,
		PID:             run.pid,
		CancelRequested: run.cancelRequested,
	}
	if run.lastError != nil {
		state.LastError = run.lastError.Error()
	}
	encoded, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return fmt.Errorf("encode local execution state: %w", err)
	}
	if err := writeAtomic(d.statePath(executionID), append(encoded, '\n'), 0o600); err != nil {
		return fmt.Errorf("persist local execution state: %w", err)
	}
	return nil
}

func (d *LocalDriver) loadPersistedRuns() error {
	entries, err := os.ReadDir(d.stateRoot)
	if err != nil {
		return fmt.Errorf("read local execution state directory: %w", err)
	}
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		path := filepath.Join(d.stateRoot, entry.Name())
		encoded, readErr := os.ReadFile(path)
		if readErr != nil {
			return fmt.Errorf("read local execution state %s: %w", path, readErr)
		}
		var state localRunState
		if err := json.Unmarshal(encoded, &state); err != nil {
			return fmt.Errorf("decode local execution state %s: %w", path, err)
		}
		if strings.TrimSpace(state.ExecutionID) == "" {
			return fmt.Errorf("local execution state %s has no execution_id", path)
		}
		run := &localRun{
			spec:            state.Spec,
			specPath:        state.SpecPath,
			logPath:         state.LogPath,
			status:          state.Status,
			modelVersion:    state.ModelVersion,
			pid:             state.PID,
			cancelRequested: state.CancelRequested,
		}
		if state.LastError != "" {
			run.lastError = errors.New(state.LastError)
		}
		changed := reconcileRecoveredLocalRun(run)
		d.runs[state.ExecutionID] = run
		if changed {
			if err := d.persistRunLocked(state.ExecutionID, run); err != nil {
				return err
			}
		}
	}
	return nil
}

func reconcileRecoveredLocalRun(run *localRun) bool {
	if run == nil {
		return false
	}
	summaryPath := filepath.Join(run.spec.Artifacts.Root, "summary.json")
	_, summaryErr := os.Stat(summaryPath)
	hasSummary := summaryErr == nil

	// Persisted terminal states are authoritative. A stale summary artifact
	// must never turn a canceled or failed execution into a successful one.
	switch run.status {
	case StatusCanceled:
		changed := run.pid != 0 || run.cancelRequested
		run.pid = 0
		run.cancelRequested = false
		return changed
	case StatusFailed:
		changed := run.pid != 0 || run.cancelRequested
		run.pid = 0
		run.cancelRequested = false
		return changed
	case StatusCompleted:
		if !hasSummary {
			run.status = StatusFailed
			run.pid = 0
			run.cancelRequested = false
			run.lastError = errors.New("persisted local execution was COMPLETED but summary.json is missing")
			return true
		}
		changed := run.pid != 0 || run.lastError != nil || run.cancelRequested
		run.pid = 0
		run.lastError = nil
		run.cancelRequested = false
		if run.modelVersion == "" {
			run.modelVersion = run.spec.Model.Version
			changed = true
		}
		return changed
	case StatusCreated:
		// CREATED remains startable. A pre-existing summary is not sufficient
		// to infer that this particular persisted execution was launched.
		changed := run.pid != 0 || run.cancelRequested
		run.pid = 0
		run.cancelRequested = false
		return changed
	}

	// For an active state, a durable summary proves the child finished after
	// the last state write. Without that evidence, the control plane cannot
	// safely reattach to the process, so fail closed instead of leaving a
	// ghost RUNNING execution.
	switch run.status {
	case StatusStarting, StatusRunning, StatusPausing, StatusPaused, StatusResuming, StatusCanceling:
		if hasSummary {
			run.status = StatusCompleted
			run.pid = 0
			run.lastError = nil
			run.cancelRequested = false
			if run.modelVersion == "" {
				run.modelVersion = run.spec.Model.Version
			}
			return true
		}
		run.status = StatusFailed
		run.pid = 0
		run.cancelRequested = false
		run.lastError = errors.New("control plane restarted while local process state could not be safely reattached")
		return true
	default:
		return false
	}
}
