package application

import (
	"context"
	"testing"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

func TestRuntimeReconcileSkipsTransitionalRecord(t *testing.T) {
	driver := &fakeExecutionDriver{}
	service, _ := newExecutionServiceForTest(t, driver)
	ctx := context.Background()
	record, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.Start(ctx, record.ID, "trace-start")
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.changeStatus(
		ctx,
		record,
		executiondomain.StatusPausing,
		"EXECUTION_PAUSING",
		"trace-pause",
		"operator",
	)
	if err != nil {
		t.Fatal(err)
	}
	beforeRevision := record.Revision

	summary, err := service.ReconcileRuntimeBackend(
		ctx,
		executiondomain.BackendDistributed,
	)
	if err != nil {
		t.Fatal(err)
	}
	if summary.Checked != 0 || summary.Skipped != 1 || summary.Updated != 0 {
		t.Fatalf("summary=%#v", summary)
	}
	stored, err := service.Get(ctx, record.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.Status != executiondomain.StatusPausing {
		t.Fatalf("status=%s, want PAUSING", stored.Status)
	}
	if stored.Revision != beforeRevision {
		t.Fatalf("revision=%d, want %d", stored.Revision, beforeRevision)
	}
}

func TestRuntimeReconcilerPromotesCompletedExecution(t *testing.T) {
	driver := &fakeExecutionDriver{}
	service, _ := newExecutionServiceForTest(t, driver)
	ctx := context.Background()
	record, err := service.Create(ctx, "", validExecutionSpec())
	if err != nil {
		t.Fatal(err)
	}
	record, err = service.Start(ctx, record.ID, "trace-start")
	if err != nil {
		t.Fatal(err)
	}
	driver.status = executiondomain.StatusCompleted

	loopCtx, cancel := context.WithCancel(context.Background())
	defer cancel()
	reports := make(chan []RuntimeReconcileResult, 1)
	done := make(chan error, 1)
	go func() {
		done <- service.RunRuntimeReconciler(
			loopCtx,
			5*time.Millisecond,
			func(results []RuntimeReconcileResult) {
				select {
				case reports <- results:
				default:
				}
			},
		)
	}()

	select {
	case results := <-reports:
		if len(results) != 1 || results[0].Error != "" {
			t.Fatalf("results=%#v", results)
		}
		cancel()
	case <-time.After(time.Second):
		t.Fatal("timed out waiting for reconciliation cycle")
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(time.Second):
		t.Fatal("reconciler did not stop after context cancellation")
	}

	stored, err := service.Get(ctx, record.ID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.Status != executiondomain.StatusCompleted || stored.CompletedAt == nil {
		t.Fatalf("stored record=%#v", stored)
	}
}

func TestRuntimeReconcilerRejectsNonPositiveInterval(t *testing.T) {
	service, _ := newExecutionServiceForTest(t, &fakeExecutionDriver{})
	if err := service.RunRuntimeReconciler(context.Background(), 0, nil); err == nil {
		t.Fatal("expected non-positive interval to be rejected")
	}
}
