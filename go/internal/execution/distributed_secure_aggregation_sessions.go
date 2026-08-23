package execution

import (
	"context"
	"errors"
	"fmt"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

func (d *DistributedDriver) ListSecureAggregationSessions(
	ctx context.Context,
	backendRunID string,
) ([]SecureAggregationSession, error) {
	if err := d.ensureConfigured(); err != nil {
		return nil, err
	}
	admin, ok := d.client.(coordinator.SecureAggregationAdminClient)
	if !ok {
		return nil, nil
	}

	const pageSize uint32 = 200
	pageToken := ""
	var result []SecureAggregationSession
	for {
		summaries, nextPageToken, err := admin.ListSecureAggregationSessions(
			ctx,
			backendRunID,
			pageToken,
			pageSize,
		)
		if err != nil {
			if errors.Is(err, coordinator.ErrFailedPrecondition) {
				return nil, nil
			}
			return nil, err
		}
		for _, summary := range summaries {
			status, found, err := admin.GetSecureAggregationSession(ctx, summary.SessionID)
			if err != nil {
				return nil, err
			}
			if !found || status.RunID != backendRunID {
				continue
			}
			result = append(result, SecureAggregationSession{
				SessionID:                     status.SessionID,
				BackendRunID:                  status.RunID,
				RoundID:                       status.RoundID,
				State:                         status.State,
				KeyAdvertisementCount:         status.KeyAdvertisementCount,
				MaskedContributionCount:       status.MaskedContributionCount,
				KeyAdvertisementDeadlineUnixS: status.KeyAdvertisementDeadlineUnixS,
				MaskedUpdateDeadlineUnixS:     status.MaskedUpdateDeadlineUnixS,
				SessionExpiryUnixS:            status.SessionExpiryUnixS,
			})
		}
		if nextPageToken == "" {
			break
		}
		if nextPageToken == pageToken {
			return nil, fmt.Errorf("secure aggregation session pagination did not advance from %q", pageToken)
		}
		pageToken = nextPageToken
	}
	return result, nil
}

func (d *DistributedDriver) AbortSecureAggregationSession(
	ctx context.Context,
	sessionID string,
	reason string,
) error {
	if err := d.ensureConfigured(); err != nil {
		return err
	}
	admin, ok := d.client.(coordinator.SecureAggregationAdminClient)
	if !ok {
		return fmt.Errorf("%w: coordinator does not expose secure aggregation administration", ErrUnsupportedMapping)
	}
	accepted, rejectionReason, err := admin.AbortSecureAggregationSession(ctx, sessionID, reason)
	if err != nil {
		return err
	}
	if !accepted {
		if rejectionReason == "" {
			rejectionReason = "coordinator rejected secure aggregation abort"
		}
		return fmt.Errorf("secure aggregation abort rejected: %s", rejectionReason)
	}
	return nil
}
