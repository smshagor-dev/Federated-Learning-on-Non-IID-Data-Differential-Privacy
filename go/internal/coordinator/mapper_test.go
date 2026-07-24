package coordinator

import (
	"errors"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestMapGrpcErrorNil(t *testing.T) {
	if mapGrpcError(nil) != nil {
		t.Fatalf("expected nil error to map to nil")
	}
}

func TestMapGrpcErrorUnavailableAndDeadlineExceeded(t *testing.T) {
	for _, code := range []codes.Code{codes.Unavailable, codes.DeadlineExceeded, codes.Canceled} {
		err := mapGrpcError(status.Error(code, "coordinator process not reachable"))
		if !errors.Is(err, ErrUnavailable) {
			t.Fatalf("code %s: expected ErrUnavailable, got %v", code, err)
		}
	}
}

func TestMapGrpcErrorNotFound(t *testing.T) {
	err := mapGrpcError(status.Error(codes.NotFound, "run not found"))
	if !errors.Is(err, ErrRunNotFound) {
		t.Fatalf("expected ErrRunNotFound, got %v", err)
	}
}

func TestMapGrpcErrorOtherCodesBecomeRejected(t *testing.T) {
	err := mapGrpcError(status.Error(codes.FailedPrecondition, "cannot start a run in terminal state COMPLETED"))
	if !errors.Is(err, ErrRejected) {
		t.Fatalf("expected ErrRejected, got %v", err)
	}
	var rejected *RejectedError
	if !errors.As(err, &rejected) {
		t.Fatalf("expected a *RejectedError, got %T", err)
	}
	if rejected.Reason != "cannot start a run in terminal state COMPLETED" {
		t.Fatalf("unexpected reason: %s", rejected.Reason)
	}
}

func TestMapGrpcErrorNonStatusError(t *testing.T) {
	err := mapGrpcError(errors.New("dial tcp: connection refused"))
	if !errors.Is(err, ErrUnavailable) {
		t.Fatalf("expected non-status errors to map to ErrUnavailable, got %v", err)
	}
}
