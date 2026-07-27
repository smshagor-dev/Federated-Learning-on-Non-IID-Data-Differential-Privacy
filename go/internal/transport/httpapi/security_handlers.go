package httpapi

// Security Operations and Administration slice (docs/security-api.md,
// docs/security-permission-model.md): the /api/v1/security/* HTTP
// surface. Every route is registered with the same broad
// withAuth(Viewer, Researcher, Admin, Service) set — authentication
// only, matching several pre-existing routes in server.go — and the
// REAL authorization decision happens inside each handler via
// security.Allows(role, permission), per the specification's "use
// permission constants not scattered role checks" requirement. This is
// deliberately a different pattern from the pre-existing (non-security)
// routes' inline role-list withAuth checks; those are untouched.

import (
	"errors"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/application"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/security"
)

// idempotencyCache backs every /api/v1/security/... mutation route's
// Idempotency-Key support (Work Package K). A single mutex guards both
// the lookup and the wrapped call: correctness (no two concurrent
// requests for the same key both execute the underlying mutation) is
// prioritized over throughput here, which is an explicit, documented
// trade-off appropriate for a research control plane, not a
// high-concurrency production API — see docs/security-api.md. Entries
// are in-memory only (lost on process restart), unlike the coordinator's
// own IdempotencyStore backing RotateCoordinatorSigningKey/
// RevokeCoordinatorSigningKey, which is file-persisted — see
// docs/known-limitations.md.
type idempotencyCache struct {
	mu      sync.Mutex
	entries map[string]idempotentResult
}

type idempotentResult struct {
	status  int
	payload any
}

func newIdempotencyCache() *idempotencyCache {
	return &idempotencyCache{entries: make(map[string]idempotentResult)}
}

func (c *idempotencyCache) run(key string, fn func() (int, any)) (int, any) {
	if key == "" {
		return fn()
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if cached, ok := c.entries[key]; ok {
		return cached.status, cached.payload
	}
	status, payload := fn()
	c.entries[key] = idempotentResult{status: status, payload: payload}
	return status, payload
}

// requestIdempotencyKey prefers the standard Idempotency-Key header,
// falling back to a JSON body field of the same name for callers that
// can't easily set custom headers (e.g. simple test scripts).
func requestIdempotencyKey(r *http.Request, bodyKey string) string {
	if header := strings.TrimSpace(r.Header.Get("Idempotency-Key")); header != "" {
		return header
	}
	return bodyKey
}

func actorFromSession(session application.AuthSession) application.Actor {
	return application.Actor{ID: session.User.ID, Email: session.User.Email, Role: string(session.User.Role)}
}

// requirePermission authorizes the current session against perm using
// the security package's permission constants (not an inline role
// list). Writes a 403 and returns false if denied.
func (s *Server) requirePermission(w http.ResponseWriter, r *http.Request, perm security.Permission) (application.AuthSession, bool) {
	session := sessionFromContext(r.Context())
	if !security.Allows(session.User.Role, perm) {
		if s.services != nil && s.services.Coordinator != nil {
			s.services.Coordinator.EmitPermissionDenied(actorFromSession(session), string(perm))
		}
		writeError(w, http.StatusForbidden, "forbidden: missing permission "+string(perm))
		return session, false
	}
	return session, true
}

func writeSecurityError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, application.ErrCoordinatorNotConfigured):
		writeError(w, http.StatusServiceUnavailable, err.Error())
	case errors.Is(err, coordinator.ErrUnavailable):
		writeError(w, http.StatusServiceUnavailable, err.Error())
	case errors.Is(err, coordinator.ErrPermissionDenied):
		writeError(w, http.StatusForbidden, err.Error())
	case errors.Is(err, coordinator.ErrNotFound):
		writeError(w, http.StatusNotFound, err.Error())
	case errors.Is(err, coordinator.ErrFailedPrecondition):
		writeError(w, http.StatusConflict, err.Error())
	default:
		writeError(w, http.StatusInternalServerError, err.Error())
	}
}

func (s *Server) handleSecurityTransport(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermTransportRead); !ok {
		return
	}
	status, err := s.services.Coordinator.GetTransportSecurityStatus(r.Context(), r.Header.Get("X-Trace-Id"))
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, status)
}

func (s *Server) handleSecurityTrustModel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermTrustRead); !ok {
		return
	}
	model, err := s.services.Coordinator.GetSecurityTrustModel(r.Context(), r.Header.Get("X-Trace-Id"))
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, model)
}

// redactedWorkerIdentity is what a VIEWER-role caller receives instead
// of the full coordinator.WorkerIdentitySummary — no certificate
// identity/fingerprint, no signing_key_id, no timestamps, no revocation
// reason. Per the specification's VIEWER restrictions: "Certificate
// fingerprints by default" and "Detailed worker identity history" are
// both explicitly not allowed for VIEWER.
type redactedWorkerIdentity struct {
	WorkerID           string `json:"worker_id"`
	RegistrationStatus string `json:"registration_status"`
}

func viewForRole(role auth.Role, identity coordinator.WorkerIdentitySummary) any {
	if role == auth.RoleViewer {
		return redactedWorkerIdentity{WorkerID: identity.WorkerID, RegistrationStatus: identity.RegistrationStatus}
	}
	return identity
}

func (s *Server) handleSecurityWorkers(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermWorkersRead)
	if !ok {
		return
	}
	identities, err := s.services.Coordinator.ListWorkerIdentities(r.Context(), r.Header.Get("X-Trace-Id"))
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	views := make([]any, 0, len(identities))
	for _, identity := range identities {
		views = append(views, viewForRole(session.User.Role, identity))
	}
	writeJSON(w, http.StatusOK, map[string]any{"workers": views})
}

func (s *Server) handleSecurityWorkerRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/security/workers/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	workerID := parts[0]

	switch {
	case len(parts) == 1 && r.Method == http.MethodGet:
		s.handleSecurityWorkerDetail(w, r, workerID)
	case len(parts) == 2 && r.Method == http.MethodPost && (parts[1] == "suspend" || parts[1] == "activate" || parts[1] == "revoke"):
		s.handleSecurityWorkerLifecycleAction(w, r, workerID, parts[1])
	case len(parts) == 2 && r.Method == http.MethodGet && parts[1] == "signing-keys":
		s.handleSecurityWorkerSigningKeys(w, r, workerID)
	case len(parts) == 4 && r.Method == http.MethodPost && parts[1] == "signing-keys" && parts[3] == "revoke":
		s.handleSecurityWorkerSigningKeyRevoke(w, r, workerID, parts[2])
	default:
		writeError(w, http.StatusNotFound, "route not found")
	}
}

func (s *Server) handleSecurityWorkerDetail(w http.ResponseWriter, r *http.Request, workerID string) {
	session, ok := s.requirePermission(w, r, security.PermWorkersRead)
	if !ok {
		return
	}
	identity, err := s.services.Coordinator.GetWorkerIdentity(r.Context(), workerID, r.Header.Get("X-Trace-Id"))
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, viewForRole(session.User.Role, identity))
}

type securityMutationBody struct {
	Reason         string `json:"reason"`
	RequestID      string `json:"request_id"`
	TraceID        string `json:"trace_id"`
	IdempotencyKey string `json:"idempotency_key"`
}

func (s *Server) handleSecurityWorkerLifecycleAction(w http.ResponseWriter, r *http.Request, workerID, action string) {
	var perm security.Permission
	switch action {
	case "suspend":
		perm = security.PermWorkersSuspend
	case "activate":
		perm = security.PermWorkersActivate
	case "revoke":
		perm = security.PermWorkersRevoke
	}
	session, ok := s.requirePermission(w, r, perm)
	if !ok {
		return
	}
	var body securityMutationBody
	if err := decodeOptionalJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	request := coordinator.WorkerLifecycleRequest{WorkerID: workerID, Reason: body.Reason, RequestID: body.RequestID, TraceID: body.TraceID}
	idempotencyKey := requestIdempotencyKey(r, body.IdempotencyKey)
	status, payload := s.securityIdempotency.run("worker:"+action+":"+workerID+":"+idempotencyKey, func() (int, any) {
		var (
			result coordinator.WorkerLifecycleResult
			err    error
		)
		switch action {
		case "suspend":
			result, err = s.services.Coordinator.SuspendWorker(r.Context(), actorFromSession(session), request)
		case "activate":
			result, err = s.services.Coordinator.ActivateWorker(r.Context(), actorFromSession(session), request)
		case "revoke":
			result, err = s.services.Coordinator.RevokeWorker(r.Context(), actorFromSession(session), request)
		}
		if err != nil {
			return securityErrorStatus(err), map[string]string{"error": err.Error()}
		}
		return http.StatusOK, result
	})
	writeJSON(w, status, payload)
}

func (s *Server) handleSecurityWorkerSigningKeys(w http.ResponseWriter, r *http.Request, workerID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermWorkerKeysRead); !ok {
		return
	}
	keys, err := s.services.Coordinator.ListWorkerSigningKeys(r.Context(), workerID, r.Header.Get("X-Trace-Id"))
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"signing_keys": keys})
}

func (s *Server) handleSecurityWorkerSigningKeyRevoke(w http.ResponseWriter, r *http.Request, workerID, keyID string) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermWorkerKeysRevoke)
	if !ok {
		return
	}
	var body securityMutationBody
	if err := decodeOptionalJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	request := coordinator.RevokeWorkerSigningKeyRequest{
		WorkerID: workerID, SigningKeyID: keyID, Reason: body.Reason, RequestID: body.RequestID, TraceID: body.TraceID,
	}
	idempotencyKey := requestIdempotencyKey(r, body.IdempotencyKey)
	status, payload := s.securityIdempotency.run("worker-key-revoke:"+workerID+":"+keyID+":"+idempotencyKey, func() (int, any) {
		result, err := s.services.Coordinator.RevokeWorkerSigningKey(r.Context(), actorFromSession(session), request)
		if err != nil {
			return securityErrorStatus(err), map[string]string{"error": err.Error()}
		}
		return http.StatusOK, result
	})
	writeJSON(w, status, payload)
}

func (s *Server) handleSecurityCoordinatorSigningKeys(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermCoordinatorKeysRead); !ok {
		return
	}
	keys, err := s.services.Coordinator.ListCoordinatorSigningKeys(r.Context(), r.Header.Get("X-Trace-Id"))
	if err != nil {
		writeSecurityError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"signing_keys": keys})
}

func (s *Server) handleSecurityCoordinatorSigningKeyRoutes(w http.ResponseWriter, r *http.Request) {
	path := strings.TrimPrefix(r.URL.Path, "/api/v1/security/coordinator/signing-keys/")
	parts := strings.Split(path, "/")
	if len(parts) == 0 || parts[0] == "" {
		writeError(w, http.StatusNotFound, "route not found")
		return
	}
	switch {
	case len(parts) == 1 && parts[0] == "rotate" && r.Method == http.MethodPost:
		s.handleSecurityCoordinatorSigningKeyRotate(w, r)
	case len(parts) == 2 && parts[1] == "revoke" && r.Method == http.MethodPost:
		s.handleSecurityCoordinatorSigningKeyRevoke(w, r, parts[0])
	default:
		writeError(w, http.StatusNotFound, "route not found")
	}
}

func (s *Server) handleSecurityCoordinatorSigningKeyRotate(w http.ResponseWriter, r *http.Request) {
	session, ok := s.requirePermission(w, r, security.PermCoordinatorKeysRotate)
	if !ok {
		return
	}
	var body struct {
		securityMutationBody
		ExpectedCurrentSigningKeyID string  `json:"expected_current_signing_key_id"`
		NewKeyExpiresAtUnixS        float64 `json:"new_key_expires_at_unix_s"`
		RequestedGracePeriodSeconds float64 `json:"requested_grace_period_seconds"`
	}
	if err := decodeOptionalJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	idempotencyKey := requestIdempotencyKey(r, body.IdempotencyKey)
	if idempotencyKey == "" {
		// Unlike worker lifecycle mutations (naturally idempotent by
		// target state), a coordinator-key rotation mints a fresh
		// Ed25519 key every time it actually executes -- an
		// idempotency key is not optional here, it is the only thing
		// that makes a client-side retry safe. See
		// docs/coordinator-signing-key-rotation.md.
		writeError(w, http.StatusBadRequest, "an Idempotency-Key header (or idempotency_key body field) is required for coordinator signing-key rotation")
		return
	}
	request := coordinator.RotateCoordinatorSigningKeyRequest{
		Reason: body.Reason, RequestID: body.RequestID, TraceID: body.TraceID, IdempotencyKey: idempotencyKey,
		ExpectedCurrentSigningKeyID: body.ExpectedCurrentSigningKeyID,
		NewKeyExpiresAtUnixS:        body.NewKeyExpiresAtUnixS,
		RequestedGracePeriodSeconds: body.RequestedGracePeriodSeconds,
	}
	status, payload := s.securityIdempotency.run("coordinator-key-rotate:"+idempotencyKey, func() (int, any) {
		result, err := s.services.Coordinator.RotateCoordinatorSigningKey(r.Context(), actorFromSession(session), request)
		if err != nil {
			return securityErrorStatus(err), map[string]string{"error": err.Error()}
		}
		if !result.Accepted {
			return http.StatusConflict, result
		}
		return http.StatusOK, result
	})
	writeJSON(w, status, payload)
}

func (s *Server) handleSecurityCoordinatorSigningKeyRevoke(w http.ResponseWriter, r *http.Request, keyID string) {
	session, ok := s.requirePermission(w, r, security.PermCoordinatorKeysRevoke)
	if !ok {
		return
	}
	var body struct {
		securityMutationBody
		ExpectedStatus string `json:"expected_status"`
	}
	if err := decodeOptionalJSON(r, &body); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	request := coordinator.RevokeCoordinatorSigningKeyRequest{
		SigningKeyID: keyID, Reason: body.Reason, RequestID: body.RequestID, TraceID: body.TraceID,
		IdempotencyKey: requestIdempotencyKey(r, body.IdempotencyKey), ExpectedStatus: body.ExpectedStatus,
	}
	status, payload := s.securityIdempotency.run("coordinator-key-revoke:"+keyID+":"+request.IdempotencyKey, func() (int, any) {
		result, err := s.services.Coordinator.RevokeCoordinatorSigningKey(r.Context(), actorFromSession(session), request)
		if err != nil {
			return securityErrorStatus(err), map[string]string{"error": err.Error()}
		}
		return http.StatusOK, result
	})
	writeJSON(w, status, payload)
}

// handleSecurityEvents: real implementation -- Security Events, Metrics,
// and Durable Audit Journal slice. Merges this Go process's own
// locally-emitted events (permission denials, idempotency outcomes,
// mutation accepted/rejected, audit access -- things that only happen
// at the HTTP layer and never reach C++) with the coordinator's own
// durable journal via SecurityClient.ListSecurityEvents, sorted by
// event_id and role-redacted. Known limitation, real and disclosed (not
// just theoretical -- found live by this slice's Playwright browser
// suite): each source (go-api, coordinator, and coordinator-relayed
// python-worker events) assigns event_id from its own independent
// sequence starting at 1, so event_id is NOT globally unique across
// this merged response -- two unrelated events from different sources
// can legitimately share the same event_id. Combined with "merging two
// independently-paginated sources means the cursor returned here is not
// a perfectly stable distributed cursor across a page boundary that
// splits unevenly between sources" (the pre-existing disclosed
// limitation), this means an `after_event_id` cursor is only a
// best-effort dedup/pagination hint, not a globally unique or strictly
// monotonic identifier. The web client compensates for the rendering
// consequence of this (React key collisions) by keying on
// `source_service:event_id` rather than `event_id` alone -- see
// security-events-console.tsx. A future slice could make event_id
// globally unique at the source (e.g. prefixing by source_service
// before assignment) if a real consumer needs a stable cross-source
// cursor; not attempted here to avoid changing the on-disk journal
// format and every existing per-journal cursor consumer for a
// UI-rendering-only defect. See docs/security-events.md.
func (s *Server) handleSecurityEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermEventsRead)
	if !ok {
		return
	}
	detailed := security.Allows(session.User.Role, security.PermEventsReadDetailed)

	query := r.URL.Query()
	limit := 100
	if raw := query.Get("limit"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			limit = parsed
		}
	}
	afterEventID := query.Get("after_event_id")
	minSeverity := query.Get("min_severity")
	subjectType := query.Get("subject_type")
	eventType := query.Get("event_type")

	var merged []observability.SecurityEvent
	if s.securityEventJournal != nil {
		local := s.securityEventJournal.List(observability.SecurityEventListFilters{
			AfterEventID: afterEventID, Limit: limit, MinSeverity: minSeverity,
			SubjectType: subjectType, EventType: eventType,
		})
		merged = append(merged, local.Events...)
	}
	if s.services != nil && s.services.Coordinator != nil && s.services.Coordinator.Configured() {
		remote, err := s.services.Coordinator.ListSecurityEvents(r.Context(), coordinator.ListSecurityEventsRequest{
			TraceID: r.Header.Get("X-Trace-Id"), AfterEventID: afterEventID, Limit: uint32(limit),
			MinSeverity: minSeverity, SubjectType: subjectType, EventType: eventType,
		})
		if err == nil {
			merged = append(merged, remote.Events...)
		}
		// A coordinator error here is deliberately non-fatal: Go-local
		// events are still worth serving even when the coordinator is
		// unreachable (matches this handler's "best-effort merge" scope).
	}
	sort.Slice(merged, func(i, k int) bool { return merged[i].EventID < merged[k].EventID })
	if len(merged) > limit {
		merged = merged[:limit]
	}
	views := make([]any, 0, len(merged))
	for _, event := range merged {
		views = append(views, redactSecurityEvent(event, detailed))
	}
	writeJSON(w, http.StatusOK, map[string]any{"events": views})
}

func redactSecurityEvent(event observability.SecurityEvent, detailed bool) any {
	if detailed {
		return event
	}
	// Redacted view: drop safe_details/reason_code/request_id/trace_id --
	// still an identifier, not a secret, but potentially operator-written
	// free text not intended for broad visibility, same rationale as
	// redactAuditEvent below.
	return map[string]any{
		"event_id":        event.EventID,
		"event_type":      event.EventType,
		"severity":        event.Severity,
		"timestamp":       event.Timestamp,
		"source_service":  event.SourceService,
		"actor_type":      event.ActorType,
		"safe_actor_id":   event.SafeActorID,
		"subject_type":    event.SubjectType,
		"safe_subject_id": event.SafeSubjectID,
		"worker_id":       event.WorkerID,
		"outcome":         event.Outcome,
	}
}

// handleSecurityAudit: reads from the new, security-specific durable
// audit journal (real pagination + filtering -- requirement 10), not
// the general-purpose Go AuditRepository (which keeps being written to
// unchanged, for every other domain, and by every security mutation
// too, for backward compatibility -- see
// application.CoordinatorService.appendSecurityAudit's doc comment).
func (s *Server) handleSecurityAudit(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermAuditRead)
	if !ok {
		return
	}
	detailed := security.Allows(session.User.Role, security.PermAuditReadDetailed)

	if s.securityAuditJournal == nil {
		writeJSON(w, http.StatusOK, map[string]any{"records": []any{}})
		return
	}

	query := r.URL.Query()
	limit := 500
	if raw := query.Get("limit"); raw != "" {
		if parsed, err := strconv.Atoi(raw); err == nil && parsed > 0 {
			limit = parsed
		}
	}
	filters := observability.SecurityAuditListFilters{
		AfterRecordID: query.Get("cursor"),
		Limit:         limit,
		ActorID:       query.Get("actor"),
		Action:        query.Get("action"),
		ResourceType:  query.Get("resource_type"),
		Outcome:       query.Get("outcome"),
	}
	if raw := query.Get("since"); raw != "" {
		if parsed, err := strconv.ParseFloat(raw, 64); err == nil {
			filters.SinceUnixS = parsed
		}
	}
	if raw := query.Get("until"); raw != "" {
		if parsed, err := strconv.ParseFloat(raw, 64); err == nil {
			filters.UntilUnixS = parsed
		}
	}

	result := s.securityAuditJournal.List(filters)
	if detailed && len(result.Records) > 0 && s.services != nil && s.services.Coordinator != nil {
		s.services.Coordinator.EmitAuditAccessed(actorFromSession(session))
	}
	views := make([]any, 0, len(result.Records))
	for _, record := range result.Records {
		views = append(views, redactSecurityAuditRecord(record, detailed))
	}
	writeJSON(w, http.StatusOK, map[string]any{"records": views, "next_cursor": result.NextCursor})
}

func redactSecurityAuditRecord(record observability.SecurityAuditRecord, detailed bool) any {
	if detailed {
		return record
	}
	// Redacted view (RESEARCHER without read_detailed): drop the free-
	// form reason/safe_details, which may carry an operator-written
	// string not intended for broad visibility -- keep only the
	// structural fields, same rationale as the general AuditRepository's
	// pre-existing redactAuditEvent below.
	return map[string]any{
		"record_id":     record.RecordID,
		"timestamp":     record.Timestamp,
		"actor_role":    record.ActorRole,
		"action":        record.Action,
		"resource_type": record.ResourceType,
		"resource_id":   record.ResourceID,
		"outcome":       record.Outcome,
	}
}

func redactAuditEvent(event observability.AuditEvent, detailed bool) any {
	if detailed {
		return event
	}
	// Redacted view (RESEARCHER without read_detailed): drop the actor's
	// email and the free-form Details map, which may carry a reason
	// string an operator wrote themselves and did not necessarily intend
	// for broad visibility -- keep only the structural fields.
	return map[string]any{
		"id":            event.ID,
		"timestamp":     event.Timestamp,
		"actor_role":    event.ActorRole,
		"action":        event.Action,
		"resource_type": event.ResourceType,
		"resource_id":   event.ResourceID,
		"outcome":       event.Outcome,
	}
}

func securityErrorStatus(err error) int {
	switch {
	case errors.Is(err, application.ErrCoordinatorNotConfigured), errors.Is(err, coordinator.ErrUnavailable):
		return http.StatusServiceUnavailable
	case errors.Is(err, coordinator.ErrPermissionDenied):
		return http.StatusForbidden
	case errors.Is(err, coordinator.ErrNotFound):
		return http.StatusNotFound
	case errors.Is(err, coordinator.ErrFailedPrecondition):
		return http.StatusConflict
	default:
		return http.StatusInternalServerError
	}
}
