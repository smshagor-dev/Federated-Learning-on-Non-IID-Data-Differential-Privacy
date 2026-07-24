package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

// Coordinator-backed routes live under /api/v1/coordinator/... rather
// than /api/v1/runs/... — the latter is already the Milestone 1 local
// run-bookkeeping resource (see handleRuns/handleRunRoutes), a distinct
// concept (project/experiment scheduling metadata) from a live federated
// round being driven by the C++ coordinator. Keeping them separate avoids
// silently changing M1 behavior. See docs/go-coordinator-integration.md.

func (s *Server) handleCoordinatorHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	status, err := s.services.Coordinator.Health(r.Context())
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": status})
}

func (s *Server) handleCoordinatorRuns(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin); err != nil {
		writeError(w, http.StatusForbidden, err.Error())
		return
	}
	var req struct {
		RunID                 string  `json:"run_id"`
		Algorithm             string  `json:"algorithm"`
		Weighting             string  `json:"weighting"`
		TotalClients          uint32  `json:"total_clients"`
		TargetClientsPerRound uint32  `json:"target_clients_per_round"`
		MaxRounds             uint32  `json:"max_rounds"`
		MinimumValidResults   uint32  `json:"minimum_valid_results"`
		ClientSelectionSeed   uint64  `json:"client_selection_seed"`
		RoundTimeoutSeconds   uint32  `json:"round_timeout_seconds"`
		ServerLR              float64 `json:"server_lr"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	if req.RunID == "" {
		writeError(w, http.StatusBadRequest, "run_id is required")
		return
	}
	snapshot, err := s.services.Coordinator.CreateRun(r.Context(), application.CreateCoordinatorRunRequest{
		RunID:                 req.RunID,
		Algorithm:             req.Algorithm,
		Weighting:             req.Weighting,
		TotalClients:          req.TotalClients,
		TargetClientsPerRound: req.TargetClientsPerRound,
		MaxRounds:             req.MaxRounds,
		MinimumValidResults:   req.MinimumValidResults,
		ClientSelectionSeed:   req.ClientSelectionSeed,
		RoundTimeoutSeconds:   req.RoundTimeoutSeconds,
		ServerLR:              req.ServerLR,
	})
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, snapshot)
}

func (s *Server) handleCoordinatorRunRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/coordinator/runs/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	runID := parts[0]

	switch {
	case len(parts) == 1:
		s.handleCoordinatorGetRun(w, r, runID)
	case len(parts) == 2 && parts[1] == "events":
		s.handleCoordinatorRunEvents(w, r, runID)
	case len(parts) == 2 && parts[1] == "metrics":
		s.handleCoordinatorRunMetrics(w, r, runID)
	case len(parts) == 3 && parts[1] == "rounds" && parts[2] == "current":
		s.handleCoordinatorCurrentRound(w, r, runID)
	case len(parts) == 2 && r.Method == http.MethodPost:
		s.handleCoordinatorLifecycleAction(w, r, runID, parts[1])
	default:
		writeError(w, http.StatusNotFound, "route not found")
	}
}

func (s *Server) handleCoordinatorGetRun(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	snapshot, err := s.services.Coordinator.GetRun(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

func (s *Server) handleCoordinatorRunMetrics(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	metrics, err := s.services.Coordinator.Metrics(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, metrics)
}

func (s *Server) handleCoordinatorCurrentRound(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	round, err := s.services.Coordinator.CurrentRound(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, round)
}

func (s *Server) handleCoordinatorLifecycleAction(w http.ResponseWriter, r *http.Request, runID, action string) {
	if err := s.services.Auth.Authorize(sessionFromContext(r.Context()), auth.RoleResearcher, auth.RoleAdmin, auth.RoleService); err != nil {
		writeError(w, http.StatusForbidden, err.Error())
		return
	}
	var body struct {
		Reason  string `json:"reason"`
		TraceID string `json:"trace_id"`
	}
	if err := decodeOptionalJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}

	var (
		snapshot coordinator.RunSnapshot
		err      error
	)
	switch action {
	case "start":
		snapshot, err = s.services.Coordinator.StartRun(r.Context(), runID, body.TraceID)
	case "pause":
		snapshot, err = s.services.Coordinator.PauseRun(r.Context(), runID, body.Reason, body.TraceID)
	case "resume":
		snapshot, err = s.services.Coordinator.ResumeRun(r.Context(), runID, body.TraceID)
	case "cancel":
		snapshot, err = s.services.Coordinator.CancelRun(r.Context(), runID, body.Reason, body.TraceID)
	default:
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, snapshot)
}

// handleCoordinatorRunEvents forwards coordinator events as
// Server-Sent Events by polling coordinator.Client.PollEvents in a loop
// (see docs/event-streaming.md for why this repo uses poll-and-forward
// rather than holding one gRPC stream per browser tab open). The client
// may pass ?after=<event_id> to resume from a cursor across reconnects;
// the stream itself also emits `id:` lines so EventSource's native
// Last-Event-ID reconnect behavior works without query-string plumbing.
func (s *Server) handleCoordinatorRunEvents(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if !s.services.Coordinator.Configured() {
		writeError(w, http.StatusServiceUnavailable, "coordinator not configured")
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}

	cursor := r.URL.Query().Get("after")
	if lastEventID := r.Header.Get("Last-Event-ID"); lastEventID != "" {
		cursor = lastEventID
	}

	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	ctx := r.Context()
	ticker := time.NewTicker(750 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			events, err := s.services.Coordinator.PollEvents(ctx, runID, cursor)
			if err != nil {
				if errors.Is(err, coordinator.ErrUnavailable) {
					fmt.Fprintf(w, "event: coordinator-unavailable\ndata: %s\n\n", jsonString(err.Error()))
					flusher.Flush()
					continue
				}
				fmt.Fprintf(w, "event: coordinator-error\ndata: %s\n\n", jsonString(err.Error()))
				flusher.Flush()
				return
			}
			if len(events) == 0 {
				continue
			}
			for _, event := range events {
				payload, marshalErr := json.Marshal(event)
				if marshalErr != nil {
					continue
				}
				fmt.Fprintf(w, "id: %s\nevent: %s\ndata: %s\n\n", event.EventID, event.Type, payload)
				cursor = event.EventID
			}
			flusher.Flush()
		}
	}
}

func writeCoordinatorError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, application.ErrCoordinatorNotConfigured):
		writeError(w, http.StatusServiceUnavailable, err.Error())
	case errors.Is(err, coordinator.ErrUnavailable):
		writeError(w, http.StatusServiceUnavailable, err.Error())
	case errors.Is(err, coordinator.ErrRunNotFound):
		writeError(w, http.StatusNotFound, err.Error())
	case errors.Is(err, coordinator.ErrRejected):
		writeError(w, http.StatusConflict, err.Error())
	default:
		writeError(w, http.StatusInternalServerError, err.Error())
	}
}

// decodeOptionalJSON decodes a JSON body if present; an empty body (no
// reason/trace_id supplied) is not an error for lifecycle actions.
func decodeOptionalJSON(r *http.Request, target any) error {
	defer r.Body.Close()
	err := json.NewDecoder(r.Body).Decode(target)
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	return nil
}

func jsonString(value string) string {
	encoded, err := json.Marshal(value)
	if err != nil {
		return `""`
	}
	return string(encoded)
}
