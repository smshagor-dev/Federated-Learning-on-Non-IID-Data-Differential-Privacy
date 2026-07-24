package observability

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"sync"
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

// MetricsRecorder is safe for concurrent use (the zero value works — see
// TestMetricsRecorderSnapshot) since httpapi wires one instance into a
// middleware invoked from every request goroutine. routeCounts and
// coordinatorRPCCounts are unbounded only in the sense that they grow
// with the number of distinct (route)/(method,outcome) label
// combinations, which is small and fixed by the set of registered HTTP
// routes and coordinator RPCs — not by request volume.
type MetricsRecorder struct {
	mu                   sync.Mutex
	requests             int
	totalMillis          float64
	routeCounts          map[string]int
	coordinatorRPCCounts map[string]int // "<method>:<outcome>" -> count
}

func (r *MetricsRecorder) RecordRequest(latencyMS float64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.requests++
	r.totalMillis += latencyMS
}

// RecordRoute additionally tags the request with a route label (e.g.
// "GET /api/v1/coordinator/runs/{runId}") for the per-route Prometheus
// counter; RecordRequest alone only affects the aggregate total/average.
func (r *MetricsRecorder) RecordRoute(route string, latencyMS float64) {
	r.RecordRequest(latencyMS)
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.routeCounts == nil {
		r.routeCounts = make(map[string]int)
	}
	r.routeCounts[route]++
}

// RecordCoordinatorRPC tracks calls made through the coordinator.Client
// interface (see go/internal/coordinator), independent of which HTTP
// route triggered them — several routes can call the same RPC (e.g.
// GetRun backs both the run-detail and metrics endpoints).
func (r *MetricsRecorder) RecordCoordinatorRPC(method, outcome string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.coordinatorRPCCounts == nil {
		r.coordinatorRPCCounts = make(map[string]int)
	}
	r.coordinatorRPCCounts[method+":"+outcome]++
}

func (r *MetricsRecorder) Snapshot(activeRuns, queuedRuns, failedRuns int) MetricsSnapshot {
	r.mu.Lock()
	defer r.mu.Unlock()
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

// WritePrometheus renders counters in the Prometheus text exposition
// format (https://prometheus.io/docs/instrumenting/exposition_formats/).
// Hand-rolled rather than pulling in client_golang: the metric set here
// is small and fixed, and this repo otherwise favors the stdlib where a
// dependency isn't already justified elsewhere.
func (r *MetricsRecorder) WritePrometheus(w io.Writer) {
	r.mu.Lock()
	requests := r.requests
	totalMillis := r.totalMillis
	routeCounts := make(map[string]int, len(r.routeCounts))
	for k, v := range r.routeCounts {
		routeCounts[k] = v
	}
	coordinatorRPCCounts := make(map[string]int, len(r.coordinatorRPCCounts))
	for k, v := range r.coordinatorRPCCounts {
		coordinatorRPCCounts[k] = v
	}
	r.mu.Unlock()

	fmt.Fprintln(w, "# HELP fl_api_requests_total Total HTTP requests handled by the Go control-plane API.")
	fmt.Fprintln(w, "# TYPE fl_api_requests_total counter")
	fmt.Fprintf(w, "fl_api_requests_total %d\n", requests)

	fmt.Fprintln(w, "# HELP fl_api_request_duration_ms_sum Sum of HTTP request durations in milliseconds.")
	fmt.Fprintln(w, "# TYPE fl_api_request_duration_ms_sum counter")
	fmt.Fprintf(w, "fl_api_request_duration_ms_sum %g\n", totalMillis)

	fmt.Fprintln(w, "# HELP fl_api_requests_by_route_total HTTP requests handled, broken down by route.")
	fmt.Fprintln(w, "# TYPE fl_api_requests_by_route_total counter")
	for _, route := range sortedKeys(routeCounts) {
		fmt.Fprintf(w, "fl_api_requests_by_route_total{route=%q} %d\n", route, routeCounts[route])
	}

	fmt.Fprintln(w, "# HELP fl_coordinator_rpc_total Coordinator RPCs issued by the Go control-plane, by method and outcome.")
	fmt.Fprintln(w, "# TYPE fl_coordinator_rpc_total counter")
	for _, key := range sortedKeys(coordinatorRPCCounts) {
		method, outcome := splitMethodOutcome(key)
		fmt.Fprintf(w, "fl_coordinator_rpc_total{method=%q,outcome=%q} %d\n", method, outcome, coordinatorRPCCounts[key])
	}
}

func sortedKeys(m map[string]int) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func splitMethodOutcome(key string) (method, outcome string) {
	for i := len(key) - 1; i >= 0; i-- {
		if key[i] == ':' {
			return key[:i], key[i+1:]
		}
	}
	return key, ""
}
