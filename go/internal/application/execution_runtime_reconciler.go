package application

import (
	"context"
	"errors"
	"sort"
	"time"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

type RuntimeReconcileResult struct {
	Backend executiondomain.Backend `json:"backend"`
	Summary ReconcileSummary        `json:"summary"`
	Error   string                  `json:"error,omitempty"`
}

type RuntimeReconcileReporter func([]RuntimeReconcileResult)

// ReconcileConfiguredRuntimeBackends performs one best-effort pass across all
// currently configured execution drivers. Backend failures are isolated in the
// returned results so one unavailable backend does not suppress reconciliation
// of another backend.
func (s *ExecutionService) ReconcileConfiguredRuntimeBackends(ctx context.Context) []RuntimeReconcileResult {
	backends := s.configuredExecutionBackends()
	results := make([]RuntimeReconcileResult, 0, len(backends))
	for _, backend := range backends {
		summary, err := s.ReconcileRuntimeBackend(ctx, backend)
		result := RuntimeReconcileResult{Backend: backend, Summary: summary}
		if err != nil {
			result.Error = err.Error()
		}
		results = append(results, result)
	}
	return results
}

// RunRuntimeReconciler periodically refreshes stable execution records until
// ctx is canceled. The caller owns the goroutine and logging/metrics policy via
// reporter. A nil reporter is allowed.
func (s *ExecutionService) RunRuntimeReconciler(
	ctx context.Context,
	interval time.Duration,
	reporter RuntimeReconcileReporter,
) error {
	if s == nil || s.repo == nil {
		return executiondomain.ErrBackendNotConfigured
	}
	if interval <= 0 {
		return errors.New("execution reconciliation interval must be positive")
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			results := s.ReconcileConfiguredRuntimeBackends(ctx)
			if reporter != nil {
				reporter(results)
			}
		}
	}
}

func (s *ExecutionService) configuredExecutionBackends() []executiondomain.Backend {
	if s == nil || s.drivers == nil {
		return nil
	}
	backends := make([]executiondomain.Backend, 0, len(s.drivers))
	for backend, driver := range s.drivers {
		if driver != nil {
			backends = append(backends, backend)
		}
	}
	sort.Slice(backends, func(i, j int) bool {
		return backends[i] < backends[j]
	})
	return backends
}
