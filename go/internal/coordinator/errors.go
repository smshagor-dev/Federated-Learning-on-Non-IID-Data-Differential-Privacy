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

	// Security Operations and Administration slice
	// (docs/security-api.md): the coordinator's ADMIN_CONTROL RPCs use
	// gRPC status codes that pre-existing mapGrpcError never
	// distinguished (everything not Unavailable/DeadlineExceeded/
	// Canceled/NotFound fell into the generic RejectedError). Introduced
	// as new sentinels rather than repurposing ErrRunNotFound/ErrRejected
	// so the HTTP layer can map each to a distinct, correct status code
	// (403/404/409) instead of guessing from a reason string.
	ErrPermissionDenied   = errors.New("permission denied")
	ErrNotFound           = errors.New("resource not found")
	ErrFailedPrecondition = errors.New("failed precondition")
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
