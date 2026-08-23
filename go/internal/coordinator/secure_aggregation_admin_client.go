package coordinator

import (
	"context"
	"strings"

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
		return nil, "", mapSecurityGrpcError(err)
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
		return SecureAggregationSessionStatus{}, false, mapSecurityGrpcError(err)
	}
	if !response.GetFound() || response.GetStatus() == nil {
		return SecureAggregationSessionStatus{}, false, nil
	}
	status := response.GetStatus()
	return SecureAggregationSessionStatus{
		SessionID:                     status.GetSessionId(),
		RunID:                         status.GetRunId(),
		RoundID:                       status.GetRoundId(),
		State:                         secureAggregationStateString(status.GetState()),
		KeyAdvertisementCount:         status.GetKeyAdvertisementCount(),
		MaskedContributionCount:       status.GetMaskedContributionCount(),
		KeyAdvertisementDeadlineUnixS: status.GetKeyAdvertisementDeadlineUnixS(),
		MaskedUpdateDeadlineUnixS:     status.GetMaskedUpdateDeadlineUnixS(),
		SessionExpiryUnixS:            status.GetSessionExpiryUnixS(),
		CreatedAtUnixS:                status.GetCreatedAtUnixS(),
		CompletedAtUnixS:              status.GetCompletedAtUnixS(),
		AbortReason:                   secureAggregationAbortReasonString(status.GetAbortReason()),
		FailureReason:                 status.GetFailureReason(),
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
		return false, "", mapSecurityGrpcError(err)
	}
	return response.GetAccepted(), response.GetReason(), nil
}
