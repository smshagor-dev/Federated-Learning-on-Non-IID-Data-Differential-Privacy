package application

import (
	"context"
	"fmt"
	"strings"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

// SweepSecureAggregationDeadlines actively drives secure-session deadlines from
// the control plane. This is independent of worker AcquireTask polling, closing
// the failure mode where an idle/disconnected cohort leaves a secure session
// past deadline forever.
func (s *ExecutionService) SweepSecureAggregationDeadlines(
	ctx context.Context,
	id string,
) (executiondomain.Record, int, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, 0, err
	}
	if record.BackendRunID == "" || record.Terminal() {
		return record, 0, nil
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, 0, err
	}
	controller, ok := driver.(executiondomain.SecureAggregationSessionController)
	if !ok {
		return record, 0, nil
	}

	sessions, err := controller.ListSecureAggregationSessions(ctx, record.BackendRunID)
	if err != nil {
		return record, 0, err
	}
	now := s.clock().UTC()
	nowUnixS := float64(now.UnixNano()) / 1_000_000_000
	aborted := 0
	for _, session := range sessions {
		if session.BackendRunID != record.BackendRunID {
			continue
		}
		if record.CurrentRound != 0 && session.RoundID != record.CurrentRound {
			continue
		}
		reason, expired := secureAggregationDeadlineReason(session, nowUnixS)
		if !expired {
			continue
		}

		watchdogReason := "secure aggregation watchdog: " + reason
		if err := controller.AbortSecureAggregationSession(ctx, session.SessionID, watchdogReason); err != nil {
			return record, aborted, fmt.Errorf(
				"abort expired secure aggregation session %s: %w",
				session.SessionID,
				err,
			)
		}
		aborted++

		canceled, cancelErr := s.Cancel(ctx, record.ID, watchdogReason, "")
		if cancelErr != nil {
			// The coordinator has already persisted a secure-session abort
			// event. Leave the execution security cursor untouched; the next
			// reconciliation pass will consume that event and retry fail-closed
			// execution termination.
			return canceled, aborted, cancelErr
		}
		s.appendEvent(canceled,
			"SECURE_AGGREGATION_WATCHDOG_ABORTED",
			"",
			watchdogReason,
			map[string]string{
				"secure_session_id":    session.SessionID,
				"secure_session_state": session.State,
				"watchdog_reason":      reason,
			})
		return canceled, aborted, nil
	}
	return record, aborted, nil
}

func secureAggregationDeadlineReason(
	session executiondomain.SecureAggregationSession,
	nowUnixS float64,
) (string, bool) {
	state := strings.ToUpper(strings.TrimSpace(session.State))
	switch state {
	case "COMPLETED", "ABORTED", "FAILED", "UNSPECIFIED", "":
		return "", false
	}

	if session.SessionExpiryUnixS > 0 && nowUnixS > session.SessionExpiryUnixS {
		return "session_expired", true
	}

	switch state {
	case "COHORT_FORMING", "KEY_ADVERTISEMENT":
		if session.KeyAdvertisementDeadlineUnixS > 0 &&
			nowUnixS > session.KeyAdvertisementDeadlineUnixS {
			return "key_advertisement_deadline_exceeded", true
		}
	case "COHORT_FROZEN", "MASKED_UPDATE_COLLECTION":
		// COHORT_FROZEN is intentional here. The C++ session transitions to
		// MASKED_UPDATE_COLLECTION only when the first masked contribution
		// arrives; a zero-contribution dropout otherwise never reaches the
		// existing in-process masked-update sweep.
		if session.MaskedUpdateDeadlineUnixS > 0 && nowUnixS > session.MaskedUpdateDeadlineUnixS {
			return "masked_update_deadline_exceeded", true
		}
	}
	return "", false
}
