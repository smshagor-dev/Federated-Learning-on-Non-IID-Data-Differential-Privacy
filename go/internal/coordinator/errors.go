package coordinator

import "errors"

var (
	// ErrUnavailable means the coordinator could not be reached at all
	// (dial failure, connection refused, deadline exceeded) — distinct
	// from ErrRejected, which means the coordinator was reached and
	// explicitly said no.
	ErrUnavailable = errors.New("coordinator unavailable")

	// ErrRejected wraps a specific reason string from the coordinator
	// (e.g. "cannot start a run in terminal state COMPLETED").
	ErrRejected = errors.New("coordinator rejected request")

	ErrRunNotFound = errors.New("run not found")
)

type RejectedError struct {
	Reason string
}

func (e *RejectedError) Error() string {
	return e.Reason
}

func (e *RejectedError) Unwrap() error {
	return ErrRejected
}
