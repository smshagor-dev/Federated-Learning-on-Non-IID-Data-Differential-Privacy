# Security Audit Journal

**Status: Implemented and validated (C++, Go).** Security Events,
Metrics, and Durable Audit Journal slice, requirements 8/9 ("a durable
security-specific audit journal", "keep security events and audit
records conceptually separate").

## Why a second durable store, distinct from `SecurityEventJournal`

An **event** answers "what happened" (a rejection, a state transition,
a signed-message acceptance) — see [security-events.md](security-events.md).
An **audit record** answers "who did what, with what outcome, and
when" for every security-sensitive mutation — an accountability trail
keyed on actor + action, not a domain-event stream. Conflating the two
would make it harder to reason about either: an event stream wants to
be broad and cheap to emit (including read-path signals like a rejected
signature); an audit trail wants to be narrow and exhaustive for every
mutation specifically, with actor identity as a first-class field.

## Relationship to the existing general-purpose `AuditRepository`

**Additive, not a replacement.** `go/internal/observability/audit.go`'s
`AuditRepository` (used for model/dataset/run/session domain actions
long before this slice, and — since the Security Operations and
Administration slice — for security mutations too) keeps being written
to, unchanged, by every existing call site
(`internal/application/security_service.go`'s `s.audit.Record(...)`
calls). This slice's `SecurityAuditJournal` is a **second, richer
record** written alongside it at the same call sites
(`appendSecurityAudit`). Zero regression risk to the general
repository's existing behavior — confirmed by
`TestSecurityAuditRedactsForResearcherButNotAdmin` passing **unmodified**
after this slice's changes (it exercises the general repository's write
path, which this slice never touched).

`GET /api/v1/security/audit` **switches** its read source from the
general repository to the new journal — the general repository keeps
serving every other, non-security domain untouched.

## Schema

`cpp/coordinator/include/fl_coordinator/security_audit_journal.hpp`'s
`SecurityAuditRecord` and `go/internal/observability/security_audit_journal.go`'s
`SecurityAuditRecord` (Go doesn't need a separate C++-mirrored type
beyond field parity — no cross-language golden fixture was built for
this schema, since audit records are never signed or checksummed
cross-language the way `SecurityEvent` is; a shared design, not a
shared wire format, was the actual requirement here):

`schema_version`, `record_id`, `timestamp`, `safe_actor_id`,
`actor_role`, `action`, `resource_type`, `resource_id`, `outcome`,
`reason`, `request_id`, `trace_id`, `safe_details`, `payload_checksum`.

Same JSONL / size-based rotation (10 MiB default, 5 retained
generations) / skip-and-recover corruption policy as
`SecurityEventJournal` — see [security-events.md](security-events.md)'s
identical section for the full rationale (an audit journal is still an
observability-adjacent artifact whose availability matters more than
strict fail-closed behavior on a corrupted trailing line; the
underlying registry files — `WorkerIdentityRegistry`,
`SigningKeyRegistry`, etc. — remain the actual source of trust-critical
truth this journal only records decisions *about*).

## C++ coordinator's own audit journal

A `SecurityAuditJournal` also exists at the C++ layer
(`cpp/coordinator/src/coordinator_service.cpp`), wired into `main.cpp`
via `FL_SECURITY_AUDIT_JOURNAL_PATH` (default `security_audit.jsonl`),
recording the same representative subset of `ADMIN_CONTROL` mutations
as the event journal (worker lifecycle, worker-key revocation,
coordinator-key rotation/revocation). This is **independent** of Go's
own `SecurityAuditJournal` instance — each service owns and persists
its own durable audit trail of the mutations it itself authorizes; Go's
journal is not (yet) fed by the coordinator's, matching this slice's
established "durable per-service journal, not a single unified store"
architecture (see [security-events.md](security-events.md)'s
cross-service architecture section for the identical reasoning applied
to events).

## Pagination and filtering (requirement 10)

`GET /api/v1/security/audit` real query parameters:

- `cursor` — opaque, `record_id`-based
- `limit` (default 500)
- `actor`, `action`, `resource_type`, `outcome` — exact-match filters
- `since`, `until` — Unix-seconds time-range bounds

Response: `{"records": [...], "next_cursor": "..."}`.

Implemented identically (same filter set) in C++'s
`SecurityAuditJournal::list`/`ListFilters` for whatever future RPC
surface wants to expose the coordinator's own journal — not currently
exposed via a gRPC RPC this slice (only Go's HTTP-facing journal is
directly queryable today; the C++ journal is available for inspection
via its persisted JSONL file, same operational posture as every other
coordinator store before a dedicated read RPC existed for it).

## Role-aware redaction and meta-audit (requirements 11/12)

- `security.audit.read` (VIEWER lacks this — `403`): base access.
- `security.audit.read_detailed` (ADMIN only): full record including
  `reason`/`safe_details`; without it (RESEARCHER), the response is
  redacted to `record_id`/`timestamp`/`actor_role`/`action`/
  `resource_type`/`resource_id`/`outcome` only (`redactSecurityAuditRecord`,
  mirroring the pre-existing `redactAuditEvent`'s identical rationale).
- **Requirement 12, "audit access to detailed security records"**:
  every detailed (ADMIN) read that actually returns ≥1 record emits a
  `SECURITY_AUDIT_ACCESSED` event into the **events** journal (not
  recursively into the audit journal itself, which would create an
  unbounded feedback loop) — `CoordinatorService.EmitAuditAccessed`.

Validated live (Docker Compose, real mTLS): a real worker-suspend
mutation → the new audit journal has a record → RESEARCHER read is
redacted (no `reason`) → ADMIN read is not → VIEWER read is `403`. See
[security-runtime-validation.md](security-runtime-validation.md).

## Deferred

- A gRPC RPC exposing the C++ coordinator's own `SecurityAuditJournal`
  for remote querying (currently file-only, same posture as every
  coordinator store before its first read RPC existed).
- Merging Go's and the coordinator's audit journals into one queryable
  view (same disclosed limitation as the events journal's cross-service
  merge).
- Retention/rotation metrics (record counts, rotation events) as their
  own Prometheus series.
