package coordinator

import (
	"context"
	"errors"
	"testing"
)

func TestMockClientCreateRunRejectsDuplicate(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()

	if _, err := client.CreateRun(ctx, CreateRunRequest{RunID: "run-1", Algorithm: "fedavg", MaxRounds: 3}); err != nil {
		t.Fatalf("first CreateRun: unexpected error: %v", err)
	}
	_, err := client.CreateRun(ctx, CreateRunRequest{RunID: "run-1", Algorithm: "fedavg", MaxRounds: 3})
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("duplicate CreateRun: expected ErrRejected, got %v", err)
	}
}

func TestMockClientLifecycleTransitions(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()
	if _, err := client.CreateRun(ctx, CreateRunRequest{RunID: "run-1", MaxRounds: 3}); err != nil {
		t.Fatalf("CreateRun: unexpected error: %v", err)
	}

	started, err := client.StartRun(ctx, "run-1", "trace-1")
	if err != nil {
		t.Fatalf("StartRun: unexpected error: %v", err)
	}
	if started.State != "RUNNING" {
		t.Fatalf("StartRun: expected RUNNING, got %s", started.State)
	}

	// Idempotent: starting an already-running run succeeds and is a no-op.
	if again, err := client.StartRun(ctx, "run-1", "trace-2"); err != nil || again.State != "RUNNING" {
		t.Fatalf("idempotent StartRun: expected (RUNNING, nil), got (%v, %v)", again, err)
	}

	paused, err := client.PauseRun(ctx, "run-1", "operator request", "trace-3")
	if err != nil || paused.State != "PAUSED" {
		t.Fatalf("PauseRun: expected (PAUSED, nil), got (%v, %v)", paused, err)
	}

	if _, err := client.StartRun(ctx, "run-1", "trace-4"); !errors.Is(err, ErrRejected) {
		t.Fatalf("StartRun on paused run: expected ErrRejected, got %v", err)
	}

	resumed, err := client.ResumeRun(ctx, "run-1", "trace-5")
	if err != nil || resumed.State != "RUNNING" {
		t.Fatalf("ResumeRun: expected (RUNNING, nil), got (%v, %v)", resumed, err)
	}

	canceled, err := client.CancelRun(ctx, "run-1", "operator abort", "trace-6")
	if err != nil || canceled.State != "CANCELED" {
		t.Fatalf("CancelRun: expected (CANCELED, nil), got (%v, %v)", canceled, err)
	}

	// Idempotent: canceling an already-canceled run succeeds.
	if again, err := client.CancelRun(ctx, "run-1", "operator abort", "trace-7"); err != nil || again.State != "CANCELED" {
		t.Fatalf("idempotent CancelRun: expected (CANCELED, nil), got (%v, %v)", again, err)
	}

	if _, err := client.ResumeRun(ctx, "run-1", "trace-8"); !errors.Is(err, ErrRejected) {
		t.Fatalf("ResumeRun on canceled run: expected ErrRejected, got %v", err)
	}
}

func TestMockClientGetRunUnknownReturnsNotFound(t *testing.T) {
	client := NewMockClient()
	_, err := client.GetRun(context.Background(), "does-not-exist")
	if !errors.Is(err, ErrRunNotFound) {
		t.Fatalf("expected ErrRunNotFound, got %v", err)
	}
}

func TestMockClientSetUnavailableFailsNextCallOnly(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()
	client.SetUnavailable()

	if _, err := client.Health(ctx); !errors.Is(err, ErrUnavailable) {
		t.Fatalf("expected ErrUnavailable on the primed call, got %v", err)
	}
	if _, err := client.Health(ctx); err != nil {
		t.Fatalf("expected the failure to be consumed after one call, got %v", err)
	}
}

func TestMockClientPollEventsCursor(t *testing.T) {
	client := NewMockClient()
	ctx := context.Background()
	if _, err := client.CreateRun(ctx, CreateRunRequest{RunID: "run-1", MaxRounds: 1}); err != nil {
		t.Fatalf("CreateRun: unexpected error: %v", err)
	}
	if _, err := client.StartRun(ctx, "run-1", ""); err != nil {
		t.Fatalf("StartRun: unexpected error: %v", err)
	}

	all, err := client.PollEvents(ctx, "run-1", "")
	if err != nil {
		t.Fatalf("PollEvents: unexpected error: %v", err)
	}
	if len(all) != 2 {
		t.Fatalf("expected 2 events (created, started), got %d: %+v", len(all), all)
	}

	afterFirst, err := client.PollEvents(ctx, "run-1", all[0].EventID)
	if err != nil {
		t.Fatalf("PollEvents after cursor: unexpected error: %v", err)
	}
	if len(afterFirst) != 1 || afterFirst[0].EventID != all[1].EventID {
		t.Fatalf("expected only the second event after cursor, got %+v", afterFirst)
	}
}

func TestRejectedErrorUnwrapsToErrRejected(t *testing.T) {
	err := &RejectedError{Reason: "cannot start a run in terminal state COMPLETED"}
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("expected RejectedError to unwrap to ErrRejected")
	}
	if err.Error() != "cannot start a run in terminal state COMPLETED" {
		t.Fatalf("unexpected Error() text: %s", err.Error())
	}
}
