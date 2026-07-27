package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

func TestCoordinatorPersonalizationEmptyForFedAvgRun(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/personalization", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Records []coordinator.PersonalizationMetricRecord `json:"records"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(payload.Records) != 0 {
		t.Fatalf("expected no personalization records for a plain run, got %d", len(payload.Records))
	}
}

func TestCoordinatorPersonalizationWithDittoRecords(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")

	client.SeedPersonalizationMetric("run-1", coordinator.PersonalizationMetricRecord{
		ClientID: "client-a", Algorithm: "ditto",
		GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: 0.7,
		SampleCount: 20, HasPersonalizedModel: true,
	})
	client.SeedPersonalizationMetric("run-1", coordinator.PersonalizationMetricRecord{
		ClientID: "client-b", Algorithm: "ditto",
		GlobalLocalAccuracy: 0.5, PersonalizedLocalAccuracy: 0.9,
		SampleCount: 20, HasPersonalizedModel: true,
	})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/personalization", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Records []coordinator.PersonalizationMetricRecord `json:"records"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(payload.Records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(payload.Records))
	}

	fairnessRecorder := httptest.NewRecorder()
	fairnessRequest := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/fairness", nil)
	fairnessRequest.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(fairnessRecorder, fairnessRequest)
	if fairnessRecorder.Code != http.StatusOK {
		t.Fatalf("fairness: expected 200, got %d: %s", fairnessRecorder.Code, fairnessRecorder.Body.String())
	}
	var fairness application.PersonalizationMetrics
	if err := json.Unmarshal(fairnessRecorder.Body.Bytes(), &fairness); err != nil {
		t.Fatalf("decode fairness: %v", err)
	}
	if fairness.ClientCount != 2 {
		t.Fatalf("expected client_count 2, got %d", fairness.ClientCount)
	}
	if fairness.WorstClientAccuracy != 0.7 {
		t.Fatalf("expected worst 0.7, got %v", fairness.WorstClientAccuracy)
	}
	if fairness.BestClientAccuracy != 0.9 {
		t.Fatalf("expected best 0.9, got %v", fairness.BestClientAccuracy)
	}

	summaryRecorder := httptest.NewRecorder()
	summaryRequest := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/algorithm-summary", nil)
	summaryRequest.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(summaryRecorder, summaryRequest)
	if summaryRecorder.Code != http.StatusOK {
		t.Fatalf("algorithm-summary: expected 200, got %d: %s", summaryRecorder.Code, summaryRecorder.Body.String())
	}
	var summary application.AlgorithmSummary
	if err := json.Unmarshal(summaryRecorder.Body.Bytes(), &summary); err != nil {
		t.Fatalf("decode summary: %v", err)
	}
	if summary.ClientCount != 2 {
		t.Fatalf("expected reporting_client_count 2, got %d", summary.ClientCount)
	}
}

func TestCoordinatorClientPersonalizationNotFound(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/clients/client-missing/personalization", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestCoordinatorClientPersonalizationFound(t *testing.T) {
	client := coordinator.NewMockClient()
	server := testServerWithCoordinator(client)
	bearer := bearerForResearcher(t, server)
	createCoordinatorRun(t, server, bearer, "run-1")
	client.SeedPersonalizationMetric("run-1", coordinator.PersonalizationMetricRecord{
		ClientID: "client-a", Algorithm: "per_fedavg",
		GlobalLocalAccuracy: 0.4, PersonalizedLocalAccuracy: 0.55,
		SampleCount: 15, HasPersonalizedModel: true,
	})

	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/clients/client-a/personalization", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var record coordinator.PersonalizationMetricRecord
	if err := json.Unmarshal(recorder.Body.Bytes(), &record); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if record.Algorithm != "per_fedavg" {
		t.Fatalf("expected algorithm per_fedavg, got %s", record.Algorithm)
	}
}

func TestCoordinatorFairnessUnconfiguredReturns503(t *testing.T) {
	server := testServerWithCoordinator(nil)
	bearer := bearerForViewer(t, server)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/coordinator/runs/run-1/fairness", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", recorder.Code)
	}
}
