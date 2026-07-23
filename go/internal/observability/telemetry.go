package observability

import (
	"encoding/json"
	"time"
)

type TelemetryEvent struct {
	Service    string            `json:"service"`
	EventType  string            `json:"event_type"`
	Timestamp  time.Time         `json:"timestamp"`
	RunID      string            `json:"run_id,omitempty"`
	RoundID    int               `json:"round_id,omitempty"`
	TraceID    string            `json:"trace_id,omitempty"`
	Attributes map[string]string `json:"attributes,omitempty"`
}

func (e TelemetryEvent) JSON() (string, error) {
	blob, err := json.Marshal(e)
	if err != nil {
		return "", err
	}
	return string(blob), nil
}

type MetricsSnapshot struct {
	ActiveRuns       int     `json:"active_runs"`
	QueuedRuns       int     `json:"queued_runs"`
	FailedRuns       int     `json:"failed_runs"`
	APIRequestsTotal int     `json:"api_requests_total"`
	AverageLatencyMS float64 `json:"average_latency_ms"`
}

type MetricsRecorder struct {
	requests    int
	totalMillis float64
}

func (r *MetricsRecorder) RecordRequest(latencyMS float64) {
	r.requests++
	r.totalMillis += latencyMS
}

func (r *MetricsRecorder) Snapshot(activeRuns, queuedRuns, failedRuns int) MetricsSnapshot {
	average := 0.0
	if r.requests > 0 {
		average = r.totalMillis / float64(r.requests)
	}
	return MetricsSnapshot{
		ActiveRuns:       activeRuns,
		QueuedRuns:       queuedRuns,
		FailedRuns:       failedRuns,
		APIRequestsTotal: r.requests,
		AverageLatencyMS: average,
	}
}
