package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

func testServerWithCoordinator(client coordinator.Client) *Server {
	services := application.NewServicesWithCoordinator(
		projects.NewInMemoryRepository(),
		experiments.NewInMemoryRepository(),
		runs.NewInMemoryRepository(),
		auth.NewInMemoryUserRepository(application.DefaultUsers(testClock)),
		auth.NewInMemorySessionRepository(),
		nil,
		client,
		testClock,
	)
	services.Auth.SetTokenSourceForTesting(func() (string, error) { return "token-test", nil })
	return NewServer(services)
}

func createCoordinatorRun(t *testing.T, server *Server, bearer, runID string) {
	t.Helper()
	body, _ := json.Marshal(map[string]any{"run_id": runID, "algorithm": "fedavg", "max_rounds": 3})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs", bytes.NewReader(body))
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusCreated {
		t.Fatalf("create coordinator run: expected 201, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCoordinatorHealthUnconfigured(t *testing.T) {
	server := testServerWithCoordinator(nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/system/coordinator-health", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when coordinator not configured, got %d", recorder.Code)
	}
}

func TestCoordinatorHealthConfigured(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/system/coordinator-health", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCoordinatorCreateRunRequiresResearcherOrAdmin(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	body, _ := json.Marshal(map[string]any{"run_id": "run-1", "algorithm": "fedavg", "max_rounds": 3})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs", bytes.NewReader(body))
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for viewer, got %d", recorder.Code)
	}
}

func TestCoordinatorCreateRunAndLifecycle(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")

	start := httptest.NewRecorder()
	startReq := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs/run-1/start", nil)
	startReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(start, startReq)
	if start.Code != http.StatusOK {
		t.Fatalf("start: expected 200, got %d: %s", start.Code, start.Body.String())
	}
	var startedSnapshot coordinator.RunSnapshot
	if err := json.Unmarshal(start.Body.Bytes(), &startedSnapshot); err != nil {
		t.Fatalf("decode start response: %v", err)
	}
	if startedSnapshot.State != "RUNNING" {
		t.Fatalf("expected RUNNING, got %s", startedSnapshot.State)
	}

	get := httptest.NewRecorder()
	getReq := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1", nil)
	getReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(get, getReq)
	if get.Code != http.StatusOK {
		t.Fatalf("get: expected 200, got %d", get.Code)
	}

	round := httptest.NewRecorder()
	roundReq := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/rounds/current", nil)
	roundReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(round, roundReq)
	if round.Code != http.StatusOK {
		t.Fatalf("current round: expected 200, got %d", round.Code)
	}

	metrics := httptest.NewRecorder()
	metricsReq := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/metrics", nil)
	metricsReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(metrics, metricsReq)
	if metrics.Code != http.StatusOK {
		t.Fatalf("metrics: expected 200, got %d", metrics.Code)
	}
	var metricsPayload application.RunMetrics
	if err := json.Unmarshal(metrics.Body.Bytes(), &metricsPayload); err != nil {
		t.Fatalf("decode metrics response: %v", err)
	}
	if metricsPayload.MaxRounds != 3 {
		t.Fatalf("expected max_rounds 3, got %d", metricsPayload.MaxRounds)
	}

	cancel := httptest.NewRecorder()
	cancelBody, _ := json.Marshal(map[string]string{"reason": "operator abort"})
	cancelReq := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs/run-1/cancel", bytes.NewReader(cancelBody))
	cancelReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(cancel, cancelReq)
	if cancel.Code != http.StatusOK {
		t.Fatalf("cancel: expected 200, got %d: %s", cancel.Code, cancel.Body.String())
	}
}

func TestCoordinatorGetRunNotFound(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	bearer := bearerForViewer(t, server)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/does-not-exist", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", recorder.Code)
	}
}

func TestCoordinatorDuplicateCreateReturnsConflict(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")

	body, _ := json.Marshal(map[string]any{"run_id": "run-1", "algorithm": "fedavg", "max_rounds": 3})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs", bytes.NewReader(body))
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusConflict {
		t.Fatalf("expected 409 on duplicate run_id, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCoordinatorRunsUnconfiguredReturns503(t *testing.T) {
	server := testServerWithCoordinator(nil)
	bearer := bearerForResearcher(t, server)
	body, _ := json.Marshal(map[string]any{"run_id": "run-1", "algorithm": "fedavg", "max_rounds": 3})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/coordinator/runs", bytes.NewReader(body))
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when coordinator not configured, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCoordinatorRunEventsStreamsServerSentEvents(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")

	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/events", nil).WithContext(ctx)
	request.Header.Set("Authorization", bearer)

	done := make(chan struct{})
	go func() {
		server.Handler().ServeHTTP(recorder, request)
		close(done)
	}()

	// The handler loop exits once the request context is done (simulating
	// client disconnect/timeout); wait for that with a generous upper bound
	// so a regression that drops the ctx.Done() case fails instead of hanging.
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("SSE handler did not return after context cancellation")
	}
	if recorder.Header().Get("Content-Type") != "text/event-stream" {
		t.Fatalf("expected text/event-stream content type, got %q", recorder.Header().Get("Content-Type"))
	}
}
