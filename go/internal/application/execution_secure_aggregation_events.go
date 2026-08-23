package application

import (
	"context"
	"fmt"
	"strconv"
	"strings"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

const (
	secureAggregationSessionAborted = "SECURE_AGGREGATION_SESSION_ABORTED"
	secureAggregationRestartAborted = "SECURE_AGGREGATION_RESTART_ABORTED"
)

type secureAbortDisposition int

const (
	secureAbortIgnore secureAbortDisposition = iota
	secureAbortCancelLiveRun
	secureAbortFailRecoveredRun
)

// ReconcileSecureAggregationSecurityEvents consumes the coordinator's durable,
// redacted security journal for one execution. The current no-dropout secure
// provider creates exactly one secure session per run/round, so a genuine
// session abort has no safe same-round retry and must terminate the execution.
// The one deliberate exception is the existing configuration-incompatible
// fallback, which is rejected before secure training begins and intentionally
// leaves the ordinary cleartext path available.
func (s *ExecutionService) ReconcileSecureAggregationSecurityEvents(
	ctx context.Context,
	id string,
) (executiondomain.Record, int, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, 0, err
	}
	if record.BackendRunID == "" {
		return record, 0, nil
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, 0, err
	}
	source, ok := driver.(executiondomain.SecurityEventSource)
	if !ok {
		return record, 0, nil
	}

	page, err := source.PollSecurityEvents(ctx, record.SecurityEventCursor, 256)
	if err != nil {
		return record, 0, err
	}
	cursor := record.SecurityEventCursor
	processed := 0
	for _, event := range page.Events {
		eventID := strings.TrimSpace(event.EventID)
		if eventID == "" {
			return record, processed, fmt.Errorf("security event for execution %s has empty event_id", record.ID)
		}
		cursor = eventID
		processed++
		if !secureEventTargetsExecution(record, event) {
			continue
		}

		disposition := classifySecureAbort(event)
		if disposition == secureAbortIgnore {
			continue
		}
		reason := secureAbortReason(event)
		metadata := map[string]string{
			"security_event_id":   eventID,
			"security_event_type": event.EventType,
			"secure_session_id":   event.SafeSubjectID,
			"security_outcome":    event.Outcome,
		}
		if reason != "" {
			metadata["security_reason_code"] = reason
		}

		switch disposition {
		case secureAbortCancelLiveRun:
			if record.Terminal() {
				return s.persistSecurityCursor(ctx, record, cursor, processed)
			}
			cancelReason := "secure aggregation session aborted"
			if reason != "" {
				cancelReason += ": " + reason
			}
			canceled, cancelErr := s.Cancel(ctx, record.ID, cancelReason, event.TraceID)
			if cancelErr != nil {
				// Do not advance the cursor. The next reconciliation pass must
				// retry the same authoritative abort event.
				return record, processed, cancelErr
			}
			canceled.SecurityEventCursor = cursor
			canceled.UpdatedAt = s.clock().UTC()
			updated, persistErr := s.repo.Update(ctx, canceled, canceled.Revision)
			if persistErr != nil {
				return canceled, processed, persistErr
			}
			s.appendEvent(updated,
				"SECURE_AGGREGATION_ABORT_PROPAGATED",
				event.TraceID,
				cancelReason,
				metadata)
			return updated, processed, nil

		case secureAbortFailRecoveredRun:
			if record.Terminal() {
				return s.persistSecurityCursor(ctx, record, cursor, processed)
			}
			now := s.clock().UTC()
			failureReason := "secure aggregation session aborted during coordinator restart"
			if reason != "" {
				failureReason += ": " + reason
			}
			record.Status = executiondomain.StatusFailed
			record.LastError = failureReason
			record.SecurityEventCursor = cursor
			record.UpdatedAt = now
			record.CompletedAt = &now
			updated, persistErr := s.repo.Update(ctx, record, record.Revision)
			if persistErr != nil {
				return record, processed, persistErr
			}
			s.appendEvent(updated,
				"EXECUTION_FAILED_SECURE_AGGREGATION",
				event.TraceID,
				failureReason,
				metadata)
			s.auditLifecycle(ctx, "execution.fail.secure_aggregation", updated)
			return updated, processed, nil
		}
	}

	if page.NextCursor != "" {
		cursor = page.NextCursor
	}
	return s.persistSecurityCursor(ctx, record, cursor, processed)
}

func (s *ExecutionService) persistSecurityCursor(
	ctx context.Context,
	record executiondomain.Record,
	cursor string,
	processed int,
) (executiondomain.Record, int, error) {
	if cursor == "" || cursor == record.SecurityEventCursor {
		return record, processed, nil
	}
	record.SecurityEventCursor = cursor
	record.UpdatedAt = s.clock().UTC()
	updated, err := s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return record, processed, err
	}
	return updated, processed, nil
}

func secureEventTargetsExecution(
	record executiondomain.Record,
	event executiondomain.BackendSecurityEvent,
) bool {
	if event.RunID != "" && event.RunID != record.BackendRunID {
		return false
	}

	roundID := event.RoundID
	if event.SafeSubjectID != "" {
		prefix := record.BackendRunID + ":"
		if strings.HasPrefix(event.SafeSubjectID, prefix) {
			parsed, err := strconv.ParseUint(strings.TrimPrefix(event.SafeSubjectID, prefix), 10, 64)
			if err != nil {
				return false
			}
			if roundID != 0 && roundID != parsed {
				return false
			}
			roundID = parsed
		} else if event.RunID == "" {
			return false
		}
	}

	if event.RunID == "" && roundID == 0 {
		return false
	}
	if record.CurrentRound != 0 && roundID != 0 && roundID != record.CurrentRound {
		return false
	}
	return true
}

func classifySecureAbort(event executiondomain.BackendSecurityEvent) secureAbortDisposition {
	switch event.EventType {
	case secureAggregationRestartAborted:
		return secureAbortFailRecoveredRun
	case secureAggregationSessionAborted:
		// The configuration-incompatible path is intentionally emitted as a
		// rejected session before secure training begins. It is the only
		// supported cleartext fallback and must not terminate the run.
		if event.Outcome == "REJECTED" && event.ReasonCode == "" &&
			strings.TrimSpace(event.SafeDetails["abort_reason"]) != "" {
			return secureAbortIgnore
		}
		return secureAbortCancelLiveRun
	default:
		return secureAbortIgnore
	}
}

func secureAbortReason(event executiondomain.BackendSecurityEvent) string {
	if value := strings.TrimSpace(event.ReasonCode); value != "" {
		return value
	}
	if value := strings.TrimSpace(event.SafeDetails["abort_reason"]); value != "" {
		return value
	}
	return strings.TrimSpace(event.SafeDetails["reason"])
}
