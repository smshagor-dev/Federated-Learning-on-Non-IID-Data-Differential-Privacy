# Security Permission Model

**Status: implemented and live-validated (`go/internal/security`).**

## Why a separate package

The pre-existing Go HTTP layer (`go/internal/transport/httpapi/server.go`)
authorizes every non-security route with an inline `auth.Role` list
passed to `withAuth(...)` or `Authorize(session, roles...)` — e.g.
`s.withAuth(auth.RoleResearcher, auth.RoleAdmin)`. That is left
completely unchanged; there is no demonstrated defect in it.

The Security Operations and Administration slice's specification
explicitly required "permission constants, not scattered role checks"
for its own surface, so `go/internal/security/permissions.go` defines
a `Permission` string type and a fixed `rolePermissions` matrix,
checked via `security.Allows(role, permission)` — one call per
handler, not a role-enum comparison repeated at every route.

## Permissions

```text
security.transport.read
security.trust.read
security.workers.read
security.workers.suspend
security.workers.activate
security.workers.revoke
security.worker_keys.read
security.worker_keys.revoke
security.coordinator_keys.read
security.coordinator_keys.rotate
security.coordinator_keys.revoke
security.events.read
security.audit.read
security.audit.read_detailed
```

## Role matrix

| Permission | ADMIN | RESEARCHER | VIEWER | SERVICE |
|---|---|---|---|---|
| `transport.read` | Yes | Yes | Yes | No |
| `trust.read` | Yes | Yes | Yes | No |
| `workers.read` | Yes | Yes | Yes | No |
| `workers.suspend` | Yes | No | No | No |
| `workers.activate` | Yes | No | No | No |
| `workers.revoke` | Yes | No | No | No |
| `worker_keys.read` | Yes | Yes | No | No |
| `worker_keys.revoke` | Yes | No | No | No |
| `coordinator_keys.read` | Yes | Yes | No | No |
| `coordinator_keys.rotate` | Yes | No | No | No |
| `coordinator_keys.revoke` | Yes | No | No | No |
| `events.read` | Yes | Yes | Yes | No |
| `audit.read` | Yes | Yes | No | No |
| `audit.read_detailed` | Yes | No | No | No |

Live-validated (`docs/security-operations-report.md`): ADMIN can
rotate/revoke coordinator keys and suspend workers; RESEARCHER can read
coordinator signing keys but is rejected (403) attempting a rotation;
VIEWER can read (redacted) worker detail but is rejected (403)
attempting an activation; an unauthenticated request is rejected (401).

## SERVICE role

**Deliberately granted zero permissions by default** — the
specification's "SERVICE Allowed only through explicitly assigned
service scopes. Do not treat SERVICE as automatically equivalent to
ADMIN" requirement. `security.HasScope(scopes, permission)` exists as
the mechanism a per-user explicit scope grant would use, but **no live
HTTP request path feeds it anything today**: `auth.User.Capabilities`/
`application.AuthSession.Capabilities` are always exactly
`capabilitiesForRole(role)` re-derived, never a genuine per-user
override, and extending `application.Actor`/`AuthSession` to carry a
real per-user scope list is out of scope for this slice (it is shared,
stable code used by every route in this API, not only the security
ones). Confirmed via `TestServiceRoleNeverAutomaticallyAdmin`
(`go/internal/security/permissions_test.go`): SERVICE has none of
ADMIN's permissions. This is documented, not silently left as an
apparent oversight — see
[known-limitations.md](known-limitations.md).

## Redaction

Two response shapes are role-aware:

- **Worker identity views**: VIEWER receives
  `{worker_id, registration_status}` only — no certificate identity,
  fingerprint, signing key id, timestamps, or revocation reason.
  RESEARCHER and ADMIN receive the full `WorkerIdentitySummary`.
  Confirmed live: a VIEWER's response body does not contain the
  worker's real certificate fingerprint; an ADMIN's does.
- **Audit records**: `security.audit.read_detailed` (ADMIN only) gets
  the full `observability.AuditEvent` (actor email, full `Details`
  map). `security.audit.read` without `read_detailed` (RESEARCHER) gets
  `{id, timestamp, actor_role, action, resource_type, resource_id,
  outcome}` — no email, no free-form details. Confirmed live: a
  RESEARCHER's audit response does not contain a mutation's `reason`
  text; an ADMIN's does.

Not redacted (all-or-nothing instead): worker signing-key listings and
coordinator signing-key listings — RESEARCHER/ADMIN see the full
record, VIEWER is denied the endpoint entirely (403), rather than
receiving a partially redacted version. This is a real, deliberate
scope choice (fewer response shapes to keep correct) documented here
rather than silently narrowed.

## What is deferred

- Per-user explicit SERVICE scope grants (no plumbing exists — see
  above).
- Redaction for worker/coordinator signing-key listings (currently
  binary: full access or 403).
- A web UI that would actually need to render these role-scoped views
  for a human — the Web Security Center itself is out of scope for
  this slice.
