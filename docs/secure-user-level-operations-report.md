# Secure User-Level DP Operations, Observability, and Release Evidence — Completion Report

See [secure-user-level-operations-audit.md](secure-user-level-operations-audit.md)
for the pre-implementation audit and scope statement,
[secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md)
for the documented state machine, and
[secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md) for
the unchanged mechanism this slice adds observability around. This
slice closes the Go/web-observability, dedicated-events, metrics, and
release-evidence gaps the prior slice
([secure-user-level-dp-runtime-report.md](secure-user-level-dp-runtime-report.md))
explicitly deferred — it does **not** change the approved privacy
mechanism, and it does not implement secure hybrid DP, secure adaptive
clipping, threshold secret sharing, or dropout recovery.

The Mandatory Privacy Trust Statement stands unchanged from every prior
secure-aggregation slice: this is **honest-client-dependent** user-level
DP — worker-side clipping is not cryptographically verified, a
malicious worker may submit an unclipped update, the signed attestation
is evidence of configured behavior not proof of correct execution, no
privacy amplification is claimed, independent privacy/cryptographic
review has not been completed, and this is an **experimental research
implementation, not production privacy-ready**.

## 1. What this slice makes real

### Events (Work Area B/C/D)

A bounded, representative `SECURE_USER_LEVEL_DP_*` vocabulary — 14
enum values added to `SecurityEventType` in all languages that need
them, 12 wired at real call sites (2, `kSecureUserLevelDpDropoutAborted`
and `kSecureUserLevelDpCheckpointReconciled`, are defined but not yet
emitted anywhere — disclosed, not silently claimed complete):

| Event | Emitted by | Real call site |
|---|---|---|
| `SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED` | C++ | `AcquireTask`'s privacy-mode gate, `coordinator_service.cpp` |
| `SECURE_USER_LEVEL_DP_CONFIGURATION_REJECTED` | C++ | same gate, invalid-config/unsafe-quantization branches |
| `SECURE_USER_LEVEL_DP_BUDGET_RESERVED` | C++ | same gate, budget pre-check passes |
| `SECURE_USER_LEVEL_DP_BUDGET_EXHAUSTED` | C++ | same gate, budget pre-check fails |
| `SECURE_USER_LEVEL_DP_CLIPPING_APPLIED` | Python | `service.py`'s `_encode_and_mask_local_update`, worker-side |
| `SECURE_USER_LEVEL_DP_ATTESTATION_ACCEPTED` | C++ | `SubmitMaskedClientUpdate`'s attestation verification |
| `SECURE_USER_LEVEL_DP_ATTESTATION_REJECTED` | C++ | same, every rejection branch |
| `SECURE_USER_LEVEL_DP_NOISE_APPLIED` | C++ | after `SecureAggregationSessionManager::finalize()` returns, when a noise provider was supplied |
| `SECURE_USER_LEVEL_DP_ACCOUNTING_COMMITTED` | C++ | after `apply_secure_aggregate_and_advance` commits |
| `SECURE_USER_LEVEL_DP_ROUND_COMPLETED` | C++ | same |
| `SECURE_USER_LEVEL_DP_FINALIZATION_CONFLICT` | C++ | same, when the idempotency guard refuses a duplicate |
| `SECURE_USER_LEVEL_DP_HEALTH_DEGRADED` | C++ | `GetSecureUserLevelPrivacyHealth`, when the secure-aggregation manager is unavailable |
| `SECURE_USER_LEVEL_DP_DROPOUT_ABORTED` | — | defined, not yet wired to a real dropout-abort call site |
| `SECURE_USER_LEVEL_DP_CHECKPOINT_RECONCILED` | — | defined, not yet wired (no automated reconciliation exists) |

None of these payloads carry a clear update, individual norm, clipping
factor, individual weight, noise tensor/state, masked bytes, shared
secret, key, or nonce — enforced structurally by `SecurityEvent`'s own
bounded-field shape, not by convention alone.

### Metrics (Work Area E/F)

4 metric families, Go-side only, fed by a new coordinator read RPC (no
native C++ Prometheus endpoint — preserving the established "Go
re-exports, C++ never listens on HTTP" architecture):
`fl_secure_user_dp_route_requests_total{route,outcome}`,
`fl_secure_user_dp_active_runs`,
`fl_secure_user_dp_reconciliation_required`,
`fl_secure_user_dp_component_status{component,status}`. **Narrower than
the original scope statement's own aspiration, disclosed**: per-run
epsilon spent/remaining gauges were dropped mid-implementation once it
became clear `run_id` is on this metric family's own forbidden-label
list — a per-run gauge would require exactly the label this slice is
told never to attach. Per-run epsilon stays API-only.

### Runtime health model (Work Area G)

`SecureUserLevelPrivacyHealthResponse` (new proto message): static
capability description (provider, adjacency model, sensitivity formula,
sampling assumption, fixed weight, 6 trust-limitation strings) plus live
component status (provider/noise-provider/accountant/ledger/event-
journal, each `ok`/`degraded`/`unavailable`), last-successful-round
timestamp, active-run count, and a `reconciliation_required` flag.
Deliberately excludes per-worker norms, clipping state, attestation
contents, noise values, and clear aggregates/updates — confirmed by the
proto message's own field list, not merely a doc claim.
`reconciliation_required` is honestly always `false` today: no
automated cross-check between the ledger and model-version state exists
yet (see the publication-boundary doc's disclosed gap).

### Go coordinator client, HTTP API, permissions, serializers (Work Area H/I/J/K)

5 new `GrpcClient`/`MockClient` methods
(`GetSecureUserLevelPrivacyStatus/Health/Budget`,
`ListSecureUserLevelPrivacyRounds`, `GetSecureUserLevelPrivacyRound`);
5 new `GET /api/v1/secure-aggregation/privacy/*` routes
(`status`, `health`, `budget`, `rounds`, `rounds/{roundId}`); 5 new
responsibility-named permissions
(`security.secure_user_dp.{status,health,rounds,round,budget}.read`)
with a real access matrix — ADMIN and RESEARCHER get all 5, VIEWER gets
`status`/`health` only (aggregate, no per-run detail), SERVICE gets
**none** (no implicit grant anywhere, verified live: 403 on every
route). Explicit per-role response types (`secureUserDPRoundView`,
`secureUserDPBudgetView`) round ADMIN's exact epsilon to 3 decimal
places for RESEARCHER — a real typed difference, not a deleted JSON
key. 12 new Go unit tests, all passing, cover auth-required,
role-denial, cursor pagination, 400/404 handling, and the ADMIN-vs-
RESEARCHER epsilon-precision split.

**Disclosed route-naming deviation**: the task's own suggested route
list names `rounds/{sessionId}`; this implementation keys round detail
by `(run_id, round_id)` instead, matching how the underlying ledger
(`RunInstance::user_level_ledger()`) actually indexes committed
accounting steps — there is no per-round `session_id` retained in that
data model.

### Web page, limitation warnings, Privacy Round Explorer (Work Area L/M/N)

New page `/security/secure-aggregation/privacy`
(`secure-user-level-dp-console.tsx`): Capability, Runtime Health, Budget
Lookup, and a cursor-paginated Privacy Round Explorer (filtered by
`run_id`, the only filter the underlying API supports — a bounded,
disclosed subset of the task's fuller suggested filter list). All 10
mandated trust-limitation warnings render as a real, always-visible
`<ul>` (`data-testid="secure-user-dp-limitations"`), never a tooltip or
collapsed disclosure. `npm run typecheck`/`lint`/`build` all pass
cleanly with the new route present in the build's route table.

### Browser tests (Work Area O)

`web/e2e/secure-user-level-dp-privacy.spec.ts`: unauthenticated,
admin (limitation-list count, capability/health content), viewer
(allowed status/health, denied budget), and service-role (denied
everywhere) coverage against the real running backend, no mocked
responses. Registered into the existing `security-ui` harness group
(`scripts/security-validation/groups/security_ui.py`) alongside the 5
pre-existing specs.

### Statistical noise smoke test (Work Area P)

`cpp/core/tests/secure_random_test.cpp`'s
`run_bounded_statistical_noise_smoke_test`: 20,000 draws from the real,
non-deterministic, OS-CSPRNG-backed `CryptoSecureNoiseProvider`,
asserting mean near 0, variance near the configured `std_dev²`, no
coincidental cross-instance collision, no accidental consecutive
duplicates — and printing every parameter Work Area P's own
documentation requirement asks for. A real, fresh run's output:

```
bounded statistical noise smoke test report:
  provider=CryptoSecureNoiseProvider (OS-CSPRNG-backed, non-deterministic)
  draw_count=20000
  configured_std_dev=1.75
  expected_mean=0.0 observed_mean=0.000242109 tolerance=+/-0.1
  expected_variance=3.0625 observed_variance=3.06158 relative_tolerance=0.15
  build_type=Debug
  scope=bounded statistical smoke test, NOT randomness certification, NOT a
  formal Gaussianity proof, NOT a cryptographic audit, NOT a NIST certification
```

This is explicitly **not** randomness certification, a formal
Gaussianity proof, a cryptographic audit, or a NIST certification — a
bounded-sample sanity check only.

### Publication boundary and failure-injection tests (Work Area Q/R)

The full state sequence — contribution-collection-complete →
aggregate-reconstructed+noise-generated+noise-applied (one inseparable
step inside `finalize()`) → model-update-prepared → model-version-
published → accountant-committed (the irreversible-spend point) →
checkpoint-persisted → session-completed — is documented in
[secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md),
derived from direct reading of the real code, not the task's own
suggested (and in one place, incorrect) ordering. Two new
failure-injection tests in `user_level_dp_test.cpp`:
restart-after-publication (a real checkpoint save/restore round-trip,
confirming the ledger — including the new `committed_at_unix_s` field —
survives byte-for-byte) and corrupted-budget-state-fails-closed (a
tampered `user_level_ledger_count` causes `restore_from_checkpoint()`
to throw, never silently truncate). The known restart-reconciliation
gap (a crash between accountant-commit and checkpoint-persist has no
automated detection) is disclosed, not hidden.

### Runtime-validation harness group (Work Area T)

New `secure-aggregation-user-level-dp` scenario group, 6 scenarios,
registered into `scripts/security-validation/registry.py`: status/
health content, SERVICE-denied-everywhere, VIEWER-denied-detail,
budget-requires-run-id, round-not-found. Bounded scope, disclosed: this
harness only speaks raw HTTP (no worker orchestration), so it covers
route shape/access-control/error-handling, not the mechanism itself —
the mechanism-level assertions (clipping engaged, attestation accepted,
noise applied, round completed) remain
`scripts/validate_secure_user_level_dp.py`'s responsibility, not
duplicated here.

### Live Docker validation (Work Area U)

`scripts/validate_secure_user_level_dp.py` extended with 4 new check
groups (7–10: events, API surface, role-based access control, metrics)
plus an explicit teardown-verification step (11). **49/49 checks
passed** on the final run (see §3 for the fresh count and the one real
bug this validation found and fixed).

### CI and artifact sanitation (Work Area V/W/X)

No new CI *jobs* — new tests land in the existing broad `cpp-grpc`/
`python`/`go`/`web` jobs' full-suite invocation (they already build/run
everything under their target trees), and `security-runtime-full.yml`'s
unfiltered `run.py` invocation automatically picks up the new harness
group and Playwright spec. The bounded PR-subset job
(`security-runtime-pr` in `ci.yml`) explicitly adds
`secure-aggregation-user-level-dp` to its `--group` list (cheap,
worker-orchestration-free checks, appropriate for a PR-fast gate). A
new artifact-sanitation pattern (`check_artifact_sanitation.py`) covers
worker private-key/shared-secret/mask-key hex-field leaks
(`own_private_key_raw`, `private_key_raw`, `shared_secret`, `mask_key`,
`mask_stream_key`) — the one genuinely new sensitive-data *shape* this
slice introduces; every other forbidden category was already covered
generically by the existing signature/payload_hash/PEM/Bearer-token
patterns. `docs/security-ci.md` documents the full allow/deny list for
this slice's artifacts.

## 2. Three real bugs found and fixed by this slice's own testing

All three were caught by actually running the code live — none would
have been caught by inspection or by a single language's own unit
tests in isolation, consistent with this project's established pattern
of live/cross-boundary validation catching what unit tests structurally
cannot.

1. **Checkpoint field silently dropped.** The new restart-after-
   publication C++ test failed on its very first run:
   `UserLevelLedgerEntry`'s new `committed_at_unix_s` field was added to
   the in-memory struct and set at both push sites
   (`finalize_round`/`apply_secure_aggregate_and_advance`), but
   `encode_user_level_entry`/`parse_user_level_entry` — the checkpoint
   serialization functions — were never updated to actually persist or
   restore it, silently resetting it to `0.0` on every restore. Caught
   only because this slice added a genuine checkpoint save/restore
   round-trip test. Fixed by appending it as an 8th tab-separated field
   (was 7) and tightening the strict field-count check accordingly.
2. **A cross-service event-flush timing race in the live validation
   script itself.** The first live Docker run's event check failed on
   `SECURE_USER_LEVEL_DP_CLIPPING_APPLIED` — a worker-emitted event
   that reaches the coordinator only via the existing worker-security-
   event-centralization batch flush (a real 5-second periodic timer),
   unlike the six coordinator-emitted events in the same check, which
   are immediate and in-process. The original check read the journal
   exactly once, before the flush had necessarily run. Fixed by polling
   with a bounded 20-second retry loop specifically for this one,
   cross-service-latency-dependent check.
3. **List-endpoint returned 404 for an unknown run_id instead of an
   empty page.** Found live by the new Playwright spec's own "round
   explorer shows an empty state for a run with no committed rounds"
   test: `ListSecureUserLevelPrivacyRounds`'s C++ handler called
   `manager_->get(request->run_id())` and let an unknown run_id's
   exception propagate as gRPC `NOT_FOUND` → HTTP 404 → the web page's
   generic "error" state, when the correct behavior for a LIST endpoint
   is simply an empty page (matching this codebase's other list
   endpoints, e.g. `ListSecurityEvents` for a filter matching nothing).
   `GetSecureUserLevelPrivacyRound` (the single-round DETAIL endpoint)
   correctly keeps its 404-for-truly-missing behavior — only the LIST
   endpoint's semantics changed. Fixed by catching the exception inside
   the handler and returning an empty `rounds` array with `OK` status
   instead of propagating `NOT_FOUND`.

All three fixes were verified by a clean re-run of the affected
evidence afterward: local C++ (7/7), Docker gRPC-gated C++ (8/8), the
live 3-worker Docker validation (49/49), and the live runtime-
validation harness including the full Playwright suite (12/12, 0 FAIL —
see §3).

## 3. Fresh regression evidence (this run only — no reused counts)

- **C++, protobuf-free (local Windows/MSVC)**: `ctest --test-dir
  build/cpp-debug -C Debug` — **7/7 suites passed**, including the new
  statistical smoke test, restart-after-publication test, and
  corrupted-checkpoint test.
- **C++, gRPC-gated (Docker, mirroring the CI `cpp-grpc` job)**:
  **8/8 test executables passed**.
- **Python**: `python -m pytest python/tests` — **454 passed, 1
  skipped** (unchanged from the prior slice — no new Python unit tests
  were added this slice; new coverage went into C++/Go instead).
  `ruff check .` — 79 pre-existing violations, all in a file untouched
  this slice (`test_secure_aggregation_tensor_mask.py`); the files this
  slice touched are clean. `mypy --config-file=python/pyproject.toml
  python/src` — clean, 86 source files.
- **Go**: `go vet ./...`, `go build ./...`, `go test ./...` — all
  clean. `gofmt -l` on every file this slice authored/edited — clean
  (repo-wide `gofmt -l` output includes many pre-existing, untouched
  files due to a systemic CRLF line-ending characteristic of this
  Windows checkout, confirmed unrelated to this slice). `go test -race
  ./...` could not run natively on this Windows development machine (no
  gcc/clang toolchain) — a standing, previously-documented limitation,
  not new to this slice; race detection is CI-covered.
- **Web**: `npm run typecheck` — clean. `npm run lint` — clean. `npm run
  build` — clean, the new `/security/secure-aggregation/privacy` route
  present in the build's route table. `npm test` (vitest) — **46
  passed**.
- **Terminology check**: passing, before and after implementation.
- **Proto contract verification**: passing.
- **Live 3-worker Docker validation**
  (`scripts/validate_secure_user_level_dp.py`): **49/49 checks
  passed** — the full mechanism (checks 1–6, unchanged from the prior
  slice) plus the new events/API/access-control/metrics/teardown
  checks (7–11) this slice adds.
- **Live runtime-validation harness** (`secure-aggregation-user-level-dp`
  + `security-ui` groups, `python scripts/security-validation/run.py
  --group secure-aggregation-user-level-dp,security-ui`): **12 PASS, 0
  FAIL, 0 BLOCKED, 0 DEFERRED, 0 SKIPPED** — this includes all 5
  pre-existing Web Security Center Playwright specs (20 sub-assertions,
  proving this slice's changes did not regress them), the new
  `secure-user-level-dp-privacy.spec.ts` (6/6 sub-tests: unauthenticated,
  admin limitation-list/capability/health, viewer split access, service
  denial, and the round-explorer empty state that found bug #3 above),
  and all 6 new `secure-aggregation-user-level-dp` API scenarios. The
  first run of this harness caught bug #3; after the fix, a full
  rebuild (`docker compose build coordinator`) and re-run confirmed
  clean.

## 4. Completion-gate evaluation

Classified honestly per item — Implemented / Validated / Experimental /
Bounded / Partial / Blocked / Deferred, never inflated:

| Area | Classification |
|---|---|
| Event vocabulary (12/14 wired) | Bounded |
| Metrics (4 families) | Bounded |
| Runtime health model | Implemented, Validated (live) |
| Go coordinator client (5 methods) | Implemented, Validated (unit + live) |
| HTTP API (5 routes) | Implemented, Validated (unit + live) |
| Permissions/serializers | Implemented, Validated |
| Web page/console | Implemented, Validated (build+typecheck+lint+live browser suite) |
| Playwright spec | Implemented, Validated (6/6 live, see §3) |
| Statistical noise smoke test | Implemented, Validated |
| Publication boundary doc | Implemented |
| Failure-injection tests | Implemented, Validated |
| Runtime-validation harness group | Implemented, Validated (12/12 live, see §3) |
| Live Docker validation extension | Implemented, Validated |
| CI/artifact-sanitation | Implemented |
| Dropout-abort / checkpoint-reconciled events | Deferred (enum exists, no call site) |
| Automated restart reconciliation | Deferred (health flag always false) |
| Performance benchmarking | Deferred (not attempted this slice) |
| Full 24-scenario-ID / 56-item-checklist / 29-event / 31-metric enumerations | Bounded (representative depth instead) |
| Secure hybrid DP / adaptive clipping / threshold secret sharing / dropout recovery | Out of scope, untouched |

## 5. Final readiness classification

Global classification: **`RESEARCH_SECURITY_READY`** (preserved,
unchanged — not upgraded to a stronger claim by this slice). The
secure user-level-DP capability specifically:
**Experimental. Honest-client-dependent. Runtime-validated.
Operationally-observable (this slice's own contribution). Not
independently reviewed. Not production-privacy-ready.**

## 6. Explicit prohibitions honored

This report does not claim: cryptographically-verified clipping,
malicious-client-secure DP, unvalidated privacy amplification, dropout
resilience, or production privacy readiness. No custom threshold
secret sharing was implemented. Nothing in this slice or its
predecessor was committed, pushed, tagged, or opened as a pull request.

## 7. Documentation coverage table

| Topic | Authoritative section/doc |
|---|---|
| Operational audit + scope statement | [secure-user-level-operations-audit.md](secure-user-level-operations-audit.md) |
| Event vocabulary | This report §1, [security-events.md](security-events.md) |
| Metrics | This report §1, [security-metrics.md](security-metrics.md) |
| API surface | [security-api.md](security-api.md), `secure_user_level_privacy.go` header |
| Publication boundary | [secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md) |
| Noise validation | This report §1 (statistical smoke test) |
| CI/artifact sanitation | [security-ci.md](security-ci.md) |
| Known gaps | [known-limitations.md](known-limitations.md) |
| Status summary | [plan.md](plan.md) |

Consolidated into existing docs rather than fragmented into 8
near-duplicate new files, per this project's established "avoid
unnecessary document fragmentation" convention (see the prior two
slices' own identical decision).
