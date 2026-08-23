package execution

import (
	"context"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

const secureAggregationSubjectType = "SECURE_AGGREGATION_SESSION"

// PollSecurityEvents projects the coordinator's durable security journal into
// the execution layer's transport-neutral event contract. The coordinator RPC
// already returns only the bounded, redacted SecurityEvent schema.
func (d *DistributedDriver) PollSecurityEvents(
	ctx context.Context,
	afterEventID string,
	limit uint32,
) (SecurityEventPage, error) {
	if err := d.ensureConfigured(); err != nil {
		return SecurityEventPage{}, err
	}
	if limit == 0 {
		limit = 256
	}
	result, err := d.client.ListSecurityEvents(ctx, coordinator.ListSecurityEventsRequest{
		AfterEventID: afterEventID,
		Limit:        limit,
		SubjectType:  secureAggregationSubjectType,
	})
	if err != nil {
		return SecurityEventPage{}, err
	}

	events := make([]BackendSecurityEvent, 0, len(result.Events))
	for _, event := range result.Events {
		var timestamp time.Time
		if event.Timestamp != "" {
			if parsed, parseErr := time.Parse(time.RFC3339Nano, event.Timestamp); parseErr == nil {
				timestamp = parsed.UTC()
			}
		}
		details := make(map[string]string, len(event.SafeDetails))
		for key, value := range event.SafeDetails {
			details[key] = value
		}
		events = append(events, BackendSecurityEvent{
			EventID:       event.EventID,
			EventType:     event.EventType,
			RunID:         event.RunID,
			RoundID:       event.RoundID,
			SafeSubjectID: event.SafeSubjectID,
			ReasonCode:    event.ReasonCode,
			TraceID:       event.TraceID,
			Outcome:       event.Outcome,
			SafeDetails:   details,
			Timestamp:     timestamp,
		})
	}
	return SecurityEventPage{Events: events, NextCursor: result.NextCursor}, nil
}
