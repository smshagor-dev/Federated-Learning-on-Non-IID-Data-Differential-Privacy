package coordinator

import (
	"fmt"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	eventsv1 "github.com/smshagor-dev/federated-learning-super-system/go/generated/events/v1"
)

// mapGrpcError translates a gRPC status error into the coordinator
// package's own error vocabulary (ErrUnavailable / ErrRunNotFound /
// RejectedError) so that callers — application services, HTTP handlers —
// never need to import grpc/codes themselves. See errors.go.
func mapGrpcError(err error) error {
	if err == nil {
		return nil
	}
	st, ok := status.FromError(err)
	if !ok {
		return fmt.Errorf("%w: %v", ErrUnavailable, err)
	}
	switch st.Code() {
	case codes.Unavailable, codes.DeadlineExceeded, codes.Canceled:
		return fmt.Errorf("%w: %s", ErrUnavailable, st.Message())
	case codes.NotFound:
		return ErrRunNotFound
	default:
		return &RejectedError{Reason: st.Message()}
	}
}

func toRunSnapshot(state, runID string, currentRound uint64, maxRounds uint32, modelVersion, algorithm string, registeredWorkers, healthyWorkers uint32) RunSnapshot {
	return RunSnapshot{
		RunID:             runID,
		State:             RunState(state),
		CurrentRound:      currentRound,
		MaxRounds:         maxRounds,
		ModelVersion:      modelVersion,
		Algorithm:         algorithm,
		RegisteredWorkers: registeredWorkers,
		HealthyWorkers:    healthyWorkers,
	}
}

func wireEventToEvent(wireEvent *eventsv1.CoordinatorEvent) Event {
	return Event{
		EventID:      wireEvent.GetEventId(),
		RunID:        wireEvent.GetRunId(),
		RoundID:      wireEvent.GetRoundId(),
		Type:         wireEvent.GetEventType(),
		ClientID:     wireEvent.GetClientId(),
		WorkerID:     wireEvent.GetWorkerId(),
		ModelVersion: wireEvent.GetModelVersion(),
		Timestamp:    wireEvent.GetTimestamp(),
		TraceID:      wireEvent.GetTraceId(),
	}
}
