package httpapi

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	executiondomain "github.com/smshagor-dev/federated-learning-super-system/go/internal/execution"
)

const executionPrefix = "/api/v1/executions"

type executionAPI struct {
	services *application.Services
	base     http.Handler
}

// WithExecutionAPI adds the canonical execution control surface in front of
// the long-lived API router. Requests outside /api/v1/executions are delegated
// unchanged, so existing routes remain backward compatible while new clients
// can use one lifecycle API instead of coordinating /runs and /coordinator/runs.
func WithExecutionAPI(base http.Handler, services *application.Services) http.Handler {
	if base == nil {
		base = http.NotFoundHandler()
	}
	return &executionAPI{services: services, base: base}
}

func (api *executionAPI) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != executionPrefix && !strings.HasPrefix(r.URL.Path, executionPrefix+"/") {
		api.base.ServeHTTP(w, r)
		return
	}
	if origin := r.Header.Get("Origin"); origin != "" {
		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Vary", "Origin")
	}
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Trace-Id")
	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}

	started := time.Now()
	defer func() {
		if api.services != nil && api.services.Metrics != nil {
			api.services.Metrics.RecordRoute(r.Method+" "+normalizeExecutionRoute(r.URL.Path), float64(time.Since(started).Microseconds())/1000.0)
		}
	}()

	engine, ok := application.ExecutionEngineFor(api.services)
	if !ok || engine == nil {
		writeError(w, http.StatusServiceUnavailable, "execution engine not configured")
		return
	}

	if r.URL.Path == executionPrefix {
		api.handleCollection(engine, w, r)
		return
	}
	if r.URL.Path == executionPrefix+"/workers" {
		api.handleWorkers(engine, w, r)
		return
	}
	api.handleResource(engine, w, r)
}

func (api *executionAPI) handleCollection(engine *application.ExecutionService, w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		ctx, ok := api.authorize(w, r, auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)
		if !ok {
			return
		}
		items, err := engine.List(ctx)
		if err != nil {
			writeExecutionError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, items)
	case http.MethodPost:
		ctx, ok := api.authorize(w, r, auth.RoleResearcher, auth.RoleAdmin)
		if !ok {
			return
		}
		var request struct {
			ExperimentID string               `json:"experiment_id"`
			Spec         executiondomain.Spec `json:"spec"`
		}
		if !decodeJSON(w, r, &request) {
			return
		}
		item, err := engine.Create(ctx, request.ExperimentID, request.Spec)
		if err != nil {
			writeExecutionError(w, err)
			return
		}
		writeJSON(w, http.StatusCreated, item)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (api *executionAPI) handleWorkers(engine *application.ExecutionService, w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	ctx, ok := api.authorize(w, r, auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)
	if !ok {
		return
	}
	backend := executiondomain.Backend(r.URL.Query().Get("backend"))
	if backend == "" {
		backend = executiondomain.BackendDistributed
	}
	workers, err := engine.Workers(ctx, backend)
	if err != nil {
		writeExecutionError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, workers)
}

func (api *executionAPI) handleResource(engine *application.ExecutionService, w http.ResponseWriter, r *http.Request) {
	rest := strings.TrimPrefix(r.URL.Path, executionPrefix+"/")
	parts := strings.Split(rest, "/")
	if len(parts) == 0 || strings.TrimSpace(parts[0]) == "" {
		writeError(w, http.StatusNotFound, "execution not found")
		return
	}
	id := parts[0]
	if len(parts) == 1 {
		if r.Method != http.MethodGet {
			writeError(w, http.StatusMethodNotAllowed, "method not allowed")
			return
		}
		ctx, ok := api.authorize(w, r, auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)
		if !ok {
			return
		}
		var item any
		var err error
		if r.URL.Query().Get("refresh") == "true" {
			item, err = engine.Reconcile(ctx, id)
		} else {
			item, err = engine.Get(ctx, id)
		}
		if err != nil {
			writeExecutionError(w, err)
			return
		}
		writeJSON(w, http.StatusOK, item)
		return
	}
	if len(parts) != 2 {
		writeError(w, http.StatusNotFound, "execution route not found")
		return
	}

	action := parts[1]
	if action == "events" {
		api.handleEvents(engine, id, w, r)
		return
	}
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	ctx, ok := api.authorize(w, r, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)
	if !ok {
		return
	}
	traceID := r.Header.Get("X-Trace-Id")
	var response any
	var err error
	switch action {
	case "start":
		response, err = engine.Start(ctx, id, traceID)
	case "pause":
		reason := decodeReason(r)
		response, err = engine.Pause(ctx, id, reason, traceID)
	case "resume":
		response, err = engine.Resume(ctx, id, traceID)
	case "cancel":
		reason := decodeReason(r)
		response, err = engine.Cancel(ctx, id, reason, traceID)
	case "reconcile":
		response, err = engine.Reconcile(ctx, id)
	default:
		writeError(w, http.StatusNotFound, "execution action not found")
		return
	}
	if err != nil {
		writeExecutionError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, response)
}

func (api *executionAPI) handleEvents(engine *application.ExecutionService, id string, w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	ctx, ok := api.authorize(w, r, auth.RoleViewer, auth.RoleResearcher, auth.RoleAdmin, auth.RoleService)
	if !ok {
		return
	}
	limit := 200
	if raw := r.URL.Query().Get("limit"); raw != "" {
		parsed, err := strconv.Atoi(raw)
		if err != nil || parsed < 1 || parsed > 5000 {
			writeError(w, http.StatusBadRequest, "limit must be an integer in [1,5000]")
			return
		}
		limit = parsed
	}
	if _, err := engine.Get(ctx, id); err != nil {
		writeExecutionError(w, err)
		return
	}
	events, err := engine.Events(ctx, id, limit)
	if err != nil {
		writeExecutionError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, events)
}

func (api *executionAPI) authorize(w http.ResponseWriter, r *http.Request, roles ...auth.Role) (context.Context, bool) {
	if api.services == nil || api.services.Auth == nil {
		writeError(w, http.StatusServiceUnavailable, "authentication service unavailable")
		return nil, false
	}
	header := strings.TrimSpace(r.Header.Get("Authorization"))
	if !strings.HasPrefix(header, "Bearer ") {
		writeError(w, http.StatusUnauthorized, "missing bearer token")
		return nil, false
	}
	session, err := api.services.Auth.Authenticate(r.Context(), strings.TrimSpace(strings.TrimPrefix(header, "Bearer ")))
	if err != nil {
		writeError(w, http.StatusUnauthorized, "unauthorized")
		return nil, false
	}
	if err := api.services.Auth.Authorize(session, roles...); err != nil {
		writeError(w, http.StatusForbidden, "forbidden")
		return nil, false
	}
	ctx := application.ContextWithActor(r.Context(), application.Actor{
		ID:    session.User.ID,
		Email: session.User.Email,
		Role:  string(session.User.Role),
	})
	return ctx, true
}

func decodeReason(r *http.Request) string {
	if r.Body == nil || r.ContentLength == 0 {
		return "operator_request"
	}
	var request struct {
		Reason string `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		return "operator_request"
	}
	if strings.TrimSpace(request.Reason) == "" {
		return "operator_request"
	}
	return strings.TrimSpace(request.Reason)
}

func writeExecutionError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, application.ErrNotFound):
		writeError(w, http.StatusNotFound, err.Error())
	case errors.Is(err, application.ErrInvalidTransition), errors.Is(err, executiondomain.ErrRevisionConflict):
		writeError(w, http.StatusConflict, err.Error())
	case errors.Is(err, executiondomain.ErrInvalidSpec), errors.Is(err, executiondomain.ErrUnsupportedMapping):
		writeError(w, http.StatusUnprocessableEntity, err.Error())
	case errors.Is(err, executiondomain.ErrBackendNotConfigured), errors.Is(err, coordinator.ErrUnavailable), errors.Is(err, coordinator.ErrCanonicalRunUnsupported):
		writeError(w, http.StatusServiceUnavailable, err.Error())
	case errors.Is(err, executiondomain.ErrSecurityPreflight):
		writeError(w, http.StatusPreconditionFailed, err.Error())
	default:
		writeError(w, http.StatusInternalServerError, err.Error())
	}
}

func normalizeExecutionRoute(path string) string {
	if path == executionPrefix || path == executionPrefix+"/workers" {
		return path
	}
	rest := strings.TrimPrefix(path, executionPrefix+"/")
	parts := strings.Split(rest, "/")
	if len(parts) == 1 {
		return executionPrefix + "/{id}"
	}
	if len(parts) >= 2 {
		return executionPrefix + "/{id}/" + parts[1]
	}
	return executionPrefix
}
