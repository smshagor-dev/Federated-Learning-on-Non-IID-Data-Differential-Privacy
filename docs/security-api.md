# Security HTTP API

**Status: implemented and live-validated over real mTLS in Docker
Compose (`go/internal/transport/httpapi/security_handlers.go`).**

## Endpoints

```text
GET  /api/v1/security/transport
GET  /api/v1/security/trust-model

GET  /api/v1/security/workers
GET  /api/v1/security/workers/{workerId}

POST /api/v1/security/workers/{workerId}/suspend
POST /api/v1/security/workers/{workerId}/activate
POST /api/v1/security/workers/{workerId}/revoke

GET  /api/v1/security/workers/{workerId}/signing-keys
POST /api/v1/security/workers/{workerId}/signing-keys/{keyId}/revoke

GET  /api/v1/security/coordinator/signing-keys
POST /api/v1/security/coordinator/signing-keys/rotate
POST /api/v1/security/coordinator/signing-keys/{keyId}/revoke

GET  /api/v1/security/events
GET  /api/v1/security/audit
```

Every route requires a bearer token (any of ADMIN/RESEARCHER/VIEWER/
SERVICE authenticate successfully); the real authorization decision is
made inside each handler via
[the permission model](security-permission-model.md), not at route
registration. An unauthenticated request gets `401`; an authenticated
but unauthorized one gets `403` with the specific missing permission
named in the error body — confirmed live for both cases.

## Request/response shapes

All responses are the Go types defined in
`go/internal/coordinator/security_client.go`, JSON-encoded — never a
raw protobuf message serialized directly (per the specification's
explicit requirement). None of them carry a private key, a raw
signature/nonce, a full signed envelope, or any other secret material
— they are already-safe wire messages one layer removed from the
protobuf types the C++ coordinator returns.

Two GET endpoints (`GET .../workers`, `GET .../workers/{workerId}`) and
one (`GET .../audit`) return a role-redacted projection instead of the
full struct for VIEWER/RESEARCHER respectively — see
[security-permission-model.md](security-permission-model.md)'s
"Redaction" section.

## Mutation safety

Every mutating endpoint (`suspend`/`activate`/`revoke`/
`signing-keys/{keyId}/revoke`/`rotate`/coordinator `revoke`) accepts:

- `reason` (JSON body)
- `request_id`, `trace_id` (JSON body, forwarded to the coordinator RPC)
- an `Idempotency-Key` HTTP header, or an `idempotency_key` JSON body
  field as a fallback

**Coordinator signing-key rotation requires an idempotency key** — a
request without one is rejected with `400` before it ever reaches the
coordinator, because a rotation mints a genuinely fresh Ed25519 key
every time it actually executes; without an idempotency key, a client
retry would produce a second, different key rather than being safely
replayable. Every other mutation's idempotency key is optional (the
underlying operation is naturally idempotent by target state — e.g.
suspending an already-suspended worker is a no-op `changed: false`).

An in-memory `idempotencyCache` (`go/internal/transport/httpapi/security_handlers.go`)
backs this: a repeated request with the same `Idempotency-Key` returns
the identical cached response rather than re-executing the mutation.
Live-validated: a rotation request retried with the same
`Idempotency-Key` returned the byte-identical response — the same new
key, not a freshly minted second one.

**Known, disclosed trade-off**: the cache is in-memory only (lost on
process restart, unlike the C++ coordinator's own file-persisted
`IdempotencyStore` backing the two coordinator-signing-key RPCs
specifically) and uses one mutex held across the entire mutation
execution — correctness over throughput, appropriate for a research
control plane, not a production API expecting concurrent mutation
volume.

## Audit

`GET /api/v1/security/audit` is backed by the **existing**,
general-purpose Go `observability.AuditRepository` (already used for
model/dataset/run domain actions), filtered to `resource_type` values
starting with `"security"`. Every security mutation handler calls
`AuditService.Record` with the real actor, action (reusing the
permission-constant names, e.g. `security.workers.suspend`), resource,
outcome, and a details map (reason/request_id/trace_id/idempotency
fields/changed). **As of the Security Events, Metrics, and Durable
Audit Journal slice**, `GET /api/v1/security/audit` reads from a
**new**, security-specific, real-paginated/filterable `SecurityAuditJournal`
instead — the general-purpose `AuditRepository` described above keeps
being written to unchanged (additive, not replaced), and continues to
serve every non-security domain. See
[security-audit-journal.md](security-audit-journal.md) for the full
design.

## Events

**As of the Security Events, Metrics, and Durable Audit Journal
slice**, `GET /api/v1/security/events` is real: it merges this Go
process's own locally-emitted events with the coordinator's own durable
journal (via the new `ListSecurityEvents` RPC), role-redacted, cursor-
paginated, filterable by `min_severity`/`subject_type`/`event_type`. No
longer `501`. See [security-events.md](security-events.md) for the full
schema, event-type registry, and producer wiring, and
[known-limitations.md](known-limitations.md) for what remains partial
(not every operation in the registry emits yet; Python-worker events
are not centralized; no background poller relays C++-origin event
counts into Prometheus).

## Go coordinator client

`go/internal/coordinator/security_client.go` defines a `SecurityClient`
interface (embedded into the main `Client` interface) with 12 methods,
implemented for real by `GrpcClient` (actual gRPC calls against the
coordinator's `ADMIN_CONTROL` RPCs) and deterministically by
`MockClient` (in-memory, for Go-side tests that don't need a live
coordinator). A dedicated `mapSecurityGrpcError` distinguishes
`PermissionDenied`/`NotFound`/`FailedPrecondition` gRPC codes into
distinct Go error sentinels (`ErrPermissionDenied`/`ErrNotFound`/
`ErrFailedPrecondition`), mapped to `403`/`404`/`409` respectively at
the HTTP layer — deliberately a separate function from the pre-existing
`mapGrpcError` (which maps `NotFound` to the run-specific
`ErrRunNotFound`, a different and still-correct meaning for its own
callers).

## Docker Compose validation

`infra/compose/docker-compose.security.yml` is an override file (not a
replacement for `docker-compose.dev.yml`) that mounts real dev-PKI
certificates into the `coordinator` and `api` services and switches
both to `FL_TRANSPORT_MODE=mtls`. **Neither compose file had ever
mounted any PKI material before this slice** — confirmed by direct
inspection at the start of this work. `python-worker` and `web` are
deliberately excluded from this override: `python-worker`'s own
`__main__.py`/`configuration.py` has never been wired with TLS
environment variables (a known, separately-disclosed gap from the
Coordinator-Signed Tasks slice), so bringing it up alongside a
`mtls`-required coordinator would break its existing plaintext
connection.

```bash
docker compose -f infra/compose/docker-compose.dev.yml \
                -f infra/compose/docker-compose.security.yml \
                up -d postgres redis coordinator api
```

Live-validated this way (real mTLS both directions, real coordinator
process, real Go API process): transport status, trust model,
coordinator signing-key listing/rotation/revocation (including a real
Ed25519 keygen and a real grace-period transition), a real
Ed25519-signed `RegisterWorker` call (via a scratch Python script using
`fl_platform.security.signing_identity`/`capability_statement`)
followed by worker listing/detail/suspend through the new HTTP surface,
role-based redaction (VIEWER worker detail, RESEARCHER audit read),
permission denial (403) for VIEWER/RESEARCHER attempting mutations
outside their role, 404 for an unknown worker, 401 for no bearer token,
HTTP-layer idempotent replay for a coordinator-key rotation, a real
audit trail across every mutation performed, the events endpoint's
honest `501`, and — independently, via a direct gRPC call bypassing
Go entirely — a worker identity rejected with `PERMISSION_DENIED` from
the new `GetTransportSecurityStatus` RPC, confirming the gRPC-layer
`ADMIN_CONTROL` gate is unchanged and still enforced beneath the new Go
permission layer. See
[security-operations-report.md](security-operations-report.md) for the
full, numbered list of checks and their results.

## Secure User-Level DP privacy API

The Secure User-Level DP Operations, Observability, and Release
Evidence slice adds 5 read-only routes under
`/api/v1/secure-aggregation/privacy/*` (`status`, `health`, `budget`,
`rounds`, `rounds/{roundId}`), each gated by its own responsibility-
named permission (`security.secure_user_dp.*.read` — see
`go/internal/security/permissions.go`) rather than the broader
`securityRoles` set this file's other routes share at the mux layer;
the fine-grained ADMIN/RESEARCHER/VIEWER/SERVICE split happens inside
each handler exactly like every route documented above. Full detail,
including the deliberate `rounds/{roundId}` vs. the task's suggested
`rounds/{sessionId}` deviation, lives in
[secure-user-level-operations-audit.md](secure-user-level-operations-audit.md)
and `go/internal/transport/httpapi/secure_user_level_privacy.go`'s own
header comment.

## What is deferred

- The Web Security Center (no web routes/components exist).
- A formal, schema-versioned security-event type and any event stream.
- Prometheus metrics for this HTTP surface.
- A durable, security-specific audit journal (the existing general
  Go audit repository is reused, not replaced).
- CI gates specific to this surface.
- Per-user explicit SERVICE-role scope grants (no plumbing exists — see
  [security-permission-model.md](security-permission-model.md)).
- Including `python-worker`/`web` in the mTLS Compose override.
