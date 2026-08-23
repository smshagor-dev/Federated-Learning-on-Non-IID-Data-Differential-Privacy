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
// configured backend with the backend's recovered runtime state. It is intended
// for process startup after drivers have loaded their own durable state, but is
// safe to call later as an operator repair action too.
//
// Individual execution failures are reported in the returned summary rather
// than aborting the pass. A repository-listing failure is returned as an error
// because no trustworthy reconciliation pass was possible in that case.
func (s *ExecutionService) ReconcileBackend(ctx context.Context, backend executiondomain.Backend) (ReconcileSummary, error) {
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
