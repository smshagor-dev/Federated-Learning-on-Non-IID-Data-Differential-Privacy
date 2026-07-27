package httpapi

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice (docs/secure-user-level-operations-audit.md), Work Areas I/J/K:
// 5 new read-only GET routes under /api/v1/secure-aggregation/privacy/*,
// each gated by its own responsibility-named permission
// (go/internal/security/permissions.go), each serialized through an
// explicit per-role response type -- never by deleting JSON keys after
// generic serialization. None of these routes ever return a clear
// update, individual norm, clipping factor, individual weight, noise
// tensor/state, masked payload, or key/secret material (see
// proto/coordinator/coordinator.proto's message comments, which this
// layer only narrows further, never widens).
//
// Route-to-round-key deviation, disclosed: the task's own literal route
// list names "rounds/{sessionId}"; this implementation's round detail is
// keyed by (run_id, round_id) instead, matching how the underlying
// ledger (RunInstance::user_level_ledger()) actually indexes committed
// accounting steps -- there is no per-round session_id in that data
// model (a session_id exists only transiently, per masked-update RPC,
// and is not retained in the ledger). "rounds/{roundId}?run_id=..." is
// the real, honest shape; a session_id-keyed route would need a second,
// unbuilt session_id -> round_id index this slice does not add.

import (
	"net/http"
	"strconv"
	"strings"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/security"
)

// -- Per-role response types (Work Area K) -----------------------------

// secureUserDPCapabilityView: identical across every role that can reach
// it -- the static mechanism description carries no per-run or
// per-actor sensitive field, so there is nothing to redact.
type secureUserDPCapabilityView struct {
	Available          bool     `json:"available"`
	Provider           string   `json:"provider"`
	AdjacencyModel     string   `json:"adjacency_model"`
	SamplingAssumption string   `json:"sampling_assumption"`
	SensitivityFormula string   `json:"sensitivity_formula"`
	NoisePlacement     string   `json:"noise_placement"`
	FixedWeight        float64  `json:"fixed_weight"`
	TrustLimitations   []string `json:"trust_limitations"`
}

func toSecureUserDPCapabilityView(c coordinator.SecureUserLevelPrivacyCapability) secureUserDPCapabilityView {
	return secureUserDPCapabilityView{
		Available:          c.Available,
		Provider:           c.Provider,
		AdjacencyModel:     c.AdjacencyModel,
		SamplingAssumption: c.SamplingAssumption,
		SensitivityFormula: c.SensitivityFormula,
		NoisePlacement:     c.NoisePlacement,
		FixedWeight:        c.FixedWeight,
		TrustLimitations:   c.TrustLimitations,
	}
}

// secureUserDPHealthView: aggregate-only (no per-run/per-worker field
// exists on the underlying type at all -- see
// coordinator.SecureUserLevelPrivacyHealth's own doc comment), so this
// too is identical across every role that can reach it.
type secureUserDPHealthView struct {
	Capability              secureUserDPCapabilityView `json:"capability"`
	ProviderStatus          string                     `json:"provider_status"`
	NoiseProviderStatus     string                     `json:"noise_provider_status"`
	AccountantStatus        string                     `json:"accountant_status"`
	LedgerStatus            string                     `json:"ledger_status"`
	EventJournalStatus      string                     `json:"event_journal_status"`
	LastSuccessfulRoundAt   string                     `json:"last_successful_round_at,omitempty"`
	ActiveRunsWithUserLevel uint64                     `json:"active_runs_with_user_level_dp"`
	ReconciliationRequired  bool                       `json:"reconciliation_required"`
	DegradedReason          string                     `json:"degraded_reason,omitempty"`
	CheckedAtUnixS          float64                    `json:"checked_at_unix_s"`
}

func toSecureUserDPHealthView(h coordinator.SecureUserLevelPrivacyHealth) secureUserDPHealthView {
	return secureUserDPHealthView{
		Capability:              toSecureUserDPCapabilityView(h.Capability),
		ProviderStatus:          h.ProviderStatus,
		NoiseProviderStatus:     h.NoiseProviderStatus,
		AccountantStatus:        h.AccountantStatus,
		LedgerStatus:            h.LedgerStatus,
		EventJournalStatus:      h.EventJournalStatus,
		LastSuccessfulRoundAt:   h.LastSuccessfulRoundAt,
		ActiveRunsWithUserLevel: h.ActiveRunsWithUserLevel,
		ReconciliationRequired:  h.ReconciliationRequired,
		DegradedReason:          h.DegradedReason,
		CheckedAtUnixS:          h.CheckedAtUnixS,
	}
}

// secureUserDPRoundView: ADMIN gets the exact epsilon_after_round;
// RESEARCHER gets it rounded to 3 decimal places -- a real, typed
// difference (Work Area K's "exact epsilon history" sensitivity item),
// not a deleted key. VIEWER never reaches this type at all (denied at
// the permission layer -- PermSecureUserDPRoundsRead/RoundRead are not
// granted to VIEWER).
type secureUserDPRoundView struct {
	RunID             string  `json:"run_id"`
	RoundID           uint64  `json:"round_id"`
	EpsilonAfterRound float64 `json:"epsilon_after_round"`
	TargetDelta       float64 `json:"target_delta"`
	NoiseMultiplier   float64 `json:"noise_multiplier"`
	ClippingBound     float64 `json:"clipping_bound"`
	NumClients        uint32  `json:"num_clients"`
	CommittedAtUnixS  float64 `json:"committed_at_unix_s"`
}

func toSecureUserDPRoundView(round coordinator.SecureUserLevelPrivacyRound, role auth.Role) secureUserDPRoundView {
	epsilon := round.EpsilonAfterRound
	if role != auth.RoleAdmin {
		epsilon = roundTo(epsilon, 3)
	}
	return secureUserDPRoundView{
		RunID:             round.RunID,
		RoundID:           round.RoundID,
		EpsilonAfterRound: epsilon,
		TargetDelta:       round.TargetDelta,
		NoiseMultiplier:   round.NoiseMultiplier,
		ClippingBound:     round.ClippingBound,
		NumClients:        round.NumClients,
		CommittedAtUnixS:  round.CommittedAtUnixS,
	}
}

// secureUserDPBudgetView: same ADMIN-exact/RESEARCHER-rounded split as
// rounds, applied to epsilon_spent/epsilon_remaining.
type secureUserDPBudgetView struct {
	RunID            string  `json:"run_id"`
	BudgetConfigured bool    `json:"budget_configured"`
	EpsilonSpent     float64 `json:"epsilon_spent"`
	EpsilonBudget    float64 `json:"epsilon_budget"`
	EpsilonRemaining float64 `json:"epsilon_remaining"`
	TargetDelta      float64 `json:"target_delta"`
	RoundsCommitted  uint64  `json:"rounds_committed"`
}

func toSecureUserDPBudgetView(budget coordinator.SecureUserLevelPrivacyBudget, role auth.Role) secureUserDPBudgetView {
	spent, remaining := budget.EpsilonSpent, budget.EpsilonRemaining
	if role != auth.RoleAdmin {
		spent = roundTo(spent, 3)
		remaining = roundTo(remaining, 3)
	}
	return secureUserDPBudgetView{
		RunID:            budget.RunID,
		BudgetConfigured: budget.BudgetConfigured,
		EpsilonSpent:     spent,
		EpsilonBudget:    budget.EpsilonBudget,
		EpsilonRemaining: remaining,
		TargetDelta:      budget.TargetDelta,
		RoundsCommitted:  budget.RoundsCommitted,
	}
}

func roundTo(value float64, places int) float64 {
	scale := 1.0
	for i := 0; i < places; i++ {
		scale *= 10.0
	}
	return float64(int64(value*scale+0.5)) / scale
}

// -- Handlers ------------------------------------------------------------

func (s *Server) handleSecureUserDPStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermSecureUserDPStatusRead); !ok {
		return
	}
	if s.services == nil || s.services.Coordinator == nil || !s.services.Coordinator.Configured() {
		writeError(w, http.StatusServiceUnavailable, "coordinator is not configured")
		return
	}
	status, err := s.services.Coordinator.GetSecureUserLevelPrivacyStatus(r.Context(), r.Header.Get("X-Trace-Id"))
	if s.services.Metrics != nil {
		s.services.Metrics.RecordSecureUserDPRouteRequest("status", routeOutcome(err))
	}
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toSecureUserDPCapabilityView(status))
}

func (s *Server) handleSecureUserDPHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermSecureUserDPHealthRead); !ok {
		return
	}
	if s.services == nil || s.services.Coordinator == nil || !s.services.Coordinator.Configured() {
		writeError(w, http.StatusServiceUnavailable, "coordinator is not configured")
		return
	}
	health, err := s.services.Coordinator.GetSecureUserLevelPrivacyHealth(r.Context(), r.Header.Get("X-Trace-Id"))
	if s.services.Metrics != nil {
		s.services.Metrics.RecordSecureUserDPRouteRequest("health", routeOutcome(err))
		if err == nil {
			s.services.Metrics.RecordSecureUserDPHealth(
				health.ActiveRunsWithUserLevel, health.ReconciliationRequired,
				health.ProviderStatus, health.NoiseProviderStatus, health.AccountantStatus,
				health.LedgerStatus, health.EventJournalStatus)
		}
	}
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toSecureUserDPHealthView(health))
}

func (s *Server) handleSecureUserDPBudget(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermSecureUserDPBudgetRead)
	if !ok {
		return
	}
	runID := r.URL.Query().Get("run_id")
	if runID == "" {
		writeError(w, http.StatusBadRequest, "run_id query parameter is required")
		return
	}
	if s.services == nil || s.services.Coordinator == nil || !s.services.Coordinator.Configured() {
		writeError(w, http.StatusServiceUnavailable, "coordinator is not configured")
		return
	}
	budget, err := s.services.Coordinator.GetSecureUserLevelPrivacyBudget(r.Context(), runID, r.Header.Get("X-Trace-Id"))
	if s.services.Metrics != nil {
		s.services.Metrics.RecordSecureUserDPRouteRequest("budget", routeOutcome(err))
	}
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, toSecureUserDPBudgetView(budget, session.User.Role))
}

// secureUserDPRoundsPageSize: bounded default/maximum page size (Work
// Area I's "bounded page size").
const secureUserDPRoundsPageSize = 100

func (s *Server) handleSecureUserDPRounds(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermSecureUserDPRoundsRead)
	if !ok {
		return
	}
	query := r.URL.Query()
	runID := query.Get("run_id")
	if runID == "" {
		writeError(w, http.StatusBadRequest, "run_id query parameter is required")
		return
	}
	limit := secureUserDPRoundsPageSize
	if raw := query.Get("limit"); raw != "" {
		if parsed, parseErr := strconv.Atoi(raw); parseErr == nil && parsed > 0 && parsed <= secureUserDPRoundsPageSize {
			limit = parsed
		}
	}
	if s.services == nil || s.services.Coordinator == nil || !s.services.Coordinator.Configured() {
		writeError(w, http.StatusServiceUnavailable, "coordinator is not configured")
		return
	}
	result, err := s.services.Coordinator.ListSecureUserLevelPrivacyRounds(r.Context(), coordinator.ListSecureUserLevelPrivacyRoundsRequest{
		RunID: runID, AfterCursor: query.Get("after_cursor"), Limit: uint32(limit), TraceID: r.Header.Get("X-Trace-Id"),
	})
	if s.services.Metrics != nil {
		s.services.Metrics.RecordSecureUserDPRouteRequest("rounds", routeOutcome(err))
	}
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	views := make([]secureUserDPRoundView, 0, len(result.Rounds))
	for _, round := range result.Rounds {
		views = append(views, toSecureUserDPRoundView(round, session.User.Role))
	}
	writeJSON(w, http.StatusOK, map[string]any{"rounds": views, "next_cursor": result.NextCursor})
}

// handleSecureUserDPRoundsRoutes dispatches everything registered under
// the "/rounds/" subtree prefix -- this codebase's established manual-
// path-parsing convention (see handleSecurityWorkerRoutes), not Go
// 1.22+ enhanced ServeMux path patterns.
func (s *Server) handleSecureUserDPRoundsRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/secure-aggregation/privacy/rounds/")
	if path == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	s.handleSecureUserDPRoundDetail(w, r, path)
}

func (s *Server) handleSecureUserDPRoundDetail(w http.ResponseWriter, r *http.Request, roundIDRaw string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermSecureUserDPRoundRead)
	if !ok {
		return
	}
	roundID, parseErr := strconv.ParseUint(roundIDRaw, 10, 64)
	if parseErr != nil {
		writeError(w, http.StatusBadRequest, "invalid roundId")
		return
	}
	runID := r.URL.Query().Get("run_id")
	if runID == "" {
		writeError(w, http.StatusBadRequest, "run_id query parameter is required")
		return
	}
	if s.services == nil || s.services.Coordinator == nil || !s.services.Coordinator.Configured() {
		writeError(w, http.StatusServiceUnavailable, "coordinator is not configured")
		return
	}
	round, found, err := s.services.Coordinator.GetSecureUserLevelPrivacyRound(r.Context(), runID, roundID, r.Header.Get("X-Trace-Id"))
	if s.services.Metrics != nil {
		s.services.Metrics.RecordSecureUserDPRouteRequest("round", routeOutcome(err))
	}
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	if !found {
		writeError(w, http.StatusNotFound, "no committed round found for that run_id/round_id")
		return
	}
	writeJSON(w, http.StatusOK, toSecureUserDPRoundView(round, session.User.Role))
}

func routeOutcome(err error) string {
	if err != nil {
		return "error"
	}
	return "ok"
}
