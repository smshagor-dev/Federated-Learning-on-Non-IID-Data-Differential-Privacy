package execution

import (
	"context"
	"errors"
	"time"
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

// BackendEvent is transport-neutral coordinator/runtime observability. EventID
// must be stable within one backend run so the control plane can persist a
// resume cursor and journal events idempotently across process restarts.
type BackendEvent struct {
	EventID   string
	Type      string
	Round     uint64
	Reason    string
	TraceID   string
	Metadata  map[string]string
	Timestamp time.Time
}

// EventSource is an optional capability implemented by backends that expose a
// resumable event stream. It is deliberately separate from Driver so local
// backends without a stream keep the lifecycle contract unchanged.
type EventSource interface {
	PollEvents(ctx context.Context, backendRunID, afterEventID string) ([]BackendEvent, error)
}

// BackendSecurityEvent is a transport-neutral projection of a safe security
// journal record. It deliberately carries identifiers and bounded metadata
// only; no signature, secret-share, tensor, or key material crosses this layer.
type BackendSecurityEvent struct {
	EventID       string
	EventType     string
	RunID         string
	RoundID       uint64
	SafeSubjectID string
	ReasonCode    string
	TraceID       string
	Outcome       string
	SafeDetails   map[string]string
	Timestamp     time.Time
}

// SecurityEventPage is one cursorable page from a backend security journal.
type SecurityEventPage struct {
	Events     []BackendSecurityEvent
	NextCursor string
}

// SecurityEventSource is an optional capability for distributed backends that
// expose a safe, resumable security event journal. Keeping it separate from
// Driver avoids expanding the base lifecycle contract for local execution.
type SecurityEventSource interface {
	PollSecurityEvents(ctx context.Context, afterEventID string, limit uint32) (SecurityEventPage, error)
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
