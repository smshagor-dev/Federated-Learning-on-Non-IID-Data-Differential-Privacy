// Package coordinator defines the Go control plane's abstraction over
// the C++ federated coordinator, plus two implementations: a real gRPC
// client (grpc_client.go) and an in-memory mock (mock_client.go) for
// tests that don't need a live coordinator.
//
// Application services (go/internal/application) depend only on the
// Client interface below, never on gRPC types directly — HTTP handlers
// call application services, which call this interface, which calls
// gRPC. See docs/go-coordinator-integration.md.
package coordinator

import (
	"context"
	"time"
)

// RunState mirrors the coordinator's RunState enum as a plain string
// (e.g. "RUNNING", "COMPLETED") rather than importing the generated
// protobuf enum type into application-layer code.
type RunState string

type RunSnapshot struct {
	RunID             string   `json:"run_id"`
	State             RunState `json:"state"`
	CurrentRound      uint64   `json:"current_round"`
	MaxRounds         uint32   `json:"max_rounds"`
	ModelVersion      string   `json:"model_version"`
	Algorithm         string   `json:"algorithm"`
	RegisteredWorkers uint32   `json:"registered_workers"`
	HealthyWorkers    uint32   `json:"healthy_workers"`
}

type CreateRunRequest struct {
	RunID                 string
	Algorithm             string
	Weighting             string
	TotalClients          uint32
	TargetClientsPerRound uint32
	MaxRounds             uint32
	MinimumValidResults   uint32
	ClientSelectionSeed   uint64
	RoundTimeoutSeconds   uint32
	ServerLR              float64
}

type Event struct {
	EventID      string `json:"event_id"`
	RunID        string `json:"run_id"`
	RoundID      uint64 `json:"round_id"`
	Type         string `json:"type"`
	ClientID     string `json:"client_id,omitempty"`
	WorkerID     string `json:"worker_id,omitempty"`
	ModelVersion string `json:"model_version,omitempty"`
	Timestamp    string `json:"timestamp,omitempty"`
	TraceID      string `json:"trace_id,omitempty"`
	Reason       string `json:"reason,omitempty"`
}

// Client is what the Go control plane needs from a federated coordinator.
// Deliberately narrow: no tensor payloads ever cross this interface (the
// Go service must not aggregate or proxy model tensors — see
// docs/go-coordinator-integration.md).
type Client interface {
	Health(ctx context.Context) (string, error)
	CreateRun(ctx context.Context, request CreateRunRequest) (RunSnapshot, error)
	StartRun(ctx context.Context, runID, traceID string) (RunSnapshot, error)
	PauseRun(ctx context.Context, runID, reason, traceID string) (RunSnapshot, error)
	ResumeRun(ctx context.Context, runID, traceID string) (RunSnapshot, error)
	CancelRun(ctx context.Context, runID, reason, traceID string) (RunSnapshot, error)
	GetRun(ctx context.Context, runID string) (RunSnapshot, error)

	// PollEvents returns events for runID published after afterEventID
	// (empty string: from the beginning of what's retained). The HTTP
	// layer's SSE/WebSocket handler calls this in a loop rather than
	// holding a single long-lived gRPC stream per browser connection —
	// see docs/event-streaming.md for why that's the simpler, more
	// reliable choice for Milestone 3's scope.
	PollEvents(ctx context.Context, runID, afterEventID string) ([]Event, error)
}

// Config for constructing a real gRPC client.
type Config struct {
	Address        string
	Insecure       bool
	DialTimeout    time.Duration
	RequestTimeout time.Duration
}

func DefaultConfig(address string) Config {
	return Config{
		Address:        address,
		Insecure:       true,
		DialTimeout:    5 * time.Second,
		RequestTimeout: 10 * time.Second,
	}
}
