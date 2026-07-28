// Package security defines the permission model for the Security
// Operations and Administration slice's HTTP API
// (docs/security-api.md, docs/security-permission-model.md). Its
// purpose is the one explicitly required by the specification: "use
// permission constants not scattered role checks" — every security
// HTTP handler calls Allows(role, PermX) instead of inlining an
// auth.Role enum comparison the way the pre-existing (non-security)
// routes in go/internal/transport/httpapi/server.go do. Those
// pre-existing routes are left untouched (no demonstrated defect in
// them), so this package only governs the new /api/v1/security/*
// surface.
package security

import "github.com/smshagor-dev/federated-learning-super-system/go/internal/auth"

// Permission is one discrete, checkable security capability. String-typed
// (not iota) so it round-trips cleanly through logs/audit records
// without a lookup table.
type Permission string

const (
	PermTransportRead         Permission = "security.transport.read"
	PermTrustRead             Permission = "security.trust.read"
	PermWorkersRead           Permission = "security.workers.read"
	PermWorkersSuspend        Permission = "security.workers.suspend"
	PermWorkersActivate       Permission = "security.workers.activate"
	PermWorkersRevoke         Permission = "security.workers.revoke"
	PermWorkerKeysRead        Permission = "security.worker_keys.read"
	PermWorkerKeysRevoke      Permission = "security.worker_keys.revoke"
	PermCoordinatorKeysRead   Permission = "security.coordinator_keys.read"
	PermCoordinatorKeysRotate Permission = "security.coordinator_keys.rotate"
	PermCoordinatorKeysRevoke Permission = "security.coordinator_keys.revoke"
	PermEventsRead            Permission = "security.events.read"
	PermEventsReadDetailed    Permission = "security.events.read_detailed"
	PermAuditRead             Permission = "security.audit.read"
	PermAuditReadDetailed     Permission = "security.audit.read_detailed"
	// Web Security Center, Event Centralization, and Security CI slice:
	// both aggregate-only reads, granted to every authenticated web role
	// per the route-visibility table (ADMIN/RESEARCHER/VIEWER all see
	// the overview and source-health pages; field-level redaction, not
	// route denial, is how VIEWER's narrower access is enforced -- see
	// handleSecurityOverview/handleSecurityEventSources).
	PermOverviewRead     Permission = "security.overview.read"
	PermEventSourcesRead Permission = "security.event_sources.read"

	// Secure User-Level DP Operations, Observability, and Release
	// Evidence slice (docs/secure-user-level-operations-audit.md), Work
	// Area J: responsibility-based names, one per new route. Status/
	// health are the static-capability + aggregate-health surfaces
	// (safe for every authenticated role, mirroring PermOverviewRead);
	// rounds/round/budget expose per-run privacy accounting (epsilon
	// history, round counts) and are therefore withheld from VIEWER --
	// the same "aggregate yes, per-entity detail no" split
	// PermWorkersRead vs. PermWorkerKeysRead already establishes.
	PermSecureUserDPStatusRead Permission = "security.secure_user_dp.status.read"
	PermSecureUserDPHealthRead Permission = "security.secure_user_dp.health.read"
	PermSecureUserDPRoundsRead Permission = "security.secure_user_dp.rounds.read"
	PermSecureUserDPRoundRead  Permission = "security.secure_user_dp.round.detail"
	PermSecureUserDPBudgetRead Permission = "security.secure_user_dp.budget.read"

	PermResearchExperimentsRead     Permission = "research.experiments.read"
	PermResearchExperimentsList     Permission = "research.experiments.list"
	PermResearchExperimentsValidate Permission = "research.experiments.validate"
	PermResearchExperimentsCreate   Permission = "research.experiments.create"
	PermResearchExperimentsStart    Permission = "research.experiments.start"
	PermResearchExperimentsCancel   Permission = "research.experiments.cancel"
	PermResearchExperimentsRetry    Permission = "research.experiments.retry"
	PermResearchRunsRead            Permission = "research.experiments.runs.read"
	PermResearchMetricsRead         Permission = "research.experiments.metrics.read"
	PermResearchEventsRead          Permission = "research.experiments.events.read"
	PermResearchArtifactsRead       Permission = "research.experiments.artifacts.read"
	PermResearchRuntimeHealthRead   Permission = "research.runtime.health.read"
)

// rolePermissions is the fixed, per-role default grant. ADMIN gets
// every permission (all safe reads plus every mutation plus detailed
// audit); RESEARCHER gets reads plus project-scoped events/redacted
// audit, no mutations; VIEWER gets only aggregate reads; SERVICE gets
// none by default — see the package doc comment and HasScope below for
// why SERVICE is never silently treated as ADMIN.
var rolePermissions = map[auth.Role]map[Permission]bool{
	auth.RoleAdmin: {
		PermTransportRead: true, PermTrustRead: true,
		PermWorkersRead: true, PermWorkersSuspend: true, PermWorkersActivate: true, PermWorkersRevoke: true,
		PermWorkerKeysRead: true, PermWorkerKeysRevoke: true,
		PermCoordinatorKeysRead: true, PermCoordinatorKeysRotate: true, PermCoordinatorKeysRevoke: true,
		PermEventsRead: true, PermEventsReadDetailed: true, PermAuditRead: true, PermAuditReadDetailed: true,
		PermOverviewRead: true, PermEventSourcesRead: true,
		PermSecureUserDPStatusRead: true, PermSecureUserDPHealthRead: true,
		PermSecureUserDPRoundsRead: true, PermSecureUserDPRoundRead: true, PermSecureUserDPBudgetRead: true,
		PermResearchExperimentsRead: true, PermResearchExperimentsList: true,
		PermResearchExperimentsValidate: true, PermResearchExperimentsCreate: true,
		PermResearchExperimentsStart: true, PermResearchExperimentsCancel: true,
		PermResearchExperimentsRetry: true,
		PermResearchRunsRead:         true, PermResearchMetricsRead: true,
		PermResearchEventsRead: true, PermResearchArtifactsRead: true,
		PermResearchRuntimeHealthRead: true,
	},
	auth.RoleResearcher: {
		PermTransportRead: true, PermTrustRead: true,
		PermWorkersRead: true, PermWorkerKeysRead: true, PermCoordinatorKeysRead: true,
		PermEventsRead: true, PermAuditRead: true,
		PermOverviewRead: true, PermEventSourcesRead: true,
		PermSecureUserDPStatusRead: true, PermSecureUserDPHealthRead: true,
		PermSecureUserDPRoundsRead: true, PermSecureUserDPRoundRead: true, PermSecureUserDPBudgetRead: true,
		PermResearchExperimentsRead: true, PermResearchExperimentsList: true,
		PermResearchExperimentsValidate: true, PermResearchExperimentsCreate: true,
		PermResearchExperimentsStart: true, PermResearchExperimentsCancel: true,
		PermResearchRunsRead: true, PermResearchMetricsRead: true,
		PermResearchEventsRead: true, PermResearchArtifactsRead: true,
		PermResearchRuntimeHealthRead: true,
	},
	auth.RoleViewer: {
		PermTransportRead: true, PermTrustRead: true,
		PermWorkersRead:  true,
		PermEventsRead:   true,
		PermOverviewRead: true, PermEventSourcesRead: true,
		// Aggregate capability/health only -- per-run round/budget
		// detail (epsilon history) is withheld from VIEWER, same split
		// as PermWorkerKeysRead being ADMIN/RESEARCHER-only.
		PermSecureUserDPStatusRead: true, PermSecureUserDPHealthRead: true,
		PermResearchExperimentsRead: true, PermResearchExperimentsList: true,
		PermResearchRunsRead: true, PermResearchMetricsRead: true,
		PermResearchEventsRead: true, PermResearchArtifactsRead: true,
		PermResearchRuntimeHealthRead: true,
	},
	// auth.RoleService: intentionally absent (empty default) -- see
	// HasScope for the explicit-scope escape hatch the specification
	// requires instead.
}

// Allows reports whether role has perm under its fixed default grant
// (ignoring any per-user explicit scope grant — see HasScope for that).
func Allows(role auth.Role, perm Permission) bool {
	return rolePermissions[role][perm]
}

// HasScope reports whether perm is present in an individual actor's own
// explicitly-assigned scope list (e.g. a SERVICE-role User's
// auth.User.Capabilities), independent of their role's fixed default.
// This is the mechanism the specification's "SERVICE Allowed only
// through explicitly assigned service scopes" requirement describes.
//
// Known limitation (see docs/security-api.md / docs/known-limitations.md):
// application.AuthSession.Capabilities (populated by
// capabilitiesForRole(user.Role) on every login/Authenticate call — see
// go/internal/application/services.go) is, today, always exactly the
// role's fixed default re-derived, never a genuine per-user grant
// distinct from role — there is no per-user scope override mechanism
// anywhere in this codebase yet for HasScope to actually read from at
// the HTTP layer. Building one would mean extending auth.User/AuthSession
// with a real per-user scope list plus admin UI/API to manage it, which
// is out of scope for an additive permission model on top of the
// existing (stable, untouched) auth system. Until that exists, HasScope
// only ever sees whatever scopes a caller explicitly passes to it (e.g.
// a test) — no live HTTP request path feeds it anything today, so every
// SERVICE-role HTTP request falls through to rolePermissions' empty
// SERVICE default and is denied. That is the fail-closed, honest outcome
// the specification asks for pending real per-user scope plumbing.
func HasScope(scopes []string, perm Permission) bool {
	for _, scope := range scopes {
		if Permission(scope) == perm {
			return true
		}
	}
	return false
}
