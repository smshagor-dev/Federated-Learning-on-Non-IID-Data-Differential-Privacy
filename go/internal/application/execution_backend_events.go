package application

import (
	"context"
	"fmt"
	"strings"

	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

// IngestBackendEvents copies resumable backend events into the durable
// execution journal and advances the execution's backend-event cursor. The
// journal write deliberately happens before the cursor update. Unique batch
// insertion makes replay safe if the process crashes between those operations.
func (s *ExecutionService) IngestBackendEvents(ctx context.Context, id string) (executiondomain.Record, int, error) {
	record, err := s.Get(ctx, id)
	if err != nil {
		return executiondomain.Record{}, 0, err
	}
	if record.BackendRunID == "" || s.journal == nil {
		return record, 0, nil
	}
	driver, err := s.drivers.Require(record.Backend)
	if err != nil {
		return record, 0, err
	}
	source, ok := driver.(executiondomain.EventSource)
	if !ok {
		return record, 0, nil
	}

	backendEvents, err := source.PollEvents(ctx, record.BackendRunID, record.BackendEventCursor)
	if err != nil {
		return record, 0, err
	}
	if len(backendEvents) == 0 {
		return record, 0, nil
	}

	cursor := record.BackendEventCursor
	journalEvents := make([]executiondomain.Event, 0, len(backendEvents))
	for _, backendEvent := range backendEvents {
		backendEventID := strings.TrimSpace(backendEvent.EventID)
		if backendEventID == "" {
			return record, 0, fmt.Errorf("backend event for execution %s has empty event_id", record.ID)
		}
		metadata := make(map[string]string, len(backendEvent.Metadata)+3)
		for key, value := range backendEvent.Metadata {
			metadata[key] = value
		}
		metadata["source"] = "coordinator"
		metadata["backend_event_id"] = backendEventID
		metadata["backend_event_type"] = backendEvent.Type

		timestamp := backendEvent.Timestamp
		if timestamp.IsZero() {
			timestamp = s.clock().UTC()
		}
		eventType := strings.ToUpper(strings.TrimSpace(backendEvent.Type))
		if eventType == "" {
			eventType = "EVENT"
		}
		journalEvents = append(journalEvents, executiondomain.Event{
			EventID:      fmt.Sprintf("%s-backend-%s", record.ID, backendEventID),
			ExecutionID:  record.ID,
			Type:         "COORDINATOR_" + eventType,
			Status:       record.Status,
			Round:        backendEvent.Round,
			BackendRunID: record.BackendRunID,
			Reason:       backendEvent.Reason,
			TraceID:      backendEvent.TraceID,
			Metadata:     metadata,
			Timestamp:    timestamp,
		})
		cursor = backendEventID
	}

	ingested, err := s.journal.AppendUniqueBatch(journalEvents)
	if err != nil {
		return record, 0, err
	}
	if cursor == record.BackendEventCursor {
		return record, ingested, nil
	}
	record.BackendEventCursor = cursor
	record.UpdatedAt = s.clock().UTC()
	updated, err := s.repo.Update(ctx, record, record.Revision)
	if err != nil {
		return record, ingested, err
	}
	return updated, ingested, nil
}
