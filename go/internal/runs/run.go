package runs

import "time"

type Status string

const (
	StatusCreated   Status = "CREATED"
	StatusQueued    Status = "QUEUED"
	StatusRunning   Status = "RUNNING"
	StatusPaused    Status = "PAUSED"
	StatusCompleted Status = "COMPLETED"
	StatusFailed    Status = "FAILED"
	StatusCanceled  Status = "CANCELED"
)

type Run struct {
	ID           string         `json:"id"`
	ExperimentID string         `json:"experiment_id"`
	Status       Status         `json:"status"`
	Config       map[string]any `json:"config"`
	CreatedAt    time.Time      `json:"created_at"`
	UpdatedAt    time.Time      `json:"updated_at"`
}
