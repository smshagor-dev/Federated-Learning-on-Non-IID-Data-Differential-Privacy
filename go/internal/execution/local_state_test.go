package execution

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRecoveredCanceledRunIgnoresStaleSummary(t *testing.T) {
	artifactRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(artifactRoot, "summary.json"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	run := &localRun{
		spec:            localDriverSpec(artifactRoot),
		status:          StatusCanceled,
		pid:             4242,
		cancelRequested: true,
	}
	changed := reconcileRecoveredLocalRun(run)
	if !changed {
		t.Fatal("expected recovered canceled state to normalize transient fields")
	}
	if run.status != StatusCanceled {
		t.Fatalf("status = %s, want CANCELED", run.status)
	}
	if run.pid != 0 || run.cancelRequested {
		t.Fatalf("transient state was not cleared: pid=%d cancel=%v", run.pid, run.cancelRequested)
	}
}

func TestRecoveredFailedRunIgnoresStaleSummary(t *testing.T) {
	artifactRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(artifactRoot, "summary.json"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	original := errors.New("training failed")
	run := &localRun{
		spec:      localDriverSpec(artifactRoot),
		status:    StatusFailed,
		pid:       99,
		lastError: original,
	}
	reconcileRecoveredLocalRun(run)
	if run.status != StatusFailed {
		t.Fatalf("status = %s, want FAILED", run.status)
	}
	if run.lastError == nil || run.lastError.Error() != original.Error() {
		t.Fatalf("failure evidence changed: %v", run.lastError)
	}
}

func TestRecoveredCompletedRunRequiresSummary(t *testing.T) {
	run := &localRun{
		spec:   localDriverSpec(t.TempDir()),
		status: StatusCompleted,
	}
	if !reconcileRecoveredLocalRun(run) {
		t.Fatal("expected missing completion evidence to change state")
	}
	if run.status != StatusFailed {
		t.Fatalf("status = %s, want FAILED", run.status)
	}
	if run.lastError == nil || !strings.Contains(run.lastError.Error(), "summary.json is missing") {
		t.Fatalf("failure evidence = %v", run.lastError)
	}
}

func TestRecoveredActiveRunWithSummaryBecomesCompleted(t *testing.T) {
	artifactRoot := t.TempDir()
	if err := os.WriteFile(filepath.Join(artifactRoot, "summary.json"), []byte("{}\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	run := &localRun{
		spec:   localDriverSpec(artifactRoot),
		status: StatusRunning,
		pid:    777,
	}
	if !reconcileRecoveredLocalRun(run) {
		t.Fatal("expected active recovered state to reconcile")
	}
	if run.status != StatusCompleted || run.pid != 0 {
		t.Fatalf("recovered run = %#v", run)
	}
}
