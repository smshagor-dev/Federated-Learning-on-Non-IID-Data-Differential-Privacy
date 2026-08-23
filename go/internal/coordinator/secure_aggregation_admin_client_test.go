package coordinator

import (
	"errors"
	"testing"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func TestSecureAggregationAdminUnimplementedIsOptionalCapability(t *testing.T) {
	err := mapSecureAggregationAdminGrpcError(
		status.Error(codes.Unimplemented, "unknown method ListSecureAggregationSessions"),
	)
	if !errors.Is(err, ErrFailedPrecondition) {
		t.Fatalf("error=%v, want ErrFailedPrecondition", err)
	}
}

func TestSecureAggregationAdminTransportFailureRemainsUnavailable(t *testing.T) {
	err := mapSecureAggregationAdminGrpcError(
		status.Error(codes.Unavailable, "coordinator unavailable"),
	)
	if !errors.Is(err, ErrUnavailable) {
		t.Fatalf("error=%v, want ErrUnavailable", err)
	}
}
