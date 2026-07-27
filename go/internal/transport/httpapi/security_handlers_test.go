package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/experiments"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/projects"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

// testServerWithCoordinatorAndAudit is testServerWithCoordinator plus a
// real in-memory audit repository -- testServerWithCoordinator itself
// passes nil (AuditService degrades to a silent no-op on both Record
// and List with a nil repo), which every pre-existing test relies on
// implicitly by never asserting anything about audit content. The
// security audit endpoint is the first thing in this codebase that
// actually needs recorded events to be readable back, hence this
// separate helper rather than changing the shared one's behavior for
// every other test.
func testServerWithCoordinatorAndAudit(t *testing.T, client coordinator.Client) *Server {
	t.Helper()
	services := application.NewServicesWithCoordinator(
		projects.NewInMemoryRepository(),
		experiments.NewInMemoryRepository(),
		runs.NewInMemoryRepository(),
		auth.NewInMemoryUserRepository(application.DefaultUsers(testClock)),
		auth.NewInMemorySessionRepository(),
		observability.NewInMemoryAuditRepository(),
		client,
		testClock,
	)
	services.Auth.SetTokenSourceForTesting(func() (string, error) { return "token-test", nil })
	return NewServer(services)
}

func bearerForAdmin(t *testing.T, server *Server) string {
	t.Helper()
	return loginAndGetBearer(t, server, "admin@fl-platform.dev", "admin-demo")
}

func doSecurityRequest(server *Server, method, path, bearer string, body any) *httptest.ResponseRecorder {
	var reader *bytes.Reader
	if body != nil {
		encoded, _ := json.Marshal(body)
		reader = bytes.NewReader(encoded)
	} else {
		reader = bytes.NewReader(nil)
	}
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(method, path, reader)
	if bearer != "" {
		request.Header.Set("Authorization", bearer)
	}
	server.Handler().ServeHTTP(recorder, request)
	return recorder
}

func TestSecurityTransportRequiresAuth(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/transport", "", nil)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with no bearer token, got %d", recorder.Code)
	}
}

func TestSecurityTransportViewerAllowed(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedTransportSecurityStatus(coordinator.TransportSecurityStatus{TransportMode: "mtls_required", MutualTLSEnforced: true})
	server := testServerWithCoordinator(client)
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/transport", bearerForViewer(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for viewer reading transport status, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var status coordinator.TransportSecurityStatus
	if err := json.Unmarshal(recorder.Body.Bytes(), &status); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if status.TransportMode != "mtls_required" || !status.MutualTLSEnforced {
		t.Fatalf("expected seeded status to pass through unmodified, got %+v", status)
	}
}

func TestSecurityWorkersViewerGetsRedactedProjection(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedWorkerIdentity(coordinator.WorkerIdentitySummary{
		WorkerID: "worker-1", RegistrationStatus: "active",
		CertificateFingerprint: "aa:bb:cc", SigningKeyID: "key-1",
	})
	server := testServerWithCoordinator(client)

	viewerRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/workers/worker-1", bearerForViewer(t, server), nil)
	if viewerRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for viewer, got %d: %s", viewerRecorder.Code, viewerRecorder.Body.String())
	}
	if bytes.Contains(viewerRecorder.Body.Bytes(), []byte("aa:bb:cc")) {
		t.Fatalf("viewer response must not contain the certificate fingerprint, got %s", viewerRecorder.Body.String())
	}

	adminRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/workers/worker-1", bearerForAdmin(t, server), nil)
	if adminRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for admin, got %d: %s", adminRecorder.Code, adminRecorder.Body.String())
	}
	if !bytes.Contains(adminRecorder.Body.Bytes(), []byte("aa:bb:cc")) {
		t.Fatalf("admin response should contain the certificate fingerprint, got %s", adminRecorder.Body.String())
	}
}

func TestSecurityWorkerSuspendRequiresAdminNotViewer(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedWorkerIdentity(coordinator.WorkerIdentitySummary{WorkerID: "worker-1", RegistrationStatus: "active"})
	server := testServerWithCoordinator(client)

	viewerRecorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/workers/worker-1/suspend", bearerForViewer(t, server),
		map[string]string{"reason": "test", "idempotency_key": "idem-1"})
	if viewerRecorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for viewer attempting to suspend a worker, got %d", viewerRecorder.Code)
	}

	adminRecorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/workers/worker-1/suspend", bearerForAdmin(t, server),
		map[string]string{"reason": "test", "idempotency_key": "idem-1"})
	if adminRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for admin suspending a worker, got %d: %s", adminRecorder.Code, adminRecorder.Body.String())
	}
	var result coordinator.WorkerLifecycleResult
	if err := json.Unmarshal(adminRecorder.Body.Bytes(), &result); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !result.Changed || result.Identity.RegistrationStatus != "suspended" {
		t.Fatalf("expected changed=true status=suspended, got %+v", result)
	}
}

func TestSecurityWorkerSuspendIsIdempotentAtHTTPLayer(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedWorkerIdentity(coordinator.WorkerIdentitySummary{WorkerID: "worker-1", RegistrationStatus: "active"})
	server := testServerWithCoordinator(client)
	bearer := bearerForAdmin(t, server)
	body := map[string]string{"reason": "test", "idempotency_key": "idem-suspend-1"}

	first := doSecurityRequest(server, http.MethodPost, "/api/v1/security/workers/worker-1/suspend", bearer, body)
	if first.Code != http.StatusOK {
		t.Fatalf("first suspend: expected 200, got %d: %s", first.Code, first.Body.String())
	}
	// Re-seed the worker back to active behind the scenes to prove the
	// SECOND call is served from the idempotency cache rather than
	// actually re-executing (which would see "active" and report
	// changed=true again).
	client.SeedWorkerIdentity(coordinator.WorkerIdentitySummary{WorkerID: "worker-1", RegistrationStatus: "active"})
	second := doSecurityRequest(server, http.MethodPost, "/api/v1/security/workers/worker-1/suspend", bearer, body)
	if second.Code != http.StatusOK || second.Body.String() != first.Body.String() {
		t.Fatalf("expected the retried request (same Idempotency-Key) to return the cached first response verbatim, got %s vs %s", second.Body.String(), first.Body.String())
	}
}

func TestSecurityCoordinatorSigningKeyRotateRequiresIdempotencyKey(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedCoordinatorSigningKey(coordinator.CoordinatorSigningKeySummary{SigningKeyID: "genesis", Status: "active"})
	server := testServerWithCoordinator(client)
	recorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/coordinator/signing-keys/rotate", bearerForAdmin(t, server),
		map[string]string{"expected_current_signing_key_id": "genesis"})
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 when no idempotency key is supplied, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestSecurityCoordinatorSigningKeyRotateAndRevoke(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedCoordinatorSigningKey(coordinator.CoordinatorSigningKeySummary{SigningKeyID: "genesis", Status: "active"})
	server := testServerWithCoordinator(client)
	admin := bearerForAdmin(t, server)

	rotateRecorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/coordinator/signing-keys/rotate", admin,
		map[string]any{"expected_current_signing_key_id": "genesis", "idempotency_key": "idem-rotate-1"})
	if rotateRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for a real rotation, got %d: %s", rotateRecorder.Code, rotateRecorder.Body.String())
	}
	var rotateResult coordinator.RotateCoordinatorSigningKeyResult
	if err := json.Unmarshal(rotateRecorder.Body.Bytes(), &rotateResult); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !rotateResult.Accepted || rotateResult.NewKey.SigningKeyID == "" {
		t.Fatalf("expected an accepted rotation with a new key id, got %+v", rotateResult)
	}

	// A researcher can read coordinator signing keys but not rotate them.
	researcherRecorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/coordinator/signing-keys/rotate", bearerForResearcher(t, server),
		map[string]any{"idempotency_key": "idem-rotate-2"})
	if researcherRecorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for researcher attempting rotation, got %d", researcherRecorder.Code)
	}

	// testServerWithCoordinator fixes the login token to one constant
	// ("token-test"), so the researcher login just above overwrote the
	// session that token resolves to -- re-login as admin to get a fresh
	// admin-bound session before the next admin-only call.
	admin = bearerForAdmin(t, server)
	revokeRecorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/coordinator/signing-keys/"+rotateResult.NewKey.SigningKeyID+"/revoke", admin,
		map[string]any{"reason": "test", "idempotency_key": "idem-revoke-1"})
	if revokeRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for revocation, got %d: %s", revokeRecorder.Code, revokeRecorder.Body.String())
	}
	var revokeResult coordinator.RevokeCoordinatorSigningKeyResult
	if err := json.Unmarshal(revokeRecorder.Body.Bytes(), &revokeResult); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !revokeResult.Changed || !revokeResult.ProductionTaskIssuanceStopped {
		t.Fatalf("expected changed=true production_task_issuance_stopped=true (sole active key revoked), got %+v", revokeResult)
	}
}

func TestSecurityEventsRequiresAuth(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events", "", nil)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with no bearer token, got %d", recorder.Code)
	}
}

// TestSecurityEventsServesRealRecords: Security Events, Metrics, and
// Durable Audit Journal slice -- this endpoint used to honestly return
// 501 (see git history); it is now real, backed by the Go-local journal
// merged with the coordinator's own ListSecurityEvents RPC.
func TestSecurityEventsServesRealRecords(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedSecurityEvent(observability.SecurityEvent{
		SchemaVersion: observability.SecurityEventSchemaVersion,
		EventID:       "00000000000000000001",
		Timestamp:     "2026-01-01T00:00:00Z",
		SourceService: "coordinator",
		EventType:     observability.EventWorkerSuspended,
		Severity:      observability.SeverityWarning,
		SafeActorID:   "go-api",
		WorkerID:      "worker-1",
		Outcome:       observability.OutcomeCompleted,
	})
	server := testServerWithCoordinator(client)

	// Trigger a Go-local event too (a permission denial), so the merge
	// covers both sources in one request.
	doSecurityRequest(server, http.MethodGet, "/api/v1/security/workers/worker-1", bearerForViewer(t, server), nil)
	deniedRecorder := doSecurityRequest(server, http.MethodPost, "/api/v1/security/workers/worker-1/suspend",
		bearerForViewer(t, server), map[string]string{"reason": "x"})
	if deniedRecorder.Code != http.StatusForbidden {
		t.Fatalf("expected viewer suspend attempt to be forbidden, got %d", deniedRecorder.Code)
	}

	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	if !bytes.Contains(recorder.Body.Bytes(), []byte("WORKER_SUSPENDED")) {
		t.Fatalf("expected the coordinator-relayed event to appear, got %s", recorder.Body.String())
	}
	if !bytes.Contains(recorder.Body.Bytes(), []byte("SECURITY_PERMISSION_DENIED")) {
		t.Fatalf("expected the Go-local permission-denied event to appear, got %s", recorder.Body.String())
	}
}

func TestSecurityEventsRedactsForResearcherButNotAdmin(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedSecurityEvent(observability.SecurityEvent{
		SchemaVersion: observability.SecurityEventSchemaVersion,
		EventID:       "00000000000000000001",
		Timestamp:     "2026-01-01T00:00:00Z",
		SourceService: "coordinator",
		EventType:     observability.EventWorkerSuspended,
		Severity:      observability.SeverityWarning,
		ReasonCode:    "administrative_suspension",
		Outcome:       observability.OutcomeCompleted,
	})
	server := testServerWithCoordinator(client)

	researcherRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events", bearerForResearcher(t, server), nil)
	if researcherRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for researcher, got %d", researcherRecorder.Code)
	}
	if bytes.Contains(researcherRecorder.Body.Bytes(), []byte("administrative_suspension")) {
		t.Fatalf("researcher (no read_detailed) must not see reason_code, got %s", researcherRecorder.Body.String())
	}

	adminRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/events", bearerForAdmin(t, server), nil)
	if !bytes.Contains(adminRecorder.Body.Bytes(), []byte("administrative_suspension")) {
		t.Fatalf("admin (has read_detailed) should see reason_code, got %s", adminRecorder.Body.String())
	}
}

func TestSecurityAuditRedactsForResearcherButNotAdmin(t *testing.T) {
	client := coordinator.NewMockClient()
	client.SeedWorkerIdentity(coordinator.WorkerIdentitySummary{WorkerID: "worker-1", RegistrationStatus: "active"})
	server := testServerWithCoordinatorAndAudit(t, client)
	admin := bearerForAdmin(t, server)

	// A real mutation, so the shared observability.AuditRepository has a
	// security-tagged record to serve back.
	doSecurityRequest(server, http.MethodPost, "/api/v1/security/workers/worker-1/suspend", admin,
		map[string]string{"reason": "audit-trail-check", "idempotency_key": "idem-audit-1"})

	researcherRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/audit", bearerForResearcher(t, server), nil)
	if researcherRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for researcher audit read, got %d: %s", researcherRecorder.Code, researcherRecorder.Body.String())
	}
	if bytes.Contains(researcherRecorder.Body.Bytes(), []byte("audit-trail-check")) {
		t.Fatalf("researcher (no read_detailed) must not see the mutation's free-form reason/details, got %s", researcherRecorder.Body.String())
	}

	// Re-login as admin: testServerWithCoordinator fixes the login token
	// to one constant, so the researcher login just above overwrote the
	// session the original admin bearer resolves to.
	admin = bearerForAdmin(t, server)
	adminRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/audit", admin, nil)
	if adminRecorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for admin audit read, got %d: %s", adminRecorder.Code, adminRecorder.Body.String())
	}
	if !bytes.Contains(adminRecorder.Body.Bytes(), []byte("audit-trail-check")) {
		t.Fatalf("admin (has read_detailed) should see the full mutation details, got %s", adminRecorder.Body.String())
	}

	viewerRecorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/audit", bearerForViewer(t, server), nil)
	if viewerRecorder.Code != http.StatusForbidden {
		t.Fatalf("expected 403 for viewer (no security.audit.read), got %d", viewerRecorder.Code)
	}
}

func TestSecurityWorkerNotFoundMapsTo404(t *testing.T) {
	server := testServerWithCoordinator(coordinator.NewMockClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/security/workers/unknown-worker", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for an unknown worker, got %d: %s", recorder.Code, recorder.Body.String())
	}
}
