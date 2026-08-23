package application

import (
	"context"
	"testing"

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

	terminal, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	terminal, err = service.Cancel(ctx, terminal.ID, "operator", "trace")
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
