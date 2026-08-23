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
// lifecycle operation was in a transitional state.
func (s *ExecutionService) ReconcileBackend(ctx context.Context, backend executiondomain.Backend) (ReconcileSummary, error) {
	return s.reconcileBackend(ctx, backend, true)
}

// ReconcileRuntimeBackend is safe for a periodic background loop. It skips
// records whose control-plane lifecycle operation is currently transitional so
// a poll cannot race an in-flight Start/Pause/Resume/Cancel request and write an
// older backend snapshot over that request.
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
		if record.BackendRunID == "" || record.Terminal() {
			summary.Skipped++
			continue
		}
		if !includeTransitional && executionStatusTransitional(record.Status) {
			summary.Skipped++
			continue
		}
		summary.Checked++
		beforeRevision := record.Revision
		updated, reconcileErr := s.Reconcile(ctx, record.ID)
		if reconcileErr != nil {
			summary.Failures = append(summary.Failures, ReconcileFailure{
				ExecutionID: record.ID,
				Error:       reconcileErr.Error(),
			})
			continue
		}
		if updated.Revision > beforeRevision {
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
