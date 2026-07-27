package observability

import (
	"bytes"
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

// TestMetricsRecorderPrivacyEpsilonNeverCombinesMechanisms is a
// regression test for the Critical Privacy Rule (docs/privacy-
// mathematics.md): recording epsilon for two different mechanisms on
// the same run must produce two independent gauge series, never a
// summed or overwritten single value.
func TestMetricsRecorderPrivacyEpsilonNeverCombinesMechanisms(t *testing.T) {
	recorder := &MetricsRecorder{}
	recorder.RecordPrivacyEpsilon("run-1", "sample_level", 1.5)
	recorder.RecordPrivacyEpsilon("run-1", "user_level", 4.2)

	var buf bytes.Buffer
	recorder.WritePrometheus(&buf)
	output := buf.String()

	if !strings.Contains(output, `fl_privacy_epsilon{run_id="run-1",mechanism="sample_level"} 1.5`) {
		t.Errorf("sample_level gauge not rendered correctly:\n%s", output)
	}
	if !strings.Contains(output, `fl_privacy_epsilon{run_id="run-1",mechanism="user_level"} 4.2`) {
		t.Errorf("user_level gauge not rendered correctly:\n%s", output)
	}
	if strings.Contains(output, "5.7") {
		t.Error("output must never contain the sum of two mechanisms' epsilon (1.5+4.2=5.7)")
	}
}

func TestMetricsRecorderPrivacyEpsilonUpdatesInPlace(t *testing.T) {
	recorder := &MetricsRecorder{}
	recorder.RecordPrivacyEpsilon("run-1", "user_level", 1.0)
	recorder.RecordPrivacyEpsilon("run-1", "user_level", 2.0)

	var buf bytes.Buffer
	recorder.WritePrometheus(&buf)
	output := buf.String()
	if !strings.Contains(output, `fl_privacy_epsilon{run_id="run-1",mechanism="user_level"} 2`) {
		t.Errorf("expected the latest recorded epsilon (2.0), got:\n%s", output)
	}
	if strings.Contains(output, `mechanism="user_level"} 1`) {
		t.Errorf("stale epsilon value should have been overwritten, not accumulated:\n%s", output)
	}
}

func TestMetricsRecorderPrivacyBudgetEvents(t *testing.T) {
	recorder := &MetricsRecorder{}
	recorder.RecordPrivacyBudgetEvent("user_level", "PRIVACY_BUDGET_WARNING")
	recorder.RecordPrivacyBudgetEvent("user_level", "PRIVACY_BUDGET_WARNING")
	recorder.RecordPrivacyBudgetEvent("user_level", "PRIVACY_BUDGET_EXCEEDED")

	var buf bytes.Buffer
	recorder.WritePrometheus(&buf)
	output := buf.String()
	if !strings.Contains(output, `fl_privacy_budget_events_total{mechanism="user_level",event_type="PRIVACY_BUDGET_WARNING"} 2`) {
		t.Errorf("warning count not rendered correctly:\n%s", output)
	}
	if !strings.Contains(output, `fl_privacy_budget_events_total{mechanism="user_level",event_type="PRIVACY_BUDGET_EXCEEDED"} 1`) {
		t.Errorf("exceeded count not rendered correctly:\n%s", output)
	}
}

func TestMetricsRecorderSecurityEventsLowCardinalityCategory(t *testing.T) {
	recorder := &MetricsRecorder{}
	recorder.RecordSecurityEvent("go-api", EventWorkerSuspended, SeverityWarning, OutcomeCompleted)
	recorder.RecordSecurityEvent("go-api", EventWorkerActivated, SeverityInfo, OutcomeCompleted)
	recorder.RecordSecurityEvent("go-api", EventSecurityPermissionDenied, SeverityWarning, OutcomeBlocked)

	var buf bytes.Buffer
	recorder.WritePrometheus(&buf)
	output := buf.String()

	// Two WORKER_* events with the same severity/outcome share one
	// worker_identity/WARNING/COMPLETED... no wait, different severities
	// (WARNING vs INFO) -- verifies category coarsening (not the raw
	// event_type) is actually applied, while distinct severity/outcome
	// combinations remain separate series.
	if !strings.Contains(output, `fl_security_events_total{source_service="go-api",category="worker_identity",severity="WARNING",outcome="COMPLETED"} 1`) {
		t.Errorf("expected a worker_identity/WARNING/COMPLETED series:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_events_total{source_service="go-api",category="worker_identity",severity="INFO",outcome="COMPLETED"} 1`) {
		t.Errorf("expected a worker_identity/INFO/COMPLETED series:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_events_total{source_service="go-api",category="administration",severity="WARNING",outcome="BLOCKED"} 1`) {
		t.Errorf("expected an administration/WARNING/BLOCKED series:\n%s", output)
	}
	if strings.Contains(output, "WORKER_SUSPENDED") || strings.Contains(output, "SECURITY_PERMISSION_DENIED") {
		t.Errorf("raw event_type must never appear as a label value (high-cardinality):\n%s", output)
	}
}

func TestMetricsRecorderSecurityEventSourceHealthGauges(t *testing.T) {
	recorder := &MetricsRecorder{}
	recorder.RecordSecurityEventSourceHealth("python-worker", 12, 3, 1, 2, 4.5, true)
	recorder.RecordSecurityEventSourceHealth("go-api", 40, 0, 0, 0, 0, false)

	var buf bytes.Buffer
	recorder.WritePrometheus(&buf)
	output := buf.String()

	if !strings.Contains(output, `fl_security_event_source_records{source_service="python-worker"} 12`) {
		t.Errorf("expected python-worker record count gauge:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_event_source_batches{source_service="python-worker",outcome="accepted"} 3`) {
		t.Errorf("expected python-worker accepted-batches gauge:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_event_source_batches{source_service="python-worker",outcome="rejected"} 1`) {
		t.Errorf("expected python-worker rejected-batches gauge:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_event_source_distinct_workers{source_service="python-worker"} 2`) {
		t.Errorf("expected python-worker distinct-workers gauge:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_event_source_lag_seconds{source_service="python-worker"} 4.5`) {
		t.Errorf("expected python-worker lag gauge:\n%s", output)
	}
	// go-api was recorded with hasLag=false -- its lag must not appear
	// as a spurious 0, which would be indistinguishable from "genuinely
	// fresh" rather than "unknown."
	if strings.Contains(output, `fl_security_event_source_lag_seconds{source_service="go-api"}`) {
		t.Errorf("an unknown lag (hasLag=false) must not be rendered at all:\n%s", output)
	}
	if !strings.Contains(output, `fl_security_event_source_records{source_service="go-api"} 40`) {
		t.Errorf("expected go-api record count gauge even with an unknown lag:\n%s", output)
	}
}
