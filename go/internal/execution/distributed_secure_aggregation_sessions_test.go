package execution

import (
	"context"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

type unsupportedSecureAdminClient struct {
	*coordinator.MockClient
}

func (c *unsupportedSecureAdminClient) ListSecureAggregationSessions(
	context.Context,
	string,
	string,
	uint32,
) ([]coordinator.SecureAggregationSessionSummary, string, error) {
	return nil, "", coordinator.ErrFailedPrecondition
}

func (c *unsupportedSecureAdminClient) GetSecureAggregationSession(
	context.Context,
	string,
) (coordinator.SecureAggregationSessionStatus, bool, error) {
	return coordinator.SecureAggregationSessionStatus{}, false, coordinator.ErrFailedPrecondition
}

func (c *unsupportedSecureAdminClient) AbortSecureAggregationSession(
	context.Context,
	string,
	string,
) (bool, string, error) {
	return false, "", coordinator.ErrFailedPrecondition
}

func TestDistributedSecureAggregationAdminCapabilityIsOptional(t *testing.T) {
	client := &unsupportedSecureAdminClient{MockClient: coordinator.NewMockClient()}
	driver := NewDistributedDriver(client)

	sessions, err := driver.ListSecureAggregationSessions(context.Background(), "run-old-coordinator")
	if err != nil {
		t.Fatalf("list secure aggregation sessions: %v", err)
	}
	if len(sessions) != 0 {
		t.Fatalf("sessions=%v, want none when optional admin RPCs are unavailable", sessions)
	}
}
