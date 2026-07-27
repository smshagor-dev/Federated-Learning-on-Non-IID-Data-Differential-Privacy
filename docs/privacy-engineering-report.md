# Privacy Engineering Phase Final Report

## 1. Repository audit (starting point)

The Foundation, Aggregation Core, Coordinator Runtime, and Personalization
& Algorithm Expansion phases delivered: a real C++ aggregation core and
gRPC coordinator runtime, a Python worker with FedAvg/FedProx/SCAFFOLD/
FedSAM/Ditto/Per-FedAvg training, a Go control plane, and a Next.js web
dashboard — all cross-language-integration-tested. Differential privacy
existed only in the **separate, older** root-level legacy prototype
(`federated/`), which is not part of this platform. This phase began
from that base; no prior-phase code was rewritten without a proven
defect (five were found and fixed across this phase; see §19).

## 2. Privacy Engineering phase architecture

Three independent DP mechanisms, each protecting a different neighboring
relation, enforced by the Critical Privacy Rule
([privacy-mathematics.md](privacy-mathematics.md)): **sample-level DP**
(Python worker, Opacus, per training example), **user-level DP** (C++
coordinator, central clip+noise, per client round contribution), and
**adaptive clipping** (C++ coordinator, a privatized quantile
controller). **Hybrid DP** composes the first two on one run without
ever combining their epsilon values. Every mechanism has: a dedicated
accountant, a dedicated ledger, a budget policy, checkpoint/recovery
coverage, worker-capability-gated task assignment (sample-level/hybrid
only), Prometheus visibility, and a web Privacy Center panel.

## 3. Contract changes (proto)

New `proto/privacy/privacy.proto`: `PrivacyMode`, `SampleLevelDPConfig`/
`UserLevelDPConfig`/`AdaptiveClippingConfig`, `SampleLevelLedgerEntry`/
`UserLevelLedgerEntry`/`AdaptiveClippingLedgerEntry`, `PrivacyBudgetPolicy`,
`WorkerPrivacyCapabilities`, `AccountantType`. `proto/coordinator/coordinator.proto`
extended: `CreateRunRequest.privacy_config`, `GetPrivacyMetrics`/
`GetPrivacyLedger`/`GetPrivacyProjection` RPCs. `proto/worker/worker.proto`
extended: `ClientTrainingTask.sample_level_dp_active`/`sample_level_privacy`,
`SubmitClientResultRequest.sample_level_privacy`,
`RegisterWorkerRequest.privacy`. All additive — no existing field
renumbered or removed. `scripts/verify_proto_contracts.py` extended and
passing.

## 4. Sample-level DP

See [privacy-mathematics.md](privacy-mathematics.md). Real Opacus
`PrivacyEngine` wrapping in the Python worker's training loop
(`fl_platform/worker/service.py`, `task_runner.py`); `SampleLevelAccountant`
backed by Opacus's own RDP/PRV/GDP accountants; a truthful
`opacus_capabilities()` install probe (never hardcoded `True`). Real
private training exercised for FedAvg and FedProx; unsupported algorithm
combinations (SCAFFOLD, FedSAM) rejected before dispatch, not silently
approximated. Live-validated through Docker Compose — real Opacus
training inside a container (see §17, §26).

## 5. User-level DP

See [user-level-dp.md](user-level-dp.md). Central clip → aggregate →
noise pipeline in `RunInstance::finalize_round`
(`cpp/coordinator/src/run_manager.cpp`), reusing the existing
aggregation algorithms unchanged. Privacy-safe weighting is enforced
(`kSampleCount` rejected at `CreateRun` for user-level/hybrid DP).
`NoiseProvider` abstraction (`DeterministicNoiseProvider` for tests,
`SecureNoiseProvider` — OS-entropy-seeded, not a CSPRNG — for runtime).
Live-validated: a real 2-round run's reported epsilon values
(5.302585092994046, 7.837641821656742) were independently hand-verified
against the RDP formula and matched to full precision.

## 6. Adaptive clipping

See [adaptive-clipping.md](adaptive-clipping.md). A quantile-based
dynamic clip bound (Andrew et al., 2021), privatizing the over-threshold
count itself as its own Gaussian-mechanism query before it ever
influences the bound. A real sign-convention bug (lowering the bound
when too many clients were being clipped, the opposite of the intended
direction) was caught by a direction-checking test during development,
not by inspection. Convergence validated statistically: the controller
settles within 25% of a distribution's true median norm after 150
rounds starting deliberately far off.

## 7. Hybrid DP

See [hybrid-dp.md](hybrid-dp.md). `PrivacyMode::kHybridDp` runs
sample-level and user-level DP simultaneously on one run — not a third
mechanism, but the first two mechanisms' existing code paths both
switched on. `RunInstance::make_descriptor` marks dispatched tasks
`sample_level_dp_active`; `finalize_round`'s clip/aggregate/noise
pipeline runs unconditionally. A round produces N sample-level entries
(one per client) and one user-level entry — never zipped together.

## 8. Privacy ledger

See [privacy-ledger.md](privacy-ledger.md). Three independent ledgers,
one authority split each: Python computes and the coordinator only
stores/relays the sample-level ledger (with a cross-check that
`run_id`/`round_id`/`client_id` match the lease-validated submission —
see §18's security-audit finding); the coordinator computes and owns
the user-level and adaptive-clipping ledgers outright. All three persist
across a coordinator restart via the existing checkpoint mechanism.

## 9. Privacy compatibility matrix

See [privacy-compatibility-matrix.md](privacy-compatibility-matrix.md).
`python/src/fl_platform/privacy/compatibility.py` is the single source
of truth (`SUPPORTED`/`EXPERIMENTAL`/`UNSUPPORTED`/`DEFERRED` per
algorithm per mechanism); `go/internal/privacy/compatibility.go` is a
hand-mirrored copy, cross-checked by test but not generated (a
documented limitation, §27). `hybrid_status()` takes the worse of the
two mechanisms' statuses. Consumed by the Go `/privacy/compatibility`
endpoint and the web experiment builder.

## 10. Worker privacy capability advertisement

See [worker-privacy-capabilities.md](worker-privacy-capabilities.md).
`WorkerPrivacyCapabilities` sent once at `RegisterWorker`, truthfully
probed (`opacus_capabilities()`, never optimistic). `RunInstance::acquire_task`
gates sample-level/hybrid-DP task assignment to only workers that
advertised `supports_sample_level_dp` — an incompatible worker never
receives a task from such a run (a `std::nullopt`, same as "no task
right now," not a hard failure). User-level-only runs impose no gate.

## 11. Privacy budget policies

See [privacy-budget-policies.md](privacy-budget-policies.md). Four
policies (`kWarnOnly`/`kStopBeforeExceeding`/`kStopAfterCurrentRound`/
`kFailRun`) applied independently per mechanism in
`RunInstance::finalize_round`, with `kStopBeforeExceeding` uniquely
preventive (checked against a projected epsilon before the round's
mechanism runs, never partially applying it). Sample-level DP's own
`epsilon_budget` field exists but was found, while documenting this
section, to be informational-only — not enforced by the worker's
training loop (a real gap, not a fixed one; see §18, §27).

## 12. Checkpoint/recovery extension

All three ledgers, each accountant's step count, and (if adaptive
clipping is active) the current clip bound are part of the coordinator's
FNV1a-checksummed checkpoint body and restored on
`restore_from_checkpoint()` — a restart mid-run does not reset
accumulated epsilon. `privacy_recovery_test.cpp` specifically confirms
epsilon and clip bound both continue their trajectory across a simulated
restart, not restart from scratch.

## 13. C++ coordinator changes

New: `fl_core/privacy.hpp`/`.cpp` (accountants, noise providers, adaptive
clip controller), `GetPrivacyMetrics`/`GetPrivacyLedger`/`GetPrivacyProjection`
RPCs, `PrivacyBudgetPolicy` enforcement, worker capability gating in
`acquire_task`. 6 new/extended coordinator test suites (`user_level_dp_test.cpp`,
`adaptive_clipping_test.cpp`, `hybrid_dp_test.cpp`, `privacy_budget_policy_test.cpp`,
`privacy_recovery_test.cpp`, `worker_privacy_capability_test.cpp`), plus
`cpp/core/tests/privacy_test.cpp`. A real cross-check gap in
`SubmitClientResult`'s sample-level entry decoding was found and fixed
during the security audit (§18). All 6 CTest suites (both Debug and
Release) pass — including one real test-assumption bug found and fixed
during this report's own final validation pass (§19.5).

## 14. Go control-plane changes

New package `internal/privacy` (hand-mirrored compatibility matrix);
extended `internal/coordinator` (privacy RPC client methods),
`internal/transport/httpapi` (`privacy_handlers.go` — compatibility
endpoint; privacy metrics/ledger/projection routes in
`coordinator_handlers.go`), `internal/observability/telemetry.go`
(`fl_privacy_epsilon{mechanism,run_id}` gauge, `fl_privacy_budget_events_total`
counter, sourced from the existing `GetPrivacyMetrics`/`StreamRunEvents`
relay — a deliberate extension of Go's existing role as the metrics
aggregation point, not a new C++ HTTP server; see §27).

## 15. Python worker changes

New `fl_platform/privacy/` package: `accounting.py` (Opacus-backed
`SampleLevelAccountant`, `opacus_capabilities()`), `compatibility.py`,
`config.py`, `ledger.py`, `adaptive_clipping.py`, `metrics.py`
(`prometheus_client`-backed, opt-in HTTP endpoint via
`WorkerConfig.metrics_port`). Real Opacus integration in
`worker/service.py`/`task_runner.py`'s training loop.
`GrpcCoordinatorClient` extended for the full privacy wire surface
(capability advertisement, ledger submission, config decoding) — one
real dropped-field bug found and fixed here (§19.2).

## 16. Web dashboard changes

New `PrivacyCenterPanel` (`web/features/runs/privacy-center-panel.tsx`):
polls all three mechanism-metrics/ledger/projection endpoints,
rendering the three mechanisms as visually separate pills and two
separate ledger tables — deliberately never arithmetically combining
epsilon values. Wired into the run operator console. A pre-existing
fabricated stub ("Privacy Center" card showing fake `Delta: 1e-5` and an
epsilon trend derived from an unrelated demo metric) was found and
replaced with an honest "Privacy mode" summary card (§19, item from
earlier in this phase).

## 17. Observability: Prometheus metrics

Python: `SAMPLE_LEVEL_TRAINING_ROUNDS_TOTAL` counter,
`SAMPLE_LEVEL_EPSILON` gauge, opt-in HTTP server. Go: privacy epsilon
gauge and budget-event counter, both sourced from the coordinator's
existing RPCs, not duplicated accounting. C++ deliberately does **not**
gain a native `/metrics` HTTP endpoint this phase — a scoped, documented
decision (new dependency + new port + new server thread is
Observability-and-Operations-phase-shaped work, not Privacy-Engineering
work); its own operational visibility remains structured JSON event
logs. See [known-limitations.md](known-limitations.md).

## 18. Security and trust-boundary audit

See [privacy-engineering-security-audit.md](privacy-engineering-security-audit.md).
Explicit Section 0 trust model (trusted coordinator operator,
honestly-reporting workers, non-cryptographic randomness, unencrypted
transport by default — this is central DP, not secure aggregation).
Path traversal, unsafe deserialization, tamper/corruption, RBAC,
sensitive-data exposure, injection, and audit-trail sections all
checked. One genuine finding: `SubmitClientResult`'s sample-level entry
decoding did not cross-check the embedded `run_id`/`round_id`/
`client_id` against the outer, lease-validated result fields — a
malicious or buggy worker could attribute a fabricated ledger entry to
a different client/round than it actually leased. **Fixed during the
audit**, with a regression test (`coordinator_service_test.cpp`)
asserting a mismatched submission is rejected.

## 19. Real bugs and gaps found and fixed during this phase

1. **C++ security**: `SubmitClientResult`'s sample-level entry decoding
   lacked the cross-check above — fixed, regression-tested (§18).
2. **Docker image**: `infra/docker/python-worker.Dockerfile` never
   installed `opacus`/`prometheus_client`; `fl_platform.privacy.__init__`
   unconditionally imports both, so every worker container (private or
   not) would have failed to start. Fixed in the Dockerfile and the
   repo-root `requirements.txt` (used by CI).
3. **Wire encoding**: `GrpcCoordinatorClient.submit_result` dropped the
   `entry_id` field when building the wire `SampleLevelLedgerEntry` —
   verified live (`"entry_id":""` before the fix, a real UUID after).
   Fixed, with a regression test in `test_grpc_coordinator_client.py`.
4. **Test correctness**: `ruff --fix` auto-applied `zip(..., strict=True)`
   to three intentionally-unequal-length `zip()` calls in
   `test_privacy_statistical_validation.py` (the "compare adjacent
   pairs" idiom, where `b = a[1:]` is deliberately one shorter) —
   `strict=True` made all three always raise. Fixed to `strict=False`.
5. **Test correctness (found during this report's own final validation
   pass)**: `hybrid_dp_test.cpp` hardcoded the assumption that the task
   acquired first (by `worker-a`) belongs to `client-a`, but
   `select_cohort`'s seeded shuffle for this test's exact
   `(seed, round_id)` deterministically assigns `client-b` first — a
   real, reproducible test failure caught by re-running the full C++
   suite as part of this phase's closing validation, not a flaky or
   environment-specific issue. Fixed by asserting against the tasks'
   actual `client_id` rather than a hardcoded label; no product code
   changed.
6. **Fabricated web data**: a pre-existing "Privacy Center" stub card
   on the run dashboard showed a fake `Delta: 1e-5` and an epsilon trend
   derived from an unrelated demo metric — replaced with an honest
   summary card once the real panel existed (§16).
7. **Documented, not fixed**: sample-level DP's `epsilon_budget` is
   informational-only (flows into the ledger projection's
   `budget_remaining` field) but nothing in the worker's training loop
   actually stops training when it's reached, unlike the four
   server-side-enforced policies covering user-level DP and adaptive
   clipping. Found while writing [privacy-budget-policies.md](privacy-budget-policies.md);
   recorded in known-limitations.md rather than silently left
   undocumented.

## 20. Files added

10 new docs (`privacy-mathematics.md`, `user-level-dp.md`,
`adaptive-clipping.md`, `hybrid-dp.md`, `privacy-ledger.md`,
`privacy-compatibility-matrix.md`, `worker-privacy-capabilities.md`,
`privacy-budget-policies.md`, `privacy-engineering-security-audit.md`,
this report). C++: `cpp/core/include/fl_core/privacy.hpp`/`.cpp`, 6 new
coordinator test files, `cpp/core/tests/privacy_test.cpp`. Python: 6 new
`fl_platform/privacy/*.py` modules, 5 new test files
(`test_privacy_accounting.py`, `test_privacy_compatibility.py`,
`test_privacy_metrics.py`, `test_privacy_statistical_validation.py`,
plus `test_private_training.py`). Go: `internal/privacy/` package,
`privacy_handlers.go` + test. Web: `privacy-center-panel.tsx`,
`privacy-api.test.ts`. Proto: `proto/privacy/privacy.proto`.

## 21. Files modified

Proto: `coordinator.proto`, `worker.proto`. C++: `run_manager.cpp`/`.hpp`,
`coordinator_service.cpp`, `worker_registry.hpp`, `checkpoint`-related
files for privacy-state persistence. Python: `privacy/__init__.py`,
`adaptive_clipping.py`, `config.py`, `ledger.py`,
`worker/coordinator_client.py`, `worker/service.py`,
`worker/__main__.py`, `worker/configuration.py`,
`test_grpc_coordinator_client.py`, `test_privacy_foundations.py`. Go:
`internal/observability/telemetry.go` + test, `coordinator_handlers.go`.
Web: `types/api.ts`, `lib/api.ts`, `run-operator-console.tsx`,
`run-dashboard.tsx`. Docs: `known-limitations.md`, `docker-runtime.md`,
`README.md`. Infra: `infra/docker/python-worker.Dockerfile`,
root `requirements.txt`. `.gitignore` (generalized scratch-directory
pattern to `*_scratch/`, §22).

## 22. Files removed

None as deliverables. Seven stray C++ test-run scratch directories
(`adaptive_clipping_test_scratch/`, `agg_manifest_scratch/`,
`hybrid_dp_test_scratch/`, `personalization_scratch/`,
`privacy_budget_policy_test_scratch/`, `user_level_dp_test_scratch/`,
`worker_privacy_capability_test_scratch/` — each created automatically
by running the corresponding CTest suite locally) were deleted; never
tracked by git, never a deliverable. `.gitignore` gained a generic
`*_scratch/` pattern so future local test runs don't leave the same
noise in `git status` again.

## 23. Tests added

* **C++**: 6 new/extended coordinator test suites plus
  `cpp/core/tests/privacy_test.cpp` — all folded into the existing
  `fl_coordinator_tests`/`fl_privacy_tests` binaries; 6/6 CTest suites
  pass in both Debug and Release.
* **Python**: 64 privacy-focused tests across
  `test_privacy_accounting.py`, `test_privacy_compatibility.py`,
  `test_privacy_foundations.py`, `test_privacy_metrics.py`,
  `test_privacy_statistical_validation.py`, `test_private_training.py`,
  and the privacy-related additions to `test_grpc_coordinator_client.py`
  (129/129 total repo-wide Python tests pass).
* **Go**: 52 passing tests across `internal/privacy`,
  `internal/observability`, and `internal/transport/httpapi` (all Go
  packages pass; `go vet`/`go build` clean).
* **Web**: 5 new tests in `privacy-api.test.ts` (26/26 total pass).

## 24. Exact validation results (this phase's closing pass)

* `python scripts/check_project_terminology.py` — passed, no prohibited
  terminology.
* `python -m pytest -q` — 129 passed.
* `python -m ruff check .` / `ruff format --check .` — clean, 77 files
  formatted.
* `mypy --config-file=python/pyproject.toml python/src` — no issues,
  59 source files.
* `cd go && gofmt -l . && go vet ./... && go build ./... && go test ./...`
  — build/vet/test all clean (`gofmt -l` flags several pre-existing,
  unrelated files as a local `core.autocrlf` line-ending artifact only —
  documented in known-limitations.md, does not reproduce in CI).
* C++ CTest, Debug and Release configs — 6/6 suites pass each (one real
  test bug found and fixed along the way; §19.5).
* `cd web && npm run typecheck && npm run lint && npm run test && npm run build`
  — all four clean; 26/26 tests pass.
* `python scripts/verify_proto_contracts.py` — passed.

## 25. Statistical validation results

See [privacy-mathematics.md](privacy-mathematics.md) and
`test_privacy_statistical_validation.py`. Parametrized (not single-point)
checks: sample-level and user-level epsilon both strictly decrease with
`noise_multiplier` across 6 values; user-level epsilon strictly
increases with `sample_rate` across 6 values; epsilon is non-decreasing
in step count across 4 different (σ, q) configurations; the adaptive
clip controller converges to within 25% of a distribution's true median
norm after 150 rounds. All genuinely validate the *shape* of the
mathematical relationships, not one hand-picked golden value.

## 26. Docker runtime results

See [docker-runtime.md](docker-runtime.md)'s Privacy Engineering
section. Live-validated end-to-end (Go API → C++ coordinator → real
Python worker) for user-level DP (2 real rounds, epsilon hand-verified
against the RDP formula to full precision) and sample-level DP (real
in-container Opacus training). Two real bugs were caught only by this
live pass (§19.2, §19.3). Hybrid DP, adaptive clipping, and
coordinator-restart privacy-state recovery were **not** driven through
live Docker this phase — a deliberate, bounded scoping decision, since
the identical `RunInstance` code paths are already exercised by real,
passing integration tests at far lower cost than orchestrating a live
multi-container restart. Clean teardown, including recovery from a
`docker compose down -v` race that left three one-off worker containers
running (resolved via explicit `docker stop`/`rm`).

## 27. Known limitations

See [known-limitations.md](known-limitations.md)'s Privacy Engineering
section: no native C++ Prometheus endpoint (Go observes coordinator
privacy state via existing RPCs instead); sample-level DP's "projected
next epsilon" is a documented placeholder (equals current epsilon, since
the coordinator doesn't own a sample-level accountant); central noise
calibration is exact only for `uniform` weighting; the legacy/C++
accountant's integer-only order search is more conservative than
Opacus's fractional orders (golden-parity tested at every shared order);
neither noise provider is a CSPRNG; `go/internal/privacy/compatibility.go`
is a hand-maintained mirror, not generated; sample-level `epsilon_budget`
is informational-only, not enforced (§19.7); the web Privacy Center
panel polls three endpoints every 5s per open run page.

## 28. Regression status

Zero regressions. Every prior-phase test (C++, Python, Go, web) stayed
green throughout this phase's work; the one C++ test failure discovered
during this phase's own final validation pass was in a **Privacy
Engineering-authored** test (`hybrid_dp_test.cpp`, §19.5), not a
regression in any earlier phase's code, and was fixed before this report
was written.

## 29. Git working-tree summary

At the time of this report: 10 new docs, ~10 new C++ files (headers +
sources + tests), ~11 new Python files (modules + tests), ~4 new Go
files, 2 new web files, 1 new proto file; a comparable set of modified
files across all five languages/layers plus `.gitignore`. Zero deletions
beyond untracked local test-scratch directories. No commits were made —
per standing instructions, this phase's work was not committed or
pushed without an explicit request.

## 30. Recommended next-phase scope

Candidates, none started this phase: (a) enforcing sample-level DP's
`epsilon_budget` in the worker's training loop, mirroring the C++-side
policies (§19.7); (b) a native C++ Prometheus endpoint, once
Observability and Operations becomes the active phase; (c) generating
`go/internal/privacy/compatibility.go` from the Python table rather than
hand-mirroring it; (d) live Docker Compose validation of hybrid DP,
adaptive clipping, and coordinator-restart privacy-state recovery, given
today's coverage stops at integration tests for those three; (e) Secure
Aggregation and Cryptographic Protocols — explicitly out of scope
through this phase by standing instruction (§31), and the natural next
step once trust-model-sensitive central DP has this much scrutiny behind
it; (f) TLS/mTLS for the coordinator, unblocking a real non-development
trust model for everything built this phase.

## 31. Explicit non-goals maintained this phase

Per standing instruction: no secure aggregation cryptography, no
homomorphic encryption, no Ray/Flower, no async/semi-sync/Byzantine-
robust aggregation, no production Kubernetes rollout, no complete
PostgreSQL migration, no Redis-based distributed scheduling, no mobile/
edge clients, no LLM/LoRA federation, no custom cryptographic RNGs, no
unvalidated custom DP mathematics (all epsilon/delta math is either
Opacus's own accountants or golden-parity-tested against them). Only
the canonical category names were used throughout, per this project's
naming policy — confirmed by `scripts/check_project_terminology.py`
passing as part of this phase's closing validation. Secure Aggregation
and Cryptographic Protocols work
was not begun. No commits, pushes, tags, or pull requests were made
without explicit request.
