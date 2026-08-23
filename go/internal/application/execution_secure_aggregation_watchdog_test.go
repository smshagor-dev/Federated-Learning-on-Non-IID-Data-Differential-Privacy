package application

import (
	"context"
	"path/filepath"
	"testing"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type secureWatchdogTestDriver struct {
	snapshot executiondomain.Snapshot
	sessions []executiondomain.SecureAggregationSession
	aborted  []string
	getCalls int
}

func (d *secureWatchdogTestDriver) Create(context.Context, string, executiondomain.Spec, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureWatchdogTestDriver) Start(context.Context, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureWatchdogTestDriver) Pause(context.Context, string, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureWatchdogTestDriver) Resume(context.Context, string, string) (executiondomain.Snapshot, error) {
	return d.snapshot, nil
}

func (d *secureWatchdogTestDriver) Cancel(_ context.Context, backendRunID, _ string, _ string) (executiondomain.Snapshot, error) {
	return executiondomain.Snapshot{
		BackendRunID: backendRunID,
		Status:       executiondomain.StatusCanceled,
		CurrentRound: d.snapshot.CurrentRound,
		MaxRounds:    d.snapshot.MaxRounds,
		ModelVersion: d.snapshot.ModelVersion,
	}, nil
}

func (d *secureWatchdogTestDriver) Get(context.Context, string) (executiondomain.Snapshot, error) {
	d.getCalls++
	return d.snapshot, nil
}

func (d *secureWatchdogTestDriver) ListWorkers(context.Context) ([]executiondomain.Worker, error) {
	return nil, nil
}

func (d *secureWatchdogTestDriver) ListSecureAggregationSessions(context.Context, string) ([]executiondomain.SecureAggregationSession, error) {
	return append([]executiondomain.SecureAggregationSession(nil), d.sessions...), nil
}

func (d *secureWatchdogTestDriver) AbortSecureAggregationSession(_ context.Context, sessionID, _ string) error {
	d.aborted = append(d.aborted, sessionID)
	return nil
}

func newSecureWatchdogTestService(
	t *testing.T,
	now time.Time,
	driver *secureWatchdogTestDriver,
) (*ExecutionService, executiondomain.Repository) {
	t.Helper()
	repo := executiondomain.NewInMemoryRepository()
	journal, err := executiondomain.NewJournal(filepath.Join(t.TempDir(), "execution-events.jsonl"))
	if err != nil {
		t.Fatalf("new journal: %v", err)
	}
	service := NewExecutionService(
		repo,
		executiondomain.DriverRegistry{executiondomain.BackendDistributed: driver},
		journal,
		nil,
		func() time.Time { return now },
		nil,
	)
	return service, repo
}

func createSecureWatchdogExecution(
	t *testing.T,
	ctx context.Context,
	repo executiondomain.Repository,
	now time.Time,
	id string,
	runID string,
	round uint64,
) executiondomain.Record {
	t.Helper()
	record, err := repo.Create(ctx, executiondomain.Record{
		ID:           id,
		Backend:      executiondomain.BackendDistributed,
		Status:       executiondomain.StatusRunning,
		BackendRunID: runID,
		CurrentRound: round,
		MaxRounds:    10,
		Revision:     1,
		CreatedAt:    now.Add(-time.Minute),
		UpdatedAt:    now.Add(-time.Minute),
	})
	if err != nil {
		t.Fatalf("create execution: %v", err)
	}
	return record
}

func TestSecureAggregationWatchdogAbortsFrozenZeroContributionSession(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 16, 0, 0, 0, time.UTC)
	nowUnixS := float64(now.Unix())
	driver := &secureWatchdogTestDriver{
		snapshot: executiondomain.Snapshot{
			BackendRunID: "run-secure-frozen",
			Status:       executiondomain.StatusRunning,
			CurrentRound: 4,
			MaxRounds:    10,
			ModelVersion: "v3",
		},
		sessions: []executiondomain.SecureAggregationSession{
			{
				SessionID:                     "run-secure-frozen:4",
				BackendRunID:                  "run-secure-frozen",
				RoundID:                       4,
				State:                         "COHORT_FROZEN",
				MaskedContributionCount:       0,
				MaskedUpdateDeadlineUnixS:     nowUnixS - 1,
				SessionExpiryUnixS:            nowUnixS + 60,
				KeyAdvertisementDeadlineUnixS: nowUnixS - 30,
			},
		},
	}
	service, repo := newSecureWatchdogTestService(t, now, driver)
	createSecureWatchdogExecution(t, ctx, repo, now, "exec-secure-frozen", "run-secure-frozen", 4)

	updated, aborted, err := service.SweepSecureAggregationDeadlines(ctx, "exec-secure-frozen")
	if err != nil {
		t.Fatalf("sweep secure aggregation deadlines: %v", err)
	}
	if aborted != 1 {
		t.Fatalf("aborted=%d, want 1", aborted)
	}
	if len(driver.aborted) != 1 || driver.aborted[0] != "run-secure-frozen:4" {
		t.Fatalf("aborted sessions=%v", driver.aborted)
	}
	if updated.Status != executiondomain.StatusCanceled {
		t.Fatalf("status=%s, want CANCELED", updated.Status)
	}

	events, err := service.Events(ctx, updated.ID, 0)
	if err != nil {
		t.Fatalf("events: %v", err)
	}
	foundWatchdog := false
	for _, event := range events {
		if event.Type != "SECURE_AGGREGATION_WATCHDOG_ABORTED" {
			continue
		}
		foundWatchdog = true
		if event.Metadata["watchdog_reason"] != "masked_update_deadline_exceeded" {
			t.Fatalf("watchdog reason=%q", event.Metadata["watchdog_reason"])
		}
		if event.Metadata["secure_session_state"] != "COHORT_FROZEN" {
			t.Fatalf("session state=%q", event.Metadata["secure_session_state"])
		}
	}
	if !foundWatchdog {
		t.Fatal("missing SECURE_AGGREGATION_WATCHDOG_ABORTED event")
	}
}

func TestSecureAggregationDeadlineReasonCoversKeyAndMaskedPhases(t *testing.T) {
	nowUnixS := 1000.0
	tests := []struct {
		name    string
		session executiondomain.SecureAggregationSession
		want    string
		expired bool
	}{
		{
			name: "forming key deadline",
			session: executiondomain.SecureAggregationSession{
				State:                         "COHORT_FORMING",
				KeyAdvertisementDeadlineUnixS: 999,
				SessionExpiryUnixS:            1100,
			},
			want:    "key_advertisement_deadline_exceeded",
			expired: true,
		},
		{
			name: "masked collection deadline",
			session: executiondomain.SecureAggregationSession{
				State:                     "MASKED_UPDATE_COLLECTION",
				MaskedUpdateDeadlineUnixS: 999,
				SessionExpiryUnixS:        1100,
			},
			want:    "masked_update_deadline_exceeded",
			expired: true,
		},
		{
			name: "session expiry wins",
			session: executiondomain.SecureAggregationSession{
				State:                         "COHORT_FORMING",
				KeyAdvertisementDeadlineUnixS: 1100,
				SessionExpiryUnixS:            999,
			},
			want:    "session_expired",
			expired: true,
		},
		{
			name: "future deadline",
			session: executiondomain.SecureAggregationSession{
				State:                         "COHORT_FORMING",
				KeyAdvertisementDeadlineUnixS: 1001,
				SessionExpiryUnixS:            1100,
			},
			expired: false,
		},
		{
			name: "completed ignored",
			session: executiondomain.SecureAggregationSession{
				State:              "COMPLETED",
				SessionExpiryUnixS: 900,
			},
			expired: false,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			reason, expired := secureAggregationDeadlineReason(test.session, nowUnixS)
			if expired != test.expired {
				t.Fatalf("expired=%v, want %v", expired, test.expired)
			}
			if reason != test.want {
				t.Fatalf("reason=%q, want %q", reason, test.want)
			}
		})
	}
}

func TestRuntimeReconcilerRunsSecureAggregationWatchdogBeforeGetRun(t *testing.T) {
	ctx := context.Background()
	now := time.Date(2026, 8, 23, 16, 5, 0, 0, time.UTC)
	nowUnixS := float64(now.Unix())
	driver := &secureWatchdogTestDriver{
		snapshot: executiondomain.Snapshot{
			BackendRunID: "run-secure-runtime",
			Status:       executiondomain.StatusRunning,
			CurrentRound: 2,
			MaxRounds:    10,
			ModelVersion: "v1",
		},
		sessions: []executiondomain.SecureAggregationSession{
			{
				SessionID:                     "run-secure-runtime:2",
				BackendRunID:                  "run-secure-runtime",
				RoundID:                       2,
				State:                         "COHORT_FORMING",
				KeyAdvertisementDeadlineUnixS: nowUnixS - 1,
				SessionExpiryUnixS:            nowUnixS + 60,
			},
		},
	}
	service, repo := newSecureWatchdogTestService(t, now, driver)
	createSecureWatchdogExecution(t, ctx, repo, now, "exec-secure-runtime", "run-secure-runtime", 2)

	summary, err := service.ReconcileRuntimeBackend(ctx, executiondomain.BackendDistributed)
	if err != nil {
		t.Fatalf("runtime reconcile: %v", err)
	}
	if len(summary.Failures) != 0 {
		t.Fatalf("failures=%+v", summary.Failures)
	}
	if summary.Checked != 1 || summary.Updated != 1 {
		t.Fatalf("summary=%+v", summary)
	}
	if driver.getCalls != 0 {
		t.Fatalf("GetRun calls=%d, want 0 after watchdog terminal cancellation", driver.getCalls)
	}
	updated, err := service.Get(ctx, "exec-secure-runtime")
	if err != nil {
		t.Fatalf("get execution: %v", err)
	}
	if updated.Status != executiondomain.StatusCanceled {
		t.Fatalf("status=%s, want CANCELED", updated.Status)
	}
}
