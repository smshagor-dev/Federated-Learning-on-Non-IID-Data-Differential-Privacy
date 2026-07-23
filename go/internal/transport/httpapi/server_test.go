package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

func testClock() time.Time {
	return time.Date(2026, 7, 22, 13, 0, 0, 0, time.UTC)
}

func testServer() *Server {
	services := application.NewServices(
		projects.NewInMemoryRepository(),
		experiments.NewInMemoryRepository(),
		runs.NewInMemoryRepository(),
		testClock,
	)
	services.Auth.SetTokenSourceForTesting(func() (string, error) { return "token-test", nil })
	return NewServer(services)
}

func TestHealthEndpoint(t *testing.T) {
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	testServer().Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", recorder.Code)
	}
}

func TestCreateProjectEndpoint(t *testing.T) {
	server := testServer()
	body, _ := json.Marshal(map[string]string{
		"name":        "Research",
		"description": "Project",
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/projects", bytes.NewReader(body))
	request.Header.Set("Authorization", bearerForResearcher(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d", recorder.Code)
	}
}

func TestProjectsRequireAuthentication(t *testing.T) {
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/projects", nil)
	testServer().Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %d", recorder.Code)
	}
}

func TestViewerCannotCreateProject(t *testing.T) {
	server := testServer()
	body, _ := json.Marshal(map[string]string{
		"name":        "Research",
		"description": "Project",
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/projects", bytes.NewReader(body))
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", recorder.Code)
	}
}

func TestLoginEndpoint(t *testing.T) {
	body, _ := json.Marshal(map[string]string{
		"email":    "researcher@fl-platform.dev",
		"password": "research-demo",
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", bytes.NewReader(body))
	testServer().Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", recorder.Code)
	}
	var payload map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode payload: %v", err)
	}
	if payload["token"] != "token-test" {
		t.Fatalf("expected token-test, got %#v", payload["token"])
	}
}

func TestAuditEventsEndpoint(t *testing.T) {
	server := testServer()

	projectBody, _ := json.Marshal(map[string]string{
		"name":        "Research",
		"description": "Project",
	})
	projectRequest := httptest.NewRequest(http.MethodPost, "/api/v1/projects", bytes.NewReader(projectBody))
	projectRequest.Header.Set("Authorization", bearerForResearcher(t, server))
	projectRecorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(projectRecorder, projectRequest)
	if projectRecorder.Code != http.StatusCreated {
		t.Fatalf("expected project create 201, got %d", projectRecorder.Code)
	}

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/audit/events?limit=5", nil)
	request.Header.Set("Authorization", bearerForResearcher(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", recorder.Code)
	}
	var events []map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &events); err != nil {
		t.Fatalf("decode events: %v", err)
	}
	if len(events) == 0 {
		t.Fatal("expected at least one audit event")
	}
	body := recorder.Body.String()
	if !strings.Contains(body, "project.create") {
		t.Fatalf("expected project.create in audit body, got %s", body)
	}
}

func TestDashboardOverviewEndpoint(t *testing.T) {
	server := testServer()
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/dashboard/overview", nil)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", recorder.Code)
	}
	body := recorder.Body.String()
	if !strings.Contains(body, "\"source\":\"live\"") {
		t.Fatalf("expected live source in body, got %s", body)
	}
}

func TestDashboardRunEndpoint(t *testing.T) {
	server := testServer()
	projectBody, _ := json.Marshal(map[string]string{
		"name":        "Research",
		"description": "Project",
	})
	projectRequest := httptest.NewRequest(http.MethodPost, "/api/v1/projects", bytes.NewReader(projectBody))
	projectRequest.Header.Set("Authorization", bearerForResearcher(t, server))
	projectRecorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(projectRecorder, projectRequest)
	if projectRecorder.Code != http.StatusCreated {
		t.Fatalf("expected project create 201, got %d", projectRecorder.Code)
	}
	var projectPayload struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(projectRecorder.Body.Bytes(), &projectPayload); err != nil {
		t.Fatalf("decode project: %v", err)
	}

	experimentBody, _ := json.Marshal(map[string]any{
		"project_id":  projectPayload.ID,
		"name":        "Experiment",
		"description": "Desc",
		"config":      map[string]any{"rounds": 20},
	})
	experimentRequest := httptest.NewRequest(http.MethodPost, "/api/v1/experiments", bytes.NewReader(experimentBody))
	experimentRequest.Header.Set("Authorization", bearerForResearcher(t, server))
	experimentRecorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(experimentRecorder, experimentRequest)
	if experimentRecorder.Code != http.StatusCreated {
		t.Fatalf("expected experiment create 201, got %d", experimentRecorder.Code)
	}
	var experimentPayload struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(experimentRecorder.Body.Bytes(), &experimentPayload); err != nil {
		t.Fatalf("decode experiment: %v", err)
	}

	runBody, _ := json.Marshal(map[string]any{
		"experiment_id": experimentPayload.ID,
		"config": map[string]any{
			"rounds":         20,
			"current_round":  5,
			"target_clients": 8,
			"mode":           "synchronous",
		},
	})
	runRequest := httptest.NewRequest(http.MethodPost, "/api/v1/runs", bytes.NewReader(runBody))
	runRequest.Header.Set("Authorization", bearerForResearcher(t, server))
	runRecorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(runRecorder, runRequest)
	if runRecorder.Code != http.StatusCreated {
		t.Fatalf("expected run create 201, got %d", runRecorder.Code)
	}
	var runPayload struct {
		ID string `json:"id"`
	}
	if err := json.Unmarshal(runRecorder.Body.Bytes(), &runPayload); err != nil {
		t.Fatalf("decode run: %v", err)
	}

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/dashboard/runs/"+runPayload.ID, nil)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", recorder.Code)
	}
	body := recorder.Body.String()
	if !strings.Contains(body, runPayload.ID) {
		t.Fatalf("expected run id in dashboard body, got %s", body)
	}
}

func bearerForResearcher(t *testing.T, server *Server) string {
	t.Helper()
	return loginAndGetBearer(t, server, "researcher@fl-platform.dev", "research-demo")
}

func bearerForViewer(t *testing.T, server *Server) string {
	t.Helper()
	return loginAndGetBearer(t, server, "viewer@fl-platform.dev", "viewer-demo")
}

func loginAndGetBearer(t *testing.T, server *Server, email, password string) string {
	t.Helper()
	body, _ := json.Marshal(map[string]string{
		"email":    email,
		"password": password,
	})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/auth/login", bytes.NewReader(body))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("login failed: %d", recorder.Code)
	}
	var payload struct {
		Token string `json:"token"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode login: %v", err)
	}
	return "Bearer " + payload.Token
}
