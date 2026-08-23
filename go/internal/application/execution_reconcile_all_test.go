package application

import (
	"context"
	"testing"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

func TestReconcileBackendUpdatesActiveAndSkipsTerminalExecutions(t *testing.T) {
	service, _ := newExecutionServiceForTest(t, reconcileOnlyDriver{})
	ctx := context.Background()

	active, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	active, err = service.Start(ctx, active.ID, "trace")
	if err != nil {
		t.Fatal(err)
	}

	// Seed a terminal execution directly. The shared test service uses a frozen
	// clock, so creating a second lifecycle execution through service.Create
	// would intentionally reuse the same time-derived ID and test ID generation
	// rather than the reconciliation behavior this case is meant to cover.
	now := time.Unix(100, 0).UTC()
	terminal, err := service.repo.Create(ctx, executiondomain.Record{
		ID:        "exec-terminal-reconcile",
		Backend:   executiondomain.BackendDistributed,
		Spec:      validExecutionSpec(),
		Status:    executiondomain.StatusCanceled,
		Revision:  1,
		CreatedAt: now,
		UpdatedAt: now,
	})
	if err != nil {
		t.Fatal(err)
	}
	if terminal.Status != executiondomain.StatusCanceled {
		t.Fatalf("terminal status = %s", terminal.Status)
	}

	summary, err := service.ReconcileBackend(ctx, executiondomain.BackendDistributed)
	if err != nil {
		t.Fatal(err)
	}
	if summary.Checked != 1 || summary.Updated != 1 || summary.Skipped != 1 || len(summary.Failures) != 0 {
		t.Fatalf("reconcile summary = %#v", summary)
	}

	persisted, err := service.Get(ctx, active.ID)
	if err != nil {
		t.Fatal(err)
	}
	if persisted.ModelVersion != "v2" || persisted.RegisteredWorkers != 5 || persisted.HealthyWorkers != 4 {
		t.Fatalf("active execution not reconciled: %#v", persisted)
	}
}
