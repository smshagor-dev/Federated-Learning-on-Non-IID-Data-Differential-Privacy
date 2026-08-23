package application

import (
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

var executionEngines sync.Map

// ConfigureExecutionEngine attaches the canonical execution engine to an
// existing Services instance without changing the long-lived constructor
// signatures used across the repository. The binding has the same lifetime as
// the Services pointer (normally the process lifetime in production).
func ConfigureExecutionEngine(
	services *Services,
	repo executiondomain.Repository,
	journal *executiondomain.Journal,
	coordinatorClient coordinator.Client,
	clock Clock,
) *ExecutionService {
	if services == nil {
		return nil
	}
	drivers := executiondomain.DriverRegistry{}
	if coordinatorClient != nil {
		drivers[executiondomain.BackendDistributed] = executiondomain.NewDistributedDriver(coordinatorClient)
	}
	engine := NewExecutionService(
		repo,
		drivers,
		journal,
		services.Experiments,
		clock,
		services.Audit,
	)
	executionEngines.Store(services, engine)
	return engine
}

func ExecutionEngineFor(services *Services) (*ExecutionService, bool) {
	if services == nil {
		return nil, false
	}
	value, ok := executionEngines.Load(services)
	if !ok {
		return nil, false
	}
	engine, ok := value.(*ExecutionService)
	return engine, ok && engine != nil
}

func ClearExecutionEngineForTests(services *Services) {
	if services != nil {
		executionEngines.Delete(services)
	}
}
