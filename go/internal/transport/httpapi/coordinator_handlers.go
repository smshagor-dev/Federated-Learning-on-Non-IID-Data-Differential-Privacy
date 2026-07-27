package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
)

// Coordinator-backed routes live under /api/v1/coordinator/... rather
// than /api/v1/runs/... — the latter is already the Foundation phase local
// run-bookkeeping resource (see handleRuns/handleRunRoutes), a distinct
// concept (project/experiment scheduling metadata) from a live federated
// round being driven by the C++ coordinator. Keeping them separate avoids
// silently changing Foundation phase behavior. See docs/go-coordinator-integration.md.

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
		// Fields below close the CreateRun wire-mapping gap — see
		// docs/create-run-wire-mapping.md.
		ClientIDs        []string `json:"client_ids"`
		LocalEpochs      uint32   `json:"local_epochs"`
		BatchSize        uint32   `json:"batch_size"`
		LearningRate     float64  `json:"learning_rate"`
		Momentum         float64  `json:"momentum"`
		WeightDecay      float64  `json:"weight_decay"`
		FedProxMu        float64  `json:"fedprox_mu"`
		TaskLeaseSeconds uint32   `json:"task_lease_seconds"`
		MaxTaskRetries   uint32   `json:"max_task_retries"`
		RequestID        string   `json:"request_id"`
		ModelManifest    struct {
			ModelID      string `json:"model_id"`
			ModelVersion string `json:"model_version"`
			Tensors      []struct {
				Name  string   `json:"name"`
				Shape []uint64 `json:"shape"`
			} `json:"tensors"`
			AggregationManifest struct {
				SharedParameterNames       []string `json:"shared_parameter_names"`
				PersonalizedParameterNames []string `json:"personalized_parameter_names"`
				FrozenParameterNames       []string `json:"frozen_parameter_names"`
				SchemaHash                 string   `json:"schema_hash"`
			} `json:"aggregation_manifest"`
		} `json:"model_manifest"`
		// Privacy Engineering phase: see docs/hybrid-dp.md. mode omitted
		// or "" means a non-private run — unchanged pre-existing
		// behavior; the other three sub-objects are only consulted by
		// the coordinator when mode actually activates their mechanism.
		Privacy struct {
			Mode        string `json:"mode"`
			SampleLevel struct {
				NoiseMultiplier float64 `json:"noise_multiplier"`
				MaxGradNorm     float64 `json:"max_grad_norm"`
				TargetDelta     float64 `json:"target_delta"`
				Accountant      string  `json:"accountant"`
				PoissonSampling bool    `json:"poisson_sampling"`
				EpsilonBudget   float64 `json:"epsilon_budget"`
			} `json:"sample_level"`
			UserLevel struct {
				NoiseMultiplier      float64 `json:"noise_multiplier"`
				TargetDelta          float64 `json:"target_delta"`
				Accountant           string  `json:"accountant"`
				InitialClippingBound float64 `json:"initial_clipping_bound"`
				WeightingStrategy    string  `json:"weighting_strategy"`
				SecureRandom         bool    `json:"secure_random"`
				EpsilonBudget        float64 `json:"epsilon_budget"`
			} `json:"user_level"`
			AdaptiveClipping struct {
				Enabled              bool    `json:"enabled"`
				TargetQuantile       float64 `json:"target_quantile"`
				ClipLearningRate     float64 `json:"clip_learning_rate"`
				InitialClip          float64 `json:"initial_clip"`
				MinClip              float64 `json:"min_clip"`
				MaxClip              float64 `json:"max_clip"`
				CountNoiseMultiplier float64 `json:"count_noise_multiplier"`
				TargetDelta          float64 `json:"target_delta"`
				EpsilonBudget        float64 `json:"epsilon_budget"`
			} `json:"adaptive_clipping"`
			WarningThresholdFraction float64 `json:"warning_threshold_fraction"`
		} `json:"privacy"`
	}
	if !decodeJSON(w, r, &req) {
		return
	}
	if req.RunID == "" {
		writeError(w, http.StatusBadRequest, "run_id is required")
		return
	}
	tensors := make([]coordinator.TensorSpec, 0, len(req.ModelManifest.Tensors))
	for _, tensor := range req.ModelManifest.Tensors {
		tensors = append(tensors, coordinator.TensorSpec{Name: tensor.Name, Shape: tensor.Shape})
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
		ClientIDs:             req.ClientIDs,
		LocalEpochs:           req.LocalEpochs,
		BatchSize:             req.BatchSize,
		LearningRate:          req.LearningRate,
		Momentum:              req.Momentum,
		WeightDecay:           req.WeightDecay,
		FedProxMu:             req.FedProxMu,
		TaskLeaseSeconds:      req.TaskLeaseSeconds,
		MaxTaskRetries:        req.MaxTaskRetries,
		RequestID:             req.RequestID,
		ModelManifest: coordinator.ModelManifest{
			ModelID:      req.ModelManifest.ModelID,
			ModelVersion: req.ModelManifest.ModelVersion,
			Tensors:      tensors,
			AggregationManifest: coordinator.AggregationManifest{
				SharedParameterNames:       req.ModelManifest.AggregationManifest.SharedParameterNames,
				PersonalizedParameterNames: req.ModelManifest.AggregationManifest.PersonalizedParameterNames,
				FrozenParameterNames:       req.ModelManifest.AggregationManifest.FrozenParameterNames,
				SchemaHash:                 req.ModelManifest.AggregationManifest.SchemaHash,
			},
		},
		Privacy: coordinator.PrivacyConfig{
			Mode: coordinator.PrivacyMode(req.Privacy.Mode),
			SampleLevel: coordinator.SampleLevelDPConfig{
				NoiseMultiplier: req.Privacy.SampleLevel.NoiseMultiplier,
				MaxGradNorm:     req.Privacy.SampleLevel.MaxGradNorm,
				TargetDelta:     req.Privacy.SampleLevel.TargetDelta,
				Accountant:      req.Privacy.SampleLevel.Accountant,
				PoissonSampling: req.Privacy.SampleLevel.PoissonSampling,
				EpsilonBudget:   req.Privacy.SampleLevel.EpsilonBudget,
			},
			UserLevel: coordinator.UserLevelDPConfig{
				NoiseMultiplier:      req.Privacy.UserLevel.NoiseMultiplier,
				TargetDelta:          req.Privacy.UserLevel.TargetDelta,
				Accountant:           req.Privacy.UserLevel.Accountant,
				InitialClippingBound: req.Privacy.UserLevel.InitialClippingBound,
				WeightingStrategy:    req.Privacy.UserLevel.WeightingStrategy,
				SecureRandom:         req.Privacy.UserLevel.SecureRandom,
				EpsilonBudget:        req.Privacy.UserLevel.EpsilonBudget,
			},
			AdaptiveClipping: coordinator.AdaptiveClippingConfig{
				Enabled:              req.Privacy.AdaptiveClipping.Enabled,
				TargetQuantile:       req.Privacy.AdaptiveClipping.TargetQuantile,
				ClipLearningRate:     req.Privacy.AdaptiveClipping.ClipLearningRate,
				InitialClip:          req.Privacy.AdaptiveClipping.InitialClip,
				MinClip:              req.Privacy.AdaptiveClipping.MinClip,
				MaxClip:              req.Privacy.AdaptiveClipping.MaxClip,
				CountNoiseMultiplier: req.Privacy.AdaptiveClipping.CountNoiseMultiplier,
				TargetDelta:          req.Privacy.AdaptiveClipping.TargetDelta,
				EpsilonBudget:        req.Privacy.AdaptiveClipping.EpsilonBudget,
			},
			WarningThresholdFraction: req.Privacy.WarningThresholdFraction,
		},
	})
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, snapshot)
}

// handleCoordinatorWorkers serves every registered worker's privacy
// capabilities (docs/worker-privacy-capabilities.md) —
// GET /api/v1/coordinator/workers.
func (s *Server) handleCoordinatorWorkers(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	workers, err := s.services.Coordinator.ListWorkers(r.Context())
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"workers": workers})
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
	case len(parts) == 2 && parts[1] == "personalization":
		s.handleCoordinatorPersonalization(w, r, runID)
	case len(parts) == 2 && parts[1] == "fairness":
		s.handleCoordinatorFairness(w, r, runID)
	case len(parts) == 2 && parts[1] == "algorithm-summary":
		s.handleCoordinatorAlgorithmSummary(w, r, runID)
	case len(parts) == 4 && parts[1] == "clients" && parts[3] == "personalization":
		s.handleCoordinatorClientPersonalization(w, r, runID, parts[2])
	case len(parts) == 3 && parts[1] == "privacy" && parts[2] == "metrics":
		s.handleCoordinatorPrivacyMetrics(w, r, runID)
	case len(parts) == 3 && parts[1] == "privacy" && parts[2] == "ledger":
		s.handleCoordinatorPrivacyLedger(w, r, runID)
	case len(parts) == 3 && parts[1] == "privacy" && parts[2] == "projection":
		s.handleCoordinatorPrivacyProjection(w, r, runID)
	case len(parts) == 2 && r.Method == http.MethodPost:
		s.handleCoordinatorLifecycleAction(w, r, runID, parts[1])
	default:
		writeError(w, http.StatusNotFound, "route not found")
	}
}

// handleCoordinatorPersonalization serves the raw per-client
// personalization records the coordinator has received for runID (Ditto/
// Per-FedAvg workers submit these; FedAvg/FedProx/SCAFFOLD/FedSAM never
// do, so an empty list is a normal response, not an error).
func (s *Server) handleCoordinatorPersonalization(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	records, err := s.services.Coordinator.PersonalizationRecords(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"run_id": runID, "records": records})
}

func (s *Server) handleCoordinatorClientPersonalization(w http.ResponseWriter, r *http.Request, runID, clientID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	record, err := s.services.Coordinator.ClientPersonalization(r.Context(), runID, clientID)
	if err != nil {
		if errors.Is(err, application.ErrClientNotFound) {
			writeError(w, http.StatusNotFound, err.Error())
			return
		}
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, record)
}

// handleCoordinatorFairness serves the computed fairness/personalization
// statistics for runID (see application/fairness.go and
// docs/fairness-metrics.md).
func (s *Server) handleCoordinatorFairness(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	fairness, err := s.services.Coordinator.Fairness(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, fairness)
}

// handleCoordinatorAlgorithmSummary serves a per-run projection combining
// the run's algorithm with its fairness statistics, for the algorithm-
// comparison dashboard view (docs/algorithm-expansion-architecture.md).
func (s *Server) handleCoordinatorAlgorithmSummary(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	summary, err := s.services.Coordinator.AlgorithmSummary(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, summary)
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

// handleCoordinatorPrivacyMetrics serves a point-in-time summary across
// all three privacy mechanisms (see coordinator.PrivacyMetricsSnapshot's
// doc comment on why SampleEpsilon is a worst-case reduction, not a
// combination) — GET /api/v1/coordinator/runs/{runId}/privacy/metrics.
// Safe to call for a non-private run (reports Has*=false, not an
// error) — Critical Privacy Rule's flip side: absence of privacy must
// be just as visible as its presence.
func (s *Server) handleCoordinatorPrivacyMetrics(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	metrics, err := s.services.Coordinator.GetPrivacyMetrics(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	// Snapshot into the Prometheus gauges (see MetricsRecorder.RecordPrivacyEpsilon's
	// doc comment) — each mechanism recorded independently, never combined.
	if s.services.Metrics != nil {
		if metrics.HasSampleLevel {
			s.services.Metrics.RecordPrivacyEpsilon(runID, "sample_level", metrics.SampleEpsilon)
		}
		if metrics.HasUserLevel {
			s.services.Metrics.RecordPrivacyEpsilon(runID, "user_level", metrics.UserEpsilon)
		}
		if metrics.HasClipping {
			s.services.Metrics.RecordPrivacyEpsilon(runID, "clipping", metrics.ClippingEpsilon)
		}
	}
	writeJSON(w, http.StatusOK, metrics)
}

// handleCoordinatorPrivacyLedger serves the full accounting history for
// all three mechanisms (each independently, never merged — see
// coordinator.PrivacyLedger's doc comment) —
// GET /api/v1/coordinator/runs/{runId}/privacy/ledger[?page_token=&page_size=].
func (s *Server) handleCoordinatorPrivacyLedger(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	pageToken := r.URL.Query().Get("page_token")
	pageSize := uint32(0)
	if raw := r.URL.Query().Get("page_size"); raw != "" {
		parsed, parseErr := strconv.ParseUint(raw, 10, 32)
		if parseErr != nil {
			writeError(w, http.StatusBadRequest, "page_size must be a non-negative integer")
			return
		}
		pageSize = uint32(parsed)
	}
	ledger, err := s.services.Coordinator.GetPrivacyLedger(r.Context(), runID, pageToken, pageSize)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, ledger)
}

// handleCoordinatorPrivacyProjection serves a one-step-ahead preview per
// mechanism (see coordinator.PrivacyProjection's doc comment on the
// nil-means-unbounded budget_remaining convention) —
// GET /api/v1/coordinator/runs/{runId}/privacy/projection.
func (s *Server) handleCoordinatorPrivacyProjection(w http.ResponseWriter, r *http.Request, runID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	projection, err := s.services.Coordinator.GetPrivacyProjection(r.Context(), runID)
	if err != nil {
		writeCoordinatorError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, projection)
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
				// Privacy Engineering phase: count budget events as they're
				// relayed (see MetricsRecorder.RecordPrivacyBudgetEvent's
				// doc comment). event.Metadata["mechanism"] is populated by
				// the C++ coordinator's finalize_round — see
				// coordinator_service.cpp's StreamRunEvents metadata relay.
				if s.services.Metrics != nil &&
					(event.Type == "PRIVACY_BUDGET_WARNING" || event.Type == "PRIVACY_BUDGET_EXCEEDED") {
					mechanism := event.Metadata["mechanism"]
					if mechanism == "" {
						mechanism = "unknown"
					}
					s.services.Metrics.RecordPrivacyBudgetEvent(mechanism, event.Type)
				}
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
