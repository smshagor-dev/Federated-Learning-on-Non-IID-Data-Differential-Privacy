package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/bootstrap"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/research"
)

func writeResearchFixtureFile(t *testing.T, path, contents string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", path, err)
	}
	if err := os.WriteFile(path, []byte(contents), 0o644); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

func writeResearchFixture(t *testing.T, root string) {
	t.Helper()
	base := filepath.Join(root, "experiments", "expresearch001")
	writeResearchFixtureFile(t, filepath.Join(base, "registry.json"), `{
  "schema_version": 1,
  "experiment_id": "expresearch001",
  "display_name": "FedAvg privacy comparison",
  "research_question": "How do privacy layers affect convergence on one fixed dataset?",
  "specification_hash": "2db1235730ba166f3632f911062d10cbe13a9a7a1b029d58cf329a769f70b8b7",
  "dataset_id": "cifar10",
  "dataset_version": "1.0",
  "dataset_checksum": "sha256:cifar10-demo",
  "partition_manifest_hash": "manifest-hash-123",
  "model_id": "groupnorm_cnn",
  "algorithm_id": "fedavg",
  "privacy_mode": "user_level_dp",
  "secure_aggregation_enabled": true,
  "secure_aggregation_provider": "SECAGG_NO_DROPOUT_EXPERIMENTAL",
  "adaptive_clipping_enabled": false,
  "declared_seed_count": 3,
  "current_state": "COMPLETED_WITH_PARTIAL_RUNS",
  "successful_run_count": 2,
  "failed_run_count": 1,
  "canceled_run_count": 0,
  "blocked_run_count": 0,
  "created_at": "2026-07-28T12:00:00Z",
  "updated_at": "2026-07-28T12:05:00Z",
  "created_actor": "researcher",
  "record_version": 2,
  "storage_format_version": 1,
  "artifact_manifest_hash": "artifact-manifest-hash",
  "environment_manifest_hash": "environment-manifest-hash",
  "degraded": false,
  "degraded_reason": ""
}`)
	writeResearchFixtureFile(t, filepath.Join(base, "artifacts.json"), `{"schema_version":1,"entries":[],"manifest_hash":"artifact-manifest-hash"}`)
	writeResearchFixtureFile(t, filepath.Join(base, "events.jsonl"), "")
	writeResearchFixtureFile(t, filepath.Join(base, "runs", "seed-1", "run.json"), `{
  "schema_version": 1,
  "experiment_id": "expresearch001",
  "specification_hash": "2db1235730ba166f3632f911062d10cbe13a9a7a1b029d58cf329a769f70b8b7",
  "seed": 1,
  "run_id": "expresearch001-seed-1-attempt-1",
  "run_attempt": 1,
  "current_state": "COMPLETED",
  "partition_manifest_hash": "manifest-hash-123",
  "model_initialization_seed": 19,
  "training_seed": 1,
  "worker_assignment_seed": 13,
  "start_timestamp": "2026-07-28T12:01:00Z",
  "completion_timestamp": "2026-07-28T12:02:00Z",
  "last_heartbeat": "2026-07-28T12:02:00Z",
  "current_round": 3,
  "expected_round_count": 3,
  "model_version": "v1",
  "environment_manifest_hash": "environment-manifest-hash",
  "metric_schema_version": 1,
  "result_summary_hash": "",
  "failure_count": 0,
  "retry_lineage": [],
  "inclusion_status": "INCLUDED",
  "exclusion_reason": "",
  "artifact_manifest_hash": "",
  "record_version": 1,
  "attempt_history": []
}`)
	writeResearchFixtureFile(t, filepath.Join(base, "runs", "seed-1", "metrics.jsonl"), "")
}

func researchServer(t *testing.T) *Server {
	t.Helper()
	root := filepath.Join(t.TempDir(), "control-plane")
	return researchServerWithRoot(t, root)
}

func researchServerWithRoot(t *testing.T, root string) *Server {
	t.Helper()
	writeResearchFixture(t, filepath.Join(root, "research"))
	services, err := bootstrap.NewPersistentServices(bootstrap.PathsForDataDir(root), testClock)
	if err != nil {
		t.Fatalf("new persistent services: %v", err)
	}
	services.Auth.SetTokenSourceForTesting(func() (string, error) { return "token-test", nil })
	return NewServer(services)
}

func TestResearchExperimentDetailViewerRedactsOperationalFields(t *testing.T) {
	server := researchServer(t)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/research/experiments/expresearch001", nil)
	request.Header.Set("Authorization", bearerForViewer(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	body := recorder.Body.String()
	if strings.Contains(body, "created_actor") || strings.Contains(body, "specification_hash") {
		t.Fatalf("viewer response must redact operational fields, got %s", body)
	}
}

func TestResearchExperimentDetailAdminIncludesOperationalFields(t *testing.T) {
	server := researchServer(t)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/research/experiments/expresearch001", nil)
	request.Header.Set("Authorization", bearerForAdmin(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	body := recorder.Body.String()
	if !strings.Contains(body, "created_actor") || !strings.Contains(body, "specification_hash") {
		t.Fatalf("admin response should include operational fields, got %s", body)
	}
}

func TestResearchRuntimeHealthServiceDenied(t *testing.T) {
	server := researchServer(t)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/research/runtime/health", nil)
	request.Header.Set("Authorization", loginAndGetBearer(t, server, "service@fl-platform.dev", "service-demo"))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for service role, got %d", recorder.Code)
	}
}

func TestResearchValidateRoutesThroughWriterService(t *testing.T) {
	stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer writer-secret" {
			t.Fatalf("unexpected auth header %q", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"schema_version":1,
			"command_id":"cmd-1",
			"command_type":"ValidateExperimentSpecification",
			"status":"SUCCEEDED",
			"durable_completion":true,
			"experiment_id":"expresearch001",
			"experiment_record_version":1,
			"specification_hash":"hash-123",
			"previous_state":"",
			"current_state":"",
			"idempotent_replay":false,
			"reason_code":"",
			"validation_errors":[],
			"completion_timestamp":"2026-07-28T12:00:00Z",
			"response_payload_hash":"payload-hash",
			"payload":{"valid":true,"compatibility_status":"SUPPORTED_FOR_SYNTHETIC_TEST_EXECUTION","specification_hash":"hash-123","validation_errors":[]}
		}`))
	}))
	defer stub.Close()

	root := filepath.Join(t.TempDir(), "control-plane")
	server := researchServerWithRoot(t, root)
	server.services.Research.SetWriter(research.NewHTTPCommandClient(stub.URL, "writer-secret", "go-control-plane", 0))

	body, _ := json.Marshal(map[string]any{
		"specification": map[string]any{
			"schema_version":     1,
			"experiment_id":      "expresearch001",
			"experiment_name":    "FedAvg privacy comparison",
			"research_question":  "How do privacy layers affect convergence on one fixed dataset?",
			"dataset":            map[string]any{"dataset_id": "cifar10", "dataset_version": "1.0", "dataset_checksum": "sha256:cifar10-demo", "split_seed": 7, "train_split_fraction": 0.8, "validation_split_fraction": 0.1, "test_split_fraction": 0.1, "preprocessing_configuration": map[string]any{}},
			"partition":          map[string]any{"strategy": "dirichlet", "num_clients": 5, "seed": 11, "minimum_client_samples": 4, "alpha": 0.3, "classes_per_client": nil, "quantity_skew_sigma": nil, "partition_manifest_hash": "manifest-hash-123"},
			"model":              map[string]any{"model_id": "groupnorm_cnn", "model_version": "v1", "initialization_seed": 19},
			"algorithm":          map[string]any{"algorithm_id": "fedavg", "parameters": map[string]any{}},
			"privacy":            map[string]any{"privacy_mode": "user_level_dp", "noise_multiplier": 1.0, "target_delta": 1e-5, "user_level_clip_norm": 1.5, "sample_level_max_grad_norm": nil, "epsilon_budget": nil, "combined_epsilon": nil, "client_weighting": "uniform"},
			"secure_aggregation": map[string]any{"provider": "SECAGG_NO_DROPOUT_EXPERIMENTAL", "dropout_recovery_requested": false},
			"adaptive_clipping":  map[string]any{"mode": "disabled", "initial_bound": nil, "min_bound": nil, "max_bound": nil, "target_quantile": nil, "learning_rate": nil, "indicator_noise_multiplier": nil},
			"runtime":            map[string]any{"max_rounds": 3, "local_epochs": 1, "batch_size": 8, "learning_rate": 0.01, "evaluation_frequency": 1, "selected_clients_per_round": 3},
			"seeds":              map[string]any{"seeds": []int{1, 2, 3}, "partition_seed": 11, "worker_assignment_seed": 13, "coordinator_seed": 17},
			"determinism_level":  "STRICT_CPU",
			"tags":               []string{},
			"creation_timestamp": "",
			"specification_hash": "",
		},
		"client_specification_hash": "hash-123",
		"correlation_id":            "corr-1",
	})
	request := httptest.NewRequest(http.MethodPost, "/api/v1/research/experiments/validate", bytes.NewReader(body))
	request.Header.Set("Authorization", bearerForResearcher(t, server))
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	if !strings.Contains(recorder.Body.String(), `"valid":true`) {
		t.Fatalf("expected validation success payload, got %s", recorder.Body.String())
	}
}

func TestResearchExperimentCreateReturnsWriterRejectionWithoutEmptyIDLookup(t *testing.T) {
	stub := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{
			"schema_version":1,
			"command_id":"cmd-create-1",
			"command_type":"CreateExperiment",
			"status":"VALIDATION_FAILED",
			"durable_completion":false,
			"experiment_id":"",
			"experiment_record_version":null,
			"specification_hash":"",
			"previous_state":"",
			"current_state":"",
			"idempotent_replay":false,
			"reason_code":"semantic_validation_failed",
			"validation_errors":["experiment_id is required"],
			"completion_timestamp":"2026-07-28T12:00:00Z",
			"response_payload_hash":"payload-hash",
			"payload":{}
		}`))
	}))
	defer stub.Close()

	root := filepath.Join(t.TempDir(), "control-plane")
	server := researchServerWithRoot(t, root)
	server.services.Research.SetWriter(research.NewHTTPCommandClient(stub.URL, "writer-secret", "go-control-plane", 0))

	body, _ := json.Marshal(map[string]any{
		"specification": map[string]any{
			"schema_version":     1,
			"experiment_id":      "expresearch001",
			"experiment_name":    "FedAvg privacy comparison",
			"research_question":  "How do privacy layers affect convergence on one fixed dataset?",
			"dataset":            map[string]any{"dataset_id": "cifar10", "dataset_version": "1.0", "dataset_checksum": "sha256:cifar10-demo", "split_seed": 7, "train_split_fraction": 0.8, "validation_split_fraction": 0.1, "test_split_fraction": 0.1, "preprocessing_configuration": map[string]any{}},
			"partition":          map[string]any{"strategy": "dirichlet", "num_clients": 5, "seed": 11, "minimum_client_samples": 4, "alpha": 0.3, "classes_per_client": nil, "quantity_skew_sigma": nil, "partition_manifest_hash": "manifest-hash-123"},
			"model":              map[string]any{"model_id": "groupnorm_cnn", "model_version": "v1", "initialization_seed": 19},
			"algorithm":          map[string]any{"algorithm_id": "fedavg", "parameters": map[string]any{}},
			"privacy":            map[string]any{"privacy_mode": "user_level_dp", "noise_multiplier": 1.0, "target_delta": 1e-5, "user_level_clip_norm": 1.5, "sample_level_max_grad_norm": nil, "epsilon_budget": nil, "combined_epsilon": nil, "client_weighting": "uniform"},
			"secure_aggregation": map[string]any{"provider": "SECAGG_NO_DROPOUT_EXPERIMENTAL", "dropout_recovery_requested": false},
			"adaptive_clipping":  map[string]any{"mode": "disabled", "initial_bound": nil, "min_bound": nil, "max_bound": nil, "target_quantile": nil, "learning_rate": nil, "indicator_noise_multiplier": nil},
			"runtime":            map[string]any{"max_rounds": 3, "local_epochs": 1, "batch_size": 8, "learning_rate": 0.01, "evaluation_frequency": 1, "selected_clients_per_round": 3},
			"seeds":              map[string]any{"seeds": []int{1, 2, 3}, "partition_seed": 11, "worker_assignment_seed": 13, "coordinator_seed": 17},
			"determinism_level":  "STRICT_CPU",
			"tags":               []string{},
			"creation_timestamp": "",
			"specification_hash": "",
		},
		"client_specification_hash": "hash-123",
		"idempotency_key":           "create-key-1",
		"correlation_id":            "corr-1",
	})
	request := httptest.NewRequest(http.MethodPost, "/api/v1/research/experiments", bytes.NewReader(body))
	request.Header.Set("Authorization", bearerForResearcher(t, server))
	request.Header.Set("Idempotency-Key", "create-key-1")
	recorder := httptest.NewRecorder()
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d: %s", recorder.Code, recorder.Body.String())
	}
	response := recorder.Body.String()
	if strings.Contains(response, "invalid research identifier: empty experiment id") {
		t.Fatalf("expected writer rejection response, got %s", response)
	}
	if !strings.Contains(response, `"reason_code":"semantic_validation_failed"`) {
		t.Fatalf("expected semantic validation failure, got %s", response)
	}
}

func TestResearchRuntimeHealthDegradesWhenWriterUnavailable(t *testing.T) {
	server := researchServer(t)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/research/runtime/health", nil)
	request.Header.Set("Authorization", bearerForResearcher(t, server))
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	body := recorder.Body.String()
	if !strings.Contains(body, `"writes_available":false`) || !strings.Contains(body, `"overall_status":"DEGRADED"`) {
		t.Fatalf("expected degraded health when writer is unavailable, got %s", body)
	}
}
