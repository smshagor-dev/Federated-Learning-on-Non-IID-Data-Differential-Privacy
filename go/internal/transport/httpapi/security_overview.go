package httpapi

// Web Security Center, Event Centralization, and Security CI slice,
// Work Package B/C: GET /api/v1/security/overview aggregates existing,
// already-real coordinator RPCs plus a bounded tally over the existing
// event journals -- it deliberately introduces no new counters that
// duplicate what the journals already record (see docs/security-
// dashboard.md). Aggregate-only: no worker IDs, no certificate
// material, no raw event payloads, no private keys.

import (
	"context"
	"net/http"
	"time"

	"github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/coordinator"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/observability"
	"github.com/smshagor-dev/federated-learning-super-system/go/internal/security"
)

// certExpiryWarningWindow: a worker/coordinator certificate expiring
// within this window counts toward the overview's warning counts.
const certExpiryWarningWindow = 7 * 24 * time.Hour

// staleSecurityEventSourceThresholdSeconds: Work Package P's fixed
// staleness threshold for GET /api/v1/security/events/sources. A source
// that has reported at least one event (LastEventAt is set) and whose
// lag exceeds this is marked Stale=true. Chosen as roughly 4x the
// python-worker's default security-event flush interval (15s, see
// WorkerConfig.security_event_flush_interval_seconds) plus the 5s Go
// polling interval the web console uses -- generous enough that a
// single missed flush cycle or a slow poll doesn't flip a healthy
// source to stale, tight enough to surface a source that has actually
// stopped reporting within about two flush cycles. A single constant
// for every source (not one threshold per source type) -- see this
// file's own "no per-worker label" discipline; the same reasoning
// applies to thresholds.
const staleSecurityEventSourceThresholdSeconds = 120.0

// recentEventTallyLimit bounds how many recent events (per source) the
// overview scans to derive signed-message/privacy-record/task/severity
// counts -- an approximate "recent activity" window, not an exhaustive
// lifetime total, so the endpoint's response time and size stay bounded
// regardless of journal size (Work Package C's "Bounded response").
const recentEventTallyLimit = 500

type securityOverviewTransport struct {
	TransportMode        string  `json:"transport_mode"`
	MutualTLSEnforced    bool    `json:"mutual_tls_enforced"`
	CheckedAtUnixS       float64 `json:"checked_at_unix_s"`
	CoordinatorAvailable bool    `json:"coordinator_available"`
}

type securityOverviewWorkerIdentities struct {
	Active                    int `json:"active"`
	Suspended                 int `json:"suspended"`
	Revoked                   int `json:"revoked"`
	Expired                   int `json:"expired"`
	CertificateExpiryWarnings int `json:"certificate_expiry_warnings"`
}

type securityOverviewWorkerSigningKeys struct {
	Active                 int `json:"active"`
	GracePeriod            int `json:"grace_period"`
	Expired                int `json:"expired"`
	Revoked                int `json:"revoked"`
	RecentRotationFailures int `json:"recent_rotation_failures"`
}

type securityOverviewCoordinatorKeys struct {
	ActiveKeyID             string  `json:"active_key_id,omitempty"`
	ActiveKeyExpiresAtUnixS float64 `json:"active_key_expires_at_unix_s"`
	GracePeriodKeyID        string  `json:"grace_period_key_id,omitempty"`
	GracePeriodEndUnixS     float64 `json:"grace_period_end_unix_s"`
	HistoricalExpiredCount  int     `json:"historical_expired_count"`
	HistoricalRevokedCount  int     `json:"historical_revoked_count"`
	TrustedBundleVersion    uint64  `json:"trusted_bundle_version"`
	BundleHealthy           bool    `json:"bundle_healthy"`
}

type securityOverviewSignedMessages struct {
	Accepted            int `json:"accepted"`
	Rejected            int `json:"rejected"`
	SignatureFailures   int `json:"signature_failures"`
	PayloadHashFailures int `json:"payload_hash_failures"`
	ReplayRejections    int `json:"replay_rejections"`
	SequenceRejections  int `json:"sequence_rejections"`
}

type securityOverviewPrivacyRecords struct {
	Accepted                int `json:"accepted"`
	Rejected                int `json:"rejected"`
	MonotonicityViolations  int `json:"monotonicity_violations"`
	ConfigurationMismatches int `json:"configuration_mismatches"`
}

type securityOverviewTasks struct {
	Signed                   int `json:"signed"`
	SigningFailures          int `json:"signing_failures"`
	VerificationFailures     int `json:"verification_failures"`
	ReplayRejections         int `json:"replay_rejections"`
	DuplicateExecutionBlocks int `json:"duplicate_execution_blocks"`
	Reissues                 int `json:"reissues"`
}

type securityOverviewJournalHealth struct {
	SizeRecords     int     `json:"size_records"`
	LastRecordAt    string  `json:"last_record_at,omitempty"`
	LagSeconds      float64 `json:"lag_seconds,omitempty"`
	RecoveredLines  int     `json:"recovered_lines"`
	Corrupted       bool    `json:"corrupted"`
	RetentionActive bool    `json:"retention_active"`
}

// securityFeatureAvailability: Work Package B's explicit, mandatory
// disclosure -- mutual TLS, signatures, and event journals are message-
// authenticity and observability mechanisms, never secure aggregation.
// These flags must never be inferred as "true" by any other part of
// this endpoint's aggregation logic.
type securityFeatureAvailability struct {
	SecureAggregationAvailable        bool `json:"secure_aggregation_available"`
	WorkerAttestationAvailable        bool `json:"worker_attestation_available"`
	VerifiableClientClippingAvailable bool `json:"verifiable_client_clipping_available"`
	ByzantineRobustnessAvailable      bool `json:"byzantine_robustness_available"`
	CentralCoordinatorObservesUpdates bool `json:"central_coordinator_observes_updates"`
}

type securityOverview struct {
	Transport                    securityOverviewTransport         `json:"transport"`
	WorkerIdentities             securityOverviewWorkerIdentities  `json:"worker_identities"`
	WorkerSigningKeys            securityOverviewWorkerSigningKeys `json:"worker_signing_keys"`
	CoordinatorKeys              securityOverviewCoordinatorKeys   `json:"coordinator_keys"`
	SignedMessages               securityOverviewSignedMessages    `json:"signed_messages"`
	PrivacyRecords               securityOverviewPrivacyRecords    `json:"privacy_records"`
	Tasks                        securityOverviewTasks             `json:"tasks"`
	EventJournal                 securityOverviewJournalHealth     `json:"event_journal"`
	AuditJournal                 securityOverviewJournalHealth     `json:"audit_journal"`
	RecentCriticalEventCount     int                               `json:"recent_critical_event_count"`
	RecentHighSeverityEventCount int                               `json:"recent_high_severity_event_count"`
	FeatureAvailability          securityFeatureAvailability       `json:"feature_availability"`
	GeneratedAtUnixS             float64                           `json:"generated_at_unix_s"`
}

func (s *Server) handleSecurityOverview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	session, ok := s.requirePermission(w, r, security.PermOverviewRead)
	if !ok {
		return
	}

	overview := s.buildSecurityOverview(r.Context(), r.Header.Get("X-Trace-Id"))

	// VIEWER may see aggregate counts but not signing-key identifiers
	// (Work Package: route-visibility table, "May not access: Signing-
	// key identifiers").
	if session.User.Role == auth.RoleViewer {
		overview.CoordinatorKeys.ActiveKeyID = ""
		overview.CoordinatorKeys.GracePeriodKeyID = ""
	}

	writeJSON(w, http.StatusOK, overview)
}

func (s *Server) buildSecurityOverview(ctx context.Context, traceID string) securityOverview {
	now := float64(time.Now().Unix())
	overview := securityOverview{
		GeneratedAtUnixS: now,
		FeatureAvailability: securityFeatureAvailability{
			SecureAggregationAvailable:        false,
			WorkerAttestationAvailable:        false,
			VerifiableClientClippingAvailable: false,
			ByzantineRobustnessAvailable:      false,
			CentralCoordinatorObservesUpdates: true,
		},
	}

	coordinatorAvailable := s.services != nil && s.services.Coordinator != nil && s.services.Coordinator.Configured()
	overview.Transport.CoordinatorAvailable = coordinatorAvailable

	if coordinatorAvailable {
		s.fillTransportAndTrustModel(ctx, traceID, &overview)
		s.fillWorkerIdentitiesAndKeys(ctx, traceID, &overview)
		s.fillCoordinatorKeys(ctx, traceID, &overview)
	}

	s.fillEventTallies(ctx, traceID, &overview)
	s.fillJournalHealth(&overview)

	return overview
}

func (s *Server) fillTransportAndTrustModel(ctx context.Context, traceID string, overview *securityOverview) {
	if status, err := s.services.Coordinator.GetTransportSecurityStatus(ctx, traceID); err == nil {
		overview.Transport.TransportMode = status.TransportMode
		overview.Transport.MutualTLSEnforced = status.MutualTLSEnforced
		overview.Transport.CheckedAtUnixS = status.CheckedAtUnixS
	}
}

func (s *Server) fillWorkerIdentitiesAndKeys(ctx context.Context, traceID string, overview *securityOverview) {
	identities, err := s.services.Coordinator.ListWorkerIdentities(ctx, traceID)
	if err != nil {
		return
	}
	warningCutoff := float64(time.Now().Add(certExpiryWarningWindow).Unix())
	for _, identity := range identities {
		switch identity.RegistrationStatus {
		case "active":
			overview.WorkerIdentities.Active++
		case "suspended":
			overview.WorkerIdentities.Suspended++
		case "revoked":
			overview.WorkerIdentities.Revoked++
		case "expired":
			overview.WorkerIdentities.Expired++
		}
		if identity.ExpiresAtUnixS > 0 && identity.ExpiresAtUnixS <= warningCutoff {
			overview.WorkerIdentities.CertificateExpiryWarnings++
		}

		keys, keyErr := s.services.Coordinator.ListWorkerSigningKeys(ctx, identity.WorkerID, traceID)
		if keyErr != nil {
			continue
		}
		for _, key := range keys {
			switch key.Status {
			case "active":
				overview.WorkerSigningKeys.Active++
			case "grace_period":
				overview.WorkerSigningKeys.GracePeriod++
			case "expired":
				overview.WorkerSigningKeys.Expired++
			case "revoked":
				overview.WorkerSigningKeys.Revoked++
			}
		}
	}
}

func (s *Server) fillCoordinatorKeys(ctx context.Context, traceID string, overview *securityOverview) {
	if model, err := s.services.Coordinator.GetSecurityTrustModel(ctx, traceID); err == nil {
		overview.CoordinatorKeys.ActiveKeyID = model.ActiveCoordinatorSigningKeyID
		overview.CoordinatorKeys.TrustedBundleVersion = model.TrustedKeyBundleVersion
		overview.CoordinatorKeys.BundleHealthy = model.TrustedKeyBundleVersion > 0
	}
	keys, err := s.services.Coordinator.ListCoordinatorSigningKeys(ctx, traceID)
	if err != nil {
		return
	}
	for _, key := range keys {
		switch key.Status {
		case "active":
			overview.CoordinatorKeys.ActiveKeyExpiresAtUnixS = key.ExpiresAtUnixS
		case "grace_period":
			overview.CoordinatorKeys.GracePeriodKeyID = key.SigningKeyID
			overview.CoordinatorKeys.GracePeriodEndUnixS = key.GracePeriodEndUnixS
		case "expired":
			overview.CoordinatorKeys.HistoricalExpiredCount++
		case "revoked":
			overview.CoordinatorKeys.HistoricalRevokedCount++
		}
	}
}

// fillEventTallies scans a bounded window of recent events (Go-local
// plus coordinator-relayed, same merge as handleSecurityEvents) and
// buckets them by category -- reuses the journal infrastructure rather
// than introducing a second set of counters that could drift from it.
func (s *Server) fillEventTallies(ctx context.Context, traceID string, overview *securityOverview) {
	var events []observability.SecurityEvent
	if s.securityEventJournal != nil {
		local := s.securityEventJournal.List(observability.SecurityEventListFilters{Limit: recentEventTallyLimit})
		events = append(events, local.Events...)
	}
	if s.services != nil && s.services.Coordinator != nil && s.services.Coordinator.Configured() {
		remote, err := s.services.Coordinator.ListSecurityEvents(ctx, coordinator.ListSecurityEventsRequest{
			TraceID: traceID, Limit: recentEventTallyLimit,
		})
		if err == nil {
			events = append(events, remote.Events...)
		}
	}

	for _, event := range events {
		tallySignedMessage(&overview.SignedMessages, event)
		tallyPrivacyRecord(&overview.PrivacyRecords, event)
		tallyTask(&overview.Tasks, event)
		if event.EventType == observability.EventWorkerKeyRotationRejected {
			overview.WorkerSigningKeys.RecentRotationFailures++
		}
		switch event.Severity {
		case observability.SeverityCritical:
			overview.RecentCriticalEventCount++
		case observability.SeverityHigh:
			overview.RecentHighSeverityEventCount++
		}
	}
}

func tallySignedMessage(counts *securityOverviewSignedMessages, event observability.SecurityEvent) {
	switch event.EventType {
	case observability.EventCapabilityAccepted, observability.EventHeartbeatAccepted,
		observability.EventClientResultAccepted:
		counts.Accepted++
	case observability.EventCapabilityRejected, observability.EventHeartbeatRejected,
		observability.EventClientResultRejected:
		counts.Rejected++
	case observability.EventSignatureVerificationFailed:
		counts.SignatureFailures++
	case observability.EventPayloadHashMismatch:
		counts.PayloadHashFailures++
	case observability.EventMessageReplayRejected:
		counts.ReplayRejections++
	case observability.EventMessageSequenceRejected:
		counts.SequenceRejections++
	}
}

func tallyPrivacyRecord(counts *securityOverviewPrivacyRecords, event observability.SecurityEvent) {
	switch event.EventType {
	case observability.EventPrivacyRecordAccepted:
		counts.Accepted++
	case observability.EventPrivacyRecordRejected:
		counts.Rejected++
		switch event.ReasonCode {
		case "monotonicity_violation":
			counts.MonotonicityViolations++
		case "configuration_mismatch":
			counts.ConfigurationMismatches++
		}
	}
}

func tallyTask(counts *securityOverviewTasks, event observability.SecurityEvent) {
	switch event.EventType {
	case observability.EventCoordinatorTaskSigned:
		counts.Signed++
	case observability.EventCoordinatorTaskSigningFailed:
		counts.SigningFailures++
	case observability.EventCoordinatorTaskRejected:
		counts.VerificationFailures++
	case observability.EventCoordinatorTaskReplayRejected:
		counts.ReplayRejections++
	case observability.EventDuplicateTaskExecutionBlocked:
		counts.DuplicateExecutionBlocks++
	case observability.EventTaskReissued:
		counts.Reissues++
	}
}

func (s *Server) fillJournalHealth(overview *securityOverview) {
	now := float64(time.Now().Unix())
	if s.securityEventJournal != nil {
		overview.EventJournal = journalHealthFromEvent(s.securityEventJournal, now)
	}
	if s.securityAuditJournal != nil {
		overview.AuditJournal = journalHealthFromAudit(s.securityAuditJournal, now)
	}
}

func journalHealthFromEvent(journal *observability.SecurityEventJournal, nowUnixS float64) securityOverviewJournalHealth {
	health := securityOverviewJournalHealth{
		SizeRecords:     journal.Size(),
		LastRecordAt:    journal.LastRecordTimestamp(),
		RecoveredLines:  journal.RecoveredLineCount(),
		Corrupted:       journal.RecoveredLineCount() > 0,
		RetentionActive: journal.HasRotated(),
	}
	if lagSeconds, ok := lagFromTimestamp(health.LastRecordAt, nowUnixS); ok {
		health.LagSeconds = lagSeconds
	}
	return health
}

func journalHealthFromAudit(journal *observability.SecurityAuditJournal, nowUnixS float64) securityOverviewJournalHealth {
	health := securityOverviewJournalHealth{
		SizeRecords:     journal.Size(),
		LastRecordAt:    journal.LastRecordTimestamp(),
		RecoveredLines:  journal.RecoveredLineCount(),
		Corrupted:       journal.RecoveredLineCount() > 0,
		RetentionActive: journal.HasRotated(),
	}
	if lagSeconds, ok := lagFromTimestamp(health.LastRecordAt, nowUnixS); ok {
		health.LagSeconds = lagSeconds
	}
	return health
}

// securityEventSourceEntry is the HTTP-facing shape for one source's
// health -- mirrors coordinator.SecurityEventSourceHealthEntry plus the
// two Go-local journals (go-api's own), which the coordinator RPC does
// not report on (see the proto comment on SecurityEventSourceHealthEntry).
type securityEventSourceEntry struct {
	SourceService       string  `json:"source_service"`
	LastEventAt         string  `json:"last_event_at,omitempty"`
	LagSeconds          float64 `json:"lag_seconds,omitempty"`
	RecordCount         uint64  `json:"record_count"`
	RecoveredLineCount  uint64  `json:"recovered_line_count"`
	Corrupted           bool    `json:"corrupted"`
	RetentionActive     bool    `json:"retention_active"`
	BatchesAccepted     uint64  `json:"batches_accepted,omitempty"`
	BatchesRejected     uint64  `json:"batches_rejected,omitempty"`
	DistinctWorkersSeen uint64  `json:"distinct_workers_seen,omitempty"`
	Stale               bool    `json:"stale"`
}

// applyStaleness sets Stale=true only when a lag value is actually
// known (LastEventAt != "") and it exceeds the fixed threshold -- a
// source that has simply never reported (LastEventAt == "") is not
// "stale", it has no data yet, a different, non-alarming state the web
// console already renders distinctly (see security-overview-console.tsx).
func (e *securityEventSourceEntry) applyStaleness() {
	if e.LastEventAt == "" {
		return
	}
	e.Stale = e.LagSeconds > staleSecurityEventSourceThresholdSeconds
}

// handleSecurityEventSources: GET /api/v1/security/events/sources (Work
// Package N). Low-cardinality source identifiers only ("coordinator",
// "go-api", "python-worker") -- never an individual worker ID, for any
// role, including VIEWER.
func (s *Server) handleSecurityEventSources(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	if _, ok := s.requirePermission(w, r, security.PermEventSourcesRead); !ok {
		return
	}

	now := float64(time.Now().Unix())
	sources := make([]securityEventSourceEntry, 0, 3)

	if s.securityEventJournal != nil {
		goEntry := securityEventSourceEntry{
			SourceService:      "go-api",
			LastEventAt:        s.securityEventJournal.LastRecordTimestamp(),
			RecordCount:        uint64(s.securityEventJournal.Size()),
			RecoveredLineCount: uint64(s.securityEventJournal.RecoveredLineCount()),
			Corrupted:          s.securityEventJournal.RecoveredLineCount() > 0,
			RetentionActive:    s.securityEventJournal.HasRotated(),
		}
		if lag, ok := lagFromTimestamp(goEntry.LastEventAt, now); ok {
			goEntry.LagSeconds = lag
		}
		goEntry.applyStaleness()
		sources = append(sources, goEntry)
	}

	if s.services != nil && s.services.Coordinator != nil && s.services.Coordinator.Configured() {
		health, err := s.services.Coordinator.GetSecurityEventSourceHealth(r.Context(), r.Header.Get("X-Trace-Id"))
		if err == nil {
			for _, entry := range health.Sources {
				mapped := securityEventSourceEntry{
					SourceService:       entry.SourceService,
					LastEventAt:         entry.LastEventAt,
					RecordCount:         entry.RecordCount,
					RecoveredLineCount:  entry.RecoveredLineCount,
					Corrupted:           entry.Corrupted,
					RetentionActive:     entry.RetentionActive,
					BatchesAccepted:     entry.BatchesAccepted,
					BatchesRejected:     entry.BatchesRejected,
					DistinctWorkersSeen: entry.DistinctWorkersSeen,
				}
				if lag, ok := lagFromTimestamp(mapped.LastEventAt, now); ok {
					mapped.LagSeconds = lag
				}
				mapped.applyStaleness()
				sources = append(sources, mapped)
			}
		}
	}

	// Web Security Center, Event Centralization, and Security CI slice:
	// feed the last-observed event-source health gauges from exactly
	// what this response is about to serve -- one source of truth, no
	// separate re-derivation.
	if s.services != nil && s.services.Metrics != nil {
		for _, source := range sources {
			// mapped.LagSeconds (above) is only ever set together with a
			// non-empty LastEventAt (see lagFromTimestamp) -- so
			// LastEventAt != "" is the precise "is a lag value known"
			// signal, not LagSeconds != 0 (which a genuinely-fresh
			// source could legitimately report).
			hasLag := source.LastEventAt != ""
			s.services.Metrics.RecordSecurityEventSourceHealth(
				source.SourceService, source.RecordCount, source.BatchesAccepted, source.BatchesRejected,
				source.DistinctWorkersSeen, source.LagSeconds, hasLag)
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{"sources": sources, "checked_at_unix_s": now})
}

func lagFromTimestamp(timestamp string, nowUnixS float64) (float64, bool) {
	if timestamp == "" {
		return 0, false
	}
	parsed, err := time.Parse("2006-01-02T15:04:05Z", timestamp)
	if err != nil {
		return 0, false
	}
	lag := nowUnixS - float64(parsed.Unix())
	if lag < 0 {
		lag = 0
	}
	return lag, true
}
