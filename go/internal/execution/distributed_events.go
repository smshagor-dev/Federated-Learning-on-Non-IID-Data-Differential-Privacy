package execution

import (
	"context"
	"time"
)

// PollEvents exposes the C++ coordinator's resumable event stream through the
// optional execution EventSource capability. Tensor payloads never cross this
// boundary; only coordinator event metadata is mirrored into the control plane.
func (d *DistributedDriver) PollEvents(ctx context.Context, backendRunID, afterEventID string) ([]BackendEvent, error) {
	if err := d.ensureConfigured(); err != nil {
		return nil, err
	}
	events, err := d.client.PollEvents(ctx, backendRunID, afterEventID)
	if err != nil {
		return nil, err
	}
	result := make([]BackendEvent, 0, len(events))
	for _, event := range events {
		metadata := make(map[string]string, len(event.Metadata)+5)
		for key, value := range event.Metadata {
			metadata[key] = value
		}
		if event.ClientID != "" {
			metadata["client_id"] = event.ClientID
		}
		if event.WorkerID != "" {
			metadata["worker_id"] = event.WorkerID
		}
		if event.ModelVersion != "" {
			metadata["model_version"] = event.ModelVersion
		}
		if event.Timestamp != "" {
			metadata["backend_timestamp"] = event.Timestamp
		}

		var timestamp time.Time
		if event.Timestamp != "" {
			parsed, parseErr := time.Parse(time.RFC3339Nano, event.Timestamp)
			if parseErr == nil {
				timestamp = parsed.UTC()
			}
		}
		result = append(result, BackendEvent{
			EventID:   event.EventID,
			Type:      event.Type,
			Round:     event.RoundID,
			Reason:    event.Reason,
			TraceID:   event.TraceID,
			Metadata:  metadata,
			Timestamp: timestamp,
		})
	}
	return result, nil
}
