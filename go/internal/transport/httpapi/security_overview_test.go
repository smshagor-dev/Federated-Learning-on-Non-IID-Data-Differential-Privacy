package httpapi

// Web Security Center, Event Centralization, and Security CI slice:
// coverage for the two endpoints added this slice
// (security_overview.go) -- GET /api/v1/security/overview and
// GET /api/v1/security/events/sources. Same package as
// security_overview.go (not _test), so this decodes directly into the
// unexported securityOverview/securityEventSourceEntry wire types
// rather than re-declaring them, matching this file's sibling
// security_handlers_test.go's convention of testing at the HTTP-handler
// level with a real MockClient.

import (
	"encoding/json"
	"net/http"
	"testing"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
)

func bearerForService(t *testing.T, server *Server) string {
	t.Helper()
	return loginAndGetBearer(t, server, "service@fl-platform.dev", "service-demo")
}

func TestSecurityOverviewRequiresAuth(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/overview", "", nil)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with no bearer token, got %d", recorder.Code)
	}
}

func TestSecurityOverviewAggregatesRealState(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedTransportSecurityStatus(coordinator.TransportSecurityStatus{
		TransportMode: "mtls_required", MutualTLSEnforced: true,
	})
	client.SeedSecurityTrustModel(coordinator.SecurityTrustModel{
		ActiveCoordinatorSigningKeyID: "coord-key-1", TrustedKeyBundleVersion: 3,
	})
	client.SeedWorkerIdentity(coordinator.WorkerIdentitySummary{
		WorkerID: "worker-1", RegistrationStatus: "active",
	})
	client.SeedWorkerSigningKey(coordinator.WorkerSigningKeySummary{
		WorkerID: "worker-1", SigningKeyID: "key-1", Status: "active",
	})
	client.SeedCoordinatorSigningKey(coordinator.CoordinatorSigningKeySummary{
		SigningKeyID: "coord-key-1", Status: "active", ExpiresAtUnixS: 9_999_999_999,
	})
	client.SeedSecurityEvent(observability.SecurityEvent{
		SchemaVersion: observability.SecurityEventSchemaVersion,
		EventID:       "00000000000000000001",
		Timestamp:     "2026-01-01T00:00:00Z",
		SourceService: "coordinator",
		EventType:     observability.EventHeartbeatAccepted,
		Severity:      observability.SeverityInfo,
		Outcome:       observability.OutcomeAccepted,
	})
	server := testServerWithCoordinator(client)

	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/overview", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var overview securityOverview
	if err := json.Unmarshal(recorder.Body.Bytes(), &overview); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if overview.Transport.TransportMode != "mtls_required" || !overview.Transport.MutualTLSEnforced {
		t.Fatalf("expected seeded transport status to pass through, got %+v", overview.Transport)
	}
	if overview.WorkerIdentities.Active != 1 {
		t.Fatalf("expected 1 active worker identity, got %+v", overview.WorkerIdentities)
	}
	if overview.WorkerSigningKeys.Active != 1 {
		t.Fatalf("expected 1 active worker signing key, got %+v", overview.WorkerSigningKeys)
	}
	if overview.CoordinatorKeys.ActiveKeyID != "coord-key-1" {
		t.Fatalf("expected admin to see the active coordinator key id, got %+v", overview.CoordinatorKeys)
	}
	if overview.SignedMessages.Accepted != 1 {
		t.Fatalf("expected the seeded HEARTBEAT_ACCEPTED event to tally as 1 accepted signed message, got %+v",
			overview.SignedMessages)
	}
	if overview.FeatureAvailability.SecureAggregationAvailable {
		t.Fatalf("secure_aggregation_available must always be false -- this platform does not implement it")
	}
	if overview.FeatureAvailability.WorkerAttestationAvailable {
		t.Fatalf("worker_attestation_available must always be false -- this platform does not implement it")
	}
	if !overview.FeatureAvailability.CentralCoordinatorObservesUpdates {
		t.Fatalf("central_coordinator_observes_updates must be true -- no secure aggregation is in place")
	}
}

func TestSecurityOverviewViewerHidesCoordinatorKeyIDs(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedSecurityTrustModel(coordinator.SecurityTrustModel{ActiveCoordinatorSigningKeyID: "coord-key-1"})
	client.SeedCoordinatorSigningKey(coordinator.CoordinatorSigningKeySummary{
		SigningKeyID: "coord-key-1", Status: "grace_period", GracePeriodEndUnixS: 9_999_999_999,
	})
	server := testServerWithCoordinator(client)

	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/overview", bearerForViewer(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for viewer, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var overview securityOverview
	if err := json.Unmarshal(recorder.Body.Bytes(), &overview); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if overview.CoordinatorKeys.ActiveKeyID != "" || overview.CoordinatorKeys.GracePeriodKeyID != "" {
		t.Fatalf("viewer must not see coordinator signing-key identifiers, got %+v", overview.CoordinatorKeys)
	}
}

func TestSecurityEventSourcesRequiresAuth(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events/sources", "", nil)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with no bearer token, got %d", recorder.Code)
	}
}

func TestSecurityEventSourcesIncludesGoAndPythonWorkerSources(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedSecurityEventSourceHealth(coordinator.SecurityEventSourceHealthResult{
		Sources: []coordinator.SecurityEventSourceHealthEntry{
			{
				SourceService:       "coordinator",
				RecordCount:         5,
				BatchesAccepted:     2,
				BatchesRejected:     1,
				DistinctWorkersSeen: 1,
			},
			{
				SourceService:   "python-worker",
				RecordCount:     0,
				BatchesAccepted: 2,
			},
		},
	})
	server := testServerWithCoordinator(client)

	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events/sources", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Sources []securityEventSourceEntry `json:"sources"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v", err)
	}
	seen := map[string]securityEventSourceEntry{}
	for _, source := range payload.Sources {
		seen[source.SourceService] = source
	}
	if _, ok := seen["go-api"]; !ok {
		t.Fatalf("expected a go-api source entry (the Go process's own local journal), got %+v", payload.Sources)
	}
	if entry, ok := seen["python-worker"]; !ok || entry.BatchesAccepted != 2 {
		t.Fatalf("expected the coordinator-relayed python-worker source with batches_accepted=2, got %+v", payload.Sources)
	}
	if entry, ok := seen["coordinator"]; !ok || entry.DistinctWorkersSeen != 1 {
		t.Fatalf("expected the coordinator-relayed coordinator source with distinct_workers_seen=1, got %+v", payload.Sources)
	}
}

func TestSecurityEventSourcesMarksStaleSourceAfterThreshold(t *testing.T) {
	client := coordinator.NewMockClient()
	staleTimestamp := time.Now().Add(-10 * time.Minute).UTC().Format("2006-01-02T15:04:05Z")
	freshTimestamp := time.Now().Add(-1 * time.Second).UTC().Format("2006-01-02T15:04:05Z")
	client.SeedSecurityEventSourceHealth(coordinator.SecurityEventSourceHealthResult{
		Sources: []coordinator.SecurityEventSourceHealthEntry{
			{SourceService: "python-worker", LastEventAt: staleTimestamp, RecordCount: 3},
			{SourceService: "coordinator", LastEventAt: freshTimestamp, RecordCount: 3},
		},
	})
	server := testServerWithCoordinator(client)

	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events/sources", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var payload struct {
		Sources []securityEventSourceEntry `json:"sources"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &payload); err != nil {
		t.Fatalf("decode: %v", err)
	}
	seen := map[string]securityEventSourceEntry{}
	for _, source := range payload.Sources {
		seen[source.SourceService] = source
	}
	if entry, ok := seen["python-worker"]; !ok || !entry.Stale {
		t.Fatalf("expected python-worker (lag ~10m > the fixed threshold) to be reported stale, got %+v", payload.Sources)
	}
	if entry, ok := seen["coordinator"]; !ok || entry.Stale {
		t.Fatalf("expected coordinator (lag ~1s) to not be reported stale, got %+v", payload.Sources)
	}
	if entry, ok := seen["go-api"]; ok && entry.Stale {
		t.Fatalf("expected the go-api source with no events yet to not be marked stale (never-reported is a distinct, non-alarming state), got %+v", entry)
	}
}

func TestSecurityEventSourcesDeniedForServiceRole(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events/sources", bearerForService(t, server), nil)
	if recorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for service role (no security.event_sources.read grant), got %d", recorder.Code)
	}
}
