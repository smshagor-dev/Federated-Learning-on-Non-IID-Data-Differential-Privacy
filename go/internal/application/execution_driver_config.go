package application

import (
	"errors"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

// RegisterDriver attaches a backend before the HTTP server starts accepting
// execution requests. Drivers are intentionally configured during process
// bootstrap; mutating the registry while executions are being served is not a
// supported operation.
func (s *ExecutionService) RegisterDriver(backend executiondomain.Backend, driver executiondomain.Driver) error {
	if s == nil {
		return executiondomain.ErrBackendNotConfigured
	}
	if backend != executiondomain.BackendLocal && backend != executiondomain.BackendDistributed {
		return errors.New("unsupported execution backend")
	}
	if driver == nil {
		return executiondomain.ErrBackendNotConfigured
	}
	if backend == executiondomain.BackendLocal {
		driver = executiondomain.EnableCheckpointLifecycle(driver)
	}
	if s.drivers == nil {
		s.drivers = executiondomain.DriverRegistry{}
	}
	s.drivers[backend] = driver
	return nil
}
