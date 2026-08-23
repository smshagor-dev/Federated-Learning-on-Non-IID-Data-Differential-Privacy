package httpapi

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/research"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/security"
)

func researchErrorStatus(err error) int {
	switch {
	case errors.Is(err, application.ErrResearchNotConfigured):
		return http.StatusServiceUnavailable
	case errors.Is(err, application.ErrResearchWriterNotConfigured), errors.Is(err, research.ErrWriterNotConfigured), errors.Is(err, research.ErrWriterUnavailable):
		return http.StatusServiceUnavailable
	case errors.Is(err, research.ErrNotFound), errors.Is(err, application.ErrNotFound):
		return http.StatusNotFound
	case errors.Is(err, research.ErrInvalidIdentifier):
		return http.StatusBadRequest
	case errors.Is(err, research.ErrCorrupted):
		return http.StatusConflict
	case errors.Is(err, research.ErrCommandConflict):
		return http.StatusConflict
	case errors.Is(err, research.ErrCommandRejected):
		return http.StatusBadRequest
	default:
		return http.StatusInternalServerError
	}
}

type viewerExperimentView struct {
	ExperimentID              string                             `json:"experiment_id"`
	DisplayName               string                             `json:"display_name"`
	ResearchQuestion          string                             `json:"research_question"`
	DatasetID                 string                             `json:"dataset_id"`
	ModelID                   string                             `json:"model_id"`
	AlgorithmID               string                             `json:"algorithm_id"`
	PrivacyMode               research.PrivacyMode               `json:"privacy_mode"`
	SecureAggregationEnabled  bool                               `json:"secure_aggregation_enabled"`
	SecureAggregationProvider research.SecureAggregationProvider `json:"secure_aggregation_provider"`
	AdaptiveClippingEnabled   bool                               `json:"adaptive_clipping_enabled"`
	DeclaredSeedCount         int                                `json:"declared_seed_count"`
	CurrentState              research.ExperimentState           `json:"current_state"`
	SuccessfulRunCount        int                                `json:"successful_run_count"`
	FailedRunCount            int                                `json:"failed_run_count"`
	CanceledRunCount          int                                `json:"canceled_run_count"`
	BlockedRunCount           int                                `json:"blocked_run_count"`
	CreatedAt                 string                             `json:"created_at"`
	UpdatedAt                 string                             `json:"updated_at"`
}

type researcherExperimentView struct {
	viewerExperimentView
	SpecificationHash     string `json:"specification_hash"`
	DatasetVersion        string `json:"dataset_version"`
	DatasetChecksum       string `json:"dataset_checksum"`
	PartitionManifestHash string `json:"partition_manifest_hash"`
}

type adminExperimentView struct {
	researcherExperimentView
	CreatedActor            string `json:"created_actor"`
	RecordVersion           int    `json:"record_version"`
	ArtifactManifestHash    string `json:"artifact_manifest_hash"`
	EnvironmentManifestHash string `json:"environment_manifest_hash"`
	Degraded                bool   `json:"degraded"`
	DegradedReason          string `json:"degraded_reason,omitempty"`
}

func projectExperimentView(role auth.Role, item research.ExperimentRegistryRecord) any {
	base := viewerExperimentView{
		ExperimentID:              item.ExperimentID,
		DisplayName:               item.DisplayName,
		ResearchQuestion:          item.ResearchQuestion,
		DatasetID:                 item.DatasetID,
		ModelID:                   item.ModelID,
		AlgorithmID:               item.AlgorithmID,
		PrivacyMode:               item.PrivacyMode,
		SecureAggregationEnabled:  item.SecureAggregationEnabled,
		SecureAggregationProvider: item.SecureAggregationProvider,
		AdaptiveClippingEnabled:   item.AdaptiveClippingEnabled,
		DeclaredSeedCount:         item.DeclaredSeedCount,
		CurrentState:              item.CurrentState,
		SuccessfulRunCount:        item.SuccessfulRunCount,
		FailedRunCount:            item.FailedRunCount,
		CanceledRunCount:          item.CanceledRunCount,
		BlockedRunCount:           item.BlockedRunCount,
		CreatedAt:                 item.CreatedAt,
		UpdatedAt:                 item.UpdatedAt,
	}
	if role == auth.RoleViewer {
		return base
	}
	researcherView := researcherExperimentView{
		viewerExperimentView:  base,
		SpecificationHash:     item.SpecificationHash,
		DatasetVersion:        item.DatasetVersion,
		DatasetChecksum:       item.DatasetChecksum,
		PartitionManifestHash: item.PartitionManifestHash,
	}
	if role != auth.RoleAdmin {
		return researcherView
	}
	return adminExperimentView{
		researcherExperimentView: researcherView,
		CreatedActor:             item.CreatedActor,
		RecordVersion:            item.RecordVersion,
		ArtifactManifestHash:     item.ArtifactManifestHash,
		EnvironmentManifestHash:  item.EnvironmentManifestHash,
		Degraded:                 item.Degraded,
		DegradedReason:           item.DegradedReason,
	}
}

func (s *Server) handleResearchExperiments(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		session, ok := s.requirePermission(w, r, security.PermResearchExperimentsList)
		if !ok {
			return
		}
		items, err := s.services.Research.ListExperiments(r.Context())
		if err != nil {
			writeError(w, researchErrorStatus(err), err.Error())
			return
		}
		views := make([]any, 0, len(items))
		for _, item := range items {
			views = append(views, projectExperimentView(session.User.Role, item))
		}
		writeJSON(w, http.StatusOK, map[string]any{"experiments": views})
	case http.MethodPost:
		s.handleResearchExperimentCreate(w, r)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s *Server) handleResearchRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/research/experiments/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	experimentID := parts[0]
	switch {
	case len(parts) == 1 && parts[0] == "stream" && r.Method == http.MethodGet:
		s.handleResearchExperimentsStream(w, r)
	case len(parts) == 1 && parts[0] == "validate" && r.Method == http.MethodPost:
		s.handleResearchValidate(w, r)
	case len(parts) == 1 && r.Method == http.MethodGet:
		s.handleResearchExperimentDetail(w, r, experimentID)
	case len(parts) == 2 && parts[1] == "start" && r.Method == http.MethodPost:
		s.handleResearchStart(w, r, experimentID)
	case len(parts) == 2 && parts[1] == "cancel" && r.Method == http.MethodPost:
		s.handleResearchCancel(w, r, experimentID)
	case len(parts) == 2 && parts[1] == "stream" && r.Method == http.MethodGet:
		s.handleResearchExperimentDetailStream(w, r, experimentID)
	case len(parts) == 2 && parts[1] == "runs" && r.Method == http.MethodGet:
		s.handleResearchRuns(w, r, experimentID)
	case len(parts) == 3 && parts[1] == "runs" && r.Method == http.MethodGet:
		s.handleResearchRunDetail(w, r, experimentID, parts[2])
	case len(parts) == 2 && parts[1] == "metrics" && r.Method == http.MethodGet:
		s.handleResearchMetrics(w, r, experimentID)
	case len(parts) == 2 && parts[1] == "events" && r.Method == http.MethodGet:
		s.handleResearchEvents(w, r, experimentID)
	case len(parts) == 2 && parts[1] == "artifacts" && r.Method == http.MethodGet:
		s.handleResearchArtifacts(w, r, experimentID)
	default:
		writeError(w, http.StatusNotFound, "route not found")
	}
}

func (s *Server) handleResearchExperimentsStream(w http.ResponseWriter, r *http.Request) {
	session, ok := s.requirePermission(w, r, security.PermResearchExperimentsList)
	if !ok {
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}

	writeSSEHeaders(w)
	flusher.Flush()

	ctx := r.Context()
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	streamID := 0
	sendSnapshot := func() bool {
		items, err := s.services.Research.ListExperiments(ctx)
		if err != nil {
			writeSSEErrorFrame(w, flusher, "research-error", err.Error())
			return false
		}
		views := make([]any, 0, len(items))
		for _, item := range items {
			views = append(views, projectExperimentView(session.User.Role, item))
		}
		streamID++
		return writeSSEJSONFrame(w, flusher, fmt.Sprintf("research-experiments-%d", streamID), "snapshot", map[string]any{
			"experiments":  views,
			"generated_at": time.Now().UTC().Format(time.RFC3339),
		})
	}

	if !sendSnapshot() {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !sendSnapshot() {
				return
			}
		}
	}
}

type researchValidateRequest struct {
	Specification           research.ExperimentSpecification `json:"specification"`
	ClientSpecificationHash string                           `json:"client_specification_hash"`
	CorrelationID           string                           `json:"correlation_id"`
}

type researchCreateRequest struct {
	Specification           research.ExperimentSpecification `json:"specification"`
	ClientSpecificationHash string                           `json:"client_specification_hash"`
	IdempotencyKey          string                           `json:"idempotency_key"`
	CorrelationID           string                           `json:"correlation_id"`
}

type researchStartRequest struct {
	ExecutionMode             string `json:"execution_mode"`
	ExpectedExperimentVersion *int   `json:"expected_experiment_version"`
	IdempotencyKey            string `json:"idempotency_key"`
	CorrelationID             string `json:"correlation_id"`
}

type researchCancelRequest struct {
	Reason                    string `json:"reason"`
	ExpectedExperimentVersion *int   `json:"expected_experiment_version"`
	IdempotencyKey            string `json:"idempotency_key"`
	CorrelationID             string `json:"correlation_id"`
}

func commandActorFromSession(session application.AuthSession) research.CommandActor {
	return research.CommandActor{
		ActorID:    session.User.ID,
		ActorEmail: session.User.Email,
		ActorRole:  string(session.User.Role),
	}
}

func (s *Server) handleResearchValidate(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requirePermission(w, r, security.PermResearchExperimentsValidate); !ok {
		return
	}
	var req researchValidateRequest
	if !decodeStrictJSON(w, r, &req, 256*1024) {
		return
	}
	result, err := s.services.Research.ValidateSpecification(
		r.Context(),
		commandActorFromSession(sessionFromContext(r.Context())),
		sessionFromContext(r.Context()).Capabilities,
		req.Specification,
		req.ClientSpecificationHash,
		req.CorrelationID,
	)
	if err != nil && !errors.Is(err, research.ErrCommandRejected) {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"valid":              result.Status == research.CommandStatusSucceeded,
		"status":             result.Status,
		"specification_hash": result.SpecificationHash,
		"reason_code":        result.ReasonCode,
		"validation_errors":  result.ValidationErrors,
	})
}

func (s *Server) handleResearchExperimentCreate(w http.ResponseWriter, r *http.Request) {
	session, ok := s.requirePermission(w, r, security.PermResearchExperimentsCreate)
	if !ok {
		return
	}
	var req researchCreateRequest
	if !decodeStrictJSON(w, r, &req, 256*1024) {
		return
	}
	idempotencyKey := requestIdempotencyKey(r, req.IdempotencyKey)
	if strings.TrimSpace(idempotencyKey) == "" {
		writeError(w, http.StatusBadRequest, "an Idempotency-Key header (or idempotency_key body field) is required")
		return
	}
	result, err := s.services.Research.CreateExperiment(
		r.Context(),
		commandActorFromSession(session),
		session.Capabilities,
		req.Specification,
		req.ClientSpecificationHash,
		idempotencyKey,
		req.CorrelationID,
	)
	if err != nil && !errors.Is(err, research.ErrCommandConflict) && !errors.Is(err, research.ErrCommandRejected) {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	if err != nil {
		writeResearchCommandError(w, err, result)
		return
	}
	item, getErr := s.services.Research.GetExperiment(r.Context(), result.ExperimentID)
	if getErr != nil {
		writeError(w, researchErrorStatus(getErr), getErr.Error())
		return
	}
	_ = s.services.Audit.Record(r.Context(), actorFromSession(session), "research.experiment.create", "research_experiment", item.ExperimentID, "success", map[string]any{"idempotent_replay": result.IdempotentReplay})
	writeJSON(w, http.StatusCreated, map[string]any{
		"experiment":         projectExperimentView(session.User.Role, item),
		"idempotent_replay":  result.IdempotentReplay,
		"command_status":     result.Status,
		"specification_hash": result.SpecificationHash,
	})
}

func (s *Server) handleResearchStart(w http.ResponseWriter, r *http.Request, experimentID string) {
	session, ok := s.requirePermission(w, r, security.PermResearchExperimentsStart)
	if !ok {
		return
	}
	var req researchStartRequest
	if !decodeStrictJSON(w, r, &req, 64*1024) {
		return
	}
	idempotencyKey := requestIdempotencyKey(r, req.IdempotencyKey)
	if strings.TrimSpace(idempotencyKey) == "" {
		writeError(w, http.StatusBadRequest, "an Idempotency-Key header (or idempotency_key body field) is required")
		return
	}
	if req.ExecutionMode != "SYNTHETIC_TEST_EXECUTION" {
		writeError(w, http.StatusBadRequest, "execution_mode must be SYNTHETIC_TEST_EXECUTION")
		return
	}
	result, err := s.services.Research.StartSyntheticExperiment(
		r.Context(),
		commandActorFromSession(session),
		session.Capabilities,
		experimentID,
		idempotencyKey,
		req.CorrelationID,
		req.ExpectedExperimentVersion,
	)
	if err != nil && !errors.Is(err, research.ErrCommandConflict) && !errors.Is(err, research.ErrCommandRejected) {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	if err != nil {
		writeResearchCommandError(w, err, result)
		return
	}
	item, getErr := s.services.Research.GetExperiment(r.Context(), result.ExperimentID)
	if getErr != nil {
		writeError(w, researchErrorStatus(getErr), getErr.Error())
		return
	}
	_ = s.services.Audit.Record(r.Context(), actorFromSession(session), "research.experiment.start", "research_experiment", item.ExperimentID, "success", map[string]any{"idempotent_replay": result.IdempotentReplay})
	writeJSON(w, http.StatusOK, map[string]any{
		"experiment":        projectExperimentView(session.User.Role, item),
		"idempotent_replay": result.IdempotentReplay,
		"command_status":    result.Status,
		"current_state":     result.CurrentState,
		"previous_state":    result.PreviousState,
		"execution_mode":    "SYNTHETIC_TEST_EXECUTION",
	})
}

func (s *Server) handleResearchCancel(w http.ResponseWriter, r *http.Request, experimentID string) {
	session, ok := s.requirePermission(w, r, security.PermResearchExperimentsCancel)
	if !ok {
		return
	}
	var req researchCancelRequest
	if !decodeStrictJSON(w, r, &req, 64*1024) {
		return
	}
	idempotencyKey := requestIdempotencyKey(r, req.IdempotencyKey)
	if strings.TrimSpace(idempotencyKey) == "" {
		writeError(w, http.StatusBadRequest, "an Idempotency-Key header (or idempotency_key body field) is required")
		return
	}
	result, err := s.services.Research.CancelExperiment(
		r.Context(),
		commandActorFromSession(session),
		session.Capabilities,
		experimentID,
		req.Reason,
		idempotencyKey,
		req.CorrelationID,
		req.ExpectedExperimentVersion,
	)
	if err != nil && !errors.Is(err, research.ErrCommandConflict) && !errors.Is(err, research.ErrCommandRejected) {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	if err != nil {
		writeResearchCommandError(w, err, result)
		return
	}
	item, getErr := s.services.Research.GetExperiment(r.Context(), result.ExperimentID)
	if getErr != nil {
		writeError(w, researchErrorStatus(getErr), getErr.Error())
		return
	}
	_ = s.services.Audit.Record(r.Context(), actorFromSession(session), "research.experiment.cancel", "research_experiment", item.ExperimentID, "success", map[string]any{"idempotent_replay": result.IdempotentReplay})
	writeJSON(w, http.StatusOK, map[string]any{
		"experiment":        projectExperimentView(session.User.Role, item),
		"idempotent_replay": result.IdempotentReplay,
		"command_status":    result.Status,
		"current_state":     result.CurrentState,
		"previous_state":    result.PreviousState,
	})
}

func writeResearchCommandError(w http.ResponseWriter, err error, result research.CommandResult) {
	response := map[string]any{
		"error":          err.Error(),
		"command_status": result.Status,
		"reason_code":    result.ReasonCode,
	}
	if len(result.ValidationErrors) > 0 {
		response["validation_errors"] = result.ValidationErrors
	}
	if result.ExperimentID != "" {
		response["experiment_id"] = result.ExperimentID
	}
	if result.ExperimentRecordVersion != nil {
		response["experiment_record_version"] = result.ExperimentRecordVersion
	}
	if result.PreviousState != "" {
		response["previous_state"] = result.PreviousState
	}
	if result.CurrentState != "" {
		response["current_state"] = result.CurrentState
	}
	writeJSON(w, researchErrorStatus(err), response)
}

func (s *Server) handleResearchExperimentDetail(w http.ResponseWriter, r *http.Request, experimentID string) {
	session, ok := s.requirePermission(w, r, security.PermResearchExperimentsRead)
	if !ok {
		return
	}
	item, err := s.services.Research.GetExperiment(r.Context(), experimentID)
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, projectExperimentView(session.User.Role, item))
}

func (s *Server) handleResearchExperimentDetailStream(w http.ResponseWriter, r *http.Request, experimentID string) {
	session, ok := s.requirePermission(w, r, security.PermResearchExperimentsRead)
	if !ok {
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermResearchRunsRead); !ok {
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermResearchMetricsRead); !ok {
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermResearchEventsRead); !ok {
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermResearchArtifactsRead); !ok {
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}

	writeSSEHeaders(w)
	flusher.Flush()

	ctx := r.Context()
	ticker := time.NewTicker(2 * time.Second)
	defer ticker.Stop()

	streamID := 0
	sendSnapshot := func() bool {
		experiment, err := s.services.Research.GetExperiment(ctx, experimentID)
		if err != nil {
			writeSSEErrorFrame(w, flusher, "research-error", err.Error())
			return false
		}
		runs, err := s.services.Research.ListRuns(ctx, experimentID)
		if err != nil {
			writeSSEErrorFrame(w, flusher, "research-error", err.Error())
			return false
		}
		metrics, recoveredMetrics, err := s.services.Research.ListMetrics(ctx, experimentID)
		if err != nil {
			writeSSEErrorFrame(w, flusher, "research-error", err.Error())
			return false
		}
		events, recoveredEvents, err := s.services.Research.ListEvents(ctx, experimentID)
		if err != nil {
			writeSSEErrorFrame(w, flusher, "research-error", err.Error())
			return false
		}
		artifacts, err := s.services.Research.ListArtifacts(ctx, experimentID)
		if err != nil {
			writeSSEErrorFrame(w, flusher, "research-error", err.Error())
			return false
		}
		streamID++
		return writeSSEJSONFrame(w, flusher, fmt.Sprintf("research-detail-%s-%d", experimentID, streamID), "snapshot", map[string]any{
			"experiment":             projectExperimentView(session.User.Role, experiment),
			"runs":                   runs,
			"metrics":                metrics,
			"events":                 events,
			"artifacts":              artifacts,
			"recovered_metric_count": recoveredMetrics,
			"recovered_event_count":  recoveredEvents,
			"generated_at":           time.Now().UTC().Format(time.RFC3339),
		})
	}

	if !sendSnapshot() {
		return
	}
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if !sendSnapshot() {
				return
			}
		}
	}
}

func (s *Server) handleResearchRuns(w http.ResponseWriter, r *http.Request, experimentID string) {
	if _, ok := s.requirePermission(w, r, security.PermResearchRunsRead); !ok {
		return
	}
	items, err := s.services.Research.ListRuns(r.Context(), experimentID)
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"runs": items})
}

func (s *Server) handleResearchRunDetail(w http.ResponseWriter, r *http.Request, experimentID, runID string) {
	if _, ok := s.requirePermission(w, r, security.PermResearchRunsRead); !ok {
		return
	}
	item, err := s.services.Research.GetRun(r.Context(), experimentID, runID)
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleResearchMetrics(w http.ResponseWriter, r *http.Request, experimentID string) {
	if _, ok := s.requirePermission(w, r, security.PermResearchMetricsRead); !ok {
		return
	}
	items, recovered, err := s.services.Research.ListMetrics(r.Context(), experimentID)
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"metrics": items, "recovered_line_count": recovered})
}

func (s *Server) handleResearchEvents(w http.ResponseWriter, r *http.Request, experimentID string) {
	if _, ok := s.requirePermission(w, r, security.PermResearchEventsRead); !ok {
		return
	}
	items, recovered, err := s.services.Research.ListEvents(r.Context(), experimentID)
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"events": items, "recovered_line_count": recovered})
}

func (s *Server) handleResearchArtifacts(w http.ResponseWriter, r *http.Request, experimentID string) {
	if _, ok := s.requirePermission(w, r, security.PermResearchArtifactsRead); !ok {
		return
	}
	item, err := s.services.Research.ListArtifacts(r.Context(), experimentID)
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) handleResearchRuntimeHealth(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermResearchRuntimeHealthRead); !ok {
		return
	}
	readerHealth, err := s.services.Research.RuntimeHealth(r.Context())
	if err != nil {
		writeError(w, researchErrorStatus(err), err.Error())
		return
	}
	session := sessionFromContext(r.Context())
	writerHealth, writerErr := s.services.Research.WriterHealth(r.Context(), commandActorFromSession(session), session.Capabilities, r.Header.Get("X-Trace-Id"))
	response := map[string]any{
		"reader":           readerHealth,
		"writer":           writerHealth,
		"reads_available":  true,
		"writes_available": writerErr == nil,
		"overall_status":   "HEALTHY",
		"degraded_reason":  "",
	}
	if writerErr != nil {
		response["overall_status"] = "DEGRADED"
		response["degraded_reason"] = "python_authoritative_writer_unavailable"
	}
	if readerHealth.DegradedReason != "" || writerHealth.Degraded {
		response["overall_status"] = "DEGRADED"
	}
	writeJSON(w, http.StatusOK, response)
}

func writeSSEHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
}

func writeSSEErrorFrame(w http.ResponseWriter, flusher http.Flusher, eventType, message string) {
	fmt.Fprintf(w, "event: %s\ndata: %s\n\n", eventType, jsonString(message))
	flusher.Flush()
}

func writeSSEJSONFrame(w http.ResponseWriter, flusher http.Flusher, id, eventType string, payload any) bool {
	body, err := json.Marshal(payload)
	if err != nil {
		writeSSEErrorFrame(w, flusher, "research-error", err.Error())
		return false
	}
	fmt.Fprintf(w, "id: %s\nevent: %s\ndata: %s\n\n", id, eventType, body)
	flusher.Flush()
	return true
}
