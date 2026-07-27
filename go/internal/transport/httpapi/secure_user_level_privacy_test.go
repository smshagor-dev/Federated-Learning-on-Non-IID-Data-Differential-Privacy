package httpapi

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice, Work Area O (Go-level coverage backing the browser tests): the
// 5 new routes, real per-role behavior (VIEWER denied on rounds/round/
// budget, ADMIN/RESEARCHER see different epsilon precision), and the
// unauthenticated-denied case -- same testServerWithCoordinator/
// doSecurityRequest/bearerFor* helpers as security_overview_test.go.

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

func seededSecureUserDPClient() *coordinator.MockClient {
	client := coordinator.NewMockClient()
	client.SeedSecureUserLevelPrivacyHealth(coordinator.SecureUserLevelPrivacyHealth{
		Capability: coordinator.SecureUserLevelPrivacyCapability{
			Available: true, Provider: "SECAGG_NO_DROPOUT_EXPERIMENTAL",
			AdjacencyModel: "ADD_REMOVE_ONE", SamplingAssumption: "NO_AMPLIFICATION",
			FixedWeight: 1.0, TrustLimitations: []string{"not_production_privacy_ready"},
		},
		ProviderStatus: "ok", NoiseProviderStatus: "ok", AccountantStatus: "ok",
		LedgerStatus: "ok", EventJournalStatus: "ok", ActiveRunsWithUserLevel: 1,
	})
	client.SeedSecureUserLevelPrivacyBudget(coordinator.SecureUserLevelPrivacyBudget{
		RunID: "run-1", BudgetConfigured: true, EpsilonSpent: 1.23456, EpsilonBudget: 10.0,
		EpsilonRemaining: 8.76544, TargetDelta: 1e-5, RoundsCommitted: 2,
	})
	client.SeedSecureUserLevelPrivacyRound(coordinator.SecureUserLevelPrivacyRound{
		RunID: "run-1", RoundID: 1, EpsilonAfterRound: 0.61728, TargetDelta: 1e-5,
		NoiseMultiplier: 1.0, ClippingBound: 0.5, NumClients: 3, CommittedAtUnixS: 1000,
	})
	client.SeedSecureUserLevelPrivacyRound(coordinator.SecureUserLevelPrivacyRound{
		RunID: "run-1", RoundID: 2, EpsilonAfterRound: 1.23456, TargetDelta: 1e-5,
		NoiseMultiplier: 1.0, ClippingBound: 0.5, NumClients: 3, CommittedAtUnixS: 2000,
	})
	return client
}

func TestSecureUserDPStatusRequiresAuth(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/status", "", nil)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 with no bearer token, got %d", recorder.Code)
	}
}

func TestSecureUserDPStatusReturnsCapability(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/status", bearerForViewer(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200 for VIEWER on /status, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var view secureUserDPCapabilityView
	if err := json.Unmarshal(recorder.Body.Bytes(), &view); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if view.Provider != "SECAGG_NO_DROPOUT_EXPERIMENTAL" {
		t.Fatalf("expected the seeded provider name to pass through, got %q", view.Provider)
	}
	if !view.Available {
		t.Fatalf("expected Available=true to pass through")
	}
}

func TestSecureUserDPHealthAvailableToEveryRole(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	for _, bearer := range []string{bearerForAdmin(t, server), bearerForResearcher(t, server), bearerForViewer(t, server)} {
		recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/health", bearer, nil)
		if recorder.Code != http.StatusOK {
			t.Fatalf("expected 200 for /health, got %d: %s", recorder.Code, recorder.Body.String())
		}
	}
}

func TestSecureUserDPServiceRoleDeniedEverywhere(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	bearer := bearerForService(t, server)
	for _, path := range []string{
		"/api/v1/secure-aggregation/privacy/status",
		"/api/v1/secure-aggregation/privacy/health",
		"/api/v1/secure-aggregation/privacy/budget?run_id=run-1",
		"/api/v1/secure-aggregation/privacy/rounds?run_id=run-1",
		"/api/v1/secure-aggregation/privacy/rounds/1?run_id=run-1",
	} {
		recorder := doSecurityRequest(server, http.MethodGet, path, bearer, nil)
		if recorder.Code != http.StatusForbidden {
			t.Fatalf("expected 403 for SERVICE role on %s (no implicit access), got %d: %s",
				path, recorder.Code, recorder.Body.String())
		}
	}
}

func TestSecureUserDPRoundsAndBudgetDeniedForViewer(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	bearer := bearerForViewer(t, server)
	for _, path := range []string{
		"/api/v1/secure-aggregation/privacy/budget?run_id=run-1",
		"/api/v1/secure-aggregation/privacy/rounds?run_id=run-1",
		"/api/v1/secure-aggregation/privacy/rounds/1?run_id=run-1",
	} {
		recorder := doSecurityRequest(server, http.MethodGet, path, bearer, nil)
		if recorder.Code != http.StatusForbidden {
			t.Fatalf("expected 403 for VIEWER on %s (per-run privacy accounting is withheld), got %d: %s",
				path, recorder.Code, recorder.Body.String())
		}
	}
}

func TestSecureUserDPBudgetRequiresRunID(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/budget", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 without run_id, got %d: %s", recorder.Code, recorder.Body.String())
	}
}

func TestSecureUserDPBudgetAdminSeesExactEpsilon(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/budget?run_id=run-1", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var view secureUserDPBudgetView
	if err := json.Unmarshal(recorder.Body.Bytes(), &view); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if view.EpsilonSpent != 1.23456 {
		t.Fatalf("expected ADMIN to see the exact seeded epsilon_spent 1.23456, got %v", view.EpsilonSpent)
	}
}

func TestSecureUserDPBudgetResearcherSeesRoundedEpsilon(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/budget?run_id=run-1", bearerForResearcher(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var view secureUserDPBudgetView
	if err := json.Unmarshal(recorder.Body.Bytes(), &view); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if view.EpsilonSpent != 1.235 {
		t.Fatalf("expected RESEARCHER to see epsilon_spent rounded to 3 places (1.235), got %v", view.EpsilonSpent)
	}
}

func TestSecureUserDPRoundsListAndCursor(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/rounds?run_id=run-1&limit=1", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", recorder.Code, recorder.Body.String())
	}
	var body struct {
		Rounds     []secureUserDPRoundView `json:"rounds"`
		NextCursor string                  `json:"next_cursor"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(body.Rounds) != 1 || body.Rounds[0].RoundID != 1 {
		t.Fatalf("expected exactly one round (round_id=1) with limit=1, got %+v", body.Rounds)
	}
	if body.NextCursor != "1" {
		t.Fatalf("expected next_cursor='1' (bounded pagination), got %q", body.NextCursor)
	}

	// Follow the cursor to the second page.
	recorder2 := doSecurityRequest(server, http.MethodGet,
		"/api/v1/secure-aggregation/privacy/rounds?run_id=run-1&limit=1&after_cursor="+body.NextCursor,
		bearerForAdmin(t, server), nil)
	var body2 struct {
		Rounds     []secureUserDPRoundView `json:"rounds"`
		NextCursor string                  `json:"next_cursor"`
	}
	if err := json.Unmarshal(recorder2.Body.Bytes(), &body2); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if len(body2.Rounds) != 1 || body2.Rounds[0].RoundID != 2 {
		t.Fatalf("expected the second page to return round_id=2, got %+v", body2.Rounds)
	}
	if body2.NextCursor != "" {
		t.Fatalf("expected an empty next_cursor once every round has been returned, got %q", body2.NextCursor)
	}
}

func TestSecureUserDPRoundDetailFoundAndNotFound(t *testing.T) {
	server := testServerWithCoordinator(seededSecureUserDPClient())
	found := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/rounds/1?run_id=run-1", bearerForAdmin(t, server), nil)
	if found.Code != http.StatusOK {
		t.Fatalf("expected 200 for an existing round, got %d: %s", found.Code, found.Body.String())
	}
	var view secureUserDPRoundView
	if err := json.Unmarshal(found.Body.Bytes(), &view); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if view.RoundID != 1 || view.RunID != "run-1" {
		t.Fatalf("expected round_id=1/run_id=run-1, got %+v", view)
	}

	notFound := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/rounds/999?run_id=run-1", bearerForAdmin(t, server), nil)
	if notFound.Code != http.StatusNotFound {
		t.Fatalf("expected 404 for a round_id that was never committed, got %d: %s", notFound.Code, notFound.Body.String())
	}
}

func TestSecureUserDPUnavailableWhenCoordinatorNotConfigured(t *testing.T) {
	server := testServerWithCoordinator(nil)
	recorder := doSecurityRequest(server, http.MethodGet, "/api/v1/secure-aggregation/privacy/health", bearerForAdmin(t, server), nil)
	if recorder.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503 when the coordinator is not configured, got %d: %s", recorder.Code, recorder.Body.String())
	}
}
