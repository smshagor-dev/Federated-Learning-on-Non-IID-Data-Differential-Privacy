# Secure User-Level DP Operations, Observability, and Release Evidence — Audit (Work Area A)

Written before implementation, per this project's established working
method. This task explicitly does **not** touch the approved privacy
mechanism (`docs/secure-user-level-dp-semantics.md`) — it closes the
operational gaps the immediately prior slice
(`docs/secure-user-level-dp-runtime-report.md`, "What remains bounded
or deferred") deliberately deferred: dedicated events, metrics, Go/Web
observability, statistical noise smoke evidence, performance evidence,
a runtime-validation harness group, and expanded live/CI evidence.

## Investigation findings (direct code reading, not the task spec's own claims)

- **Go role-gating pattern** (`go/internal/transport/httpapi/server.go:115`):
  every existing `/api/v1/security/*` route is registered as
  `mux.Handle(path, s.withAuth(securityRoles...)(http.HandlerFunc(...)))`
  where `securityRoles := []auth.Role{auth.RoleViewer, auth.RoleResearcher,
  auth.RoleAdmin, auth.RoleService}` — i.e. today's security routes grant
  SERVICE role blanket read access. This task's Work Area J explicitly
  requires "SERVICE gets NO implicit access — every permission must be
  explicit," which is a **stricter** policy than the existing
  `securityRoles` pattern. The new `/api/v1/secure-aggregation/privacy/*`
  routes therefore do **not** reuse `securityRoles`; they use a
  dedicated `Allows(role, perm)` check (see
  `go/internal/security/permissions.go`'s existing `rolePermissions` map,
  which already excludes `auth.RoleService` entirely — this is the
  correct existing precedent to extend, not `securityRoles`).
- **Permission naming precedent** (`permissions.go:22-44`):
  `security.<area>.<verb>` (e.g. `security.overview.read`,
  `security.event_sources.read`) — the new
  `security.secure_user_dp.{status,rounds,round,budget,health}.read`
  names follow this exactly.
- **Metrics precedent** (`go/internal/observability/telemetry.go`):
  Go-side hand-rolled Prometheus text exposition (no `client_golang`
  dependency), one `MetricsRecorder` struct field + `Record*` method +
  `WritePrometheus` block per metric family, fed either by Go's own
  request handling or by a value the coordinator returns over a
  read RPC (`RecordSecurityEventSourceHealth` is the exact precedent for
  "coordinator computes, Go re-exports as a gauge, no native C++ HTTP
  endpoint" — reused verbatim for this task's Work Area F instruction).
- **Web/Playwright infrastructure is real and reusable**: `web/e2e/*.spec.ts`
  (5 existing specs) use `loginAs(page, role)` from `./fixtures/auth`
  against a real, unmocked backend; `web/playwright.config.ts` and the
  `playwright`/`playwright-core` packages are installed. No
  `web/app/security/secure-aggregation` or `.../privacy` page exists yet.
  This means Work Area O (browser tests) is genuinely implementable, not
  a "no framework" deferral.
- **`scripts/security-validation/registry.py`** has 14 existing scenario
  groups (`transport`, `worker_identity`, `worker_keys`, `signed_messages`,
  `privacy_authenticity`, `signed_tasks`, `event_centralization`,
  `event_journal`, `audit_journal`, `security_api`, `security_ui`,
  `metrics`, `recovery`, `regression`) — no `secure_aggregation` or
  `secure_user_level_dp` group exists. The `framework.py` harness
  (`Scenario`/`Context`/`Status`) mechanically prevents a
  BLOCKED/DEFERRED scenario from ever reporting PASS (`__post_init__`
  raises if a `run` callable is paired with those statuses) and requires
  `ctx.assert_true` to fire at least once for a real PASS — this is the
  harness this task's Work Area T scenario group is added into.
- **Doc-name discrepancies** (already confirmed twice in prior slices,
  reconfirmed here): `docs/security-runtime-scenario-registry.md` does
  not exist (real: `scripts/security-validation/registry.py`);
  `docs/privacy-accounting.md` does not exist (real:
  `docs/privacy-mathematics.md`); `docs/privacy-budget-policy.md` does
  not exist (real: `docs/privacy-budget-policies.md`).
- **CMake flags in the task's "Required Validation Commands"**
  (`-DFL_ENABLE_CRYPTO=ON`, `-DFL_BUILD_GRPC_COORDINATOR=ON`,
  `-DFL_ENABLE_MTLS=ON`, `-DFL_ENABLE_SECURE_AGGREGATION=ON`) do not
  correspond to this repo's real CMake option set (confirmed against
  `cpp/CMakeLists.txt`/`Makefile` again this slice, matching every prior
  slice's identical finding). The real commands used for validation in
  this slice are the plain `cmake -S cpp -B build/cpp-debug` +
  `ctest` invocation for the protobuf-free target set, and the
  Docker-based build (`infra/docker/cpp-coordinator.Dockerfile`,
  `scripts/generate_protos.sh`) for the gRPC-gated coordinator — not
  invented new CMake options.
- **`go test -race ./...`** cannot run natively on this Windows
  development machine (no gcc/clang toolchain present, a standing,
  previously-documented limitation, not new to this slice) — `go vet`,
  `go build`, and `go test ./...` (without `-race`) run natively; race
  detection is CI-covered (`go` CI job) and is recorded here as a
  disclosed local-machine limitation, not silently skipped.

## Per-capability operational coverage table (Work Area A)

Status legend: **Y** = yes/present, **N** = no, **B** = bounded (real,
partial), **—** = not applicable this slice.

| Capability | Implemented | Unit-tested | Cross-lang-tested | Docker-tested | Restart-tested | API-observable | Web-observable | Event-emitted | Metric-emitted | Sanitized evidence |
|---|---|---|---|---|---|---|---|---|---|---|
| Worker-side global L2 clipping | Y | Y | — | Y | N | B (health only) | B | B (new) | B (new) | B |
| Quantization-aware effective sensitivity | Y | Y | Y | Y | N | B | B | N | B (new) | B |
| Signed user-level privacy attestation | Y | Y | Y | Y | N | N | N | B (new) | B (new) | B |
| Central aggregate Gaussian noise (deterministic test) | Y | Y | — | B (indirect) | N | N | N | B (new) | B (new) | B |
| Central aggregate Gaussian noise (production CSPRNG) | Y | N (new smoke test this slice) | — | Y | N | N | N | B (new) | B (new) | B (new) |
| Accountant commit exactly once | Y | Y | — | Y | B (new tests) | B (new) | B (new) | B (new) | B (new) | B |
| Budget pre-check ("reservation") | Y | Y | — | B | N | B (new) | B (new) | B (new) | B (new) | B |
| Publication boundary ordering | Y (implicit) | N | — | B (indirect) | N | N | N | B (new) | N | B (new, this slice's doc) |
| Go coordinator client read methods | N | N | — | — | — | — | — | — | — | — |
| Go HTTP read routes | N | N | — | — | — | — | — | — | — | — |
| Web privacy observability page | N | N | — | — | — | — | — | — | — | — |
| Runtime-validation harness group | N | — | — | — | — | — | — | — | — | — |

Rows below "Publication boundary ordering" describe pure gaps this
slice fills; rows above describe the mechanism (already real, from the
prior slice) gaining new observability without any change to its
behavior.

## Scope statement

This task's literal specification (Work Areas A–Z, a 36-step
implementation order, ~70 completion gates, a 61-section final report)
is, again, far larger than any single slice can cover at uniform,
maximal depth. Following this project's own established precedent
(three prior oversized slices, each with a disclosed Full/Bounded/
Deferred split), this slice is scoped as follows:

**Full depth** (real, working, tested code — not stubs):
- Work Area A: this audit.
- Work Area B/C/D: a **bounded, representative** `SECURE_USER_LEVEL_DP_*`
  event vocabulary (~14 of the ~29 named types — the lifecycle
  checkpoints an operator most needs: configuration accepted/rejected,
  budget pre-check passed/exhausted, clipping applied, attestation
  accepted/rejected, noise applied, accounting committed, round
  completed, dropout/finalization-conflict aborted), wired at real
  Python/C++ call sites — not merely enum definitions. The remaining
  ~15 names (finer-grained sub-steps like separate
  encoding/masking/signing-stage events) are documented as unwired,
  matching the exact disclosure pattern used for event coverage in the
  "Security Events, Metrics, and Durable Audit Journal" slice's own
  Work Package M.
- Work Area E/F: a **bounded, representative** metric set, Go-side
  only, fed by one new coordinator read RPC — no native C++ Prometheus
  endpoint added (preserving the established architecture). **Narrower
  than originally scoped here, disclosed**: per-run epsilon spent/
  remaining gauges are not implemented, because `run_id` is on this
  metric family's own forbidden-label list (see Work Area E's own
  instruction) — a per-run gauge would require exactly the label this
  slice is told never to attach. What is implemented instead: 4 metric
  families (`fl_secure_user_dp_route_requests_total{route,outcome}`,
  `_active_runs`, `_reconciliation_required`,
  `_component_status{component,status}`) — route-level request counts
  and aggregate-only runtime health. Per-run epsilon stays API-only
  (`GET .../privacy/budget`), never a metric — see
  docs/security-metrics.md.
- Work Area G/H/I/J/K: the typed privacy runtime-health model, the Go
  coordinator client's 5 new read methods, the 5 new HTTP routes, the
  5 new permission names with a real ADMIN/RESEARCHER/VIEWER/SERVICE
  matrix (SERVICE excluded by default, per the task's explicit
  instruction), and per-role response types — all real.
- Work Area L/M/N: one new Web page
  (`/security/secure-aggregation/privacy`) with real sections
  (Capability, Mechanism, Budget, Runtime Health), the prominent
  limitation warnings, and a bounded round explorer (cursor pagination
  against the new list route; a bounded filter set, not every filter
  the task lists).
- Work Area O: real Playwright tests against the new page (per-role +
  a bounded set of failure states — not all six the task lists).
- Work Area P: a real, new, explicitly-bounded statistical smoke test
  against the production `CryptoSecureNoiseProvider` (documented draw
  count/tolerance/environment; explicitly not described as
  certification).
- Work Area R: the publication-boundary state machine, documented, plus
  a bounded set of new failure-injection tests (not every adjacent-pair
  combination the task enumerates).
- Work Area T: a new `secure_aggregation_user_level_dp` scenario group
  in the existing harness — a bounded scenario count (not all 24 named
  IDs; the highest-value subset: status, health, configuration
  accept/reject, worker-clip, attestation accept/reject, noise-apply,
  budget reserve/exhausted, events, metrics, api, web).
- Work Area U: the existing `validate_secure_user_level_dp.py` extended
  with a bounded set of new checks (API responses, event presence,
  metric presence, duplicate-finalization rejection, teardown
  verification) — not a literal 56-item enumeration.
- Work Area Y/Z: consolidated documentation (this audit +
  `docs/secure-user-level-operations-report.md` + targeted updates to
  existing docs, not 8 new near-duplicate files) and a `plan.md` status
  block.

**Real but bounded**:
- Work Area Q: a **focused** set of new accounting tests (duplicate
  finalization, restart-before/after-publication, corrupted budget
  state fail-closed) added to the existing `user_level_dp_test.cpp` /
  Python test suites, not a wholly separate new test binary.
- Work Area V/W/X: no new CI *jobs* are added (matching the immediately
  prior slice's own finding that the existing broad `cpp-grpc`/
  `python`/`go`/`web` jobs already pick up new tests by full-suite
  invocation) — new tests land inside those jobs; the artifact
  allow/deny policy is documented (extending
  `docs/security-ci.md`'s existing sanitation policy) rather than a
  wholesale new detection engine, since no genuinely new sensitive-data
  shape is introduced beyond what `_SECRET_PATTERNS`-style detection
  already covers (noise tensors/keys/nonces were already forbidden
  categories from the prior slice).

**Deferred, disclosed, never reported as done**:
- Work Area S: performance benchmarking — the existing benchmark
  harness (`cpp/benchmarks/aggregation_benchmark.cpp`) is not extended
  with new secure-user-level-DP-specific entries this slice; disclosed
  as a real gap, not fabricated with invented numbers.
- The literal 24-scenario-ID / 56-item-checklist / 61-section /
  70-gate / 29-event / 31-metric enumerations — addressed at
  representative, real depth above, not literal exhaustive counts.
- Everything under "Explicitly Out of Scope" in the task
  (secure hybrid DP, secure adaptive clipping, threshold secret
  sharing, dropout recovery, and the rest) — none of it is touched.

See [secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md)
for the unchanged mechanism this slice adds observability around, and
[secure-user-level-dp-runtime-report.md](secure-user-level-dp-runtime-report.md)'s
"What remains bounded or deferred" section for the exact gaps this
slice is closing.
