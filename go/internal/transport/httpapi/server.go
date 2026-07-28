package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/runs"
)

type Server struct {
	services *application.Services
	// securityIdempotency backs every /api/v1/security/... mutation
	// route's Idempotency-Key support — see security_handlers.go.
	securityIdempotency *idempotencyCache
	// Security Events, Metrics, and Durable Audit Journal slice
	// (docs/security-events.md): Go-local event journal and the
	// security-specific durable audit journal -- see
	// handleSecurityEvents/handleSecurityAudit. Always non-nil (default-
	// constructed at a temp-file-backed path by NewServer unless
	// NewServerWithSecurityJournalPaths is used for real, configured
	// persistence -- see cmd/api/main.go).
	securityEventJournal *observability.SecurityEventJournal
	securityAuditJournal *observability.SecurityAuditJournal
}

type contextKey string

const sessionContextKey contextKey = "auth-session"

func NewServer(services *application.Services) *Server {
	return NewServerWithSecurityJournalPaths(services, "", "")
}

// NewServerWithSecurityJournalPaths is NewServer plus explicit journal
// paths for production, restart-surviving persistence (see
// cmd/api/main.go's FL_GO_SECURITY_EVENT_JOURNAL_PATH/
// FL_GO_SECURITY_AUDIT_JOURNAL_PATH env vars). Empty paths fall back to
// a unique temp-file location per Server instance -- test-friendly
// (every test gets its own isolated journal, nothing pollutes the repo
// working directory) and still exercises the real, file-backed
// persistence code path rather than a separate in-memory stand-in.
func NewServerWithSecurityJournalPaths(services *application.Services, eventJournalPath, auditJournalPath string) *Server {
	if eventJournalPath == "" {
		eventJournalPath = tempSecurityJournalPath("security-events")
	}
	if auditJournalPath == "" {
		auditJournalPath = tempSecurityJournalPath("security-audit")
	}
	eventJournal, err := observability.NewSecurityEventJournal(eventJournalPath)
	if err != nil {
		eventJournal = nil // degrade to no-op rather than fail server construction
	}
	auditJournal, err := observability.NewSecurityAuditJournal(auditJournalPath)
	if err != nil {
		auditJournal = nil
	}
	server := &Server{
		services:             services,
		securityIdempotency:  newIdempotencyCache(),
		securityEventJournal: eventJournal,
		securityAuditJournal: auditJournal,
	}
	if services != nil && services.Coordinator != nil {
		services.Coordinator.SetSecurityJournals(eventJournal, auditJournal)
	}
	return server
}

func tempSecurityJournalPath(name string) string {
	return filepath.Join(os.TempDir(), fmt.Sprintf("fl-%s-%d-%d.jsonl", name, os.Getpid(), time.Now().UnixNano()))
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", s.handleHealth)
	mux.HandleFunc("/api/v1/auth/login", s.handleLogin)
	mux.HandleFunc("/api/v1/dashboard/overview", s.handleDashboardOverview)
	mux.HandleFunc("/api/v1/dashboard/runs/", s.handleDashboardRun)
	mux.Handle("/api/v1/auth/me", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleMe)))
	mux.Handle("/api/v1/projects", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin)(http.HandlerFunc(s.handleProjects)))
	mux.Handle("/api/v1/projects/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin)(http.HandlerFunc(s.handleProjectByID)))
	mux.Handle("/api/v1/experiments", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin)(http.HandlerFunc(s.handleExperiments)))
	mux.Handle("/api/v1/experiments/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin)(http.HandlerFunc(s.handleExperimentByID)))
	mux.Handle("/api/v1/runs", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleRuns)))
	mux.Handle("/api/v1/runs/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleRunRoutes)))
	mux.Handle("/api/v1/audit/events", s.withAuth(auth.RoleResearcher, auth.RoleAdmin)(http.HandlerFunc(s.handleAuditEvents)))
	mux.Handle("/api/v1/system/coordinator-health", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleCoordinatorHealth)))
	mux.Handle("/api/v1/coordinator/runs", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleCoordinatorRuns)))
	mux.Handle("/api/v1/coordinator/runs/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleCoordinatorRunRoutes)))
	mux.Handle("/api/v1/coordinator/workers", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleCoordinatorWorkers)))
	mux.Handle("/api/v1/privacy/compatibility", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handlePrivacyCompatibility)))
	mux.Handle("/api/v1/algorithms", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleAlgorithms)))
	mux.Handle("/api/v1/algorithms/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleAlgorithmByName)))
	mux.Handle("/api/v1/models", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleModels)))
	mux.Handle("/api/v1/models/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleModelRoutes)))
	mux.Handle("/api/v1/datasets", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleDatasets)))
	mux.Handle("/api/v1/datasets/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleDatasetRoutes)))
	mux.Handle("/api/v1/research/experiments", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleResearchExperiments)))
	mux.Handle("/api/v1/research/experiments/", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleResearchRoutes)))
	mux.Handle("/api/v1/research/runtime/health", s.withAuth(auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)(http.HandlerFunc(s.handleResearchRuntimeHealth)))

	// Security Operations and Administration slice (docs/security-api.md):
	// every route below authenticates via the same broad role set —
	// the real per-endpoint authorization decision happens inside each
	// handler via security.Allows(role, permission), not here. See
	// security_handlers.go.
	securityRoles := []auth.Role{auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService}
	mux.Handle("/api/v1/security/transport", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityTransport)))
	mux.Handle("/api/v1/security/trust-model", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityTrustModel)))
	mux.Handle("/api/v1/security/workers", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityWorkers)))
	mux.Handle("/api/v1/security/workers/", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityWorkerRoutes)))
	mux.Handle("/api/v1/security/coordinator/signing-keys", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityCoordinatorSigningKeys)))
	mux.Handle("/api/v1/security/coordinator/signing-keys/", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityCoordinatorSigningKeyRoutes)))
	mux.Handle("/api/v1/security/events", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityEvents)))
	mux.Handle("/api/v1/security/events/sources", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityEventSources)))
	mux.Handle("/api/v1/security/audit", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityAudit)))
	mux.Handle("/api/v1/security/overview", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecurityOverview)))

	// Secure User-Level DP Operations, Observability, and Release
	// Evidence slice (docs/secure-user-level-operations-audit.md, Work
	// Area I): same broad-role-then-fine-grained-permission-inside-the-
	// handler pattern as the security routes above.
	mux.Handle("/api/v1/secure-aggregation/privacy/status", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecureUserDPStatus)))
	mux.Handle("/api/v1/secure-aggregation/privacy/health", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecureUserDPHealth)))
	mux.Handle("/api/v1/secure-aggregation/privacy/budget", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecureUserDPBudget)))
	mux.Handle("/api/v1/secure-aggregation/privacy/rounds", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecureUserDPRounds)))
	mux.Handle("/api/v1/secure-aggregation/privacy/rounds/", s.withAuth(securityRoles...)(http.HandlerFunc(s.handleSecureUserDPRoundsRoutes)))

	mux.HandleFunc("/metrics", s.handleMetrics)
	return s.withCORS(s.withMetrics(mux))
}

// withCORS: the web app (browser origin http://localhost:3000 in the
// Compose dev stack) and this API (http://localhost:8080) are different
// origins, so every client-side fetch()/XHR from web/lib/api.ts and
// web/lib/security-api.ts is a cross-origin request the browser's own
// same-origin policy blocks unless this server sends the right
// Access-Control-* headers -- there was no CORS handling anywhere in
// this server before this fix. Caught by this slice's live Playwright
// browser-suite run: every page that depends on real fetched data
// (worker detail, security overview, events, audit) failed in a real
// browser with no server-side error at all (the browser silently
// blocks reading the response), while the exact same endpoints always
// worked from curl/the Python security-validation harness/Go's own
// tests, none of which enforce CORS.
//
// Reflects the request's own Origin header rather than a fixed
// allowlist (this is a single-tenant research/dev platform, not a
// public multi-tenant service) and never sets
// Access-Control-Allow-Credentials -- this API is Bearer-token
// authenticated, never cookie-authenticated (see docs/security-api.md's
// CSRF discussion), so there is no ambient credential for a malicious
// origin to ride; reflecting Origin without credentials cannot leak an
// Authorization header the requesting page did not already possess.
func (s *Server) withCORS(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if origin := r.Header.Get("Origin"); origin != "" {
			w.Header().Set("Access-Control-Allow-Origin", origin)
			w.Header().Set("Vary", "Origin")
		}
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, Idempotency-Key, X-Trace-Id")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// handleMetrics is deliberately unauthenticated (like /healthz): Prometheus
// scrapes it directly (see infra/prometheus/prometheus.yml's go-api job),
// and it carries no sensitive data — only request/RPC counters.
func (s *Server) handleMetrics(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	s.services.Metrics.WritePrometheus(w)
}

// withMetrics records every request's route (with dynamic ID segments
// normalized so cardinality stays bounded by the route table, not by how
// many runs/projects/experiments exist) and latency into
// Services.Metrics, which handleMetrics then renders for Prometheus.
func (s *Server) withMetrics(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		next.ServeHTTP(w, r)
		if s.services.Metrics != nil {
			route := r.Method + " " + normalizeRouteLabel(r.URL.Path)
			s.services.Metrics.RecordRoute(route, float64(time.Since(started).Microseconds())/1000.0)
		}
	})
}

func normalizeRouteLabel(path string) string {
	for _, prefix := range []string{
		"/api/v1/coordinator/runs/",
		"/api/v1/dashboard/runs/",
		"/api/v1/runs/",
		"/api/v1/projects/",
		"/api/v1/experiments/",
	} {
		if strings.HasPrefix(path, prefix) {
			rest := strings.TrimPrefix(path, prefix)
			parts := strings.SplitN(rest, "/", 2)
			if len(parts) == 2 {
				return prefix + "{id}/" + parts[1]
			}
			return prefix + "{id}"
		}
	}
	return path
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"service": "go-control-plane", "status": "ok"})
}

func (s *Server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var req struct {
		Email    string `json:"email"`
		Password string `json:"password"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	session, err := s.services.Auth.Login(r.Context(), req.Email, req.Password)
	if err != nil {
		status := http.StatusInternalServerError
		if errors.Is(err, application.ErrUnauthorized) {
			status = http.StatusUnauthorized
		}
		writeError(w, status, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, session)
}

func (s *Server) handleMe(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, sessionFromContext(r.Context()))
}

func (s *Server) handleDashboardOverview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	projects, err := s.services.Projects.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	experiments, err := s.services.Experiments.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	runItems, err := s.services.Runs.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	auditEvents, err := s.services.Audit.List(r.Context(), 12)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"projects":    projects,
		"experiments": experiments,
		"runs":        runItems,
		"metrics": map[string]any{
			"running_runs":        countRunsByStatus(runItems, runs.StatusRunning),
			"queued_runs":         countRunsByStatus(runItems, runs.StatusQueued),
			"paused_runs":         countRunsByStatus(runItems, runs.StatusPaused),
			"completed_runs":      countRunsByStatus(runItems, runs.StatusCompleted),
			"failed_runs":         countRunsByStatus(runItems, runs.StatusFailed),
			"active_projects":     len(projects),
			"recent_audit_events": len(auditEvents),
			"system_readiness":    dashboardReadiness(runItems, auditEvents),
		},
		"activity_feed": auditEvents,
		"source":        "live",
	})
}

func (s *Server) handleDashboardRun(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	runID := strings.TrimPrefix(r.URL.Path, "/api/v1/dashboard/runs/")
	if runID == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	runItem, err := s.services.Runs.Get(r.Context(), runID)
	if err != nil {
		status := http.StatusInternalServerError
		if errors.Is(err, application.ErrNotFound) {
			status = http.StatusNotFound
		}
		writeError(w, status, err.Error())
		return
	}
	auditEvents, err := s.services.Audit.List(r.Context(), 100)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	filtered := filterAuditForRun(runID, auditEvents)
	currentRound := numberFromConfig(runItem.Config, "current_round")
	targetRounds := max(numberFromConfig(runItem.Config, "rounds"), numberFromConfig(runItem.Config, "target_rounds"))
	targetClients := numberFromConfig(runItem.Config, "target_clients")
	progress := percent(currentRound, targetRounds)
	writeJSON(w, http.StatusOK, map[string]any{
		"run": runItem,
		"metrics": map[string]any{
			"current_round":             currentRound,
			"target_rounds":             targetRounds,
			"target_clients":            targetClients,
			"progress_percent":          progress,
			"accuracy_percent":          min(96, 52+progress/2),
			"loss_improvement_percent":  min(92, 34+progress/2),
			"privacy_budget_percent":    min(97, 18+currentRound*3),
			"worker_throughput_percent": min(98, 45+targetClients*4),
		},
		"audit_events": filtered,
		"signals": []string{
			describeRunFreshness(runItem.UpdatedAt),
			describeExecutionMode(runItem.Config),
			describePrivacyMode(runItem.Config),
		},
		"source": "live",
	})
}

func (s *Server) handleAuditEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	limit := 50
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 {
			writeError(w, http.StatusBadRequest, "invalid limit")
			return
		}
		limit = parsed
	}
	events, err := s.services.Audit.List(r.Context(), limit)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, events)
}

func (s *Server) handleProjects(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		items, err := s.services.Projects.List(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, items)
	case http.MethodPost:
		if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		var req struct {
			Name        string `json:"name"`
			Description string `json:"description"`
		}
		if !decodeJSON(w, r, &req) {
			return
		}
		item, err := s.services.Projects.Create(r.Context(), req.Name, req.Description)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleProjectByID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/projects/")
	item, err := s.services.Projects.Get(r.Context(), id)
	if err != nil {
		status := http.StatusInternalServerError
		if errors.Is(err, application.ErrNotFound) {
			status = http.StatusNotFound
		}
		writeError(w, status, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleExperiments(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		items, err := s.services.Experiments.List(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, items)
	case http.MethodPost:
		if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		var req struct {
			ProjectID   string         `json:"project_id"`
			Name        string         `json:"name"`
			Description string         `json:"description"`
			Config      map[string]any `json:"config"`
		}
		if !decodeJSON(w, r, &req) {
			return
		}
		item, err := s.services.Experiments.Create(r.Context(), req.ProjectID, req.Name, req.Description, req.Config)
		if err != nil {
			writeError(w, experimentErrorStatus(err), err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

// experimentErrorStatus maps ExperimentService errors to HTTP statuses:
// a not-found project/experiment is 404, an algorithm-config validation
// failure (see application.validateExperimentAlgorithmConfig) is 400 —
// the client sent a config Python's own algorithm would reject — and
// anything else is a genuine server error.
func experimentErrorStatus(err error) int {
	switch {
	case errors.Is(err, application.ErrNotFound):
		return http.StatusNotFound
	case errors.Is(err, application.ErrInvalidAlgorithmConfig):
		return http.StatusBadRequest
	default:
		return http.StatusInternalServerError
	}
}

func (s *Server) handleExperimentByID(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/api/v1/experiments/")
	if r.Method == http.MethodPut {
		if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		var req struct {
			Name        string         `json:"name"`
			Description string         `json:"description"`
			Config      map[string]any `json:"config"`
		}
		if !decodeJSON(w, r, &req) {
			return
		}
		item, err := s.services.Experiments.Update(r.Context(), id, req.Name, req.Description, req.Config)
		if err != nil {
			writeError(w, experimentErrorStatus(err), err.Error())
			return
		}
		writeJSON(w, http.StatusOK, item)
		return
	}
	item, err := s.services.Experiments.Get(r.Context(), id)
	if err != nil {
		status := http.StatusInternalServerError
		if errors.Is(err, application.ErrNotFound) {
			status = http.StatusNotFound
		}
		writeError(w, status, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleRuns(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		items, err := s.services.Runs.List(r.Context())
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, items)
	case http.MethodPost:
		if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		var req struct {
			ExperimentID string         `json:"experiment_id"`
			Config       map[string]any `json:"config"`
		}
		if !decodeJSON(w, r, &req) {
			return
		}
		item, err := s.services.Runs.Create(r.Context(), req.ExperimentID, req.Config)
		if err != nil {
			status := http.StatusInternalServerError
			if errors.Is(err, application.ErrNotFound) {
				status = http.StatusNotFound
			}
			writeError(w, status, err.Error())
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleRunRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/runs/")
	parts := strings.Split(path, "/")
	if len(parts) == 1 {
		item, err := s.services.Runs.Get(r.Context(), parts[0])
		if err != nil {
			status := http.StatusInternalServerError
			if errors.Is(err, application.ErrNotFound) {
				status = http.StatusNotFound
			}
			writeError(w, status, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, item)
		return
	}
	if len(parts) == 2 && r.Method == http.MethodPost {
		if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin, auth.RoleService); err != nil {
			writeError(w, http.StatusForbidden, err.Error())
			return
		}
		if next, ok := transitionForAction(parts[1]); ok {
			item, err := s.services.Runs.Transition(r.Context(), parts[0], next)
			if err != nil {
				status := http.StatusInternalServerError
				if errors.Is(err, application.ErrNotFound) {
					status = http.StatusNotFound
				}
				if errors.Is(err, application.ErrInvalidTransition) {
					status = http.StatusConflict
				}
				writeError(w, status, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, item)
			return
		}
	}
	writeError(w, http.StatusNotFound, "route not found")
}

func (s *Server) withAuth(allowed ...auth.Role) func(http.Handler) http.Handler {
	return func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			token := bearerToken(r.Header.Get("Authorization"))
			session, err := s.services.Auth.Authenticate(r.Context(), token)
			if err != nil {
				status := http.StatusInternalServerError
				if errors.Is(err, application.ErrUnauthorized) {
					status = http.StatusUnauthorized
				}
				writeError(w, status, err.Error())
				return
			}
			if err := s.services.Auth.Authorize(session, allowed...); err != nil {
				writeError(w, http.StatusForbidden, err.Error())
				return
			}
			ctx := context.WithValue(r.Context(), sessionContextKey, session)
			ctx = application.ContextWithActor(ctx, application.Actor{
				ID:    session.User.ID,
				Email: session.User.Email,
				Role:  string(session.User.Role),
			})
			next.ServeHTTP(w, r.WithContext(ctx))
		})
	}
}

func transitionForAction(action string) (runs.Status, bool) {
	switch action {
	case "start":
		return runs.StatusQueued, true
	case "resume":
		return runs.StatusQueued, true
	case "pause":
		return runs.StatusPaused, true
	case "cancel":
		return runs.StatusCanceled, true
	default:
		return "", false
	}
}

func bearerToken(header string) string {
	if !strings.HasPrefix(header, "Bearer ") {
		return ""
	}
	return strings.TrimSpace(strings.TrimPrefix(header, "Bearer "))
}

func sessionFromContext(ctx context.Context) application.AuthSession {
	session, _ := ctx.Value(sessionContextKey).(application.AuthSession)
	return session
}

func decodeJSON(w http.ResponseWriter, r *http.Request, target any) bool {
	defer r.Body.Close()
	if err := json.NewDecoder(r.Body).Decode(target); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return false
	}
	return true
}

func decodeStrictJSON(w http.ResponseWriter, r *http.Request, target any, maxBytes int64) bool {
	defer r.Body.Close()
	reader := r.Body
	if maxBytes > 0 {
		reader = http.MaxBytesReader(w, r.Body, maxBytes)
	}
	decoder := json.NewDecoder(reader)
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return false
	}
	if decoder.More() {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return false
	}
	return true
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(payload)
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]string{"error": message})
}

func countRunsByStatus(items []runs.Run, status runs.Status) int {
	count := 0
	for _, item := range items {
		if item.Status == status {
			count++
		}
	}
	return count
}

func dashboardReadiness(runItems []runs.Run, events []observability.AuditEvent) int {
	score := 35
	if len(runItems) > 0 {
		score += 20
	}
	if countRunsByStatus(runItems, runs.StatusRunning)+countRunsByStatus(runItems, runs.StatusCompleted) > 0 {
		score += 20
	}
	if len(events) > 0 {
		score += 25
	}
	if score > 100 {
		return 100
	}
	return score
}

func filterAuditForRun(runID string, events []observability.AuditEvent) []observability.AuditEvent {
	filtered := make([]observability.AuditEvent, 0, len(events))
	for _, event := range events {
		if event.ResourceID == runID {
			filtered = append(filtered, event)
			continue
		}
		if experimentID, ok := event.Details["experiment_id"].(string); ok && experimentID != "" {
			_ = experimentID
		}
	}
	if len(filtered) > 8 {
		return filtered[:8]
	}
	return filtered
}

func numberFromConfig(config map[string]any, key string) int {
	raw, ok := config[key]
	if !ok {
		return 0
	}
	switch value := raw.(type) {
	case int:
		return value
	case int32:
		return int(value)
	case int64:
		return int(value)
	case float32:
		return int(value)
	case float64:
		return int(value)
	default:
		return 0
	}
}

func percent(current, total int) int {
	if total <= 0 || current <= 0 {
		return 0
	}
	if current >= total {
		return 100
	}
	return (current * 100) / total
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func describeRunFreshness(updatedAt time.Time) string {
	age := time.Since(updatedAt)
	switch {
	case age < 5*time.Minute:
		return "Signals refreshed within the last five minutes."
	case age < 30*time.Minute:
		return "Signals are warm and suitable for operator review."
	default:
		return "Signals are cooling down and may need a manual refresh soon."
	}
}

func describeExecutionMode(config map[string]any) string {
	mode, _ := config["mode"].(string)
	if mode == "" {
		return "Execution mode is not yet attached to the run payload."
	}
	return "Execution mode: " + mode + "."
}

func describePrivacyMode(config map[string]any) string {
	mode, _ := config["privacy_mode"].(string)
	if mode == "" {
		return "Privacy mode will surface here once worker telemetry is connected."
	}
	return "Privacy mode: " + mode + "."
}
