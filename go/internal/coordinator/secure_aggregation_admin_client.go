package coordinator

import (
	"context"
	"fmt"
	"strings"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	coordinatorv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/coordinator/v1"
)

// SecureAggregationSessionSummary is the lightweight list projection exposed
// by the coordinator's secure-aggregation administration RPC.
type SecureAggregationSessionSummary struct {
	SessionID        string
	RunID            string
	RoundID          uint64
	State            string
	CreatedAtUnixS   float64
	CompletedAtUnixS float64
}

// SecureAggregationSessionStatus is the detail projection needed by the
// control-plane watchdog. It contains lifecycle/deadline metadata only; no key,
// mask, secret-share, or tensor material is exposed by the wire message.
type SecureAggregationSessionStatus struct {
	SessionID                     string
	RunID                         string
	RoundID                       uint64
	State                         string
	KeyAdvertisementCount         uint64
	MaskedContributionCount       uint64
	KeyAdvertisementDeadlineUnixS float64
	MaskedUpdateDeadlineUnixS     float64
	SessionExpiryUnixS            float64
	CreatedAtUnixS                float64
	CompletedAtUnixS              float64
	AbortReason                   string
	FailureReason                 string
}

// SecureAggregationAdminClient is intentionally separate from Client. The
// unified execution engine can discover this optional capability without
// forcing every historical/mock Client implementation to grow admin methods.
type SecureAggregationAdminClient interface {
	ListSecureAggregationSessions(
		ctx context.Context,
		runID string,
		pageToken string,
		pageSize uint32,
	) ([]SecureAggregationSessionSummary, string, error)
	GetSecureAggregationSession(
		ctx context.Context,
		sessionID string,
	) (SecureAggregationSessionStatus, bool, error)
	AbortSecureAggregationSession(
		ctx context.Context,
		sessionID string,
		reason string,
	) (bool, string, error)
}

func secureAggregationStateString(value coordinatorv1.SecureAggregationSessionState) string {
	return strings.TrimPrefix(value.String(), "SECURE_AGGREGATION_SESSION_STATE_")
}

func secureAggregationAbortReasonString(value coordinatorv1.SecureAggregationAbortReason) string {
	return strings.TrimPrefix(value.String(), "SECURE_AGGREGATION_ABORT_REASON_")
}

// mapSecureAggregationAdminGrpcError preserves the general security-RPC error
// vocabulary while adding one compatibility rule for this optional surface:
// older coordinators may not expose these RPCs at all, which gRPC reports as
// UNIMPLEMENTED. Treat that like an unavailable optional feature rather than a
// generic rejected mutation.
func mapSecureAggregationAdminGrpcError(err error) error {
	if err == nil {
		return nil
	}
	if status.Code(err) == codes.Unimplemented {
		return fmt.Errorf("%w: secure aggregation administration is not implemented by this coordinator", ErrFailedPrecondition)
	}
	return mapSecurityGrpcError(err)
}

func (c *GrpcClient) ListSecureAggregationSessions(
	ctx context.Context,
	runID string,
	pageToken string,
	pageSize uint32,
) ([]SecureAggregationSessionSummary, string, error) {
	response, err := c.stub.ListSecureAggregationSessions(ctx, &coordinatorv1.ListSecureAggregationSessionsRequest{
		RunId:     runID,
		PageSize:  pageSize,
		PageToken: pageToken,
	})
	if err != nil {
		return nil, "", mapSecureAggregationAdminGrpcError(err)
	}
	result := make([]SecureAggregationSessionSummary, 0, len(response.GetSessions()))
	for _, session := range response.GetSessions() {
		result = append(result, SecureAggregationSessionSummary{
			SessionID:        session.GetSessionId(),
			RunID:            session.GetRunId(),
			RoundID:          session.GetRoundId(),
			State:            secureAggregationStateString(session.GetState()),
			CreatedAtUnixS:   session.GetCreatedAtUnixS(),
			CompletedAtUnixS: session.GetCompletedAtUnixS(),
		})
	}
	return result, response.GetNextPageToken(), nil
}

func (c *GrpcClient) GetSecureAggregationSession(
	ctx context.Context,
	sessionID string,
) (SecureAggregationSessionStatus, bool, error) {
	response, err := c.stub.GetSecureAggregationSession(ctx, &coordinatorv1.GetSecureAggregationSessionRequest{
		SessionId: sessionID,
	})
	if err != nil {
		return SecureAggregationSessionStatus{}, false, mapSecureAggregationAdminGrpcError(err)
	}
	if !response.GetFound() || response.GetStatus() == nil {
		return SecureAggregationSessionStatus{}, false, nil
	}
	statusRecord := response.GetStatus()
	return SecureAggregationSessionStatus{
		SessionID:                     statusRecord.GetSessionId(),
		RunID:                         statusRecord.GetRunId(),
		RoundID:                       statusRecord.GetRoundId(),
		State:                         secureAggregationStateString(statusRecord.GetState()),
		KeyAdvertisementCount:         statusRecord.GetKeyAdvertisementCount(),
		MaskedContributionCount:       statusRecord.GetMaskedContributionCount(),
		KeyAdvertisementDeadlineUnixS: statusRecord.GetKeyAdvertisementDeadlineUnixS(),
		MaskedUpdateDeadlineUnixS:     statusRecord.GetMaskedUpdateDeadlineUnixS(),
		SessionExpiryUnixS:            statusRecord.GetSessionExpiryUnixS(),
		CreatedAtUnixS:                statusRecord.GetCreatedAtUnixS(),
		CompletedAtUnixS:              statusRecord.GetCompletedAtUnixS(),
		AbortReason:                   secureAggregationAbortReasonString(statusRecord.GetAbortReason()),
		FailureReason:                 statusRecord.GetFailureReason(),
	}, true, nil
}

func (c *GrpcClient) AbortSecureAggregationSession(
	ctx context.Context,
	sessionID string,
	reason string,
) (bool, string, error) {
	response, err := c.stub.AbortSecureAggregationSession(ctx, &coordinatorv1.AbortSecureAggregationSessionRequest{
		SessionId: sessionID,
		Reason:    reason,
	})
	if err != nil {
		return false, "", mapSecureAggregationAdminGrpcError(err)
	}
	return response.GetAccepted(), response.GetReason(), nil
}
