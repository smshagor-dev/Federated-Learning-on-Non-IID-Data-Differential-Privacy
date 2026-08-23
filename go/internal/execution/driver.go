package execution

import (
	"context"
	"errors"
)

var (
	ErrBackendNotConfigured = errors.New("execution backend is not configured")
	ErrUnsupportedMapping   = errors.New("execution spec cannot be represented by this backend")
	ErrSecurityPreflight    = errors.New("execution security preflight failed")
)

type Snapshot struct {
	BackendRunID      string
	Status            Status
	CurrentRound      uint64
	MaxRounds         uint32
	ModelVersion      string
	RegisteredWorkers uint32
	HealthyWorkers    uint32
}

type Worker struct {
	WorkerID            string
	Status              string
	Device              string
	CPUCount            uint32
	GPUAvailable        bool
	GPUCount            uint32
	SupportedAlgorithms []string
	LastHeartbeatUnixS  float64
}

// Driver is the lifecycle contract every execution backend must satisfy.
// Local and distributed backends may have very different transports, but
// lifecycle state, snapshots, and failure semantics converge here.
type Driver interface {
	Create(ctx context.Context, executionID string, spec Spec, traceID string) (Snapshot, error)
	Start(ctx context.Context, backendRunID, traceID string) (Snapshot, error)
	Pause(ctx context.Context, backendRunID, reason, traceID string) (Snapshot, error)
	Resume(ctx context.Context, backendRunID, traceID string) (Snapshot, error)
	Cancel(ctx context.Context, backendRunID, reason, traceID string) (Snapshot, error)
	Get(ctx context.Context, backendRunID string) (Snapshot, error)
	ListWorkers(ctx context.Context) ([]Worker, error)
}

type DriverRegistry map[Backend]Driver

func (registry DriverRegistry) Require(backend Backend) (Driver, error) {
	driver, ok := registry[backend]
	if !ok || driver == nil {
		return nil, ErrBackendNotConfigured
	}
	return driver, nil
}
