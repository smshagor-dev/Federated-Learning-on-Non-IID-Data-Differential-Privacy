# Security Observability Inventory

**Status: Updated in place — Security Events, Metrics, and Durable Audit
Journal slice landed.** Status-column entries marked `EVENT+JOURNAL` or
`EVENT+AUDIT+JOURNAL` below reflect this slice's real, live-validated
wiring (see [security-events.md](security-events.md),
[security-audit-journal.md](security-audit-journal.md), and
[security-runtime-validation.md](security-runtime-validation.md));
everything else in this table reflects the pre-slice baseline and
remains an accurate account of what is still not wired — this slice
covers a representative, documented subset of operations, not every row.
This is the authoritative table required by Work Package A: for every
security-sensitive operation in the platform, what currently records it
(structured log, in-process domain event, durable persisted event, audit
record, metric), what identifiers it carries, its severity, its sensitive
fields, and its real (not aspirational) implementation status as of this
audit.

Legend for **Status**: `NONE` (no observable record at all beyond maybe a
gRPC error), `LOG` (structured stderr line only — `structured_log.cpp`'s
`log_event`), `ENUM` (a `CoordinatorEventType` enum value exists but is not
routed through `EventBus`, so effectively still log-only), `PARTIAL` (some
but not all of log/event/audit/metric exist).

## Transport

| Operation | Source | Structured log | Domain event | Persisted event | Audit record | Metric | Request ID | Trace ID | Severity | Sensitive fields | Redaction needed | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Coordinator mTLS startup | C++ | No | No | No | No | No | N/A | No | INFO | None | None | NONE |
| Go→C++ mTLS connection | Go | No | No | No | No | No | No | No | INFO | None | None | NONE |
| Python→C++ mTLS connection | Python | No | No | No | No | No | No | No | INFO | None | None | NONE |
| Certificate rejection | C++ | gRPC error only | No | No | No | No | No | No | HIGH | Cert fingerprint | Fingerprint | NONE |
| URI SAN mismatch | C++ | gRPC error only | No | No | No | No | No | No | HIGH | SAN value | SAN value | NONE |
| Certificate fingerprint rejection | C++ | gRPC error only | No | No | No | No | No | No | HIGH | Fingerprint | Fingerprint | NONE |
| Certificate expiry | C++ | gRPC error only | No | No | No | No | No | No | WARNING | Expiry time | None | NONE |
| Insecure-development startup | C++ | Startup banner (stdout) | No | No | No | No | N/A | No | WARNING | None | None | LOG |

## Worker identity

| Operation | Source | Structured log | Domain event | Persisted event | Audit record | Metric | Request ID | Trace ID | Severity | Sensitive fields | Redaction needed | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Worker registration | C++ | No | No | No | No | No | No | No | INFO | Cert fingerprint, signing key | Fingerprint, key | NONE |
| Worker identity rejection | C++ | gRPC error only | No | No | No | No | No | No | WARNING | Rejection reason | None | NONE |
| Worker suspension | C++, Go | Yes (`log_event`) | `kWorkerSuspended` (enum) | **Yes** (`SecurityEventJournal`, both C++ and Go) | Yes (`AuditRepository` + **new** `SecurityAuditJournal`) | No | Yes | Yes | WARNING | worker_id, reason | None | EVENT+AUDIT+JOURNAL |
| Worker activation | C++, Go | Yes | `kWorkerActivated` (enum) | **Yes** | Yes + **new journal** | No | Yes | Yes | INFO | worker_id | None | EVENT+AUDIT+JOURNAL |
| Worker revocation | C++, Go | Yes | `kWorkerRevoked` (enum) | **Yes** | Yes + **new journal** | No | Yes | Yes | HIGH | worker_id, reason | None | EVENT+AUDIT+JOURNAL |
| Active lease cancellation | C++, Go | Yes | `kTaskCanceledByRevocation` (enum) | **Yes** (`ACTIVE_LEASE_CANCELED`) | No | No | No | Yes | INFO | task_id, worker_id | None | EVENT+JOURNAL |

## Worker signing keys

| Operation | Source | Structured log | Domain event | Persisted event | Audit record | Metric | Request ID | Trace ID | Severity | Sensitive fields | Redaction needed | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Legacy key migration | C++ | Yes (`SIGNING_KEY_MIGRATED`) | No | No | No | No | No | No | INFO | signing_key_id | key id | LOG |
| Initial key registration | C++ | No | No | No | No | No | No | No | INFO | signing_key_id | key id | NONE |
| Key rotation request | C++ | No | No | No | No | No | No | No | INFO | signing_key_id | key id | NONE |
| Key rotation acceptance | C++ | No | No | No | No | No | No | No | INFO | signing_key_id (old+new) | key id | NONE |
| Key rotation rejection | C++ | gRPC error only | No | No | No | No | No | No | WARNING | reason | None | NONE |
| Grace-period transition | C++ | No | No | No | No | No | No | No | INFO | signing_key_id | key id | NONE |
| Key expiry | C++ | No (lazy check only) | No | No | No | No | No | No | WARNING | signing_key_id | key id | NONE |
| Key revocation | C++, Go | No (C++ has `log_event`) | No | **Yes** (`WORKER_KEY_REVOKED`) | Yes + **new journal** | No | Yes (Go) | Yes | HIGH | signing_key_id, reason | key id | EVENT+AUDIT+JOURNAL |
| Message rejected by key state | C++ | gRPC error only | No | No | No | No | No | No | WARNING | signing_key_id, worker_id | key id | NONE |

## Signed worker messages

| Operation | Source | Structured log | Domain event | Persisted event | Audit record | Metric | Request ID | Trace ID | Severity | Sensitive fields | Redaction needed | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Capability accepted/rejected | C++ | No | `kWorkerRegistered` only on accept | No | No | No | No | No | INFO/WARNING | worker_id | None | NONE |
| Heartbeat accepted/rejected | C++ | No | No | **Yes** (`HEARTBEAT_ACCEPTED`/mapped rejection type) | No | No | No | No | INFO/WARNING | worker_id | None | EVENT+JOURNAL |
| Client-result accepted/rejected | C++ | No | `kClientResultAccepted`/`kClientResultRejected` (per-run `EventBus` only) | No | No | No | No | No | INFO/WARNING | client_id, worker_id | None | ENUM |
| Privacy-record accepted/rejected | C++ | No | No | No | No | No | No | No | WARNING | client_id, worker_id, epsilon | epsilon (never combined) | NONE |
| Signature verification failed | C++/Python | gRPC error only | No | No | No | No | No | No | HIGH | worker_id, signing_key_id | key id | NONE |
| Payload-hash mismatch | C++/Python | gRPC error only | No | No | No | No | No | No | HIGH | worker_id | None | NONE |
| Replay rejected | C++ | gRPC error only | No | No | No | No | No | No | HIGH | worker_id, stream | None | NONE |
| Sequence rejected | C++ | gRPC error only | No | No | No | No | No | No | WARNING | worker_id, stream | None | NONE |
| Message expired | C++/Python | gRPC error only | No | No | No | No | No | No | WARNING | worker_id | None | NONE |

## Coordinator tasks

| Operation | Source | Structured log | Domain event | Persisted event | Audit record | Metric | Request ID | Trace ID | Severity | Sensitive fields | Redaction needed | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Coordinator signing identity load | C++ | No | No | No | No | No | No | No | INFO | signing_key_id | key id | NONE |
| Task signing | C++ | No | No | No | No | No | No | No | INFO | task_id | None | NONE |
| Task-signing failure | C++ | No | No | No | No | No | No | No | CRITICAL | reason | None | NONE |
| Task issuance | C++ | No | `kTaskAssigned` (per-run `EventBus`) | No | No | No | No | No | INFO | task_id, worker_id | None | ENUM |
| Task verification | Python | No | No | No | No | No | No | No | INFO | task_id | None | NONE |
| Task rejection (16 reasons) | Python | No | No | **Yes** (`COORDINATOR_TASK_REJECTED` + specific mapped type) | No | No | No | No | WARNING/HIGH | task_id, reason code | None | EVENT+JOURNAL |
| Task replay rejection | Python | No | No | **Yes** (`COORDINATOR_TASK_REPLAY_REJECTED`) | No | No | No | No | HIGH | task_id, worker_id | None | EVENT+JOURNAL |
| Duplicate-execution prevention | Python | No | No | **Yes** (`DUPLICATE_TASK_EXECUTION_BLOCKED`) | No | No | No | No | WARNING | task_id, attempt | None | EVENT+JOURNAL |
| Accepted-task recovery | Python | No | No | No | No | No | No | No | WARNING | task_id | None | NONE |
| Task reissue | C++ | No | `kTaskAssigned` (same enum, higher attempt) | No | No | No | No | No | INFO | task_id, attempt | None | ENUM |

## Security administration (Go HTTP layer)

| Operation | Source | Structured log | Domain event | Persisted event | Audit record | Metric | Request ID | Trace ID | Severity | Sensitive fields | Redaction needed | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Permission denial | Go | No | No | **Yes** (`SECURITY_PERMISSION_DENIED`, one call site covers every route) | No | **Yes** (`fl_security_events_total`) | No | No | WARNING | actor role, permission | None | EVENT+JOURNAL |
| Idempotency replay | Go | No | No | **Yes** (`IDEMPOTENCY_REPLAY_ACCEPTED`, coordinator-key rotation) | No | **Yes** | Yes (body) | Yes (body) | INFO | idempotency key | key value | EVENT+JOURNAL |
| Idempotency conflict | Go | No | No | **Yes** (`SECURITY_MUTATION_REJECTED`) | No | **Yes** | Yes | Yes | WARNING | idempotency key | key value | EVENT+JOURNAL |
| Worker lifecycle mutation | Go | No | No | **Yes** | Yes (`AuditRepository` + **new** `SecurityAuditJournal`) | **Yes** | Yes | Yes | INFO/HIGH | worker_id, reason | reason (redacted view) | EVENT+AUDIT+JOURNAL |
| Coordinator-key administration | Go | No | No | **Yes** (`SECURITY_MUTATION_ACCEPTED`/`_REJECTED`) | Yes + **new journal** | **Yes** | Yes | Yes | HIGH | signing_key_id, reason | reason | EVENT+AUDIT+JOURNAL |
| Detailed audit access | Go | No | No | **Yes** (`SECURITY_AUDIT_ACCESSED`, meta-audit) | No (not itself audited — by design, avoids recursive feedback) | **Yes** | No | No | INFO | actor role | None | EVENT+JOURNAL |

## Cross-cutting gaps confirmed by this audit (pre-slice baseline)

- No formal, cross-service, schema-versioned event type existed anywhere.
- No durable, queryable event history — `EventBus` is per-run, in-memory,
  capacity-bounded, and worker-lifecycle/key-lifecycle events never reach
  it at all (only the 27-value `CoordinatorEventType` enum + ad hoc stderr
  logging). **`EventBus` itself is unchanged by this slice** — the new
  journals are separate, global, persistent stores, not an extension of it.
- The only durable audit trail was the general-purpose Go `AuditRepository`,
  which only sees Go-mediated HTTP mutations.
- No Prometheus security metrics existed anywhere (Go, C++, or Python).
- `docs/known-limitations.md` and `plan.md` already disclosed this
  accurately at the time; no discrepancy was found between what those
  documents claimed and what the code did for this specific area.

## What this slice actually closed (see status column above: `EVENT+JOURNAL`/`EVENT+AUDIT+JOURNAL`)

- A shared, versioned, cross-language event schema, with real durable
  journals (C++, Python, Go) — see [security-events.md](security-events.md).
- A new, security-specific, paginated/filterable durable audit journal,
  additive to the pre-existing `AuditRepository` — see
  [security-audit-journal.md](security-audit-journal.md).
- Low-cardinality Prometheus counters in Go and Python — see
  [security-metrics.md](security-metrics.md).
- A real `GET /api/v1/security/events` (no longer `501`).

## What remains open after this slice

- Most rows above are still `NONE`/`LOG`/`ENUM` — this slice wired a
  representative, documented subset of operations (see
  [known-limitations.md](known-limitations.md)'s "Security Events,
  Metrics, and Durable Audit Journal slice" section for the exact list),
  not every row in this table.
- No native C++ Prometheus endpoint; no background poller relaying
  C++/Python event counts into Go's Prometheus counters.
- Python-worker events are not shipped to the coordinator/Go.

This document is updated in place (status column only), rather than
duplicated into a second "after" table, so it stays the single source of
truth — the next slice that wires additional rows should update this
file again rather than writing a new one.
