package observability

import (
	"strings"
	"testing"
	"time"
)

func TestTelemetryEventJSON(t *testing.T) {
	event := TelemetryEvent{
		Service:   "go-api",
		EventType: "run.created",
		Timestamp: time.Date(2026, 7, 22, 14, 0, 0, 0, time.UTC),
		RunID:     "run-1",
		TraceID:   "trace-1",
		Attributes: map[string]string{
			"status": "CREATED",
		},
	}
	blob, err := event.JSON()
	if err != nil {
		t.Fatalf("json: %v", err)
	}
	if !strings.Contains(blob, `"event_type":"run.created"`) {
		t.Fatalf("unexpected json: %s", blob)
	}
}

func TestMetricsRecorderSnapshot(t *testing.T) {
	recorder := &MetricsRecorder{}
	recorder.RecordRequest(10)
	recorder.RecordRequest(20)
	snapshot := recorder.Snapshot(2, 1, 0)
	if snapshot.APIRequestsTotal != 2 {
		t.Fatalf("expected 2 requests, got %d", snapshot.APIRequestsTotal)
	}
	if snapshot.AverageLatencyMS != 15 {
		t.Fatalf("expected average 15, got %v", snapshot.AverageLatencyMS)
	}
}
