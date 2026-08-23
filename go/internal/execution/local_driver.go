package execution

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
)

type LocalDriverConfig struct {
	RepositoryRoot   string
	PythonExecutable string
	CommandFactory   func(repositoryRoot, pythonExecutable, specPath string) *exec.Cmd
}

type localRun struct {
	spec            Spec
	specPath        string
	logPath         string
	status          Status
	modelVersion    string
	command         *exec.Cmd
	logFile         *os.File
	lastError       error
	cancelRequested bool
}

type LocalDriver struct {
	mu     sync.RWMutex
	config LocalDriverConfig
	runs   map[string]*localRun
}

func NewLocalDriver(config LocalDriverConfig) (*LocalDriver, error) {
	root := strings.TrimSpace(config.RepositoryRoot)
	if root == "" {
		return nil, errors.New("local execution repository root is required")
	}
	absoluteRoot, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("resolve local execution repository root: %w", err)
	}
	info, err := os.Stat(absoluteRoot)
	if err != nil {
		return nil, fmt.Errorf("local execution repository root: %w", err)
	}
	if !info.IsDir() {
		return nil, errors.New("local execution repository root must be a directory")
	}
	if _, err := os.Stat(filepath.Join(absoluteRoot, "main.py")); err != nil {
		return nil, fmt.Errorf("local execution repository root does not contain main.py: %w", err)
	}
	if _, err := os.Stat(filepath.Join(absoluteRoot, "scripts", "run_local_execution.py")); err != nil {
		return nil, fmt.Errorf("local execution adapter script is unavailable: %w", err)
	}
	python := strings.TrimSpace(config.PythonExecutable)
	if python == "" {
		python = "python3"
	}
	config.RepositoryRoot = absoluteRoot
	config.PythonExecutable = python
	return &LocalDriver{config: config, runs: map[string]*localRun{}}, nil
}

func (d *LocalDriver) Create(_ context.Context, executionID string, spec Spec, _ string) (Snapshot, error) {
	if d == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	if err := validateLocalMapping(spec); err != nil {
		return Snapshot{}, err
	}
	if strings.TrimSpace(executionID) == "" {
		return Snapshot{}, errors.New("execution id is required")
	}
	artifactRoot := filepath.Clean(spec.Artifacts.Root)
	if !filepath.IsAbs(artifactRoot) {
		artifactRoot = filepath.Join(d.config.RepositoryRoot, artifactRoot)
	}
	artifactRoot, err := filepath.Abs(artifactRoot)
	if err != nil {
		return Snapshot{}, fmt.Errorf("resolve artifact root: %w", err)
	}
	controlDir := filepath.Join(artifactRoot, "execution-control")
	if err := os.MkdirAll(controlDir, 0o755); err != nil {
		return Snapshot{}, fmt.Errorf("create local execution control directory: %w", err)
	}
	canonicalSpec := spec
	canonicalSpec.Artifacts.Root = artifactRoot
	encoded, err := json.MarshalIndent(canonicalSpec, "", "  ")
	if err != nil {
		return Snapshot{}, fmt.Errorf("encode canonical execution spec: %w", err)
	}
	specPath := filepath.Join(controlDir, "execution-spec.json")
	if err := writeAtomic(specPath, append(encoded, '\n'), 0o600); err != nil {
		return Snapshot{}, fmt.Errorf("persist canonical execution spec: %w", err)
	}

	d.mu.Lock()
	defer d.mu.Unlock()
	if existing, ok := d.runs[executionID]; ok {
		return localSnapshot(executionID, existing), nil
	}
	d.runs[executionID] = &localRun{
		spec:         canonicalSpec,
		specPath:     specPath,
		logPath:      filepath.Join(controlDir, "local-execution.log"),
		status:       StatusCreated,
		modelVersion: canonicalSpec.Model.Version,
	}
	return localSnapshot(executionID, d.runs[executionID]), nil
}

func (d *LocalDriver) Start(_ context.Context, backendRunID, _ string) (Snapshot, error) {
	if d == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	d.mu.Lock()
	run, ok := d.runs[backendRunID]
	if !ok {
		d.mu.Unlock()
		return Snapshot{}, errors.New("local execution run not found")
	}
	if run.status == StatusRunning {
		snapshot := localSnapshot(backendRunID, run)
		d.mu.Unlock()
		return snapshot, nil
	}
	if run.status != StatusCreated {
		status := run.status
		d.mu.Unlock()
		return Snapshot{}, fmt.Errorf("%w: cannot start local run from %s", ErrUnsupportedMapping, status)
	}

	logFile, err := os.OpenFile(run.logPath, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		d.mu.Unlock()
		return Snapshot{}, fmt.Errorf("open local execution log: %w", err)
	}
	command := d.commandFor(run.specPath)
	command.Dir = d.config.RepositoryRoot
	command.Stdout = logFile
	command.Stderr = logFile
	if err := command.Start(); err != nil {
		_ = logFile.Close()
		d.mu.Unlock()
		return Snapshot{}, fmt.Errorf("start local execution process: %w", err)
	}
	run.command = command
	run.logFile = logFile
	run.status = StatusRunning
	run.lastError = nil
	run.cancelRequested = false
	snapshot := localSnapshot(backendRunID, run)
	d.mu.Unlock()

	go d.waitForLocalRun(backendRunID, command)
	return snapshot, nil
}

func (d *LocalDriver) Pause(_ context.Context, backendRunID, _, _ string) (Snapshot, error) {
	if d == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	return Snapshot{}, fmt.Errorf("%w: local backend does not yet support checkpoint-safe pause for run %q", ErrUnsupportedMapping, backendRunID)
}

func (d *LocalDriver) Resume(_ context.Context, backendRunID, _ string) (Snapshot, error) {
	if d == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	return Snapshot{}, fmt.Errorf("%w: local backend does not yet support checkpoint-safe resume for run %q", ErrUnsupportedMapping, backendRunID)
}

func (d *LocalDriver) Cancel(_ context.Context, backendRunID, _, _ string) (Snapshot, error) {
	if d == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	d.mu.Lock()
	run, ok := d.runs[backendRunID]
	if !ok {
		d.mu.Unlock()
		return Snapshot{}, errors.New("local execution run not found")
	}
	if run.status == StatusCanceled || run.status == StatusCompleted || run.status == StatusFailed {
		snapshot := localSnapshot(backendRunID, run)
		d.mu.Unlock()
		return snapshot, nil
	}
	run.cancelRequested = true
	command := run.command
	if command == nil || command.Process == nil {
		run.status = StatusCanceled
		snapshot := localSnapshot(backendRunID, run)
		d.mu.Unlock()
		return snapshot, nil
	}
	d.mu.Unlock()

	if err := command.Process.Kill(); err != nil && !errors.Is(err, os.ErrProcessDone) {
		return Snapshot{}, fmt.Errorf("kill local execution process: %w", err)
	}

	d.mu.Lock()
	defer d.mu.Unlock()
	run = d.runs[backendRunID]
	run.status = StatusCanceled
	return localSnapshot(backendRunID, run), nil
}

func (d *LocalDriver) Get(_ context.Context, backendRunID string) (Snapshot, error) {
	if d == nil {
		return Snapshot{}, ErrBackendNotConfigured
	}
	d.mu.RLock()
	defer d.mu.RUnlock()
	run, ok := d.runs[backendRunID]
	if !ok {
		return Snapshot{}, errors.New("local execution run not found")
	}
	return localSnapshot(backendRunID, run), nil
}

func (d *LocalDriver) ListWorkers(_ context.Context) ([]Worker, error) {
	if d == nil {
		return nil, ErrBackendNotConfigured
	}
	return []Worker{
		{
			WorkerID:            "local-host",
			Status:              "AVAILABLE",
			Device:              runtime.GOOS + "/" + runtime.GOARCH,
			CPUCount:            uint32(runtime.NumCPU()),
			GPUAvailable:        false,
			GPUCount:            0,
			SupportedAlgorithms: []string{"fedavg", "fedprox", "scaffold"},
		},
	}, nil
}

func (d *LocalDriver) commandFor(specPath string) *exec.Cmd {
	if d.config.CommandFactory != nil {
		return d.config.CommandFactory(d.config.RepositoryRoot, d.config.PythonExecutable, specPath)
	}
	return exec.Command(
		d.config.PythonExecutable,
		filepath.Join(d.config.RepositoryRoot, "scripts", "run_local_execution.py"),
		"--spec",
		specPath,
	)
}

func (d *LocalDriver) waitForLocalRun(backendRunID string, command *exec.Cmd) {
	err := command.Wait()
	d.mu.Lock()
	defer d.mu.Unlock()
	run, ok := d.runs[backendRunID]
	if !ok || run.command != command {
		return
	}
	if run.logFile != nil {
		_ = run.logFile.Close()
		run.logFile = nil
	}
	run.command = nil
	if run.cancelRequested || run.status == StatusCanceled {
		run.status = StatusCanceled
		return
	}
	if err != nil {
		run.status = StatusFailed
		run.lastError = err
		return
	}
	summaryPath := filepath.Join(run.spec.Artifacts.Root, "summary.json")
	if _, statErr := os.Stat(summaryPath); statErr != nil {
		run.status = StatusFailed
		run.lastError = fmt.Errorf("local execution exited successfully without summary.json: %w", statErr)
		return
	}
	run.status = StatusCompleted
	run.modelVersion = run.spec.Model.Version
}

func validateLocalMapping(spec Spec) error {
	if err := spec.Validate(); err != nil {
		return err
	}
	if spec.Backend != BackendLocal {
		return fmt.Errorf("%w: local driver received backend %q", ErrUnsupportedMapping, spec.Backend)
	}
	architecture := strings.ToLower(strings.TrimSpace(spec.Model.ArchitectureName))
	if architecture == "" {
		architecture = strings.ToLower(strings.TrimSpace(spec.Model.Name))
	}
	if architecture != "cnn" {
		return fmt.Errorf("%w: local root backend currently supports model architecture cnn only", ErrUnsupportedMapping)
	}
	switch strings.ToLower(strings.TrimSpace(spec.Algorithm.Name)) {
	case "fedavg", "fedprox", "scaffold":
	default:
		return fmt.Errorf("%w: local root backend algorithm %q is unsupported", ErrUnsupportedMapping, spec.Algorithm.Name)
	}
	if spec.Federation.SchedulingMode != SchedulingSynchronous {
		return fmt.Errorf("%w: local root backend supports synchronous scheduling only", ErrUnsupportedMapping)
	}
	if spec.Privacy.Mode != PrivacyNone && spec.Privacy.Mode != PrivacyUserLevel {
		return fmt.Errorf("%w: local root backend supports privacy none or user_level_dp only", ErrUnsupportedMapping)
	}
	if spec.Privacy.AdaptiveClipping.Enabled {
		return fmt.Errorf("%w: adaptive clipping is not implemented by the local root backend", ErrUnsupportedMapping)
	}
	if spec.Security.RequireAuthenticatedWorkers || spec.Security.RequireSignedTasks || spec.Security.RequireSignedResults || spec.Security.SecureAggregation {
		return fmt.Errorf("%w: worker transport/signing/secure aggregation policies require the distributed backend", ErrUnsupportedMapping)
	}
	if spec.Privacy.Mode == PrivacyUserLevel {
		if strings.EqualFold(spec.Algorithm.Name, "scaffold") {
			return fmt.Errorf("%w: local DP-enabled SCAFFOLD is intentionally unsupported", ErrUnsupportedMapping)
		}
		if spec.Federation.SamplingStrategy != SamplingPoisson {
			return fmt.Errorf("%w: local user-level DP requires poisson sampling", ErrUnsupportedMapping)
		}
		if spec.Federation.Weighting != "uniform" {
			return fmt.Errorf("%w: local user-level DP requires uniform weighting", ErrUnsupportedMapping)
		}
		if !strings.EqualFold(strings.TrimSpace(spec.Privacy.UserLevel.Accountant), "rdp") {
			return fmt.Errorf("%w: local root backend currently implements only the RDP accountant", ErrUnsupportedMapping)
		}
		if !strings.EqualFold(strings.TrimSpace(spec.Privacy.UserLevel.WeightingStrategy), "uniform") {
			return fmt.Errorf("%w: local user-level DP requires weighting_strategy=uniform", ErrUnsupportedMapping)
		}
		if spec.Privacy.UserLevel.EpsilonBudget > 0 {
			return fmt.Errorf("%w: local root backend does not yet implement epsilon_budget stop-policy enforcement", ErrUnsupportedMapping)
		}
		if spec.Privacy.UserLevel.SecureRandom {
			return fmt.Errorf("%w: local root backend does not claim a cryptographically secure Gaussian RNG", ErrUnsupportedMapping)
		}
	}
	return nil
}

func localSnapshot(backendRunID string, run *localRun) Snapshot {
	if run == nil {
		return Snapshot{BackendRunID: backendRunID}
	}
	currentRound := uint64(0)
	if run.status == StatusCompleted {
		currentRound = uint64(run.spec.Federation.Rounds)
	}
	return Snapshot{
		BackendRunID:      backendRunID,
		Status:            run.status,
		CurrentRound:      currentRound,
		MaxRounds:         run.spec.Federation.Rounds,
		ModelVersion:      run.modelVersion,
		RegisteredWorkers: 1,
		HealthyWorkers:    1,
	}
}

func writeAtomic(path string, content []byte, mode os.FileMode) error {
	directory := filepath.Dir(path)
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(directory, ".execution-*.tmp")
	if err != nil {
		return err
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if err := temporary.Chmod(mode); err != nil {
		_ = temporary.Close()
		return err
	}
	if _, err := temporary.Write(content); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Sync(); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryPath, path)
}
