# Security Events

**Status: Implemented and validated (C++, Python, Go).** Security
Events, Metrics, and Durable Audit Journal slice. See
[security-observability-inventory.md](security-observability-inventory.md)
for the per-operation "before" audit this slice closes, and
[security-runtime-validation.md](security-runtime-validation.md) for how
every claim below was actually checked.

## Schema (Work Package B)

One shared record shape, `schema_version = 1`, mirrored field-for-field in:

- C++: `cpp/coordinator/include/fl_coordinator/security_event.hpp`
- Python: `python/src/fl_platform/security/security_event.py`
- Go: `go/internal/observability/security_event.go`

Fields: `schema_version`, `event_id`, `event_type`, `severity`,
`timestamp`, `source_service`, `source_component`, `actor_type`,
`safe_actor_id`, `subject_type`, `safe_subject_id`, `worker_id`,
`run_id`, `round_id`, `task_id`, `safe_signing_key_id`, `request_id`,
`trace_id`, `outcome`, `reason_code`, `safe_details`,
`payload_checksum`.

`event_id`/`timestamp`/`payload_checksum` are assigned by the journal on
emit, never by the caller (mirrors `EventBus::publish`'s existing
"fills in event_id/timestamp if not already set" contract).

### Enums (stable string values, not language-native enums where that would break cross-language JSON)

- **Severity**: `INFO`, `WARNING`, `HIGH`, `CRITICAL`
- **Outcome**: `ACCEPTED`, `REJECTED`, `COMPLETED`, `FAILED`, `BLOCKED`, `CANCELED`
- **Actor type**: `USER`, `SERVICE`, `WORKER`, `COORDINATOR`, `SYSTEM`
- **Subject type**: `TRANSPORT`, `CERTIFICATE`, `WORKER_IDENTITY`,
  `WORKER_SIGNING_KEY`, `COORDINATOR_SIGNING_KEY`, `CAPABILITY`,
  `HEARTBEAT`, `CLIENT_RESULT`, `PRIVACY_RECORD`, `TRAINING_TASK`,
  `REPLAY_STATE`, `TASK_LEASE`, `AUDIT_QUERY`, `SECURITY_MUTATION`

### Bounds (enforced by `validate_security_event`/`ValidateSecurityEvent`, all three languages)

- `reason_code` ≤ 128 characters
- `safe_details` ≤ 10 keys
- each `safe_details` value ≤ 256 characters
- `schema_version` must equal 1; `severity`/`outcome`/`actor_type`/`subject_type` must be recognized values

A failing event is logged locally and **dropped**, never thrown/raised
back to the caller — observability must never be allowed to break a
real security decision path. Validated by C++
`security_event_test.cpp`, Python `test_security_event.py`, Go
`security_event_test.go`.

### What is never included

Private keys, raw signatures, raw nonces, raw certificate PEM, full
signed payloads, tensor values, dataset samples, privacy noise,
secret-share data. Every `safe_*` field name is a reminder: put an
opaque identifier there, never a credential.

## Canonical serialization and payload checksum (Work Package B)

`payload_checksum` is an FNV-1a 64-bit hex digest (matching every other
checksum in this codebase's persistence layer — corruption/tamper-in-
transit detection only, explicitly **not** a cryptographic MAC) computed
over a canonical, key-sorted JSON encoding of every field **except**
`event_id`/`timestamp`/`payload_checksum` itself.

A real, independently-generated cross-language golden fixture (not a
tautological self-check — each side computed its own output separately
from the same fixed input) proves byte-for-byte parity across all three
languages for a fixed test event:

```
{"actor_type":"SERVICE","event_type":"WORKER_SUSPENDED","outcome":"COMPLETED",
"reason_code":"administrative_suspension","request_id":"","round_id":0,"run_id":"",
"safe_actor_id":"go-api","safe_details":{},"safe_signing_key_id":"",
"safe_subject_id":"worker-1","schema_version":1,"severity":"WARNING",
"source_component":"worker_registry","source_service":"coordinator",
"subject_type":"WORKER_IDENTITY","task_id":"","trace_id":"","worker_id":"worker-1"}
```
checksum: `2a1507521d258521`

Reproduced identically by `cpp/coordinator/tests/security_event_test.cpp`,
`python/tests/test_security_event.py`'s
`test_cross_language_golden_fixture`, and
`go/internal/observability/security_event_test.go`'s
`TestCrossLanguageGoldenFixture`.

The C++ encoder deliberately uses a simplified per-byte (not full
UTF-8-aware) escape for non-ASCII bytes, unlike
`capability_statement_verifier.cpp`'s encoder — every field in this
schema is expected to be an ASCII identifier or reason code in
practice, and this schema's checksum is not used as a cryptographic
signature input, only for corruption detection. Documented as a known,
disclosed deviation, not an oversight.

## Event type registry (Work Package C)

All 55 required event types are defined with a default severity
mapping (`default_severity`/`DefaultSeverity`/Python module-level
mapping, kept in sync by hand across the three languages — no shared
codegen exists for this mapping):

| Category | Event types |
|---|---|
| Transport | `TRANSPORT_MTLS_STARTED`, `TRANSPORT_MTLS_FAILED`, `TRANSPORT_INSECURE_DEVELOPMENT_STARTED`, `PEER_CERTIFICATE_ACCEPTED`, `PEER_CERTIFICATE_REJECTED`, `CERTIFICATE_IDENTITY_MISMATCH`, `CERTIFICATE_FINGERPRINT_REJECTED`, `CERTIFICATE_EXPIRED` |
| Worker identity | `WORKER_REGISTERED`, `WORKER_REGISTRATION_REJECTED`, `WORKER_SUSPENDED`, `WORKER_ACTIVATED`, `WORKER_REVOKED`, `WORKER_STATUS_RPC_REJECTED`, `ACTIVE_LEASE_CANCELED` |
| Worker signing keys | `WORKER_KEY_MIGRATED`, `WORKER_KEY_REGISTERED`, `WORKER_KEY_ROTATION_REQUESTED`, `WORKER_KEY_ROTATION_ACCEPTED`, `WORKER_KEY_ROTATION_REJECTED`, `WORKER_KEY_GRACE_STARTED`, `WORKER_KEY_EXPIRED`, `WORKER_KEY_REVOKED`, `MESSAGE_REJECTED_BY_KEY_STATE` |
| Signed worker messages | `CAPABILITY_ACCEPTED`, `CAPABILITY_REJECTED`, `HEARTBEAT_ACCEPTED`, `HEARTBEAT_REJECTED`, `CLIENT_RESULT_ACCEPTED`, `CLIENT_RESULT_REJECTED`, `PRIVACY_RECORD_ACCEPTED`, `PRIVACY_RECORD_REJECTED`, `SIGNATURE_VERIFICATION_FAILED`, `PAYLOAD_HASH_MISMATCH`, `MESSAGE_REPLAY_REJECTED`, `MESSAGE_SEQUENCE_REJECTED`, `MESSAGE_EXPIRED` |
| Coordinator tasks | `COORDINATOR_SIGNING_KEY_LOADED`, `COORDINATOR_SIGNING_KEY_REJECTED`, `COORDINATOR_TASK_SIGNED`, `COORDINATOR_TASK_SIGNING_FAILED`, `COORDINATOR_TASK_ISSUED`, `COORDINATOR_TASK_VERIFIED`, `COORDINATOR_TASK_REJECTED`, `COORDINATOR_TASK_REPLAY_REJECTED`, `DUPLICATE_TASK_EXECUTION_BLOCKED`, `ACCEPTED_TASK_RECOVERY_STARTED`, `ACCEPTED_TASK_RECOVERY_COMPLETED`, `TASK_REISSUED` |
| Administration | `SECURITY_PERMISSION_DENIED`, `SECURITY_MUTATION_ACCEPTED`, `SECURITY_MUTATION_REJECTED`, `IDEMPOTENCY_REPLAY_ACCEPTED`, `IDEMPOTENCY_CONFLICT_REJECTED`, `SECURITY_AUDIT_ACCESSED` |

The Secure User-Level DP Operations, Observability, and Release
Evidence slice adds a further 12
`SECURE_USER_LEVEL_DP_*` types (configuration accepted/rejected,
budget reserved/exhausted, clipping applied, attestation accepted/
rejected, noise applied, accounting committed, round completed,
finalization conflict, health degraded) — see
[secure-user-level-operations-audit.md](secure-user-level-operations-audit.md)'s
scope statement for the exact list and which call sites emit each one.

## Producer interface (Work Package D)

C++: `SecurityEventSink` (pure virtual `emit(SecurityEvent)`),
implemented by `SecurityEventJournal`; a `NullSecurityEventSink` exists
for call sites/tests that don't wire a real journal. Python/Go don't
need a separate interface type — `SecurityEventJournal.emit()`/
`(*SecurityEventJournal).Emit()` are called directly, matching each
language's existing convention (no interface-heavy style elsewhere in
this codebase for single-implementation stores).

## Durable event journal (Work Package D/E)

One journal type per language (`cpp/coordinator/include/fl_coordinator/security_event_journal.hpp`,
`python/src/fl_platform/security/security_event_journal.py`,
`go/internal/observability/security_event_journal.go`), all sharing the
same design:

- **Format**: JSON Lines — one canonical, key-sorted JSON object per
  line, each self-checksummed. Chosen over the tab-separated format
  used by this codebase's small trust-critical registries
  (`IdempotencyStore`, `ReplayProtectionStore`, etc.) because a journal
  is unbounded/append-only and must be diffable/parseable by more than
  one language; C++ needed a small, hand-written, strictly-scoped JSON
  parser for this (`security_journal_json.hpp`/`.cpp` — not a
  general-purpose JSON library; Python/Go use their standard `json`
  packages).
- **Corruption policy deliberately differs from the registries**: those
  stores throw on any corruption because a silently-wrong trust
  decision is dangerous. This journal instead **skips and recovers** —
  a malformed or checksum-failing line is dropped and counted
  (`recovered_line_count()`), loading continues. Availability over
  strictness, since this is an observability artifact, not a trust
  decision.
- **Rotation/retention**: size-based rotation (default 10 MiB),
  atomic rename to `.1`/`.2`/... up to a configurable retention count
  (default 5). `list()` serves only the currently-active file — rotated
  files remain on disk for out-of-band inspection but are not queried.
  Disclosed scope limit, not an oversight.
- **Cursor pagination + filtering**: `after_event_id`/`limit`/
  `min_severity`/`subject_type`/`event_type`.

Validated (all three languages): emit+list round-trip, restart
persistence, cursor pagination, severity filter, invalid-event-dropped-
not-thrown, corruption recovery (hand-corrupted trailing lines skipped,
valid records survive), rotation-at-threshold, retention-count
enforcement.

## Cross-service architecture

- **C++ coordinator** owns the authoritative durable journal and gains
  one new `ADMIN_CONTROL` RPC, `ListSecurityEvents` (cursor + filters),
  following the exact shape of existing admin RPCs
  (`ListWorkerIdentities`, `GetSecurityTrustModel`). Request/response
  wire types: `SecurityEventRecord`, `ListSecurityEventsRequest`,
  `ListSecurityEventsResponse` (`proto/coordinator/coordinator.proto`).
- **Go** has its own local journal for HTTP-layer-only events
  (permission denials, idempotency outcomes, mutation accepted/
  rejected, audit access — things that never reach C++), and merges
  those with the coordinator's own events (fetched via
  `SecurityClient.ListSecurityEvents`) in `GET /api/v1/security/events`,
  sorted by `event_id`. A coordinator error is non-fatal to this merge —
  Go-local events are still worth serving when the coordinator is
  unreachable. Known limitation: the merged cursor is not a perfectly
  stable distributed cursor across a page boundary that splits unevenly
  between the two sources — acceptable for this slice's scope.
- **Python worker** persists its own events locally (its own JSONL
  journal + Prometheus counters via
  `security/metrics.py`/`ensure_metrics_server_started`) but **does not
  ship them to the coordinator/Go this slice** — that would require a
  new signed wire message type, out of scope here and explicitly
  disclosed rather than silently expanded.

## Where events are actually emitted (representative coverage, not exhaustive)

- **C++**: coordinator startup (`TRANSPORT_MTLS_STARTED`/
  `TRANSPORT_INSECURE_DEVELOPMENT_STARTED`), `SuspendWorker`/
  `ActivateWorker`/`RevokeWorker` (+ `ACTIVE_LEASE_CANCELED` when leases
  are actually canceled), `RevokeWorkerSigningKey`,
  `RotateCoordinatorSigningKey`/`RevokeCoordinatorSigningKey` (accept/
  reject/idempotent-replay), `Heartbeat` (accept/reject/replay-reject —
  representative wiring for the signed-worker-message category), and a
  `SECURITY_PERMISSION_DENIED` event at every `ADMIN_CONTROL` RPC's
  permission-denial branch. **Not yet wired**: `SubmitClientResult`/
  privacy-record/`RotateWorkerSigningKey` signature-failure paths (the
  same `security_event_type_for_envelope_rejection` mapping used for
  `Heartbeat` applies identically — a follow-on pass, not a design gap).
- **Python**: `coordinator_client.py`'s `acquire_task` verification
  pipeline (`COORDINATOR_TASK_VERIFIED`, plus `COORDINATOR_TASK_REJECTED`/
  `COORDINATOR_TASK_REPLAY_REJECTED`/`DUPLICATE_TASK_EXECUTION_BLOCKED`
  depending on the specific one of 16 rejection reasons).
- **Go**: every security mutation handler
  (`internal/application/security_service.go`) emits alongside its
  existing `AuditService.Record` call; `requirePermission` emits
  `SECURITY_PERMISSION_DENIED` for every `/api/v1/security/...` route in
  one place; `handleSecurityAudit` emits `SECURITY_AUDIT_ACCESSED` when
  a detailed read actually returns data.

See [security-observability-inventory.md](security-observability-inventory.md)
for the full per-operation table this slice updates in place.

## Deferred

- Full coverage of every event type in the registry at every call site
  listed in Work Package A (this slice wires a representative,
  documented subset — see above).
- Shipping Python-worker-originated events to the coordinator/Go.
- A background poller relaying C++/Python event counts into Go's
  Prometheus metrics in real time (see [security-metrics.md](security-metrics.md)).
- Cross-run/global event correlation beyond what `event_id`/`trace_id`
  already provide.
