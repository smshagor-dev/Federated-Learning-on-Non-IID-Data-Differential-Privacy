package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
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
}

type contextKey string

const sessionContextKey contextKey = "auth-session"

func NewServer(services *application.Services) *Server {
	return &Server{services: services}
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
	return mux
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
