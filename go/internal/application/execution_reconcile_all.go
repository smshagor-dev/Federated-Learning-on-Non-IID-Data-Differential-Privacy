package application

import (
	"context"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type ReconcileFailure struct {
	ExecutionID string `json:"execution_id"`
	Error       string `json:"error"`
}

type ReconcileSummary struct {
	Backend  executiondomain.Backend `json:"backend"`
	Checked  int                     `json:"checked"`
	Updated  int                     `json:"updated"`
	Skipped  int                     `json:"skipped"`
	Failures []ReconcileFailure      `json:"failures,omitempty"`
}

// ReconcileBackend synchronizes every durable, non-terminal execution for one
// configured backend with the backend's recovered runtime state. Startup uses
// this stronger mode because a previous process may have stopped while a
// lifecycle operation was in a transitional state. It also recovers the
// backend event cursor so coordinator events emitted before the restart become
// durable execution-journal entries.
func (s *ExecutionService) ReconcileBackend(ctx context.Context, backend executiondomain.Backend) (ReconcileSummary, error) {
	return s.reconcileBackend(ctx, backend, true)
}

// ReconcileRuntimeBackend is safe for a periodic background loop. It skips
// records whose control-plane lifecycle operation is currently transitional so
// a poll cannot race an in-flight Start/Pause/Resume/Cancel request and write an
// older backend snapshot over that request. Secure-aggregation security events
// and deadlines are checked on every stable tick because a secure session can
// expire while the ordinary run snapshot remains WAITING_FOR_CLIENTS.
func (s *ExecutionService) ReconcileRuntimeBackend(ctx context.Context, backend executiondomain.Backend) (ReconcileSummary, error) {
	return s.reconcileBackend(ctx, backend, false)
}

func (s *ExecutionService) reconcileBackend(ctx context.Context, backend executiondomain.Backend, includeTransitional bool) (ReconcileSummary, error) {
	summary := ReconcileSummary{Backend: backend}
	if s == nil || s.repo == nil {
		return summary, executiondomain.ErrBackendNotConfigured
	}
	if _, err := s.drivers.Require(backend); err != nil {
		return summary, err
	}
	records, err := s.repo.List(ctx)
	if err != nil {
		return summary, err
	}
	for _, record := range records {
		if record.Backend != backend {
			continue
		}
		if record.BackendRunID == "" {
			summary.Skipped++
			continue
		}
		if record.Terminal() {
			// A process can crash after the backend reached a terminal state
			// but before its final coordinator events were copied into the
			// execution journal. Startup performs one recovery poll when no
			// cursor has ever been persisted for that execution.
			if includeTransitional && record.BackendEventCursor == "" {
				beforeRevision := record.Revision
				updated, _, ingestErr := s.IngestBackendEvents(ctx, record.ID)
				if ingestErr != nil {
					summary.Failures = append(summary.Failures, ReconcileFailure{
						ExecutionID: record.ID,
						Error:       ingestErr.Error(),
					})
					continue
				}
				if updated.Revision > beforeRevision {
					summary.Updated++
				} else {
					summary.Skipped++
				}
				continue
			}
			summary.Skipped++
			continue
		}
		if !includeTransitional && executionStatusTransitional(record.Status) {
			summary.Skipped++
			continue
		}

		summary.Checked++
		beforeRevision := record.Revision
		executionUpdated := false

		// Security reconciliation deliberately happens before GetRun. A
		// coordinator restart destroys in-memory secure-session key/mask
		// material by design; its durable security journal can therefore be
		// the only surviving evidence that the old run can no longer finish.
		withSecurity, _, securityErr := s.ReconcileSecureAggregationSecurityEvents(ctx, record.ID)
		if securityErr != nil {
			summary.Failures = append(summary.Failures, ReconcileFailure{
				ExecutionID: record.ID,
				Error:       securityErr.Error(),
			})
			continue
		}
		if withSecurity.Revision > beforeRevision {
			executionUpdated = true
		}
		if withSecurity.Terminal() {
			if executionUpdated {
				summary.Updated++
			}
			continue
		}
		record = withSecurity

		// The secure-session watchdog is independent of worker polling. It
		// actively expires stuck key-advertisement/masked-update sessions,
		// including COHORT_FROZEN sessions with zero masked contributions.
		// This must run before the ordinary backend snapshot reconciliation:
		// a WAITING_FOR_CLIENTS snapshot can remain unchanged indefinitely
		// while the secure protocol has already crossed its deadline.
		beforeWatchdogRevision := record.Revision
		withWatchdog, _, watchdogErr := s.SweepSecureAggregationDeadlines(ctx, record.ID)
		if watchdogErr != nil {
			if executionUpdated {
				summary.Updated++
			}
			summary.Failures = append(summary.Failures, ReconcileFailure{
				ExecutionID: record.ID,
				Error:       watchdogErr.Error(),
			})
			continue
		}
		if withWatchdog.Revision > beforeWatchdogRevision {
			executionUpdated = true
		}
		if withWatchdog.Terminal() {
			if executionUpdated {
				summary.Updated++
			}
			continue
		}
		record = withWatchdog

		beforeBackendRevision := record.Revision
		updated, reconcileErr := s.Reconcile(ctx, record.ID)
		if reconcileErr != nil {
			if executionUpdated {
				summary.Updated++
			}
			summary.Failures = append(summary.Failures, ReconcileFailure{
				ExecutionID: record.ID,
				Error:       reconcileErr.Error(),
			})
			continue
		}
		backendStateUpdated := updated.Revision > beforeBackendRevision
		if backendStateUpdated {
			executionUpdated = true
		}

		// Startup always performs one event catch-up for active executions.
		// The periodic loop only polls the ordinary coordinator event stream
		// when the backend snapshot changed. Secure-session aborts use the
		// separate security-journal/watchdog passes above and therefore do
		// not depend on an ordinary state/model/round transition to become
		// visible.
		if includeTransitional || backendStateUpdated {
			withEvents, _, ingestErr := s.IngestBackendEvents(ctx, updated.ID)
			if ingestErr != nil {
				if executionUpdated {
					summary.Updated++
				}
				summary.Failures = append(summary.Failures, ReconcileFailure{
					ExecutionID: updated.ID,
					Error:       ingestErr.Error(),
				})
				continue
			}
			if withEvents.Revision > updated.Revision {
				executionUpdated = true
			}
		}
		if executionUpdated {
			summary.Updated++
		}
	}
	return summary, nil
}

func executionStatusTransitional(status executiondomain.Status) bool {
	switch status {
	case executiondomain.StatusStarting,
		executiondomain.StatusPausing,
		executiondomain.StatusResuming,
		executiondomain.StatusCanceling:
		return true
	default:
		return false
	}
}
