# Web Security Center, Event Centralization, and Security CI — Slice Report

**Superseded, this is a frozen historical snapshot of the slice it
describes.** The gaps this report discloses as not-yet-done (no browser
automation, `python-worker` excluded from mTLS, no Grafana dashboard,
harness not modularized) were closed by the follow-on Security Runtime
Completion and Release Evidence slice — see
[security-runtime-completion-report.md](security-runtime-completion-report.md)
and [security-runtime-validation.md](security-runtime-validation.md)
for the current, live-validated state (a real Playwright browser suite,
37/0/0/57/0 across the full 94-scenario runtime matrix, a Grafana
dashboard, and CI runtime-validation gates all now exist). Left as-is
below per this project's "do not reuse a prior pass's numbers" policy —
every count in this file is specific to the slice it was written for.

**Scope actually delivered: a full Web Security Center (6 routes, real
backend-enforced permissions, real admin mutations with reason +
confirmation + idempotency), signed centralization of Python-worker-
originated security events into the coordinator's own journal (new
proto contracts, a new gRPC RPC with full signature/replay/binding
verification, a Python persistent worker-side queue), event-source
health, new low-cardinality Prometheus gauges for event centralization,
one Grafana-ready metrics surface (no dashboard built), two new
security-focused CI gates, and tests across all four languages.** This
was tiered deliberately (see "Scope statement" in the approved plan and
§2 below) rather than attempting literal maximal coverage of every
enumerated work-package bullet, scenario, and CI job in the original
specification. Full-depth items got real, tested, working code;
bounded items got real-but-partial coverage; explicitly out-of-scope
items are itemized as **DEFERRED** or **BLOCKED** throughout this
report and in [known-limitations.md](known-limitations.md) — never
silently narrowed or claimed as done.

**This report does not claim secure aggregation is implemented or
complete, and no custom threshold secret sharing was implemented.**
Nothing in the "Do not implement" list (pairwise masking, private
client masks, fixed-point secure-aggregation encoding, threshold secret
sharing, share reconstruction, dropout recovery, secure unmasking,
secure aggregate reconstruction, homomorphic encryption, Byzantine-
robust aggregation, remote attestation, TEEs, TPM integration, Ray,
Flower, asynchronous/semi-synchronous aggregation, PostgreSQL/Redis/
MinIO/S3 migration, production Kubernetes rollout) was implemented.

---

## 1. Working method followed before writing code

Per the task's required working method: `git status` and the tracked/
untracked file inventory were inspected; `plan.md` and the prior
slice's docs (`security-events.md`, `security-metrics.md`,
`security-audit-journal.md`, `security-runtime-validation.md`,
`security-api.md`, `security-permission-model.md`) were read in full;
the terminology checker was run clean before starting; the frontend
shell (`app-shell.tsx`), an existing dashboard route (`app/audit/`),
the role/permission handling
(`go/internal/security/permissions.go`), the HTTP API client
(`lib/api.ts`), existing query/polling conventions
(`PrivacyCenterPanel`), and the existing SSE infrastructure
(`lib/coordinator-events.ts`) were read directly, not assumed. This
confirmed several load-bearing facts before any code was written:
zero reusable table/modal/toast components existed; the existing SSE
mechanism is strictly per-run-scoped; no `Idempotency-Key` or caller-
supplied `AbortSignal` support existed anywhere in `lib/api.ts`;
`AppShell` had zero role-based nav gating; CI had no job building the
gRPC-gated coordinator and no secret-scanning job; `infra/grafana/`
provisioned a datasource only, zero dashboards. Each of these findings
directly shaped a design decision recorded in the approved plan
(`lucky-petting-horizon.md`) and repeated in the relevant section below.

## 2. Scope statement and tiering (restated from the approved plan)

The literal specification enumerates roughly 26 work packages, a
62-scenario runtime matrix, and ~28 CI jobs. Three tiers were applied,
consistent with this project's established practice on every prior
oversized slice:

- **Full depth** (real, working, tested code): the entire Web Security
  Center (§7-§13), the security overview and event-source-health
  endpoints, the signed worker-event-batch centralization pipeline
  (§14-§17), event-centralization metrics (§20), documentation, and
  `plan.md`.
- **Real but bounded depth**: two new event types wired for the new
  RPC (not an exhaustive pass across the ~55-type registry); two
  genuinely new CI gates (not ~20 near-duplicates of the existing
  full-suite jobs); tests added for every new code path this slice
  introduced (not a re-derivation of the entire prior slices' test
  suites).
- **Explicitly deferred/blocked, never reported as passing**: a
  Grafana dashboard, a modularized 62-scenario validation harness,
  browser end-to-end automation, and a live Docker Compose run of
  `SubmitWorkerSecurityEvents` with a real Python worker process over
  real mTLS. See §31 for the itemized reasons.

## 3. Design decisions

1. **Security overview** aggregates existing, already-real coordinator
   RPCs (`GetTransportSecurityStatus`, `GetSecurityTrustModel`,
   `ListWorkerIdentities`, `ListWorkerSigningKeys`,
   `ListCoordinatorSigningKeys`) plus a bounded (limit 500) tally over
   the existing event journals — no new counters duplicating what the
   journals already record. Two small additive journal methods
   (`LastRecordTimestamp`, `HasRotated`) were added in all three
   languages to support this and event-source health.
2. **Event-source health** is backed by one new, justified C++
   `ADMIN_CONTROL` RPC, `GetSecurityEventSourceHealth`, reporting only
   centrally-computable aggregates (batch accept/reject counts,
   distinct-workers-seen). Individual worker queue depth is inherently
   worker-local and is surfaced only as an explicitly-untrusted,
   self-reported hint field inside each batch submission — never
   trusted as ground truth.
3. **Event centralization reuses the exact existing
   `SignedWorkerEnvelope`/`resolve_signing_key`/`ReplayProtectionStore`
   pipeline** that already handles Heartbeat/ClientResult/
   PrivacyRecord/KeyRotation — one new `MessageStream` value
   (`kSecurityEvents`) and one new `MessageType`
   (`MESSAGE_TYPE_SECURITY_EVENT_BATCH`), not a parallel crypto system.
   The worker-side queue reuses `SecurityEventJournal` (Python) as its
   storage engine with a small sidecar cursor file, not a second
   persistence mechanism.
4. **Live web updates are polling-based** (5s interval,
   `AbortController`-cancelled, bounded ring buffers on the event/audit
   explorers), not a new global SSE handler — the existing SSE
   mechanism is strictly per-run-scoped and this codebase already has
   an established 5s-poll precedent (`PrivacyCenterPanel`).
5. **CSRF**: this API is Bearer-token-authenticated, never cookie-based
   — a cross-origin page cannot read or attach a bearer token it was
   never given, so classic CSRF does not apply. Documented as the
   existing, adequate mitigation rather than bolting on a redundant
   CSRF-token system onto a non-cookie API.

## 4. Proto contracts added

`proto/worker/worker.proto`: `SignedWorkerEnvelope.MessageType.MESSAGE_TYPE_SECURITY_EVENT_BATCH = 12`,
`SignedWorkerEnvelope.MessageStream.MESSAGE_STREAM_SECURITY_EVENTS = 8`,
`WorkerSecurityEventPayload`, `SignedWorkerSecurityEventBatch`.

`proto/coordinator/coordinator.proto`: `SubmitWorkerSecurityEventsRequest`,
`SubmitWorkerSecurityEventsResponse`, and the `SubmitWorkerSecurityEvents`
RPC on `CoordinatorService`. (`GetSecurityEventSourceHealth` and its
request/response messages were added in the portion of this session
before the mid-session context summary, together with the RPC's C++
implementation stub — this pass completed the RPC body, the Go
endpoint, and all associated tests/metrics/docs.)

Regenerated via `python -m grpc_tools.protoc` for Go and Python
bindings (no standalone `protoc` binary is installed on this Windows
development machine — see [protobuf-generation.md](protobuf-generation.md)
for why `grpc_tools.protoc`'s bundled compiler is used as the
documented substitute). `scripts/verify_proto_contracts.py` passes.

## 5. C++ changes

- **`SubmitWorkerSecurityEvents` RPC** (`coordinator_service.cpp`):
  mTLS worker-identity binding → worker-status check → batch/worker_id
  match → bounded batch size (`kMaxSecurityEventBatchSize = 200`,
  rejected wholesale, never truncated) → envelope presence →
  `resolve_signing_key`(`SignedMessageKind::kSecurityEventBatch`,
  permits `ACTIVE`/`GRACE_PERIOD`) → payload-hash + signature
  verification → replay protection on the new
  `MessageStream::kSecurityEvents` track → per-event validation
  (recognized event_type/severity/actor_type/subject_type/outcome
  strings plus the shared `validate_security_event` bounds), with an
  individual malformed event skipped (counted in
  `rejected_event_count`) rather than failing the whole
  already-authenticated batch. Emits one
  `WORKER_SECURITY_EVENT_BATCH_ACCEPTED`/`_REJECTED` event about the
  batch itself, `source_service="coordinator"`, distinct from the
  individually-relayed events (`source_service="python-worker"`).
- **`security_event_batch_payload_hash_input`**
  (`signed_envelope_verifier.hpp`/`.cpp`): canonical JSON, alphabetical
  key order, events hashed in submission order (not re-sorted — order
  is part of what gets signed, unlike client-result's tensor/metric
  lists).
- **`SecurityEventSink::emit` now returns the assigned `event_id`**
  (was `void`) — an additive signature change (every pre-existing call
  site ignores the return value and still compiles unchanged) needed
  so the batch RPC can report `last_accepted_event_id`.
- **`WORKER_EVENT_BATCH` subject type** and
  **`WORKER_SECURITY_EVENT_BATCH_ACCEPTED`/`_REJECTED` event types**
  added to the shared `SecurityEventType`/`SecuritySubjectType`
  registries (C++ `security_event.hpp`/`.cpp`).
- **`MessageStream::kSecurityEvents`** added to
  `replay_protection_store.hpp`/`.cpp`.

## 6. Python changes

- **`python/src/fl_platform/worker/security_event_queue.py`** (new):
  `WorkerSecurityEventQueue` wraps an existing `SecurityEventJournal`
  instance — `select_pending(max_batch_size)`, `mark_acknowledged`
  (atomic sidecar cursor file, temp-file-then-replace), and
  `pending_count_hint()`. At-least-once delivery: the cursor only
  advances after the coordinator confirms acceptance.
- **`signed_envelope.py`**: `MESSAGE_TYPE_SECURITY_EVENT_BATCH`,
  `MESSAGE_STREAM_SECURITY_EVENTS`, `WorkerSecurityEventFields`,
  `WorkerSecurityEventBatchFields`,
  `security_event_batch_payload_hash_input` — mirrors the C++
  canonicalization byte-for-byte (verified via a cross-language golden
  fixture, see §22).
- **`coordinator_client.py`** (`GrpcCoordinatorClient`):
  `submit_security_events(worker_id, max_batch_size=100)` — builds,
  signs, and submits a batch via the identical
  `EnvelopeFields`/`sign_envelope` pattern `rotate_signing_key` already
  uses; no-op if the journal/signing identity was never configured;
  advances the local queue cursor only on `response.accepted == True`.
- A small pre-existing `ruff format` line-length issue in
  `security_event_journal.py` (from the portion of this session before
  the mid-session summary) was fixed during this pass's final
  regression sweep — unrelated to this session's own new code, but
  caught and fixed rather than left for a future pass.

## 7. Go changes — security overview and event-source health

- **`GET /api/v1/security/overview`** (`security_overview.go`):
  aggregates transport, worker identity/signing-key counts, coordinator
  key status, signed-message/privacy-record/task-authenticity tallies,
  event/audit journal health, and an explicit
  `feature_availability` block (`secure_aggregation_available: false`,
  etc, always false except `central_coordinator_observes_updates:
  true`). VIEWER role has coordinator signing-key identifiers cleared
  server-side.
- **`GET /api/v1/security/events/sources`**: merges the Go-local
  journal's own health with the coordinator-relayed
  `GetSecurityEventSourceHealth` RPC result.
- **`PermOverviewRead`/`PermEventSourcesRead`** permissions, granted to
  ADMIN/RESEARCHER/VIEWER, not SERVICE.

## 8. Go changes — event-centralization metrics

New Prometheus gauges in `telemetry.go`, fed on every
`GET /api/v1/security/events/sources` poll from exactly the response
that endpoint is about to serve (one source of truth):
`fl_security_event_source_records{source_service}`,
`fl_security_event_source_batches{source_service,outcome}` (outcome:
`accepted`/`rejected`), `fl_security_event_source_distinct_workers{source_service}`,
`fl_security_event_source_lag_seconds{source_service}` (an unknown lag
is never coerced to `0` — it is simply omitted from that series, so
"fresh" is never confused with "no data yet"). `source_service` is one
of a small fixed set (`go-api`/`coordinator`/`python-worker`) —
low-cardinality by construction, matching this project's existing
`fl_security_events_total` label discipline.

## 9. Web Security Center — routes

Six routes, all thin server components (`dynamic = "force-dynamic"`)
wrapping one client console each, matching `app/audit/page.tsx`'s
template. See [web-security-center.md](web-security-center.md) for the
full route/permission/component table. None fetch seed data
server-side (every `/api/v1/security/*` route requires a Bearer token
that only exists in browser `localStorage`), unlike `app/audit/page.tsx`.

- `/security` — aggregate overview, event-source health table,
  explicit "does not implement secure aggregation" disclosure banner.
- `/security/workers` — list, client-side search/status-filter/sort/
  pagination (the Go endpoint returns the full set, no server cursor —
  client-side pagination matches this codebase's existing
  `audit-console.tsx` convention), role-aware columns (VIEWER sees
  only `worker_id`/`registration_status`).
- `/security/workers/[workerId]` — identity, signing keys, a
  client-filtered recent-activity panel (from the events endpoint),
  admin suspend/activate/revoke/revoke-key actions.
- `/security/coordinator-keys` — rotate (with an inline expected-
  current-key/expiry/grace-period form inside the confirm dialog) and
  revoke.
- `/security/events` — live-polled, filterable (severity/subject_type/
  event_type/search), bounded 500-event ring buffer.
- `/security/audit` — live-polled, filterable (actor/action/
  resource_type/outcome), cursor-paginated against the real
  `SecurityAuditJournal` cursor, bounded 500-record ring buffer.

## 10. Web Security Center — admin mutation safety

Every mutation routes through one shared `ConfirmDialog` component
(not five hand-rolled confirm flows), guaranteeing:

- A required, non-empty operator-written **reason**.
- A route-specific **consequence explanation** plus an explicit
  **acknowledgment checkbox** before Confirm is even enabled.
- A fresh **`Idempotency-Key`** (`crypto.randomUUID()`), minted once
  per dialog *open* and reused across every confirm click within that
  session — a retry after a failed submission (without closing the
  dialog) carries the identical key, so the server-side idempotency
  cache treats it as the same request rather than double-executing.
- **Server enforcement, not just client hiding**: mutation buttons are
  hidden for non-ADMIN roles client-side, but the real gate is the Go
  handler's `security.Allows(role, perm)` check — a direct API call
  from a non-admin session still gets a real 403, verified by
  `TestSecurityWorkerSuspendRequiresAdminNotViewer` and
  `TestSecurityCoordinatorSigningKeyRotateAndRevoke` (pre-existing
  tests, unchanged) plus this slice's own
  `TestSecurityEventSourcesDeniedForServiceRole`.

## 11. Web client API layer

`lib/security-api.ts` (new, separate from `lib/api.ts`): every function
accepts an optional caller-supplied `AbortSignal` (combined with an 8s
internal timeout via `AbortSignal.any`); mutation functions require an
`idempotencyKey` and send it as the `Idempotency-Key` header. Two
contracts matching `lib/api.ts`'s existing split: list/detail reads
return `T | undefined` on failure (and, specifically, list reads return
`undefined` — not `[]` — on failure, preserving the "unreachable" vs
"genuinely empty" distinction so a console renders an accurate banner
instead of a misleadingly-empty table); mutations throw with the
server's `error` message.

## 12. Shared components

- `components/confirm-dialog.tsx` — see §10.
- `components/security-status-pill.tsx` — a security-domain-specific
  status pill, kept separate from the existing `StatusPill`
  (`components/status-pill.tsx`, typed to `RunStatus`) since several
  status words (e.g. "failed", "completed") would otherwise collide
  with differently-colored security meanings.
- `components/security-subnav.tsx` — shared secondary nav across the
  six routes.
- `lib/use-stored-session.ts` — the `localStorage` session read/parse
  step, previously duplicated inline in every client feature, factored
  into one hook once a sixth call site needed it.
- `lib/security-format.ts` — small, page-agnostic timestamp/lag/boolean
  formatting helpers.

## 13. `AppShell` and CSS

One new `<Link href="/security">Security Center</Link>` nav entry — no
role-based gating (matches the pre-existing, zero-gating convention for
every other nav entry; real enforcement is server-side, see §10). New
CSS: `.status-pill.sec-*` variant classes, `.modal-overlay`/
`.modal-card`/`.modal-ack-row`/`.modal-idempotency-note` (no
modal/dialog classes existed before this slice), `.security-hero-card`,
`.security-limitation-banner`.

## 14. `docker-compose.security.yml` / mTLS coverage

**Not extended this slice.** The mTLS override still covers only
`coordinator`↔`api`; `python-worker` remains excluded (a pre-existing,
disclosed gap from the prior slice — see §31 and
[known-limitations.md](known-limitations.md)). `SubmitWorkerSecurityEvents`
was therefore not validated over a live Compose stack with a real
worker process this slice.

## 15. Grafana

**Not built this slice (DEFERRED).** `infra/grafana/` still provisions
only a datasource. The metrics this slice adds
(`fl_security_event_source_*`) are real, scrapeable Prometheus gauges
— a dashboard could be built against them without any further backend
work — but no dashboard JSON or provisioning config was authored. See
§31 for why this was deliberately deferred rather than attempted
partially.

## 16. CI changes

Two new jobs added to `.github/workflows/ci.yml` (13 → 15 jobs):

- **`cpp-grpc`**: installs `protobuf-compiler`/`protobuf-compiler-grpc`/
  `libprotobuf-dev`/`libgrpc++-dev` directly on the `ubuntu-latest`
  runner (no nested Docker build needed — the runner already has apt),
  generates protobuf/gRPC C++ bindings, configures/builds
  `fl_coordinator_grpc_server` + all five gRPC-gated test executables,
  and runs `ctest`. Closes a real, previously-disclosed gap: no CI job
  had ever built or tested the gRPC-gated coordinator since the
  Coordinator Runtime phase — every validation of that code path was
  ad hoc local Docker builds (including every one this session, see
  §21).
- **`secret-scan`**: `git grep` across every tracked file for PEM
  private-key headers and AWS access-key-ID patterns. Broader than the
  pre-existing `pki-verify` job's tracked-secret check (which only
  inspects `certs/dev*`/`*.key.pem`). Pattern-based, not a dedicated
  tool (gitleaks/trufflehog) — see §31 for the disclosed scope
  narrowing.

YAML validated with `yaml.safe_load` (all 15 jobs parse). No existing
job was modified.

## 17. CI artifact policy

No CI artifact upload was added this slice (the pre-existing
`cpp-benchmark` job's artifact upload is unchanged and out of this
slice's scope). No new job persists private keys, certificates, raw
signed payloads, nonces, datasets, client updates, privacy noise, or
sensitive audit records — `cpp-grpc` and `secret-scan` produce only
build/test logs and a pass/fail exit code.

## 18. Files added this session

```
proto/worker/worker.proto                                  (modified)
proto/coordinator/coordinator.proto                         (modified)
cpp/coordinator/include/fl_coordinator/coordinator_service.hpp
cpp/coordinator/src/coordinator_service.cpp
cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp
cpp/coordinator/src/replay_protection_store.cpp
cpp/coordinator/include/fl_coordinator/security_event.hpp
cpp/coordinator/src/security_event.cpp
cpp/coordinator/include/fl_coordinator/security_event_journal.hpp
cpp/coordinator/src/security_event_journal.cpp
cpp/coordinator/include/fl_coordinator/signed_envelope_verifier.hpp
cpp/coordinator/src/signed_envelope_verifier.cpp
cpp/coordinator/tests/signed_envelope_verifier_test.cpp
cpp/coordinator/tests/coordinator_service_test.cpp
go/internal/transport/httpapi/security_overview.go              (new)
go/internal/transport/httpapi/security_overview_test.go         (new)
go/internal/observability/security_event_journal.go
go/internal/observability/security_audit_journal.go
go/internal/observability/telemetry.go
go/internal/observability/telemetry_test.go
go/internal/security/permissions.go
go/internal/transport/httpapi/server.go
go/internal/coordinator/security_client.go
go/internal/coordinator/client.go
go/internal/coordinator/mock_client.go
go/internal/coordinator/security_mock_client.go
go/internal/application/security_service.go
python/src/fl_platform/worker/security_event_queue.py           (new)
python/src/fl_platform/security/security_event_journal.py
python/src/fl_platform/security/signed_envelope.py
python/src/fl_platform/worker/coordinator_client.py
python/tests/test_security_event_batch.py                       (new)
python/tests/test_security_event_queue.py                       (new)
web/lib/security-api.ts                                         (new)
web/lib/security-format.ts                                      (new)
web/lib/use-stored-session.ts                                   (new)
web/components/confirm-dialog.tsx                                (new)
web/components/security-status-pill.tsx                          (new)
web/components/security-subnav.tsx                                (new)
web/components/app-shell.tsx
web/types/api.ts
web/app/globals.css
web/features/security/security-overview-console.tsx              (new)
web/features/security/security-workers-console.tsx               (new)
web/features/security/security-worker-detail-console.tsx         (new)
web/features/security/security-coordinator-keys-console.tsx      (new)
web/features/security/security-events-console.tsx                (new)
web/features/security/security-audit-console.tsx                 (new)
web/app/security/page.tsx                                        (new)
web/app/security/workers/page.tsx                                (new)
web/app/security/workers/[workerId]/page.tsx                     (new)
web/app/security/coordinator-keys/page.tsx                       (new)
web/app/security/events/page.tsx                                 (new)
web/app/security/audit/page.tsx                                  (new)
web/tests/security-api.test.ts                                   (new)
web/tests/security-status-pill.test.tsx                          (new)
web/tests/confirm-dialog.test.tsx                                 (new)
.github/workflows/ci.yml
docs/security-event-centralization.md                            (new)
docs/web-security-center.md                                      (new)
docs/security-ui-report.md                                       (new, this file)
docs/known-limitations.md
plan.md
```

`go/generated/**` and `python/src/fl_platform/generated/**` were
regenerated (gitignored, never committed — see
[protobuf-generation.md](protobuf-generation.md)).

## 19. Exact commands executed (this finalization pass)

```text
python scripts/check_project_terminology.py
python scripts/verify_proto_contracts.py
python -m pytest tests python/tests -q
python -m ruff check .
python -m ruff format --check .
python -m mypy --config-file=python/pyproject.toml python/src
cd go && go build ./... && go vet ./... && gofmt -l . && go test ./...
cd web && npm run lint && npm run typecheck && npm run test && npm run build
```

(The live Docker gRPC build/ctest run — §21 — was executed earlier in
this same working session, before this finalization pass, and was not
re-run here since no C++ source changed after it completed.)

## 20. Pass / fail / blocked results (fresh counts, this pass)

| Command | Result |
|---|---|
| `check_project_terminology.py` | **Pass** — no prohibited roadmap terminology found |
| `verify_proto_contracts.py` | **Pass** — protobuf contract compatibility checks passed |
| `pytest tests python/tests -q` | **Pass** — 336 passed, 1 skipped |
| `ruff check .` | **Pass** — all checks passed |
| `ruff format --check .` | **Pass** — 110 files already formatted (one pre-existing line-length issue fixed during this pass, see §6) |
| `mypy --config-file=python/pyproject.toml python/src` | **Pass** — no issues found in 76 source files |
| `go build ./...` | **Pass** |
| `go vet ./...` | **Pass** |
| `gofmt -l .` | Reports pre-existing drift in files this slice did not touch (see §29) — zero files from this slice's own changes are listed |
| `go test ./...` | **Pass** — all packages |
| `npm run lint` | **Pass** |
| `npm run typecheck` | **Pass** |
| `npm run test` | **Pass** — 46 passed (7 test files) |
| `npm run build` | **Pass** — all 6 new `/security*` routes compiled, dynamic/server-rendered as expected |

No command in this list is reported as passing without having actually
been run during this pass.

## 21. Live Docker gRPC build results (executed earlier this session)

`mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`, real
`protobuf-compiler`/`protobuf-compiler-grpc`/`libprotobuf-dev`/
`libgrpc++-dev` installed via apt, real protoc-generated C++/gRPC
bindings, `cmake -DCMAKE_BUILD_TYPE=Release`:

- `fl_coordinator_grpc_server` — builds clean (the real, runnable gRPC
  server binary).
- **12/12 `ctest` suites pass**: `fl_core_smoke`, `fl_aggregator_golden`,
  `fl_validation_tests`, `fl_checkpoint_tests`, `fl_privacy_tests`,
  `fl_secure_random_tests`, `fl_coordinator_tests`,
  `fl_coordinator_grpc_tests` (includes this slice's new
  `SubmitWorkerSecurityEvents` integration block, §22),
  `fl_capability_statement_verifier_tests`,
  `fl_signed_envelope_verifier_tests` (includes this slice's new batch
  hash/sign/verify tests, §22), `fl_coordinator_task_signing_tests`,
  `fl_peer_identity_tests`.
- One real bug was caught and fixed by this Docker run (see §23): the
  new integration test initially used small fixture timestamps
  (`5000.0`) for envelope `issued_at`/`expires_at`, which
  `CoordinatorServiceImpl::now_unix_s()` (real wall clock, not an
  injectable test clock) immediately rejected as expired — fixed by
  anchoring every envelope timestamp in the new test to
  `std::chrono::system_clock::now()`.

## 22. `SubmitWorkerSecurityEvents` test coverage detail

**C++ (`coordinator_service_test.cpp`, new block)**: a fully-wired
`CoordinatorServiceImpl` (real `WorkerIdentityRegistry`,
`SigningKeyRegistry`, `ReplayProtectionStore`, `SecurityEventJournal`)
covering: a validly signed 2-event batch is accepted, journals 3
records (2 events + 1 batch-summary event), `accepted_event_count == 2`;
resubmitting the identical envelope is rejected as a replay and
journals nothing further; an unregistered worker_id is rejected
(`unknown_worker`); a 201-event batch is rejected wholesale
(`batch_too_large`, `accepted_event_count == 0`, not a truncated
subset); a batch with one well-formed event and one event carrying an
unrecognized `event_type` is accepted at the batch level with
`accepted_event_count == 1, rejected_event_count == 1`; and
`GetSecurityEventSourceHealth` correctly reports 2 accepted batches, 3
rejected batches, and 1 distinct worker seen after the above sequence.

**C++ (`signed_envelope_verifier_test.cpp`, new section)**: canonical
JSON key ordering, a cross-language golden fixture (byte-for-byte
identical to the Python-generated string embedded in
`test_security_event_batch.py`), determinism, tamper detection (an
added event changes the hash; swapping two events' order changes the
hash — proving order is preserved, not canonically re-sorted), and a
full Ed25519 sign/verify round trip using
`MESSAGE_TYPE_SECURITY_EVENT_BATCH`.

**Python (`test_security_event_batch.py`, 7 tests)**: the mirror-image
of the C++ hash tests, including the identical golden fixture string.

**Python (`test_security_event_queue.py`, 8 tests)**: empty-queue
selection, enqueue-then-select, cursor advancement on acknowledgment,
partial acknowledgment leaving a remainder, at-least-once redelivery
when acknowledgment never happens (simulating a crash or a rejected
submission), cursor persistence across a simulated worker restart (a
fresh `SecurityEventJournal` + `WorkerSecurityEventQueue` over the same
on-disk path), a no-op empty-id acknowledgment, and a malformed cursor
file raising rather than silently resetting.

**Go (`security_overview_test.go`, 6 tests)**: overview auth
requirement, real-state aggregation (transport/worker/signing-key/
coordinator-key/signed-message tallies, `feature_availability` always
disclosing no secure aggregation), VIEWER coordinator-key-ID redaction,
event-sources auth requirement, `go-api`/`coordinator`/`python-worker`
sources all present with the right aggregate counts, SERVICE role
denial.

**Go (`telemetry_test.go`, 1 new test)**: the four new
`fl_security_event_source_*` gauges render correctly, and an unknown
lag (`hasLag=false`) is never rendered as a spurious `0`.

**Web (`security-api.test.ts`, 7 tests)**: overview read + undefined-
on-503, the undefined-vs-`[]` distinction for worker lists, the
`Idempotency-Key` header + body on a lifecycle mutation, throwing with
the server's error message on a failed mutation, `AbortSignal`
cancellation resolving to `undefined` rather than rejecting, and
rotation-specific field serialization.

**Web (`confirm-dialog.test.tsx`, 8 tests) / `security-status-pill.test.tsx`
(5 tests)**: closed-state renders nothing, title/consequence text,
Confirm disabled until reason+checkbox, Cancel callback, Confirm
callback carries `{reason, idempotencyKey}`, a fresh idempotency key is
minted on every re-open, busy-state disabling, error display; and the
status-variant mapping (good/warn/bad/neutral, including an
unrecognized-status fallback that doesn't throw).

## 23. Bugs found and fixed during this pass

- **`SecurityEventSink::emit` signature**: needed to change from
  `void` to `std::string` (returning the assigned `event_id`) so the
  batch RPC's response could report `last_accepted_event_id`. Verified
  additive: every pre-existing call site (which discards the return
  value) still compiles and behaves identically; the local
  `fl_coordinator_tests` suite (25/25) and the Docker `ctest` suite
  (12/12) both pass unchanged.
- **Test-fixture timestamp bug** (§21): fixed-value envelope timestamps
  (`5000.0`) fail against `now_unix_s()`'s real wall clock. Fixed in
  the new integration test by anchoring to
  `std::chrono::system_clock::now()`.
- **`record_rejection` call sites**: an initial draft threaded a
  `rejection_code` string parameter through every call site of a
  batch-rejection-counting helper lambda that never actually used the
  parameter — simplified to a no-argument lambda across all nine call
  sites during implementation, before this was ever exercised live.
- **A pre-existing `ruff format` line-length violation** in
  `security_event_journal.py` (from the portion of this session before
  the mid-session context summary) was caught and fixed during this
  pass's final regression sweep (§6).

## 24. Security findings

No vulnerabilities were found in the pre-existing code this slice
builds on. Two design considerations were resolved deliberately during
implementation:

- **Worker-reported `queue_depth_hint` is never trusted.**
  `GetSecurityEventSourceHealth` reports only centrally-observable
  aggregates; a compromised or buggy worker asserting an arbitrary
  `queue_depth_hint` cannot influence any coordinator-side decision or
  displayed "ground truth" value — it is documented, in the proto
  comment and in code, as an explicitly untrusted, display-only signal.
- **A malformed individual event does not compromise batch-level
  authenticity.** Per-event validation happens strictly *after*
  envelope signature/replay verification succeeds — an attacker cannot
  use a crafted malformed event to bypass or weaken the batch's own
  cryptographic acceptance decision; it can only cause that one event
  to be skipped from an otherwise-legitimately-signed batch.

## 25. Remaining trust assumptions

- The coordinator's decision to accept a security-event batch rests
  entirely on the worker's own signing key being ACTIVE/GRACE_PERIOD in
  `SigningKeyRegistry` — a worker whose key was compromised but not yet
  revoked can submit fabricated (but self-consistent) security events
  about itself. This is the same trust boundary every other signed
  worker message already operates under; nothing new was introduced by
  this slice specifically for events.
- Web Security Center admin mutations trust the Go API's session/role
  system unchanged — no new authentication mechanism was introduced.
- The `Idempotency-Key` values this slice's web UI mints
  (`crypto.randomUUID()`) are trusted client-side entropy; the server-
  side idempotency cache (unchanged, pre-existing) is the actual
  safety mechanism, not the randomness quality of the client-generated
  key itself.

## 26. Known limitations

See the "Web Security Center, Event Centralization, and Security CI
slice" section of [known-limitations.md](known-limitations.md) for the
complete, itemized list. Summarized: critical event coverage remains a
representative subset (two new event types added, not an exhaustive
registry pass); no Grafana dashboard; the validation harness was not
modularized/expanded into the full 62-scenario matrix; no browser
end-to-end automation; `SubmitWorkerSecurityEvents` was not exercised
over a live Docker Compose stack with a real Python worker process and
real mTLS end-to-end (validated instead via a live Docker `ctest`
build plus isolated Python unit tests sharing an identical,
golden-fixture-verified canonicalization); `docker-compose.security.yml`'s
mTLS override still does not cover `python-worker`; no per-user
`HasScope` plumbing for the SERVICE role (pre-existing, unchanged).

## 27. Regression status

**Zero regressions.** Fresh counts, this pass: Python 336 passed / 1
skipped (was 321 before this session's Python test additions — 15 new
tests, all passing, zero prior tests broken); Go all packages passing
(`go build`/`go vet`/`go test` all clean, 6 new tests added); web 46
passed (was 26 — 20 new tests, `npm run lint`/`typecheck`/`build` all
clean); C++ local (Windows/MSVC, non-gRPC-gated) `fl_coordinator_tests`
25/25 internal test groups pass; C++ Docker (gRPC-gated) 12/12 `ctest`
suites pass. `ruff check`/`ruff format --check`/`mypy` all clean.
Terminology checker and proto-contract compatibility checker both pass.

## 28. Git working-tree summary

No commits, pushes, tags, or pull requests were made, per standing
instruction — only local file changes exist. This slice's own new/
modified files are listed in §18. The broader working tree
(`git status --short` currently reports 419 changed paths) also
carries the cumulative, uncommitted diff of every prior slice in this
multi-slice project session — none of that has been committed either,
consistent with the same standing instruction applying throughout.

## 29. Note on `gofmt -l .` output

`gofmt -l .` (run fresh during this pass, §20) lists a number of files
this slice did not touch (`internal/algorithms/algorithms_test.go`,
`internal/application/dataset_service.go`/`fairness.go`/
`model_service.go` and their test files,
`internal/bootstrap/persistence.go`,
`internal/datasets/repository.go`/`repository_test.go`,
`internal/models/repository.go`/`repository_test.go`) — pre-existing
formatting drift, confirmed present before this session started (the
initial `git status` at session start already showed these as modified
paths). Every file this slice added or modified is confirmed absent
from that list — this slice's own Go code is `gofmt`-clean.

## 30. Documentation updated

- [security-event-centralization.md](security-event-centralization.md)
  (new) — the full event-centralization pipeline design/contract.
- [web-security-center.md](web-security-center.md) (new) — the full
  Web Security Center route/permission/component reference.
- [known-limitations.md](known-limitations.md) — one stale bullet
  ("Python-worker-originated events are not shipped to the coordinator")
  marked RESOLVED with a forward reference; one bullet (native C++
  Prometheus endpoint) clarified to distinguish the still-true "no
  per-event relay" from the newly-added "aggregate health gauges are
  relayed"; a new dedicated section appended.
- `plan.md` — new §5.15, "Completed (partially): Web Security Center,
  Event Centralization, and Security CI," following the exact
  Implemented/Validated/Current-limitations structure every prior
  slice in §5 uses.
- `docs/security-ui-report.md` (this file, new).

## 31. Recommended next work

In priority order:

1. **Exhaustive critical-event-coverage pass** across the full ~55-
   (now 57-, including this slice's two new types) event registry in
   C++/Python/Go — this and every prior slice have wired only a
   representative subset.
2. **A Grafana security dashboard**, provisioned against the metrics
   this slice (and the prior slice) already expose — no backend work
   is blocking this, only the dashboard JSON/provisioning itself.
3. **Extend `docker-compose.security.yml`'s mTLS override to
   `python-worker`**, then run a real live Compose validation of
   `SubmitWorkerSecurityEvents` end-to-end (real worker process, real
   mTLS, real coordinator) — the single most valuable remaining gap
   this slice's own scope statement explicitly deferred.
4. **Modularize and expand `scripts/security-validation/`** into a
   real, runnable scenario-group harness (not literally 62 hand-written
   live scenarios, but a meaningful, automated superset of the existing
   12/18/23-check scripts from prior slices), producing machine-
   readable PASS/FAIL/BLOCKED/DEFERRED output.
5. **Browser end-to-end automation** — introducing Playwright (or
   similar) is a real, standalone decision with its own tradeoffs
   (new dependency, new CI time cost) that should be made
   deliberately, not folded into an already-oversized slice.
6. Only after 1-5, or independently at the user's discretion: continue
   toward secure aggregation protocol work — still blocked, as in
   every prior slice, on selecting and vetting a real threshold
   secret-sharing library.

Explicit non-goals maintained this slice, per standing instruction: no
secure aggregation protocol execution, pairwise masking, private client
masks, fixed-point secure-aggregation encoding, threshold secret
sharing, share reconstruction, dropout recovery, secure unmasking,
secure aggregate reconstruction, homomorphic encryption, Byzantine-
robust aggregation, remote worker attestation, trusted execution
environments, TPM integration, Ray, Flower runtime, asynchronous/
semi-synchronous aggregation, PostgreSQL/Redis/MinIO/S3 migration, or
production Kubernetes rollout.

---

## Completion gates — evaluated

| # | Gate | Status |
|---|---|---|
| 1 | Web Security Center: overview page real and live | **Pass** — §9, §20 |
| 2 | Worker identity/signing-key admin views (list + detail + actions) | **Pass** — §9, §10 |
| 3 | Coordinator signing-key admin view (rotate/revoke) | **Pass** — §9, §10 |
| 4 | Security event explorer | **Pass** — §9 |
| 5 | Security audit explorer | **Pass** — §9 |
| 6 | Client API layer: typed, abort-capable, idempotency-key-capable | **Pass** — §11 |
| 7 | Admin mutations require reason + confirmation + idempotency + safe retry | **Pass** — §10 |
| 8 | Real-time updates, bounded buffer | **Pass** — §9 (polling, not SSE — deliberate, §3.4) |
| 9 | Signed worker-event-batch wire contract | **Pass** — §4 |
| 10 | Coordinator-side signature/replay/binding verification for batches | **Pass** — §5, §22 |
| 11 | Bounded batch/event size, no unsigned ingestion path added | **Pass** — §5 |
| 12 | Worker-side persistent queue, restart-safe, bounded | **Pass** — §6, §22 |
| 13 | Event-source health endpoint (C++ + Go) | **Pass** — §7 (RPC body/Go endpoint completed this pass) |
| 14 | Low-cardinality event-centralization metrics | **Pass** — §8, §22 |
| 15 | Grafana dashboard | **DEFERRED** — §15, §26, §31 |
| 16 | Security-focused CI gates (genuinely new, not near-duplicates) | **Pass** (2 of the many enumerated) — §16 |
| 17 | CI artifact sanitation policy respected | **Pass** — §17 (no new artifact upload added at all) |
| 18 | Exhaustive critical-event-coverage pass | **DEFERRED** — §26, §31 |
| 19 | Full 62-scenario runtime matrix / expanded validation harness | **DEFERRED** — §26, §31 |
| 20 | Browser end-to-end automation | **BLOCKED** (no framework in repo; deliberate non-goal this slice) — §26, §31 |
| 21 | Live Docker Compose validation of `SubmitWorkerSecurityEvents` with a real worker + real mTLS | **DEFERRED** — §14, §26, §31 |
| 22 | Tests added across all four languages, zero regressions | **Pass** — §22, §27 |
| 23 | Documentation and `plan.md` updated | **Pass** — §30 |
| 24 | No secure aggregation / "Do not implement" list violated | **Pass** — never attempted, verified by inspection of every diff in §18 |

**16 of 24 gates fully passed; 5 explicitly DEFERRED with stated
reasons; 1 explicitly BLOCKED (no browser-automation framework
exists); 2 gates (13, 16) note real, if bounded, completion.** No gate
in this table is reported as passing without the corresponding section
above actually demonstrating it.
