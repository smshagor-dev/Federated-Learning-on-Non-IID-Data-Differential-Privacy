package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/privacy"
)

// TestCoordinatorCreateRunMapsPrivacyConfigThroughHTTP is a regression
// test for the Go control plane's privacy-config gap: a caller had no
// way to actually create a private run via the HTTP API even though
// CreateRunRequest.Privacy exists at every layer beneath it. Posts a
// hybrid-DP privacy body and asserts it reaches the mock coordinator
// client's CreateRun call.
func TestCoordinatorCreateRunMapsPrivacyConfigThroughHTTP(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)

	body, _ := json.Marshal(map[string]any{
		"run_id":     "run-hybrid",
		"algorithm":  "fedavg",
		"max_rounds": 3,
		"privacy": map[string]any{
			"mode": "hybrid_dp",
			"sample_level": map[string]any{
				"noise_multiplier": 0.9,
				"max_grad_norm":    1.2,
				"target_delta":     1e-6,
				"accountant":       "rdp",
			},
			"user_level": map[string]any{
				"noise_multiplier":       1.0,
				"target_delta":           1e-5,
				"initial_clipping_bound": 5.0,
				"weighting_strategy":     "uniform",
				"epsilon_budget":         50.0,
			},
		},
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs", bytes.NewReader(body))
	request.Header.Set("Authorization", bearerForResearcher(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusCreated {
		t.Fatalf("create run: expected 201, got %d: %s", recorder.Code, recorder.Body.String())
	}

	got := client.PrivacyConfigFor("run-hybrid")
	if got.Mode != coordinator.PrivacyModeHybrid {
		t.Errorf("Mode = %q, want %q", got.Mode, coordinator.PrivacyModeHybrid)
	}
	if got.SampleLevel.NoiseMultiplier != 0.9 {
		t.Errorf("SampleLevel.NoiseMultiplier = %v, want 0.9", got.SampleLevel.NoiseMultiplier)
	}
	if got.UserLevel.InitialClippingBound != 5.0 {
		t.Errorf("UserLevel.InitialClippingBound = %v, want 5.0", got.UserLevel.InitialClippingBound)
	}
	if got.UserLevel.EpsilonBudget != 50.0 {
		t.Errorf("UserLevel.EpsilonBudget = %v, want 50.0", got.UserLevel.EpsilonBudget)
	}
}

func TestCoordinatorPrivacyMetricsEndpoint(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	createCoordinatorRun(t, server, bearerForResearcher(t, server), "run-1")

	client.SeedPrivacyMetrics("run-1", coordinator.PrivacyMetricsSnapshot{
		RunID:          "run-1",
		HasSampleLevel: true,
		SampleEpsilon:  1.5,
		HasUserLevel:   true,
		UserEpsilon:    3.0,
	})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/privacy/metrics", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}

	var got coordinator.PrivacyMetricsSnapshot
	if err := json.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if got.SampleEpsilon != 1.5 || got.UserEpsilon != 3.0 {
		t.Errorf("epsilons not round-tripped through HTTP JSON: %+v", got)
	}
	// Critical Privacy Rule: the response must carry these as two
	// distinct fields, never a single combined "epsilon" key — decoding
	// the raw JSON as a generic map catches an accidental field rename
	// that a strongly-typed decode above wouldn't.
	var raw map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &raw); err != nil {
		t.Fatalf("decode raw response: %v", err)
	}
	if _, ok := raw["epsilon"]; ok {
		t.Error("response must never expose a single combined 'epsilon' field")
	}
	if _, ok := raw["sample_epsilon"]; !ok {
		t.Error("response missing sample_epsilon")
	}
	if _, ok := raw["user_epsilon"]; !ok {
		t.Error("response missing user_epsilon")
	}
}

func TestCoordinatorPrivacyMetricsUnknownRun(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/does-not-exist/privacy/metrics", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for unknown run, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCoordinatorPrivacyLedgerEndpoint(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	createCoordinatorRun(t, server, bearerForResearcher(t, server), "run-1")

	client.SeedPrivacyLedger("run-1", coordinator.PrivacyLedger{
		SampleLevelEntries: []coordinator.SampleLevelLedgerEntry{
			{RunID: "run-1", ClientID: "client-a", Epsilon: 1.1},
			{RunID: "run-1", ClientID: "client-b", Epsilon: 1.3},
		},
		UserLevelEntries: []coordinator.UserLevelLedgerEntry{
			{RunID: "run-1", RoundID: 1, Epsilon: 2.0, NumClients: 2},
		},
	})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/privacy/ledger", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}

	var got coordinator.PrivacyLedger
	if err := json.Unmarshal(recorder.Body.Bytes(), &got); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(got.SampleLevelEntries) != 2 {
		t.Errorf("SampleLevelEntries = %d entries, want 2", len(got.SampleLevelEntries))
	}
	if len(got.UserLevelEntries) != 1 {
		t.Errorf("UserLevelEntries = %d entries, want 1", len(got.UserLevelEntries))
	}
	if len(got.ClippingEntries) != 0 {
		t.Errorf("ClippingEntries = %d entries, want 0 (not seeded)", len(got.ClippingEntries))
	}
}

func TestCoordinatorPrivacyProjectionEndpointOmitsUnboundedBudget(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	createCoordinatorRun(t, server, bearerForResearcher(t, server), "run-1")

	client.SeedPrivacyProjection("run-1", coordinator.PrivacyProjection{
		HasUserLevel:             true,
		UserCurrentEpsilon:       2.0,
		UserProjectedNextEpsilon: 2.5,
		UserBudgetRemaining:      nil, // unbounded — must not crash JSON encoding
	})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/privacy/projection", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}

	var raw map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &raw); err != nil {
		t.Fatalf("decode raw response: %v", err)
	}
	if _, present := raw["user_budget_remaining"]; present {
		t.Error("user_budget_remaining should be omitted entirely when unbounded (omitempty nil pointer)")
	}
	if raw["user_projected_next_epsilon"] != 2.5 {
		t.Errorf("user_projected_next_epsilon = %v, want 2.5", raw["user_projected_next_epsilon"])
	}
}

func TestCoordinatorWorkersEndpoint(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	client.SeedWorker(coordinator.WorkerSummary{
		WorkerID: "worker-a",
		Status:   "IDLE",
		Privacy: coordinator.WorkerPrivacyCapabilities{
			SupportsSampleLevelDP: true,
			OpacusVersion:         "1.6.0",
			SupportedAccountants:  []string{"rdp"},
		},
	})
	client.SeedWorker(coordinator.WorkerSummary{WorkerID: "worker-b", Status: "IDLE"})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/workers", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}

	var body struct {
		Workers []coordinator.WorkerSummary `json:"workers"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(body.Workers) != 2 {
		t.Fatalf("expected 2 workers, got %d", len(body.Workers))
	}
	if body.Workers[0].WorkerID != "worker-a" || !body.Workers[0].Privacy.SupportsSampleLevelDP {
		t.Errorf("worker-a's privacy capabilities not exposed: %+v", body.Workers[0])
	}
	if body.Workers[1].Privacy.SupportsSampleLevelDP {
		t.Errorf("worker-b should not advertise sample-level DP support: %+v", body.Workers[1])
	}
}

func TestPrivacyCompatibilityEndpointFullMatrix(t *testing.T) {
	server := testServerWithCoordinator(nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/privacy/compatibility", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}

	var body struct {
		Algorithms []privacy.CompatibilityMatrix `json:"algorithms"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(body.Algorithms) != len(privacy.Algorithms) {
		t.Fatalf("expected %d algorithms, got %d", len(privacy.Algorithms), len(body.Algorithms))
	}
	// Static data must not require a configured coordinator — the mock
	// client above was passed as nil.
}

func TestPrivacyCompatibilityEndpointSingleAlgorithm(t *testing.T) {
	server := testServerWithCoordinator(nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/privacy/compatibility?algorithm=scaffold", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}

	var row privacy.CompatibilityMatrix
	if err := json.Unmarshal(recorder.Body.Bytes(), &row); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if row.Algorithm != "scaffold" {
		t.Errorf("Algorithm = %q, want scaffold", row.Algorithm)
	}
	if row.SampleLevel.Status != privacy.StatusUnsupported {
		t.Errorf("scaffold sample-level status = %v, want unsupported", row.SampleLevel.Status)
	}
}

func TestPrivacyCompatibilityEndpointUnknownAlgorithm(t *testing.T) {
	server := testServerWithCoordinator(nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/privacy/compatibility?algorithm=not-a-real-algorithm", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for unknown algorithm, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

// TestCoordinatorPrivacyMetricsEndpointUpdatesPrometheusGauge is a
// regression test for the Privacy Engineering phase's Prometheus wiring:
// fetching a run's /privacy/metrics must update the fl_privacy_epsilon
// gauge that GET /metrics later exposes, with mechanism as a label
// (never combined across mechanisms).
func TestCoordinatorPrivacyMetricsEndpointUpdatesPrometheusGauge(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	createCoordinatorRun(t, server, bearerForResearcher(t, server), "run-1")
	client.SeedPrivacyMetrics("run-1", coordinator.PrivacyMetricsSnapshot{
		RunID:          "run-1",
		HasSampleLevel: true,
		SampleEpsilon:  1.5,
		HasUserLevel:   true,
		UserEpsilon:    4.2,
	})

	metricsRequest := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/privacy/metrics", nil)
	metricsRequest.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(httptest.NewRecorder(), metricsRequest)

	promRecorder := httptest.NewRecorder()
	promRequest := httptest.NewRequest(http.MethodGet, "/metrics", nil)
	server.Handler().ServeHTTP(promRecorder, promRequest)
	body := promRecorder.Body.String()

	if !strings.Contains(body, `fl_privacy_epsilon{run_id="run-1",mechanism="sample_level"} 1.5`) {
		t.Errorf("/metrics missing sample_level gauge:\n%s", body)
	}
	if !strings.Contains(body, `fl_privacy_epsilon{run_id="run-1",mechanism="user_level"} 4.2`) {
		t.Errorf("/metrics missing user_level gauge:\n%s", body)
	}
}

func TestCoordinatorPrivacyEndpointsMethodNotAllowed(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	createCoordinatorRun(t, server, bearerForResearcher(t, server), "run-1")

	for _, path := range []string{
		"/api/v1/coordinator/runs/run-1/privacy/metrics",
		"/api/v1/coordinator/runs/run-1/privacy/ledger",
		"/api/v1/coordinator/runs/run-1/privacy/projection",
	} {
		recorder := httptest.NewRecorder()
		request := httptest.NewRequest(http.MethodPost, path, nil)
		request.Header.Set("Authorization", bearerForViewer(t, server))
		server.Handler().ServeHTTP(recorder, request)
		if recorder.Code != http.StatusMethodNotAllowed {
			t.Errorf("%s: expected 405 for POST, got %d", path, recorder.Code)
		}
	}
}
