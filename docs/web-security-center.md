# Web Security Center

**Status: Implemented and validated.** Web Security Center, Event
Centralization, and Security CI slice, Work Packages A-K. See
[security-event-centralization.md](security-event-centralization.md)
for the backend event-relay pipeline this UI surfaces, and
[known-limitations.md](known-limitations.md) for disclosed gaps.

## Routes

| Route | Component | Backend endpoint(s) |
| --- | --- | --- |
| `/security` | `SecurityOverviewConsole` | `GET /api/v1/security/overview`, `GET /api/v1/security/events/sources` |
| `/security/workers` | `SecurityWorkersConsole` | `GET /api/v1/security/workers` |
| `/security/workers/[workerId]` | `SecurityWorkerDetailConsole` | `GET /api/v1/security/workers/{id}`, `GET /api/v1/security/workers/{id}/signing-keys`, `POST .../suspend\|activate\|revoke`, `POST .../signing-keys/{keyId}/revoke`, `GET /api/v1/security/events` (client-filtered by worker_id for the recent-activity panel) |
| `/security/coordinator-keys` | `SecurityCoordinatorKeysConsole` | `GET /api/v1/security/coordinator/signing-keys`, `POST .../rotate`, `POST .../{keyId}/revoke` |
| `/security/events` | `SecurityEventsConsole` | `GET /api/v1/security/events` |
| `/security/audit` | `SecurityAuditConsole` | `GET /api/v1/security/audit` |

Every page is a thin server component (`app/security/**/page.tsx`,
`dynamic = "force-dynamic"`) wrapping `AppShell` around one client
console component. Unlike `app/audit/page.tsx`, none of these fetch
seed data server-side: every `/api/v1/security/*` route requires a
Bearer token, and the token only exists in the browser's `localStorage`
session (`lib/use-stored-session.ts`), so there is no unauthenticated
data to prefetch.

## Role visibility

Real enforcement is server-side (`go/internal/security/permissions.go`).
The client mirrors it only for *what renders* (never as the actual
security boundary — a client-side check can't stop a direct API call):

- **ADMIN**: full read + every mutation (suspend/activate/revoke
  workers, revoke worker signing keys, rotate/revoke coordinator
  signing keys).
- **RESEARCHER**: full read (redacted per Go's `read_detailed`
  permission on events/audit), no mutations.
- **VIEWER**: aggregate reads only. Coordinator signing-key identifiers
  are blanked server-side (`security_overview.go` clears
  `ActiveKeyID`/`GracePeriodKeyID` for `RoleViewer`); worker identity
  detail is redacted to `{worker_id, registration_status}` only.
- **SERVICE**: no `security.overview.read`/`security.event_sources.read`
  grant — the overview and event-sources pages show an explicit
  "not available for this role" message rather than a generic error.

## Admin mutations: reason + confirmation + idempotency + safe retry

Every mutating action (`ConfirmDialog`, `components/confirm-dialog.tsx`)
requires, before the Confirm button is even enabled:

1. A non-empty, operator-written **reason** (recorded in the security
   audit journal via the mutation's `reason` field).
2. An explicit **acknowledgment checkbox** ("I have read the
   consequences above and confirm this action") alongside a
   route-specific **consequence explanation** (e.g. revoking a
   coordinator signing key: "task issuance halts until a new key is
   rotated in").
3. A fresh **Idempotency-Key** (`crypto.randomUUID()`), minted once per
   dialog *open* and reused across every confirm click within that same
   open session — so a failed submission followed by a retry (without
   closing the dialog) carries the identical key, letting the server-
   side idempotency cache (`securityIdempotency` in
   `go/internal/transport/httpapi/security_handlers.go`) treat it as
   the same request rather than executing the action twice. This is
   the "safe retry" property.

Coordinator signing-key rotation additionally requires the operator to
supply `expected_current_signing_key_id` (compare-and-set against the
live active key) — the Go API rejects rotation outright without an
`Idempotency-Key`, since (unlike worker lifecycle mutations, which are
naturally idempotent by target state) a rotation mints a fresh Ed25519
key every time it actually executes.

## Live updates

Polling, not a new SSE stream — see the plan's "Design decisions"
section for the full rationale (the existing SSE mechanism,
`subscribeToCoordinatorEvents`, is strictly per-run-scoped and cannot
serve a global security feed without an entirely new handler; this
codebase already has a "poll on a fixed interval" precedent in
`PrivacyCenterPanel`). Every console polls its endpoint(s) every 5
seconds via `setInterval`, cancels the in-flight request via
`AbortController` on unmount/dependency change, and — for the event and
audit explorers specifically — maintains a **bounded ring buffer**
(`MAX_BUFFERED_EVENTS`/`MAX_BUFFERED_RECORDS = 500`, drop-oldest) so a
long-lived browser tab's memory usage never grows unbounded.

## Client API layer (`lib/security-api.ts`)

A separate module from the older `lib/api.ts`, not an extension of it —
every function here accepts an optional caller-supplied `AbortSignal`
(combined with an 8s internal timeout via `AbortSignal.any`) and
mutation functions require an `idempotencyKey`, neither of which exists
on `lib/api.ts`'s older functions. Two contracts, matching
`lib/api.ts`'s existing split:

- **List/detail reads return `T | undefined`** on failure or a
  non-`2xx` response — and, critically, list reads (`listSecurityWorkersWithToken`,
  `listSecurityEventsWithToken`, etc) return `undefined` (not `[]`) on
  failure, preserving the distinction between "coordinator unreachable"
  and "genuinely empty" so a console can render an accurate
  "unavailable" banner instead of a misleading empty table.
- **Mutations throw** on failure, with the server's `error` message
  where available.

## What this does not do

- No new C++/Go gRPC streaming for the web layer — the existing
  per-run `StreamRunEvents` RPC and its Go relay are untouched; the
  Security Center's "live updates" are polling only (see above).
- No browser end-to-end automation (no Playwright/Cypress in this
  repository) — verified via Vitest component/API-layer tests, `npm run
  build`, `npm run typecheck`, `npm run lint`. See
  [known-limitations.md](known-limitations.md).
- No chart library — Grafana (already in the compose stack) is the
  intended tool for time-series visualization of the new Prometheus
  metrics; no Grafana dashboard was built this slice (see
  [known-limitations.md](known-limitations.md)).
