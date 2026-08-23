package execution

import "time"

type Status string

const (
	StatusCreated   Status = "CREATED"
	StatusStarting  Status = "STARTING"
	StatusRunning   Status = "RUNNING"
	StatusPausing   Status = "PAUSING"
	StatusPaused    Status = "PAUSED"
	StatusResuming  Status = "RESUMING"
	StatusCanceling Status = "CANCELING"
	StatusCanceled  Status = "CANCELED"
	StatusCompleted Status = "COMPLETED"
	StatusFailed    Status = "FAILED"
)

type Record struct {
	ID                  string     `json:"id"`
	ExperimentID        string     `json:"experiment_id,omitempty"`
	Backend             Backend    `json:"backend"`
	Spec                Spec       `json:"spec"`
	SpecHash            string     `json:"spec_hash"`
	Status              Status     `json:"status"`
	BackendRunID        string     `json:"backend_run_id,omitempty"`
	BackendEventCursor  string     `json:"backend_event_cursor,omitempty"`
	SecurityEventCursor string     `json:"security_event_cursor,omitempty"`
	CurrentRound        uint64     `json:"current_round"`
	MaxRounds           uint32     `json:"max_rounds"`
	ModelVersion        string     `json:"model_version,omitempty"`
	RegisteredWorkers   uint32     `json:"registered_workers"`
	HealthyWorkers      uint32     `json:"healthy_workers"`
	LastError           string     `json:"last_error,omitempty"`
	Revision            uint64     `json:"revision"`
	CreatedAt           time.Time  `json:"created_at"`
	UpdatedAt           time.Time  `json:"updated_at"`
	StartedAt           *time.Time `json:"started_at,omitempty"`
	CompletedAt         *time.Time `json:"completed_at,omitempty"`
}

func (r Record) Terminal() bool {
	switch r.Status {
	case StatusCanceled, StatusCompleted, StatusFailed:
		return true
	default:
		return false
	}
}

func CanRequestStart(status Status) bool {
	return status == StatusCreated
}

func CanRequestPause(status Status) bool {
	return status == StatusRunning
}

func CanRequestResume(status Status) bool {
	return status == StatusPaused
}

func CanRequestCancel(status Status) bool {
	record := Record{Status: status}
	return !record.Terminal() && status != StatusCanceling
}

type Event struct {
	EventID      string            `json:"event_id"`
	ExecutionID  string            `json:"execution_id"`
	Type         string            `json:"type"`
	Status       Status            `json:"status,omitempty"`
	Round        uint64            `json:"round,omitempty"`
	BackendRunID string            `json:"backend_run_id,omitempty"`
	Reason       string            `json:"reason,omitempty"`
	TraceID      string            `json:"trace_id,omitempty"`
	Metadata     map[string]string `json:"metadata,omitempty"`
	Timestamp    time.Time         `json:"timestamp"`
}
