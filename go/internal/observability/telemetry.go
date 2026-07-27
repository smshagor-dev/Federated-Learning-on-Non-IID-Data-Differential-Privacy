package observability

import (
	"encoding/json"
	"fmt"
	"io"
	"sort"
	"strings"
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

	// Privacy Engineering phase (docs/privacy-mathematics.md's Critical
	// Privacy Rule): the last-observed epsilon per (run_id, mechanism),
	// updated whenever a client fetches a run's /privacy/metrics — never
	// combined across mechanisms, exposed as a gauge labeled by
	// mechanism precisely so Prometheus/Grafana can never accidentally
	// sum sample-level and user-level epsilon into one series. Bounded
	// by the number of distinct runs this process has observed privacy
	// data for over its lifetime — not by request volume, same
	// reasoning as routeCounts above.
	privacyEpsilon map[string]float64 // "<run_id>:<mechanism>" -> epsilon

	// Counts PRIVACY_BUDGET_WARNING/PRIVACY_BUDGET_EXCEEDED events
	// observed while relaying a run's event stream (see
	// handleCoordinatorRunEvents) — bounded by the fixed
	// (mechanism, event_type) label combinations, same as
	// coordinatorRPCCounts.
	privacyBudgetEvents map[string]int // "<mechanism>:<event_type>" -> count

	// Security Events, Metrics, and Durable Audit Journal slice
	// (docs/security-metrics.md): bounded by (source_service x category x
	// severity x outcome) -- a handful of fixed values each, same
	// low-cardinality-by-construction reasoning as privacyBudgetEvents
	// above. Fed from CoordinatorService.emitSecurityEvent, so it
	// currently reflects Go-originated events only (permission denials,
	// idempotency outcomes, mutation accepted/rejected, audit access) --
	// C++/Python-originated per-event counts are not individually relayed
	// into this counter (that would mean either a background poller re-
	// deriving individual events from ListSecurityEvents, or the C++ side
	// exposing its own Prometheus endpoint, both out of scope here per
	// the "no new C++ Prometheus endpoint" decision). What IS relayed,
	// below, is the coordinator's own aggregate event-source health,
	// fed on every GET /api/v1/security/events/sources request.
	securityEvents map[string]int // "<source_service>:<category>:<severity>:<outcome>" -> count

	// Web Security Center, Event Centralization, and Security CI slice:
	// last-observed event-source health gauges, fed by
	// RecordSecurityEventSourceHealth every time handleSecurityEventSources
	// polls the coordinator's GetSecurityEventSourceHealth RPC (plus the
	// Go-local journal's own health) -- gauges, not locally-accumulated
	// counters, since batch accept/reject counts are the coordinator's
	// own aggregate, not something this Go process increments itself.
	// source_service is one of a small fixed set ("go-api", "coordinator",
	// "python-worker") -- low-cardinality by construction.
	securityEventSourceRecords         map[string]uint64  // "<source_service>" -> record_count
	securityEventSourceBatches         map[string]uint64  // "<source_service>:<outcome>" -> count (outcome: accepted|rejected)
	securityEventSourceDistinctWorkers map[string]uint64  // "<source_service>" -> distinct_workers_seen
	securityEventSourceLagSeconds      map[string]float64 // "<source_service>" -> lag_seconds (only set when known)

	// Secure User-Level DP Operations, Observability, and Release
	// Evidence slice (docs/secure-user-level-operations-audit.md), Work
	// Area E/F: a bounded, representative subset of the requested ~31
	// named metrics -- request-level counters Go itself observes handling
	// the 5 new /api/v1/secure-aggregation/privacy/* routes, plus
	// aggregate-only gauges fed by polling GetSecureUserLevelPrivacyHealth
	// (never per-run/per-round -- run_id/round_id are on this metric
	// family's own forbidden-label list, unlike the older fl_privacy_epsilon
	// gauge above). No epsilon-spent/remaining gauge exists here for the
	// same reason: epsilon is inherently per-run, and a per-run label
	// would violate that same forbidden-label list -- per-run epsilon
	// stays API-only (GetSecureUserLevelPrivacyBudget), never a metric.
	secureUserDPRouteRequests   map[string]int    // "<route>:<outcome>" -> count
	secureUserDPActiveRuns      uint64            // last-observed gauge
	secureUserDPReconciliation  bool              // last-observed gauge
	secureUserDPComponentStatus map[string]string // "<component>" -> status ("ok"/"degraded"/"unavailable")
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

// RecordPrivacyEpsilon sets the last-observed epsilon gauge for
// (runID, mechanism). mechanism must be one of "sample_level",
// "user_level", "clipping" — callers pass these independently per
// mechanism, never a combined value (see the struct field's doc
// comment).
func (r *MetricsRecorder) RecordPrivacyEpsilon(runID, mechanism string, epsilon float64) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.privacyEpsilon == nil {
		r.privacyEpsilon = make(map[string]float64)
	}
	r.privacyEpsilon[runID+":"+mechanism] = epsilon
}

// RecordPrivacyBudgetEvent increments the counter for a
// PRIVACY_BUDGET_WARNING/PRIVACY_BUDGET_EXCEEDED event observed for
// mechanism (see the struct field's doc comment).
func (r *MetricsRecorder) RecordPrivacyBudgetEvent(mechanism, eventType string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.privacyBudgetEvents == nil {
		r.privacyBudgetEvents = make(map[string]int)
	}
	r.privacyBudgetEvents[mechanism+":"+eventType]++
}

// securityEventCategory coarsens the ~55-value event_type enum into a
// handful of buckets -- Work Package requirement "Avoid high-cardinality
// labels," mirroring python/src/fl_platform/security/metrics.py's
// event_category function exactly (kept in sync by hand).
func securityEventCategory(eventType string) string {
	switch {
	case strings.HasPrefix(eventType, "TRANSPORT_"), strings.HasPrefix(eventType, "PEER_CERTIFICATE_"),
		strings.HasPrefix(eventType, "CERTIFICATE_"):
		return "transport"
	case strings.HasPrefix(eventType, "WORKER_KEY_"), eventType == "MESSAGE_REJECTED_BY_KEY_STATE":
		return "worker_signing_key"
	case strings.HasPrefix(eventType, "WORKER_"):
		return "worker_identity"
	case strings.HasPrefix(eventType, "CAPABILITY_"), strings.HasPrefix(eventType, "HEARTBEAT_"),
		strings.HasPrefix(eventType, "CLIENT_RESULT_"), strings.HasPrefix(eventType, "PRIVACY_RECORD_"),
		strings.HasPrefix(eventType, "SIGNATURE_"), strings.HasPrefix(eventType, "PAYLOAD_HASH_"),
		strings.HasPrefix(eventType, "MESSAGE_"):
		return "signed_message"
	case strings.HasPrefix(eventType, "COORDINATOR_"), strings.HasPrefix(eventType, "ACCEPTED_TASK_"),
		strings.HasPrefix(eventType, "DUPLICATE_TASK_"), eventType == "TASK_REISSUED":
		return "coordinator_task"
	case strings.HasPrefix(eventType, "SECURITY_"), strings.HasPrefix(eventType, "IDEMPOTENCY_"):
		return "administration"
	default:
		return "other"
	}
}

// RecordSecurityEvent feeds fl_security_events_total. sourceService is
// typically "go-api" (see CoordinatorService.emitSecurityEvent) but the
// label exists so a future relay of C++/Python-originated events can
// share the same counter without a schema change.
func (r *MetricsRecorder) RecordSecurityEvent(sourceService, eventType, severity, outcome string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.securityEvents == nil {
		r.securityEvents = make(map[string]int)
	}
	key := sourceService + ":" + securityEventCategory(eventType) + ":" + severity + ":" + outcome
	r.securityEvents[key]++
}

// RecordSecurityEventSourceHealth updates the last-observed
// event-centralization gauges for one source (see the struct fields'
// doc comment). hasLag distinguishes "lag is 0 seconds" from "lag is
// unknown" (no record yet) -- an unknown lag is simply not recorded,
// never coerced to 0, so a "fresh" gauge value can never be confused
// with a "no data yet" one.
func (r *MetricsRecorder) RecordSecurityEventSourceHealth(
	sourceService string, recordCount, batchesAccepted, batchesRejected, distinctWorkersSeen uint64,
	lagSeconds float64, hasLag bool,
) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.securityEventSourceRecords == nil {
		r.securityEventSourceRecords = make(map[string]uint64)
	}
	if r.securityEventSourceBatches == nil {
		r.securityEventSourceBatches = make(map[string]uint64)
	}
	if r.securityEventSourceDistinctWorkers == nil {
		r.securityEventSourceDistinctWorkers = make(map[string]uint64)
	}
	if r.securityEventSourceLagSeconds == nil {
		r.securityEventSourceLagSeconds = make(map[string]float64)
	}
	r.securityEventSourceRecords[sourceService] = recordCount
	r.securityEventSourceBatches[sourceService+":accepted"] = batchesAccepted
	r.securityEventSourceBatches[sourceService+":rejected"] = batchesRejected
	r.securityEventSourceDistinctWorkers[sourceService] = distinctWorkersSeen
	if hasLag {
		r.securityEventSourceLagSeconds[sourceService] = lagSeconds
	}
}

// RecordSecureUserDPRouteRequest increments the per-route/outcome
// counter for one of the 5 new /api/v1/secure-aggregation/privacy/*
// routes. route is one of "status"/"health"/"rounds"/"round"/"budget"
// -- a small fixed set, low-cardinality by construction.
func (r *MetricsRecorder) RecordSecureUserDPRouteRequest(route, outcome string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.secureUserDPRouteRequests == nil {
		r.secureUserDPRouteRequests = make(map[string]int)
	}
	r.secureUserDPRouteRequests[route+":"+outcome]++
}

// RecordSecureUserDPHealth updates the last-observed aggregate gauges
// fed by polling GetSecureUserLevelPrivacyHealth -- see the struct
// fields' doc comment for why no per-run gauge exists here.
func (r *MetricsRecorder) RecordSecureUserDPHealth(
	activeRuns uint64, reconciliationRequired bool,
	providerStatus, noiseProviderStatus, accountantStatus, ledgerStatus, eventJournalStatus string,
) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.secureUserDPActiveRuns = activeRuns
	r.secureUserDPReconciliation = reconciliationRequired
	if r.secureUserDPComponentStatus == nil {
		r.secureUserDPComponentStatus = make(map[string]string)
	}
	r.secureUserDPComponentStatus["provider"] = providerStatus
	r.secureUserDPComponentStatus["noise_provider"] = noiseProviderStatus
	r.secureUserDPComponentStatus["accountant"] = accountantStatus
	r.secureUserDPComponentStatus["ledger"] = ledgerStatus
	r.secureUserDPComponentStatus["event_journal"] = eventJournalStatus
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
	privacyEpsilon := make(map[string]float64, len(r.privacyEpsilon))
	for k, v := range r.privacyEpsilon {
		privacyEpsilon[k] = v
	}
	privacyBudgetEvents := make(map[string]int, len(r.privacyBudgetEvents))
	for k, v := range r.privacyBudgetEvents {
		privacyBudgetEvents[k] = v
	}
	securityEvents := make(map[string]int, len(r.securityEvents))
	for k, v := range r.securityEvents {
		securityEvents[k] = v
	}
	securityEventSourceRecords := make(map[string]uint64, len(r.securityEventSourceRecords))
	for k, v := range r.securityEventSourceRecords {
		securityEventSourceRecords[k] = v
	}
	securityEventSourceBatches := make(map[string]uint64, len(r.securityEventSourceBatches))
	for k, v := range r.securityEventSourceBatches {
		securityEventSourceBatches[k] = v
	}
	securityEventSourceDistinctWorkers := make(map[string]uint64, len(r.securityEventSourceDistinctWorkers))
	for k, v := range r.securityEventSourceDistinctWorkers {
		securityEventSourceDistinctWorkers[k] = v
	}
	securityEventSourceLagSeconds := make(map[string]float64, len(r.securityEventSourceLagSeconds))
	for k, v := range r.securityEventSourceLagSeconds {
		securityEventSourceLagSeconds[k] = v
	}
	secureUserDPRouteRequests := make(map[string]int, len(r.secureUserDPRouteRequests))
	for k, v := range r.secureUserDPRouteRequests {
		secureUserDPRouteRequests[k] = v
	}
	secureUserDPActiveRuns := r.secureUserDPActiveRuns
	secureUserDPReconciliation := r.secureUserDPReconciliation
	secureUserDPComponentStatus := make(map[string]string, len(r.secureUserDPComponentStatus))
	for k, v := range r.secureUserDPComponentStatus {
		secureUserDPComponentStatus[k] = v
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

	// Privacy Engineering phase (docs/privacy-mathematics.md's Critical
	// Privacy Rule): mechanism is always a label, never folded into the
	// value — a PromQL query that sums across mechanism by accident
	// still produces a labeled breakdown an operator can immediately
	// see is wrong, rather than a silently-combined epsilon.
	fmt.Fprintln(w, "# HELP fl_privacy_epsilon Last-observed epsilon for a run's privacy mechanism (never combined across mechanisms).")
	fmt.Fprintln(w, "# TYPE fl_privacy_epsilon gauge")
	for _, key := range sortedKeys2(privacyEpsilon) {
		runID, mechanism := splitMethodOutcome(key)
		fmt.Fprintf(w, "fl_privacy_epsilon{run_id=%q,mechanism=%q} %g\n", runID, mechanism, privacyEpsilon[key])
	}

	fmt.Fprintln(w, "# HELP fl_privacy_budget_events_total Privacy budget warning/exceeded events observed, by mechanism and event type.")
	fmt.Fprintln(w, "# TYPE fl_privacy_budget_events_total counter")
	for _, key := range sortedKeys(privacyBudgetEvents) {
		mechanism, eventType := splitMethodOutcome(key)
		fmt.Fprintf(w, "fl_privacy_budget_events_total{mechanism=%q,event_type=%q} %d\n", mechanism, eventType, privacyBudgetEvents[key])
	}

	// Security Events, Metrics, and Durable Audit Journal slice
	// (docs/security-metrics.md): source_service/category/severity/outcome
	// are each a small, fixed set of values -- deliberately never the raw
	// ~55-value event_type (see securityEventCategory's doc comment).
	fmt.Fprintln(w, "# HELP fl_security_events_total Security-relevant events observed, by source service/category/severity/outcome.")
	fmt.Fprintln(w, "# TYPE fl_security_events_total counter")
	for _, key := range sortedKeys(securityEvents) {
		sourceService, category, severity, outcome := splitSecurityEventKey(key)
		fmt.Fprintf(w, "fl_security_events_total{source_service=%q,category=%q,severity=%q,outcome=%q} %d\n",
			sourceService, category, severity, outcome, securityEvents[key])
	}

	// Web Security Center, Event Centralization, and Security CI slice
	// (docs/security-event-centralization.md): last-observed event-source
	// health, fed on every GET /api/v1/security/events/sources request --
	// source_service is one of "go-api"/"coordinator"/"python-worker",
	// low-cardinality by construction (see RecordSecurityEventSourceHealth).
	fmt.Fprintln(w, "# HELP fl_security_event_source_records Last-observed record count for a security-event source.")
	fmt.Fprintln(w, "# TYPE fl_security_event_source_records gauge")
	for _, sourceService := range sortedKeysUint64(securityEventSourceRecords) {
		fmt.Fprintf(w, "fl_security_event_source_records{source_service=%q} %d\n",
			sourceService, securityEventSourceRecords[sourceService])
	}

	fmt.Fprintln(w, "# HELP fl_security_event_source_batches Last-observed worker security-event batch accept/reject counts, by source and outcome.")
	fmt.Fprintln(w, "# TYPE fl_security_event_source_batches gauge")
	for _, key := range sortedKeysUint64(securityEventSourceBatches) {
		sourceService, outcome := splitMethodOutcome(key)
		fmt.Fprintf(w, "fl_security_event_source_batches{source_service=%q,outcome=%q} %d\n",
			sourceService, outcome, securityEventSourceBatches[key])
	}

	fmt.Fprintln(w, "# HELP fl_security_event_source_distinct_workers Last-observed distinct worker_id count seen submitting security-event batches, by source.")
	fmt.Fprintln(w, "# TYPE fl_security_event_source_distinct_workers gauge")
	for _, sourceService := range sortedKeysUint64(securityEventSourceDistinctWorkers) {
		fmt.Fprintf(w, "fl_security_event_source_distinct_workers{source_service=%q} %d\n",
			sourceService, securityEventSourceDistinctWorkers[sourceService])
	}

	fmt.Fprintln(w, "# HELP fl_security_event_source_lag_seconds Seconds since the last event/batch accepted from a security-event source, by source.")
	fmt.Fprintln(w, "# TYPE fl_security_event_source_lag_seconds gauge")
	for _, sourceService := range sortedKeys2(securityEventSourceLagSeconds) {
		fmt.Fprintf(w, "fl_security_event_source_lag_seconds{source_service=%q} %g\n",
			sourceService, securityEventSourceLagSeconds[sourceService])
	}

	// Secure User-Level DP Operations, Observability, and Release
	// Evidence slice (docs/secure-user-level-operations-audit.md): route
	// is one of "status"/"health"/"rounds"/"round"/"budget", outcome is
	// "ok"/"error" -- both small fixed sets.
	fmt.Fprintln(w, "# HELP fl_secure_user_dp_route_requests_total Requests handled by the secure user-level-DP privacy observability routes, by route and outcome.")
	fmt.Fprintln(w, "# TYPE fl_secure_user_dp_route_requests_total counter")
	for _, key := range sortedKeys(secureUserDPRouteRequests) {
		route, outcome := splitMethodOutcome(key)
		fmt.Fprintf(w, "fl_secure_user_dp_route_requests_total{route=%q,outcome=%q} %d\n",
			route, outcome, secureUserDPRouteRequests[key])
	}

	fmt.Fprintln(w, "# HELP fl_secure_user_dp_active_runs Last-observed count of runs currently using the secure user-level-DP mechanism (aggregate only, never per-run).")
	fmt.Fprintln(w, "# TYPE fl_secure_user_dp_active_runs gauge")
	fmt.Fprintf(w, "fl_secure_user_dp_active_runs %d\n", secureUserDPActiveRuns)

	fmt.Fprintln(w, "# HELP fl_secure_user_dp_reconciliation_required 1 if the secure user-level-DP runtime currently reports a reconciliation-required condition, else 0.")
	fmt.Fprintln(w, "# TYPE fl_secure_user_dp_reconciliation_required gauge")
	reconciliationValue := 0
	if secureUserDPReconciliation {
		reconciliationValue = 1
	}
	fmt.Fprintf(w, "fl_secure_user_dp_reconciliation_required %d\n", reconciliationValue)

	// component is one of "provider"/"noise_provider"/"accountant"/
	// "ledger"/"event_journal"; status is one of "ok"/"degraded"/
	// "unavailable" -- both small fixed sets, an info-style gauge (value
	// always 1 for the currently-observed status).
	fmt.Fprintln(w, "# HELP fl_secure_user_dp_component_status 1 for the currently-observed status of a secure user-level-DP runtime component, by component and status.")
	fmt.Fprintln(w, "# TYPE fl_secure_user_dp_component_status gauge")
	for _, component := range sortedKeysString(secureUserDPComponentStatus) {
		fmt.Fprintf(w, "fl_secure_user_dp_component_status{component=%q,status=%q} 1\n",
			component, secureUserDPComponentStatus[component])
	}
}

func splitSecurityEventKey(key string) (sourceService, category, severity, outcome string) {
	parts := strings.SplitN(key, ":", 4)
	for len(parts) < 4 {
		parts = append(parts, "")
	}
	return parts[0], parts[1], parts[2], parts[3]
}

func sortedKeys2(m map[string]float64) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func sortedKeys(m map[string]int) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func sortedKeysString(m map[string]string) []string {
	keys := make([]string, 0, len(m))
	for k := range m {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	return keys
}

func sortedKeysUint64(m map[string]uint64) []string {
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
