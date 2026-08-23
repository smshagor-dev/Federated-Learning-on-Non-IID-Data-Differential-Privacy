package execution

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const localPausedExitCode = 75

type localPausedMarker struct {
	SchemaVersion   int    `json:"schema_version"`
	Status          string `json:"status"`
	Algorithm       string `json:"algorithm"`
	RoundsCompleted uint64 `json:"rounds_completed"`
	CheckpointPath  string `json:"checkpoint_path"`
}

type checkpointLocalDriver struct {
	base *LocalDriver
}

// EnableCheckpointLifecycle upgrades the concrete local process driver with
// round-boundary pause/resume semantics. Other Driver implementations are
// returned unchanged.
func EnableCheckpointLifecycle(driver Driver) Driver {
	base, ok := driver.(*LocalDriver)
	if !ok || base == nil {
		return driver
	}
	return &checkpointLocalDriver{base: base}
}

func (d *checkpointLocalDriver) Create(ctx context.Context, executionID string, spec Spec, traceID string) (Snapshot, error) {
	return d.base.Create(ctx, executionID, spec, traceID)
}

func (d *checkpointLocalDriver) Start(_ context.Context, backendRunID, _ string) (Snapshot, error) {
	return d.launch(backendRunID, StatusCreated, false)
}

func (d *checkpointLocalDriver) Pause(ctx context.Context, backendRunID, reason, _ string) (Snapshot, error) {
	if d == nil || d.base == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	select {
	case <-ctx.Done():
		return Snapshot{}, ctx.Err()
	default:
	}

	d.base.mu.Lock()
	run, ok := d.base.runs[backendRunID]
	if !ok {
		d.base.mu.Unlock()
		return Snapshot{}, errors.New("local execution run not found")
	}
	if run.status != StatusRunning {
		status := run.status
		d.base.mu.Unlock()
		return Snapshot{}, fmt.Errorf("%w: cannot pause local run from %s", ErrUnsupportedMapping, status)
	}
	if !run.spec.Artifacts.PersistCheckpoints {
		d.base.mu.Unlock()
		return Snapshot{}, fmt.Errorf("%w: local pause requires artifacts.persist_checkpoints=true", ErrUnsupportedMapping)
	}
	if run.command == nil || run.command.Process == nil {
		d.base.mu.Unlock()
		return Snapshot{}, errors.New("local execution process is not attached")
	}
	pauseRequest := localControlPath(run, "pause.request")
	payload, err := json.Marshal(map[string]any{
		"schema_version": 1,
		"reason":         reason,
		"requested_at":   time.Now().UTC().Format(time.RFC3339Nano),
	})
	if err != nil {
		d.base.mu.Unlock()
		return Snapshot{}, err
	}
	if err := writeAtomic(pauseRequest, append(payload, '\n'), 0o600); err != nil {
		d.base.mu.Unlock()
		return Snapshot{}, fmt.Errorf("persist local pause request: %w", err)
	}
	run.status = StatusPausing
	run.lastError = nil
	if err := d.base.persistRunLocked(backendRunID, run); err != nil {
		_ = os.Remove(pauseRequest)
		run.status = StatusRunning
		d.base.mu.Unlock()
		return Snapshot{}, err
	}
	d.base.mu.Unlock()

	// Once the pause request is durable, wait for the child to acknowledge it
	// at a round boundary. Client cancellation must not rewrite backend truth to
	// RUNNING while the already-issued pause is still being processed.
	for {
		time.Sleep(20 * time.Millisecond)
		d.base.mu.RLock()
		run = d.base.runs[backendRunID]
		status := run.status
		snapshot := d.snapshotLocked(backendRunID, run)
		lastErr := run.lastError
		d.base.mu.RUnlock()
		switch status {
		case StatusPaused:
			return snapshot, nil
		case StatusFailed:
			if lastErr != nil {
				return snapshot, lastErr
			}
			return snapshot, errors.New("local execution failed while pausing")
		case StatusCanceled, StatusCompleted:
			return snapshot, fmt.Errorf("local execution reached %s while pausing", status)
		}
	}
}

func (d *checkpointLocalDriver) Resume(_ context.Context, backendRunID, _ string) (Snapshot, error) {
	if d == nil || d.base == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	d.base.mu.RLock()
	run, ok := d.base.runs[backendRunID]
	if !ok {
		d.base.mu.RUnlock()
		return Snapshot{}, errors.New("local execution run not found")
	}
	_, evidenceErr := readLocalPauseEvidence(run)
	d.base.mu.RUnlock()
	if evidenceErr != nil {
		return Snapshot{}, fmt.Errorf("resume local execution: %w", evidenceErr)
	}
	return d.launch(backendRunID, StatusPaused, true)
}

func (d *checkpointLocalDriver) Cancel(ctx context.Context, backendRunID, reason, traceID string) (Snapshot, error) {
	return d.base.Cancel(ctx, backendRunID, reason, traceID)
}

func (d *checkpointLocalDriver) Get(_ context.Context, backendRunID string) (Snapshot, error) {
	if d == nil || d.base == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	d.base.mu.RLock()
	defer d.base.mu.RUnlock()
	run, ok := d.base.runs[backendRunID]
	if !ok {
		return Snapshot{}, errors.New("local execution run not found")
	}
	return d.snapshotLocked(backendRunID, run), nil
}

func (d *checkpointLocalDriver) ListWorkers(ctx context.Context) ([]Worker, error) {
	return d.base.ListWorkers(ctx)
}

func (d *checkpointLocalDriver) launch(backendRunID string, expected Status, resume bool) (Snapshot, error) {
	if d == nil || d.base == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	d.base.mu.Lock()
	run, ok := d.base.runs[backendRunID]
	if !ok {
		d.base.mu.Unlock()
		return Snapshot{}, errors.New("local execution run not found")
	}
	if run.status == StatusRunning {
		snapshot := d.snapshotLocked(backendRunID, run)
		d.base.mu.Unlock()
		return snapshot, nil
	}
	if run.status != expected {
		status := run.status
		d.base.mu.Unlock()
		return Snapshot{}, fmt.Errorf("%w: cannot launch local run from %s", ErrUnsupportedMapping, status)
	}
	if resume {
		if _, err := readLocalPauseEvidence(run); err != nil {
			d.base.mu.Unlock()
			return Snapshot{}, err
		}
		resumePayload := []byte("{\"schema_version\":1}\n")
		if err := writeAtomic(localControlPath(run, "resume.request"), resumePayload, 0o600); err != nil {
			d.base.mu.Unlock()
			return Snapshot{}, fmt.Errorf("persist local resume request: %w", err)
		}
		_ = os.Remove(localControlPath(run, "pause.request"))
	} else {
		_ = os.Remove(localControlPath(run, "resume.request"))
		_ = os.Remove(localControlPath(run, "pause.request"))
		_ = os.Remove(localControlPath(run, "paused.json"))
	}

	logFile, err := os.OpenFile(run.logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		d.base.mu.Unlock()
		return Snapshot{}, fmt.Errorf("open local execution log: %w", err)
	}
	command := d.base.commandFor(run.specPath)
	command.Dir = d.base.config.RepositoryRoot
	command.Stdout = logFile
	command.Stderr = logFile
	if err := command.Start(); err != nil {
		_ = logFile.Close()
		d.base.mu.Unlock()
		return Snapshot{}, fmt.Errorf("start local execution process: %w", err)
	}
	run.command = command
	run.logFile = logFile
	run.pid = command.Process.Pid
	run.status = StatusRunning
	run.lastError = nil
	run.cancelRequested = false
	if err := d.base.persistRunLocked(backendRunID, run); err != nil {
		_ = command.Process.Kill()
		_ = command.Wait()
		_ = logFile.Close()
		run.command = nil
		run.logFile = nil
		run.pid = 0
		run.status = expected
		d.base.mu.Unlock()
		return Snapshot{}, err
	}
	snapshot := d.snapshotLocked(backendRunID, run)
	d.base.mu.Unlock()
	go d.waitForRun(backendRunID, command)
	return snapshot, nil
}

func (d *checkpointLocalDriver) waitForRun(backendRunID string, command interface{ Wait() error }) {
	err := command.Wait()
	d.base.mu.Lock()
	defer d.base.mu.Unlock()
	run, ok := d.base.runs[backendRunID]
	if !ok {
		return
	}
	if run.logFile != nil {
		_ = run.logFile.Close()
		run.logFile = nil
	}
	run.command = nil
	run.pid = 0
	if run.cancelRequested || run.status == StatusCanceled {
		run.status = StatusCanceled
		run.lastError = nil
	} else if isPausedExit(err) {
		marker, markerErr := readLocalPauseEvidence(run)
		if markerErr != nil {
			run.status = StatusFailed
			run.lastError = markerErr
		} else {
			run.status = StatusPaused
			run.lastError = nil
			if marker.RoundsCompleted >= uint64(run.spec.Federation.Rounds) {
				run.status = StatusFailed
				run.lastError = errors.New("paused marker cannot represent a fully completed execution")
			}
		}
	} else if err != nil {
		run.status = StatusFailed
		run.lastError = err
	} else {
		summaryPath := filepath.Join(run.spec.Artifacts.Root, "summary.json")
		if _, statErr := os.Stat(summaryPath); statErr != nil {
			run.status = StatusFailed
			run.lastError = fmt.Errorf("local execution exited successfully without summary.json: %w", statErr)
		} else {
			run.status = StatusCompleted
			run.modelVersion = run.spec.Model.Version
			run.lastError = nil
			_ = os.Remove(localControlPath(run, "pause.request"))
		}
	}
	if persistErr := d.base.persistRunLocked(backendRunID, run); persistErr != nil {
		run.status = StatusFailed
		run.lastError = persistErr
	}
}

func (d *checkpointLocalDriver) snapshotLocked(backendRunID string, run *localRun) Snapshot {
	snapshot := localSnapshot(backendRunID, run)
	if run == nil {
		return snapshot
	}
	if marker, err := readLocalPauseEvidence(run); err == nil {
		if run.status == StatusPaused || run.status == StatusPausing || run.status == StatusRunning || run.status == StatusResuming {
			snapshot.CurrentRound = marker.RoundsCompleted
		}
	}
	return snapshot
}

func localControlPath(run *localRun, name string) string {
	return filepath.Join(run.spec.Artifacts.Root, "execution-control", name)
}

func readLocalPauseEvidence(run *localRun) (localPausedMarker, error) {
	if run == nil {
		return localPausedMarker{}, errors.New("local execution state is unavailable")
	}
	markerPath := localControlPath(run, "paused.json")
	encoded, err := os.ReadFile(markerPath)
	if err != nil {
		return localPausedMarker{}, fmt.Errorf("read paused marker: %w", err)
	}
	var marker localPausedMarker
	if err := json.Unmarshal(encoded, &marker); err != nil {
		return localPausedMarker{}, fmt.Errorf("decode paused marker: %w", err)
	}
	if marker.SchemaVersion != 1 || !strings.EqualFold(marker.Status, "PAUSED") {
		return localPausedMarker{}, errors.New("paused marker has an unsupported schema or status")
	}
	if marker.RoundsCompleted == 0 || marker.RoundsCompleted >= uint64(run.spec.Federation.Rounds) {
		return localPausedMarker{}, errors.New("paused marker rounds_completed is outside the resumable range")
	}
	if !strings.EqualFold(marker.Algorithm, run.spec.Algorithm.Name) {
		return localPausedMarker{}, errors.New("paused marker algorithm does not match execution spec")
	}
	expectedCheckpoint := localControlPath(run, "runtime-checkpoint.pt")
	markerCheckpoint, err := filepath.Abs(marker.CheckpointPath)
	if err != nil {
		return localPausedMarker{}, fmt.Errorf("resolve paused checkpoint path: %w", err)
	}
	expectedCheckpoint, err = filepath.Abs(expectedCheckpoint)
	if err != nil {
		return localPausedMarker{}, fmt.Errorf("resolve expected checkpoint path: %w", err)
	}
	if filepath.Clean(markerCheckpoint) != filepath.Clean(expectedCheckpoint) {
		return localPausedMarker{}, errors.New("paused marker checkpoint path does not match execution control path")
	}
	if _, err := os.Stat(expectedCheckpoint); err != nil {
		return localPausedMarker{}, fmt.Errorf("paused checkpoint is unavailable: %w", err)
	}
	return marker, nil
}

func isPausedExit(err error) bool {
	if err == nil {
		return false
	}
	var exitErr interface{ ExitCode() int }
	return errors.As(err, &exitErr) && exitErr.ExitCode() == localPausedExitCode
}
