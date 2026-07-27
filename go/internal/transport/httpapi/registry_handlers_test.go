package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/datasets"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/models"
)

func TestAlgorithmsListEndpoint(t *testing.T) {
	server := testServer()
	bearer := bearerForViewer(t, server)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/algorithms", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var descriptors []map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &descriptors); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(descriptors) != 6 {
		t.Fatalf("expected 6 algorithms, got %d", len(descriptors))
	}
}

func TestAlgorithmByNameNotFound(t *testing.T) {
	server := testServer()
	bearer := bearerForViewer(t, server)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/algorithms/does-not-exist", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", recorder.Code)
	}
}

func TestAlgorithmByNameFedSam(t *testing.T) {
	server := testServer()
	bearer := bearerForViewer(t, server)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/v1/algorithms/fedsam", nil)
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var descriptor map[string]any
	if err := json.Unmarshal(recorder.Body.Bytes(), &descriptor); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if descriptor["name"] != "fedsam" {
		t.Fatalf("expected name fedsam, got %v", descriptor["name"])
	}
}

func TestModelsRegisterRequiresResearcherOrAdmin(t *testing.T) {
	server := testServer()
	bearer := bearerForViewer(t, server)
	body, _ := json.Marshal(models.Model{Name: "cnn", Version: "1"})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/models", bytes.NewReader(body))
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403, got %d", recorder.Code)
	}
}

func TestModelsRegisterAndLifecycle(t *testing.T) {
	server := testServer()
	bearer := bearerForResearcher(t, server)

	body, _ := json.Marshal(models.Model{Name: "cnn", Version: "1", StateDictSchemaHash: "hash-1"})
	createRecorder := httptest.NewRecorder()
	createReq := httptest.NewRequest(http.MethodPost, "/api/v1/models", bytes.NewReader(body))
	createReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(createRecorder, createReq)
	if createRecorder.Code != http.StatusCreated {
		t.Fatalf("create: expected 201, got %d: %s", createRecorder.Code, createRecorder.Body.String())
	}

	getRecorder := httptest.NewRecorder()
	getReq := httptest.NewRequest(http.MethodGet, "/api/v1/models/cnn/1", nil)
	getReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(getRecorder, getReq)
	if getRecorder.Code != http.StatusOK {
		t.Fatalf("get: expected 200, got %d: %s", getRecorder.Code, getRecorder.Body.String())
	}

	validateBody, _ := json.Marshal(map[string]string{"actual_schema_hash": "hash-1"})
	validateRecorder := httptest.NewRecorder()
	validateReq := httptest.NewRequest(http.MethodPost, "/api/v1/models/cnn/1/validate", bytes.NewReader(validateBody))
	validateReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(validateRecorder, validateReq)
	if validateRecorder.Code != http.StatusOK {
		t.Fatalf("validate: expected 200, got %d: %s", validateRecorder.Code, validateRecorder.Body.String())
	}

	activateRecorder := httptest.NewRecorder()
	activateReq := httptest.NewRequest(http.MethodPost, "/api/v1/models/cnn/1/activate", nil)
	activateReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(activateRecorder, activateReq)
	if activateRecorder.Code != http.StatusOK {
		t.Fatalf("activate: expected 200, got %d: %s", activateRecorder.Code, activateRecorder.Body.String())
	}
	var activated models.Model
	if err := json.Unmarshal(activateRecorder.Body.Bytes(), &activated); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if activated.Status != models.StatusActive {
		t.Fatalf("expected ACTIVE, got %s", activated.Status)
	}
}

func TestModelsDuplicateRegisterReturnsConflict(t *testing.T) {
	server := testServer()
	bearer := bearerForResearcher(t, server)
	body, _ := json.Marshal(models.Model{Name: "cnn", Version: "1"})

	first := httptest.NewRecorder()
	firstReq := httptest.NewRequest(http.MethodPost, "/api/v1/models", bytes.NewReader(body))
	firstReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(first, firstReq)
	if first.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d", first.Code)
	}

	second := httptest.NewRecorder()
	secondReq := httptest.NewRequest(http.MethodPost, "/api/v1/models", bytes.NewReader(body))
	secondReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(second, secondReq)
	if second.Code != http.StatusConflict {
		t.Fatalf("expected 409, got %d: %s", second.Code, second.Body.String())
	}
}

func TestDatasetsRegisterAndPartitionLifecycle(t *testing.T) {
	server := testServer()
	bearer := bearerForResearcher(t, server)

	body, _ := json.Marshal(datasets.Dataset{DatasetID: "mnist-iid", NumClasses: 10, TrainSampleCount: 60000})
	createRecorder := httptest.NewRecorder()
	createReq := httptest.NewRequest(http.MethodPost, "/api/v1/datasets", bytes.NewReader(body))
	createReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(createRecorder, createReq)
	if createRecorder.Code != http.StatusCreated {
		t.Fatalf("create: expected 201, got %d: %s", createRecorder.Code, createRecorder.Body.String())
	}

	validateRecorder := httptest.NewRecorder()
	validateReq := httptest.NewRequest(http.MethodPost, "/api/v1/datasets/mnist-iid/validate", nil)
	validateReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(validateRecorder, validateReq)
	if validateRecorder.Code != http.StatusOK {
		t.Fatalf("validate: expected 200, got %d: %s", validateRecorder.Code, validateRecorder.Body.String())
	}

	partitionBody, _ := json.Marshal(datasets.Partition{PartitionID: "p1", Strategy: "iid", NumClients: 4})
	partitionRecorder := httptest.NewRecorder()
	partitionReq := httptest.NewRequest(http.MethodPost, "/api/v1/datasets/mnist-iid/partitions", bytes.NewReader(partitionBody))
	partitionReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(partitionRecorder, partitionReq)
	if partitionRecorder.Code != http.StatusCreated {
		t.Fatalf("create partition: expected 201, got %d: %s", partitionRecorder.Code, partitionRecorder.Body.String())
	}

	listRecorder := httptest.NewRecorder()
	listReq := httptest.NewRequest(http.MethodGet, "/api/v1/datasets/mnist-iid/partitions", nil)
	listReq.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(listRecorder, listReq)
	if listRecorder.Code != http.StatusOK {
		t.Fatalf("list partitions: expected 200, got %d: %s", listRecorder.Code, listRecorder.Body.String())
	}
	var partitions []datasets.Partition
	if err := json.Unmarshal(listRecorder.Body.Bytes(), &partitions); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(partitions) != 1 {
		t.Fatalf("expected 1 partition, got %d", len(partitions))
	}
}

func TestDatasetsCreatePartitionRejectsUnknownDataset(t *testing.T) {
	server := testServer()
	bearer := bearerForResearcher(t, server)
	partitionBody, _ := json.Marshal(datasets.Partition{PartitionID: "p1", Strategy: "iid", NumClients: 4})
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodPost, "/api/v1/datasets/does-not-exist/partitions", bytes.NewReader(partitionBody))
	request.Header.Set("Authorization", bearer)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d: %s", recorder.Code, recorder.Body.String())
	}
}
