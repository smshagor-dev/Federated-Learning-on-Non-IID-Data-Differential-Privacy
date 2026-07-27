# Known Limitations

Consolidated list. Anything not mentioned here as a gap should be assumed
implemented and tested as described in the other `docs/` files — this
document exists specifically to prevent scaffold code from being read as
production-ready.

## Coordinator Runtime Phase (coordinator runtime / gRPC / Docker Compose)

* **The C++ gRPC coordinator server (`fl_coordinator_grpc_server`) is real
  and, as of this phase, has actually been compiled and run** — see
  [docker-runtime.md](docker-runtime.md) and
  [coordinator-runtime.md](coordinator-runtime.md). It still cannot be
  built on this Windows/MSVC development machine (no local gRPC C++
  install); `infra/docker/cpp-coordinator.Dockerfile` builds it for real
  on Ubuntu via apt, and that image was run, exercised end-to-end over
  HTTP→Go→gRPC→C++, and torn down as part of this phase's validation.
* **Resolved in the Privacy Engineering phase:** Python `GrpcCoordinatorClient`
  now implements every `CoordinatorClient` method (`register_worker`,
  `acquire_task`, `submit_result`, `create_run`, and the rest), not just
  `Health()` — see [create-run-wire-mapping.md](create-run-wire-mapping.md).
  `python -m fl_platform.worker` runs the real register→acquire→train→submit
  loop when `FL_WORKER_RUN_ID` is configured, falling back to `Health()`-only
  polling otherwise. Validated with a real two-round FedAvg run through
  Docker Compose (Go API → C++ coordinator → Python worker container,
  real PyTorch training, real aggregation, `model_version` v0→v1→v2,
  `RUN_COMPLETED`).
* **Coordinator→Go event streaming has a several-second poll window, not
  sub-second push.** `GrpcClient.PollEvents` (`go/internal/coordinator/grpc_client.go`)
  bounds each `StreamRunEvents` call to `pollEventsWindow` (8s). This
  value was arrived at empirically: over docker-compose's bridge network,
  a fresh gRPC stream from the Go client to the C++ coordinator was
  observed to take longer than 5s (but well under 12s) to yield its first
  message, for reasons not fully root-caused (ruled out: IPv6/DNS
  happy-eyeballs — the container resolves a single A record for
  `coordinator`). The same call over the coordinator's host-published
  port, and the equivalent call from the Python gRPC client over the
  identical bridge network, did not show this delay. See
  [event-streaming.md](event-streaming.md) for the full investigation
  and the elapsed-time-based (not status-code-based) fix this required.
* **A distinct, more serious event-streaming gap found during the Privacy
  Engineering phase's live validation:** `GET
  /api/v1/coordinator/runs/{runId}/events` (the SSE relay) reproducibly
  stopped delivering events partway through a real two-round FedAvg run —
  it delivered events up through round 2's second `TASK_ASSIGNED`
  correctly, then never delivered `CLIENT_RESULT_ACCEPTED`,
  `AGGREGATION_STARTED/COMPLETED`, `MODEL_VERSION_UPDATED`,
  `CHECKPOINT_COMPLETED`, or `RUN_COMPLETED`, even after waiting well
  beyond `pollEventsWindow`. This is **not** a data-completeness bug: the
  coordinator's own `EventBus::poll()` (`cpp/coordinator/src/event_bus.cpp`)
  is a plain, non-destructive, cursor-based query, confirmed correct by
  inspection and by the coordinator's own structured stdout log showing
  all 24 events for the run, in order, ending in `RUN_COMPLETED`. A fresh
  request with `?after=<the-last-delivered-event-id>` also returned
  nothing, ruling out "stuck single connection" as the explanation. The
  gap is somewhere in the Go `PollEvents`/`StreamRunEvents` client-side
  relay (`go/internal/coordinator/grpc_client.go`) or the HTTP SSE
  handler (`handleCoordinatorRunEvents` in
  `go/internal/transport/httpapi/coordinator_handlers.go`), not root-caused
  further in this pass — investigating it competes with the same
  previously-documented gRPC-streaming nondeterminism above. Confirmed via
  direct `GET /api/v1/coordinator/runs/{runId}` polling (not the event
  stream) that the run genuinely reached `COMPLETED` with `model_version:
  v2`. Until fixed, do not rely on the SSE endpoint alone to detect run
  completion; poll `GetRun`/`GET .../runs/{runId}` instead.
* **`grafana` does not start via `docker compose up` on this machine** —
  host port 3001 is held by an unrelated process outside Docker (`netstat`
  confirms a non-Docker PID). Every other service in `docker-compose.yml`,
  including the two new the Coordinator Runtime phase services (`coordinator`,
  `python-worker`), started and reached a healthy/running state in the
  same run. Not a regression from this phase; carried over from
  the Foundation phase's identical note.
* **`mlflow`'s Prometheus scrape target is unhealthy (404 on `/metrics`)**
  — mlflow does not expose a Prometheus endpoint by default. Pre-existing
  from the Foundation phase's `infra/prometheus/prometheus.yml`, unrelated to this
  phase's work; the *other* previously-broken scrape target
  (`go-api`, which had no `/metrics` route registered at all before this
  phase) is now fixed and scraping successfully — see
  [coordinator-runtime-validation.md](coordinator-runtime-validation.md).
* **Coordinator security is local-development-grade.** `insecure` gRPC
  credentials, no TLS/mTLS, no per-worker auth token, no request-rate
  limiting beyond gRPC's own message-size caps. TLS/mTLS has a named
  configuration hook (`GrpcClient`'s `Insecure` field;
  `grpc::InsecureServerCredentials()` in `main.cpp`) but no actual
  certificate handling. See [coordinator-runtime.md](coordinator-runtime.md).
* **No secure aggregation, no per-round differential privacy application,
  no asynchronous/semi-synchronous round execution** — explicitly out of
  scope for this phase by instruction (see the "explicitly out of
  scope" section below); the existing scaffolds are unchanged.

## Environment-specific (this machine, not a design gap)

* **Go race tests do not run locally.** `go test -race` requires cgo and a
  C compiler; neither gcc nor clang is installed on this Windows machine.
  Added to CI (`ubuntu-latest`, which has gcc) instead of skipped outright.
* **`protoc` is not installed locally.** Contract-compatibility checking
  (`make proto-check`) does not need it and passes locally; actual code
  generation (`make proto`) only runs in CI where `protoc` is installed.
* **clang-format / clang-tidy / AddressSanitizer / UndefinedBehaviorSanitizer
  do not run locally** (MSVC-only toolchain on this machine, no Clang
  installed). Added to CI on `ubuntu-latest`.
* **ThreadSanitizer is not wired up anywhere**, including CI. It is not
  supported under MSVC, and adding a Linux-only TSan job was out of the
  explicit scope actually exercised in this pass; the Makefile/CMake
  scaffolding for ASan/UBSan can be extended the same way if a future pass
  adds it.
* **`gofmt -l` locally flags several pre-existing Go files
  (`internal/models/repository.go`, `internal/application/fairness.go`,
  `internal/datasets/repository.go`, and others predating this phase)
  as unformatted — this is a false positive caused by
  `core.autocrlf=true` on this Windows checkout giving those files CRLF
  line terminators, which `gofmt` (LF-only output) always flags,
  regardless of actual formatting.** Confirmed via `file <path>`
  (`ASCII text, with CRLF line terminators`) contrasted against files
  edited this session (LF-only, not flagged). CI's `gofmt check` step
  (`.github/workflows/ci.yml`) runs on `ubuntu-latest`, where checkout
  preserves the repository's committed LF line endings, so this does not
  reproduce in CI — a local-machine-only artifact, not a real formatting
  regression, and not something a global `gofmt -w`/line-ending rewrite
  should be run to "fix" here since that would touch dozens of unrelated
  files well outside Privacy Engineering's scope.
* **RESOLVED in the Coordinator Runtime phase: the `web` Docker image now builds
  (~17s).** The the Foundation phase note below described the symptom
  (`next build`'s static-generation phase hanging in this Docker Desktop
  VM); the actual root cause was `getOverviewData()`/`getRunData()` being
  called during Next.js's build-time static prerendering against a
  backend that isn't running at build time, which hung rather than failed
  fast. Fixed with `export const dynamic = "force-dynamic"` on the three
  pages that fetch live backend data (`web/app/page.tsx`,
  `web/app/audit/page.tsx`, `web/app/runs/[runId]/page.tsx`), which tells
  Next.js those routes are always server-rendered per-request and must
  never be prerendered at build time. Left here rather than deleted so
  the historical symptom (which looked like a resource/timeout problem)
  isn't rediscovered as "unexplained."

## Design gaps, stated honestly (not yet implemented)

* **`ModelSnapshot` is not a distinct type.** The aggregation core is
  delta-based (`AggregatedUpdate`/`AggregationResult` carries `model_delta`,
  not a full model); nothing in this phase owns a persistent global
  model snapshot end-to-end, so introducing the type now would have no
  real behavior behind it. See
  [aggregation-core-architecture.md](aggregation-core-architecture.md).
* **SCAFFOLD does not validate control-variate staleness.** The validator
  checks that a control tensor is present and well-formed when required,
  but there is no version/staleness marker on control variates today. See
  [scaffold-state.md](scaffold-state.md).
* **SCAFFOLD does not persist per-client control variates (`c_i`).** The
  C++ core only ever sees each client's already-computed delta
  (`c_i^+ - c_i`); a coordinator that needs `c_i` across non-consecutive
  rounds for the same client would need a separate store (object storage
  reference or similar), which does not exist yet. See
  [scaffold-state.md](scaffold-state.md).
* **FedYogi's second moment is not clamped away from going negative.** If
  it does (a real possibility of the signed update rule under adversarial
  or unusual delta sequences), `sqrt` of a negative value propagates to
  NaN and fails tensor validation on the next use — the aggregator raises
  rather than silently clamping, but this means a bad round can make a
  FedYogi run unrecoverable without a code change, not just a
  configuration change. See [fedopt.md](fedopt.md).
* **Windows checkpoint replace is not perfectly atomic.** The POSIX
  `rename()`-over-existing-file semantics that make
  `AggregatorCheckpointStore::save_to_file` atomic on Linux/macOS do not
  hold on Windows; the fallback (`remove` then `rename`) has a small window
  where neither the old nor new checkpoint exists. See
  [checkpoint-format.md](checkpoint-format.md).
* **No cryptographic checkpoint integrity.** The checksum is FNV-1a
  (corruption detection), not a MAC/signature (tamper detection by an
  adversary with filesystem access). Not a regression — no cryptographic
  guarantee was ever claimed — but worth stating since "checksum" can be
  misread as "authenticated."
* **Benchmark harness is a custom `std::chrono` timer, not Google
  Benchmark.** See [benchmarking.md](benchmarking.md) for the specific
  reasoning (no C++ package manager configured; FetchContent would add a
  multi-minute build).
* **`resnet18_sized_approx` benchmark size is 500K parameters, not
  ResNet-18's real ~11M.** Deliberately scaled down to keep a 500-client
  sweep's memory bounded on a shared development machine (11M × 500 × 8
  bytes ≈ 44 GB otherwise). Real-scale benchmarking is future work.
* **No peak-memory measurement in the benchmark harness** (no
  cross-platform RSS sampling added); total client-delta bytes processed is
  reported as an exact, platform-independent proxy instead.
* **Enum *values* (not just field numbers) are not yet asserted by the
  protobuf contract-compatibility script.** Field-number stability is
  checked; the numeric value each enum member resolves to is not
  separately asserted. See [protobuf-generation.md](protobuf-generation.md).
* **No TypeScript protobuf bindings.** Nothing in the current architecture
  talks gRPC/protobuf from the browser (the dashboard uses REST/JSON
  against the Go API), so generating them would be dead code. Revisit if
  that changes.
* **Playwright E2E is not added.** The dashboard's backend is
  demo-data-backed, not a stable live system to test end-to-end yet. See
  [testing.md](testing.md).
* **`web/package.json` pulls Next.js 15.0.0, which `npm audit` flags with a
  disclosed critical CVE (CVE-2025-66478), plus several other
  moderate/high dev-dependency advisories (vitest/vite/esbuild chain).**
  Upgrading was out of scope for this pass (the fixes are major-version
  bumps to Next/Vitest that risk breaking the existing config/tests without
  separate verification) and is called out here rather than silently left
  for someone to discover via `npm audit`.

## Explicitly out of scope for this task (by instruction, not oversight)

**Superseded by the Algorithm Expansion phase for FedSAM/Ditto/Per-FedAvg** — see the
the Algorithm Expansion phase section below; those three are now real, tested training
algorithms, not scaffolds. Opacus/sample-level DP training, secure
aggregation cryptography, Ray/Flower execution, asynchronous/buffered
aggregation, and the production Go database layer (PostgreSQL/Redis/MinIO
integration for the project/experiment/run/model/dataset bookkeeping
repositories, which remain file/in-memory-backed) remain scaffold-only or
unimplemented, per the explicit instruction not to expand them beyond
compilation/contract compatibility. Live dashboard↔backend integration is
now partial: the Go API's own project/experiment/run/audit data and the
coordinator-run/model/dataset/personalization endpoints are all real and
live (see [go-coordinator-integration.md](go-coordinator-integration.md));
training metrics like accuracy/loss for the Foundation-era dashboard
demo endpoints remain out of scope.

## Algorithm Expansion Phase (FedSAM/Ditto/Per-FedAvg, personalization, registries)

* **Dataset partitioning in Go is metadata-only, not a recomputation of
  Python's partitioning.** `go/internal/datasets`'s `Partition` stores a
  manifest's shape and per-client sample *counts*; it does not hold or
  compute actual per-sample index assignments or label histograms
  (`client_indices`/`label_distribution_summary` in Python's
  `PartitionManifestRecord`) since that requires the real labeled dataset,
  which never crosses into Go. A caller (an operator, or a future
  Python-to-Go sync) supplies the already-computed manifest; Go validates
  its structure (known strategy, dirichlet requires alpha, pathological
  requires classes_per_client, positive client count) but trusts the
  supplied counts. See [dataset-registry.md](dataset-registry.md).
* **Go's model/dataset registries are single-JSON-file-backed, not
  one-file-per-entry like Python's.** `models.FileRepository`/
  `datasets.FileRepository` persist the *entire* collection to one file
  per call (mirroring `projects`/`experiments`'s existing pattern), rather
  than Python's `{name}__{version}.json` per-entry layout. Functionally
  equivalent (both are real, tested, atomic-write-based persistence) but
  worth knowing if diffing the two implementations' on-disk layouts.
* **Personalized models may contain client-specific information.** A
  Ditto/Per-FedAvg client's personalized state dict is trained on that
  client's own local data and is therefore more likely to memorize
  client-specific patterns than a purely aggregated global model. The
  current `FilesystemPersonalizedModelStore` (see
  [personalized-model-store.md](personalized-model-store.md)) protects
  against path traversal (`_VALID_ID` regex on every path segment),
  tampering/corruption (SHA-256 artifact checksum, ownership check on
  load), and unsafe deserialization (`torch.load(weights_only=True)`) —
  but has **no encryption at rest, no per-client access control beyond
  what the filesystem itself provides, and no redaction/anonymization**.
  Stronger storage protections (encryption at rest, per-tenant access
  control) are recommended before any deployment where the storage
  filesystem itself isn't already a trust boundary — this is unchanged
  from, and consistent with, this project's existing "coordinator
  security is local-development-grade" note above.
* **Go's personalization/fairness endpoints use the same role-based access
  as other coordinator-run routes** (any authenticated Viewer/Researcher/
  Admin/Service may read them) — there is no additional per-client-data
  access restriction (e.g. a Viewer can see every client's personalized
  accuracy for a run they can already see other metrics for). Consistent
  with this project's existing RBAC granularity (resource-level, not
  field-level); a finer-grained model would be a concern for a later phase.
* **The Go fairness formulas (`go/internal/application/fairness.go`) are
  an independent reimplementation of Python's
  `fl_platform.personalization.metrics`, not a shared library.** Both are
  unit-tested against the same worked examples (percentile interpolation,
  Jain's index edge cases) to keep them from drifting apart, but there is
  no automated cross-language equivalence test beyond that — a future
  change to one formula could silently diverge from the other if its
  worked-example tests aren't updated in lockstep. See
  [fairness-metrics.md](fairness-metrics.md).
* **FedSAM's two-pass training is not claimed to converge faster or
  better than FedAvg** — it is expected to be slower per batch (two
  forward/backward passes instead of one); see
  [benchmarking.md](benchmarking.md) for measured overhead.
* **Go protobuf stubs (`go/generated/`) required regenerating via a
  throwaway Docker container this phase** (no local `protoc`
  install — consistent with the pre-existing "protoc is not installed
  locally" note above) to pick up the new `AggregationManifest`/
  `PersonalizationMetricRecord`/`GetPersonalizationSummary` proto
  messages. The regenerated stubs are gitignored, as before; anyone
  regenerating them needs either a local `protoc`+`protoc-gen-go`+
  `protoc-gen-go-grpc` install or the same Docker approach (see
  `scripts/generate_protos.sh`'s Go section).

## Privacy Engineering Phase (sample-level/user-level DP, adaptive clipping)

* **The C++ coordinator does not expose a native Prometheus `/metrics`
  HTTP endpoint.** It is a pure gRPC server (`cpp/coordinator/main.cpp`)
  with no second HTTP listener, and no `prometheus-cpp` dependency
  exists anywhere in the CMake build. Adding one would mean either
  vendoring `prometheus-cpp` (a new build dependency, a new port, new
  Docker/compose wiring) or hand-rolling a raw-socket HTTP responder —
  both are genuinely new infrastructure capability, not something this
  phase's privacy-accounting work should bolt on hastily. Instead:
  privacy state the C++ coordinator owns (`fl_privacy_epsilon{mechanism=
  "user_level"|"clipping"}`, `fl_privacy_budget_events_total`) is
  exported from the **Go** control plane's `/metrics` endpoint
  (`go/internal/observability/telemetry.go`), sourced from the existing
  `GetPrivacyMetrics`/`GetPrivacyLedger` RPCs and the `StreamRunEvents`
  relay — Go was already the metrics aggregation point for coordinator
  activity (see the pre-existing `fl_coordinator_rpc_total` counter), so
  this is an extension of an established pattern, not a new one. The
  C++ side's own operational visibility remains its structured JSON
  logs (`structured_log.cpp`), which already emit one line per
  `CoordinatorEvent` including `PRIVACY_BUDGET_WARNING`/
  `PRIVACY_BUDGET_EXCEEDED` with `mechanism`/`policy` fields. A native
  C++ Prometheus endpoint is a reasonable candidate for a future
  Observability and Operations phase, not this one.
* **Sample-level DP's "projected next epsilon"
  (`GetPrivacyProjection`'s `sample_projected_next_epsilon`) is not a
  genuine one-round-ahead forecast — it equals the current epsilon.**
  The coordinator does not own a sample-level accountant (that state
  lives entirely in each Python worker's Opacus instance) so a real
  projection isn't computable centrally without either replicating
  Opacus's accounting server-side or having workers report a forecast
  themselves (neither implemented this phase). Reporting the current
  value unchanged is a documented placeholder, not a fabricated
  forecast — see `run_manager.cpp`'s `privacy_projection()`.
* **Central user-level DP noise calibration
  (`noise_std = noise_multiplier * clip_bound / target_clients_per_round`)
  is exact only for `uniform` weighting.** For `capped_sample_count`/
  `normalized_bounded` weighting strategies, the true maximum per-client
  weight is config-dependent (a function of the cap/normalization
  bound relative to the actual cohort's sample-count distribution) and
  was not derived this phase; the same formula is used as a documented
  approximation. See `fl_core/privacy.hpp`'s `add_central_gaussian_noise`
  call site in `run_manager.cpp`.
* **`UserLevelAccountant`'s legacy-derived RDP order search is
  integer-only (2–64, plus {80,96,128,256,512}: ~69 orders) where
  Opacus's own accountant searches ~151 fractional orders.** Golden-
  parity tested to match Opacus exactly at every shared integer order
  (see `python/tests/test_privacy_accounting.py`), so this is a valid
  but measurably more conservative (higher) epsilon than Opacus would
  report for the same (noise_multiplier, sample_rate, steps) — not a
  formula bug, just coarser order granularity. Applies to both the C++
  (`cpp/core/src/privacy.cpp`) and Python (`federated.dp_accountant`)
  implementations, which share this same order set.
* **`SecureNoiseProvider`/worker `supports_secure_random` do not claim
  cryptographic security.** `SecureNoiseProvider` seeds `std::mt19937_64`
  from `std::random_device` (OS entropy) — a reasonable non-deterministic
  default for research use, but not a CSPRNG. The Python worker always
  reports `supports_secure_random=false` in its advertised
  `WorkerPrivacyCapabilities` for the same reason (Opacus/PyTorch's own
  RNG, not a CSPRNG). Neither should be read as meeting a production
  cryptographic-randomness bar.
* **`go/internal/privacy/compatibility.go` is a hand-maintained mirror
  of `python/src/fl_platform/privacy/compatibility.py`, not generated
  from a shared source.** The two are kept in sync by hand (Go's test
  suite cross-checks specific known values, e.g. `scaffold`'s
  sample-level status, against the Python table's stated reasoning) but
  there is no automated equivalence test across languages beyond that —
  a future change to one table could silently diverge from the other.
  Same category of limitation as the pre-existing Go/Python fairness-
  formula duplication noted above.
* **RESOLVED in the Secure Aggregation and Cryptographic Protocols
  category: `SampleLevelDPConfig.epsilon_budget` is now actively
  enforced worker-side**, via `fl_platform.privacy.budget_enforcement`
  wired into `run_private_local_training`/`worker/service.py` (checked
  before each optimizer step for the preventive policy, and after each
  step for the reactive ones — not just surfaced after the fact in the
  ledger projection, which is what this note originally flagged as
  missing during the Privacy Engineering category). See
  [privacy-budget-policies.md](privacy-budget-policies.md)'s "Sample-level
  DP's budget" section for the full design and its own remaining scope
  gaps: the wire contract does not yet carry `sample_budget_policy`
  end-to-end (proto field added, Python bindings not regenerated in this
  environment — see the protoc note above), and enforcement state does
  not persist across a worker *process* restart or hand off between
  different worker processes serving the same client across rounds.
* **The web Privacy Center panel polls three separate REST endpoints
  every 5 seconds per open run page** (`/privacy/metrics`,
  `/privacy/ledger`, `/privacy/projection` — see
  `web/features/runs/privacy-center-panel.tsx`), matching the existing
  personalization panel's polling pattern rather than a push-based
  update mechanism. Fine at today's scale; would need consolidating
  into fewer requests (or a shared SSE/WebSocket channel) if many
  operators keep run pages open simultaneously against a production
  coordinator.

## Secure Aggregation and Cryptographic Protocols category (closure-gate pass)

This category's own required scope (mTLS, worker identity, a published
secure-aggregation protocol, Go/web integration, Docker validation) is
far larger than one pass — see
[secure-aggregation-report.md](secure-aggregation-report.md) for the
itemized status of every requirement. This section covers only the
limitations of what *was* built this pass.

* **`fl::core::SecureRandomProvider`/`OsEntropySecureRandomProvider`
  (`cpp/core/include/fl_core/secure_random.hpp`) is a real, tested
  OS-CSPRNG-backed provider, but is not wired into the live user-level-DP
  noise path.** `run_manager.cpp`'s `add_central_gaussian_noise` call
  site still uses the pre-existing `SecureNoiseProvider`
  (`privacy.hpp`), which seeds one `mt19937_64` once from
  `std::random_device` and reuses it for every element — not a CSPRNG,
  as that header's own doc comment already stated. Swapping the call
  site to draw fresh OS entropy per tensor element (what
  `OsEntropySecureRandomProvider` does today) would mean one OS syscall
  per double, a severe performance regression at real tensor scale; the
  correct fix (seed a ChaCha20-based stream from OS entropy once per
  noise-generation call) needs the OpenSSL-EVP-backed ChaCha20 wrapper
  now implemented in `secure_aggregation_crypto.cpp` (see
  [secure-aggregation-cryptographic-provider.md](secure-aggregation-cryptographic-provider.md))
  wired into this call site — implemented as a reusable primitive this
  pass, but not yet wired into `run_manager.cpp`'s noise path, which is
  a documented but not-yet-executed integration step. See
  [secure-aggregation-architecture.md](secure-aggregation-architecture.md)'s
  §7 for the full reasoning.
* **`fl_platform.privacy.secure_random.worker_reports_secure_random_support()`
  still returns `False`.** This is correct, not a gap: it answers
  whether the worker's own noise-generation path routes through a
  CSPRNG, and today it doesn't (Opacus generates sample-level noise with
  its own RNG). `secure_random_available()` (a different, real,
  now-tested function) is `True` — the two are deliberately independent
  so a future secure-aggregation masking implementation can flip only
  the second function once it actually exists, without ever having
  claimed the capability early.
* **Sample-level budget enforcement does not persist across a worker
  process restart, and its policy is not yet wire-configurable end to
  end.** See this document's existing "Sample-level DP's budget"
  resolution note above for the detail — the enforcer is scoped to one
  worker process's in-memory lifetime per client, and the new
  `sample_budget_policy` proto field is not yet consumed by
  `coordinator_client.py`'s wire decode (needs `protoc` regeneration
  this environment cannot perform without Docker/CI).
* **No threshold secret-sharing dependency has been selected.** A real
  review (see [cryptographic-primitives.md](cryptographic-primitives.md)
  §4) found no C++ or Python library meeting this project's bar for a
  maintained, independently-reviewed implementation. Encrypted
  secret-share distribution and dropout recovery — required for the
  secure-aggregation protocol itself — cannot be implemented until this
  is resolved; reported as a blocker rather than worked around with an
  unreviewed dependency or hand-written arithmetic. **This remains
  unresolved and out of scope** in the Secure Aggregation Protocol
  Foundation slice below — no threshold secret sharing, dropout
  recovery, or partial-cohort reconstruction of any kind was
  implemented.

## Secure Aggregation Protocol Foundation and No-Dropout Masked-Sum Core slice

Implements and tests the cryptographic and mathematical **core** of a
no-dropout, honest-client-dependent masked-sum protocol — real code,
real cross-language golden fixtures, real Docker-validated OpenSSL
crypto, real ctest/pytest evidence — but does **not** wire it into any
live RPC, coordinator/worker network path, or actual training round.
See [secure-aggregation-protocol-foundation.md](secure-aggregation-protocol-foundation.md)
for the full Tier 1 (implemented) / Tier 2 (deferred, with reasons)
scope split this slice was built against.

**What is real and tested** (all of it, both languages, with 28 C++
`fl_coordinator_tests` checks + 3 standalone gRPC-gated executables +
23+11+6 Python `pytest` tests, cross-language golden fixtures verified
in both directions):

* Fixed-point encoding into a `mod 2^64` ring
  (`secure_aggregation_encoding.{hpp,cpp}` /
  `fixed_point_encoding.py`), with an explicit, overflow-checked domain
  bounds proof (Work Package G).
* The cohort state machine and session configuration contract
  (`secure_aggregation_session.{hpp,cpp}` /
  `cohort_state_machine.py`) — `COHORT_FORMING → KEY_ADVERTISEMENT →
  COHORT_FROZEN → MASKED_UPDATE_COLLECTION → AGGREGATE_VALIDATION →
  COMPLETED`, any non-terminal state `→ ABORTED`, any state `→ FAILED`,
  enforced mechanically (no implicit transitions).
* Pairwise mask sign-cancellation arithmetic
  (`secure_aggregation_mask.{hpp,cpp}` / `pairwise_mask.py`) — proven,
  not merely asserted, to sum to exactly zero across a complete cohort.
* Real OpenSSL-EVP-backed (C++) and PyNaCl/`cryptography`-backed
  (Python) X25519 key agreement, HKDF-SHA-256 key derivation, ChaCha20
  (IETF/RFC 8439) keystream generation, and SHA-256 cohort-commitment/
  session-configuration-hash canonical hashing
  (`secure_aggregation_crypto.{hpp,cpp}` / `crypto.py`) — Docker-built
  and ctest-validated (this repo's OpenSSL/gRPC-gated build only
  configures in Docker/CI, never on the Windows development machine).
* Tensor/weight mask generation
  (`secure_aggregation_tensor_mask.{hpp,cpp}` / `tensor_mask.py`) and a
  **capstone integration test**, independently implemented and passing
  in both languages, that constructs a real 4-participant cohort (real
  X25519 keys, real pairwise shared secrets, real HKDF/ChaCha20-derived
  masks) and proves: (a) the complete cohort's masked-sum decodes to
  the exact true aggregate, and (b) removing even one participant's
  masked contribution breaks that cancellation — the concrete
  mathematical justification for this protocol's mandatory
  abort-on-dropout behavior.

**What is explicitly NOT built in this slice** (Tier 2, deferred with
reasons in secure-aggregation-protocol-foundation.md — not reported as
done, not silently skipped):

* No protobuf wire messages, no gRPC RPCs, no coordinator or worker
  handler wiring — a session cannot actually be created, advertised, or
  driven over the network. Everything above is a tested library, not a
  running protocol.
* No live FedAvg integration — `secure_aggregation` is not called from
  any aggregation code path.
* No dropout *detection* (as opposed to the dropout-breaks-cancellation
  *math*, which is proven above) — there is no deadline/timeout logic
  watching a live session, because there is no live session.
* No `EVENT_*`/metric emission call sites, no Go/web observability, no
  validation-harness scenario group, no artifact-sanitation pattern
  additions, no real multi-worker Docker validation of an actual round.
* `compute_session_configuration_hash` covers `scale_factor` (the
  top-level field) but not `fixed_point_profile`'s other sub-fields
  (`rounding_rule`, `max_input_magnitude`, `max_client_weight`,
  `max_cohort_size`, `safety_margin`) — a session could change one of
  those nested values without changing this hash. See
  `fixtures/secure_aggregation/session_configuration_hash_golden.json`'s
  `known_limitation` field.
* Provider naming and every doc in this slice consistently use
  `SECAGG_NO_DROPOUT_EXPERIMENTAL` — never `SECURE_AGGREGATION_COMPLETE`
  or an unqualified "secure aggregation supported" claim.

## Secure Aggregation Wire Protocol and Live No-Dropout Execution slice

Builds real, versioned protobuf wire contracts and a real, tested,
in-memory C++ `SecureAggregationSessionManager` orchestration class on
top of the prior slice's cryptographic/math core — but **does not**
make secure aggregation a live, gRPC-reachable protocol. See
[secure-aggregation-wire-protocol-audit.md](secure-aggregation-wire-protocol-audit.md)
for the starting-state audit and
[secure-aggregation-wire-protocol-foundation.md](secure-aggregation-wire-protocol-foundation.md)
for the full Tier 1/Tier 2 scope split this slice was built against.

**What is real and tested this slice**:

* Every proto message/enum/RPC the task specification requires
  (`FixedPointEncodingProfile`, `CryptographicProviderProfile`,
  `SecureAggregationSessionConfig`, `FrozenCohortRoster`,
  `MaskedClientUpdate`, six new RPCs, etc.), additive only — no
  existing field renumbered, no existing message touched at all.
  Bindings regenerated for real in C++/Python/Go (Docker), verified by
  `scripts/verify_proto_contracts.py`.
* `MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT`/
  `_MASKED_UPDATE` and `MESSAGE_STREAM_SECURE_AGGREGATION` added to
  `SignedWorkerEnvelope`'s wire enums — the wire mirror of the prior
  slice's C++-only `MessageStream::kSecureAggregation` placeholder.
* `SecureAggregationSessionManager`
  (`secure_aggregation_session_manager.{hpp,cpp}`) — a real, thread-
  safe, in-memory orchestration class implementing
  `create_session`/`advertise_key`/`freeze_cohort`/
  `submit_masked_update`/`finalize`/`abort`/`find`/`list`, with real
  validation at every step (participant/duplicate/deadline/checksum/
  cohort-commitment/domain-bounds checks) and a real bridge from
  masked-ring-value contributions to a decoded
  `fl::core::AggregationResult` — proven end to end by a capstone test
  that drives a real 3-participant cohort through the manager's full
  public API using real X25519/HKDF/ChaCha20 and gets back the exact
  correct FedAvg-weighted average.
* Six coordinator RPCs (`AdvertiseSecureAggregationKey`,
  `GetFrozenCohortRoster`, `SubmitMaskedClientUpdate`,
  `GetSecureAggregationSession`, `ListSecureAggregationSessions`,
  `AbortSecureAggregationSession`) are declared in the proto and
  return explicit, documented `UNIMPLEMENTED` gRPC status from
  `CoordinatorServiceImpl` — the same precedent as the pre-existing
  `GetRound`/`GetModelManifest` — rather than silently falling through
  to a generic default or a fabricated success response.
* A genuine pre-existing infrastructure bug found and fixed during
  this slice's own Docker validation: `scripts/generate_protos.sh`
  checked for a `python` binary, which does not exist on the Debian-
  based image this project's own gRPC Docker build and CI use (only
  `python3` does) — meaning real Python gRPC stub/`.pyi` regeneration
  had likely never actually run inside that image before, silently
  falling back to message-types-only generation even when
  `grpcio-tools` was correctly installed. Fixed to resolve whichever
  interpreter actually exists.

**What is explicitly NOT built this slice** (Tier 2, deferred with
reasons in secure-aggregation-wire-protocol-foundation.md):

* `SecureAggregationSessionManager` is not wired into
  `CoordinatorServiceImpl`'s constructor or `main.cpp` — it is real,
  tested, callable orchestration logic, not yet reachable over a live
  gRPC connection. No RPC handler actually verifies a signature,
  checks replay/sequence state, or calls into the manager.
  `freeze_cohort()`'s returned roster has empty
  `coordinator_signing_key_id`/`signature` fields for the same reason
  (no live `CoordinatorSigningIdentity` is injected).
  `submit_masked_update()`'s per-tensor shape validation is against
  the *first-received contribution's own shape*, not an independently-
  sourced `ModelManifest` (this manager has no access to one this
  pass) — a documented simplification, not a full Work Package O
  validation pipeline.
* No Python worker integration (`WorkerService.run()` has zero secure-
  aggregation awareness), no secure task binding on `ClientTrainingTask`,
  no session creation hooked into a live round, no events/metrics/Go
  APIs/web observability, no validation-harness scenario group, no
  real multi-worker Docker validation, no performance benchmarking, no
  new CI gates. Docker Compose's worker topology remains single-
  instance and hand-pinned (confirmed in the audit, not parameterized
  for a multi-worker cohort test).
* Cross-language wire fixtures (Work Package AD) are not added this
  slice — they require the live signing code for key advertisements/
  rosters/masked updates, which is Tier 2.

## Secure Cohort Handshake and Signed Roster Runtime slice

See [secure-cohort-handshake-foundation.md](secure-cohort-handshake-foundation.md)
for the design decisions and
[secure-cohort-handshake-report.md](secure-cohort-handshake-report.md)
for the full completion report. This slice makes the handshake real,
gRPC-reachable, and live-validated end to end through
`READY_FOR_MASKED_TRAINING` — it stops there; masked model-update
submission and secure aggregate finalization remain out of scope, per
this slice's own explicit instruction.

**Implemented and validated**: coordinator-owned secure-aggregation
sessions, created lazily on a round's first `AcquireTask` call
(`FL_SECURE_AGGREGATION_ENABLED` coordinator-wide opt-in) using the
round's real selected cohort; a new persistent, safe-metadata-only
`SecureAggregationSessionStore` (same tab-separated/FNV-1a-checksum/
atomic-write pattern as `WorkerIdentityRegistry`) wired into the
session manager, with restart-abort reconciliation; a new
`SecureAggregationTaskBinding` folded into the real coordinator task
signature (`secure_aggregation_configuration_hash`, a sibling hash like
`personalization_configuration_hash`, never mixed into
`task_payload_hash`); live `AdvertiseSecureAggregationKey` (full
SIGNED_WORKER_MESSAGE pipeline: mTLS, identity/status, signing-key
resolution, payload-hash recompute, Ed25519 verify, replay/sequence,
domain call, security-event emission) with automatic cohort freeze the
moment the last participant's advertisement completes the cohort; real
Ed25519 roster signing (`freeze_cohort()`'s new optional
`CoordinatorSigningIdentity*` parameter); live `GetFrozenCohortRoster`,
`GetSecureAggregationSession`, `ListSecureAggregationSessions`
(read-only), and `AbortSecureAggregationSession` (ADMIN_CONTROL-gated);
a periodic expired-advertisement-deadline sweep, invoked from
`AcquireTask`. On the Python side: fresh per-session X25519 ephemeral
key generation, signed key-advertisement construction/verification,
full frozen-roster verification (signature, session/run/round/
model_version binding, own-participant-entry match, duplicate/invalid/
all-zero peer-key detection) — all wired into `WorkerService.run()`'s
real control flow, gated on `task.secure_aggregation.secure_aggregation_active`,
raising `SecureCohortHandshakeError` (handled exactly like
`CoordinatorTaskRejectedError`: no training happens for that task) on
any failure. `SubmitMaskedClientUpdate` remains explicitly
`UNIMPLEMENTED`, unchanged, per this slice's own mandatory constraint.

**Live-validated, not just unit-tested**: a real three-worker Docker
Compose stack (`infra/compose/docker-compose.secure-cohort-handshake.yml`,
`scripts/validate_secure_cohort_handshake.py`) — real mTLS, real
per-worker Ed25519 signing identities, a real gRPC coordinator with
`FL_SECURE_AGGREGATION_ENABLED=true` — drove three independent worker
containers through the complete handshake against a real 3-client
`fedavg` run: all three generated fresh keys, advertised them, had the
coordinator freeze the complete cohort, retrieved and independently
verified the coordinator-signed frozen roster, and logged reaching
`READY_FOR_MASKED_TRAINING`. 7/7 script assertions passed. C++: 100%
Docker gRPC-gated ctest pass (`fl_coordinator_grpc_tests` plus the new
`fl_secure_aggregation_session_manager_tests` covering the store/
manager additions), local Windows `fl_coordinator_tests` including the
new `secure_aggregation_session_store_test.cpp`. Python: 349 passed, 6
skipped (`python -m pytest python/tests`, up from 338 before this
slice's own regression-test additions), including 9 new
`key_advertisement.py` tests, a `SecureAggregationConfigurationHashTests`
class, a `KeyAdvertisementHashTests` class, and a new
`SECURE_AGGREGATION_BINDING_MISMATCH` rejection test — the last of
these also fixed a real, this-slice-caused regression in
`test_coordinator_task_verifier.py`'s fixture (missing the new hash
field, causing every existing test in that file to fail once the
verifier check went live).

**Real, previously-undiscovered bugs found and fixed while validating
this slice** (none of them specific to secure aggregation itself):

* `scripts/generate_protos.sh` — the repository's only tracked `*.sh`
  file — checked out with CRLF line endings on this Windows development
  machine's default `core.autocrlf=true` git configuration (no
  `.gitattributes` existed at all before this slice). Both
  `infra/docker/cpp-coordinator.Dockerfile` and
  `infra/docker/python-worker.Dockerfile` `COPY` this file into a Linux
  build context and run it via `bash scripts/generate_protos.sh`, which
  failed with `set: pipefail: invalid option name` (the embedded `\r`)
  — breaking every Docker Compose build of the coordinator/python-worker
  images from a fresh Windows clone. Fixed with a new `.gitattributes`
  (`*.sh text eol=lf`) plus a working-tree line-ending fix to the one
  affected file.
* The `.github/workflows/ci.yml` `cpp-grpc` job's `ctest --test-dir
  build/cpp-grpc --output-on-failure` step ran against the *entire*
  registered test set in that build tree (CMake registers `add_test()`
  at configure time regardless of which `--target`s were actually
  built), not just the handful of gRPC-gated executables the job's own
  `--target` list builds — every protobuf-free `fl_coordinator`/`fl_core`
  test (never built in this job; covered instead by `cpp-debug`/
  `cpp-release`) reported `Not Run`, which `ctest` treats as a failure.
  Reproduced live in a throwaway Docker container mirroring the exact
  CI job before this slice's own target-list addition — this means the
  job, as previously written, would fail in real GitHub Actions CI
  regardless of anything in this slice. Fixed with a `-R` regex
  restricting `ctest` to exactly the targets the job actually builds
  (now including `fl_secure_aggregation_crypto_tests`/
  `fl_secure_aggregation_tensor_mask_tests`/
  `fl_secure_aggregation_session_manager_tests`, closing a second,
  separate gap: those three targets existed but were never in the
  job's `--target` list before this slice).
* `docker-compose.security.yml`'s coordinator override writes the
  coordinator's signed public-key bundle
  (`FL_COORDINATOR_SIGNING_KEY_BUNDLE_PATH`) into the container's own
  private writable layer — no compose file shared that file with any
  worker container, even though every worker needs it on disk to
  verify a signed task or a frozen roster (see
  [message-authenticity-report.md](message-authenticity-report.md)
  section 11). Fixed in
  `docker-compose.secure-cohort-handshake.yml` with a shared named
  volume (`coordinator-trust-bundle`), read-write on `coordinator`,
  read-only on every worker.
* `docker compose up -d --build <services>` rebuilds the *entire
  dependency graph* of the named services, not just the named services
  themselves. Since every worker service `depends_on: coordinator`, a
  second `--build up` invocation for only the newly-added workers also
  rebuilt (and therefore silently recreated) the already-running,
  already-stateful `coordinator` container — wiping the very run this
  validation script had just created on it. Worked around in
  `scripts/validate_secure_cohort_handshake.py` by building every image
  exactly once (`docker compose build`) before either `up` call, never
  passing `--build` to `up` itself.
* `WorkerService.run()`'s `acquire_task()` call site only catches
  `CoordinatorUnavailableError` and `CoordinatorTaskRejectedError`
  around `self._client.acquire_task(...)` — any other rejection the
  gRPC layer raises as the more general `CoordinatorRejectedError`
  (e.g. `AcquireTask` returning `FAILED_PRECONDITION: unknown run_id`
  when a worker container starts before its configured run exists,
  exactly the ordering this validation script originally raced into)
  propagates uncaught and crashes the worker process outright, rather
  than being logged and retried on the next poll like every other
  transient rejection. **Not fixed this slice** — worked around by
  script-level sequencing (bring up infra, create and start the run,
  *then* bring up the workers) rather than by broadening
  `WorkerService`'s exception handling, since that touches this
  project's established worker main-loop robustness contract and is
  outside this slice's 20-item scope. Left as a disclosed, real gap for
  a future slice.

**Explicitly not built this slice** (per the task's own scope
boundary, restated from
[secure-cohort-handshake-foundation.md](secure-cohort-handshake-foundation.md)):
`SubmitMaskedClientUpdate` remains `UNIMPLEMENTED`; no tensor/weight
masking in the production worker; no secure aggregate finalization; no
FedAvg model-version advance through secure aggregation (the validated
Docker run's actual round completes via the pre-existing *unmasked*
training/submission path, unchanged); no sample-private/user-level/
hybrid/adaptive-clipping secure modes; no dropout recovery or threshold
secret sharing (no vetted dependency selected, per the Threshold
Secret-Sharing Restriction — no custom Shamir secret sharing, no
finite-field share interpolation, implemented anywhere in this slice);
no Go secure-aggregation APIs; no web secure-aggregation observability
pages; no new Prometheus metrics (the new `SecurityEventType` values,
journaled and queryable via the existing `ListSecurityEvents` RPC, are
this slice's "minimal events and metrics" surface, per its own scope
item 17).

## Masked Update Runtime and No-Dropout Secure FedAvg Finalization slice

See [secure-aggregation-masked-runtime-audit.md](secure-aggregation-masked-runtime-audit.md)
for the pre-implementation audit/design note and
[secure-aggregation-masked-runtime-report.md](secure-aggregation-masked-runtime-report.md)
for the full completion report. This slice makes the round the prior
slice's handshake was building toward actually happen: workers train,
fixed-point encode, pairwise-mask, sign, and submit real
`MaskedClientUpdate`s; the coordinator verifies, persists, and — once
the complete frozen cohort has submitted — finalizes, decodes, and
advances the model version through the masked path.

**Implemented and live-validated**: worker-side masked-update
construction (`masked_update.py`) wired into `WorkerService.run()`
(structurally the only submission path a secure-bound task ever takes
— no cleartext fallback exists in that branch); a live
`SubmitMaskedClientUpdate` RPC (full signed-message pipeline, matching
`AdvertiseSecureAggregationKey`'s already-proven shape) that finalizes
the session and bridges the decoded aggregate into the live round via
a new `RunInstance::apply_secure_aggregate_and_advance` method; a
masked-update deadline sweep (`sweep_expired_masked_update_deadlines`);
coordinator-enforced cleartext prohibition on `SubmitClientResult` for
any run/round bound to a secure session (except the deliberate
privacy-mode-incompatible fallback); algorithm/privacy-mode
compatibility gating restricting secure aggregation to `fedavg` with no
privacy mode or sample-level DP; five new `SecurityEventType` values
for the masked-update lifecycle. A real three-worker Docker Compose
stack (`infra/compose/docker-compose.masked-update-runtime.yml`,
`scripts/validate_masked_update_runtime.py`) drove a real single-round
3-client FedAvg run through the complete masked path: all three
workers reached `READY_FOR_MASKED_TRAINING`, trained, masked, and
submitted; the coordinator accepted all three, finalized, and
`model_version` genuinely advanced `v0 → v1`; the run reached
`COMPLETED`. 15/15 automated checks passed. C++: 8/8 gRPC-gated
Docker ctest targets, 7/7 local Windows protobuf-free suites. Python:
413 passed, 1 skipped.

**Two real bugs found and fixed by this slice's own testing** (see the
report doc for the full detail):

* `sweep_expired_masked_update_deadlines` could abort a session whose
  complete cohort had already submitted and was merely awaiting
  `finalize()` — caught by a new C++ test before it ever reached
  Docker, fixed by skipping sessions where the contribution count
  already meets the cohort size.
* **`SubmitMaskedClientUpdate` and `AdvertiseSecureAggregationKey`
  shared one `MessageStream::kSecureAggregation` replay track**, while
  the worker's own local `SequenceStateStore` already tracks them as
  two independent counters — so every worker's first-ever masked
  update was rejected as a replay of its own key advertisement. Found
  live, by this slice's own first Docker validation run (not by any
  prior unit test, since none had driven both message types through
  one shared `ReplayProtectionStore` instance before). Fixed by adding
  `MessageStream::kSecureAggregationMaskedUpdate` as a genuinely
  independent track — which is exactly what that enum's own doc
  comment, written by the *prior* slice, already said key
  advertisements and masked updates would need. A regression test now
  pins the exact scenario.

**Explicitly not built this slice** (bounded/deferred, per the audit
doc's own scope statement, not oversights): Go read-only APIs and web
secure-aggregation observability (no HTTP/UI surface for session state
exists yet); native Prometheus metrics for secure aggregation (same
"no native C++ `/metrics` endpoint" decision as the Privacy Engineering
phase); performance benchmarking; a dedicated live gRPC test harness
for `SubmitMaskedClientUpdate` at the `coordinator_service_test.cpp`
level (RPC-level correctness is proven live via Docker validation
instead, matching the precedent `AdvertiseSecureAggregationKey` already
set). Threshold secret sharing, dropout recovery, partial-cohort
finalization, Byzantine-robust aggregation, ZK proofs, verifiable
clipping, attestation, TEE/TPM, and homomorphic encryption remain
entirely out of scope, unchanged from every prior secure-aggregation
slice.

## Secure User-Level Differential Privacy Runtime slice

See [secure-user-level-dp-runtime-audit.md](secure-user-level-dp-runtime-audit.md)
for the pre-implementation audit,
[secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md)
for the full mechanism specification (adjacency model, sensitivity,
noise placement, quantization margin, budget reservation design), and
[secure-user-level-dp-runtime-report.md](secure-user-level-dp-runtime-report.md)
for the full completion report. This slice makes `USER_LEVEL_DP`
usable under secure aggregation for the first time: worker-side
clipping replaces the coordinator-side clipping the existing
(cleartext) mechanism used, which was structurally incompatible with
secure aggregation's "coordinator never sees an individual update"
guarantee.

**Implemented and live-validated**: worker-side deterministic global
L2 clipping (`user_level_clipping.py`) applied before fixed-point
encoding, with a fixed weight of exactly 1 per user (variable
weighting rejected as `SECURE_USER_LEVEL_DP_VARIABLE_WEIGHT_UNSUPPORTED`);
a quantization-aware effective sensitivity
(`clip_norm + sqrt(N)*(0.5/scale_factor)`) noise is calibrated
against, never the optimistic unquantized clip norm; a self-contained
signed `SignedUserLevelPrivacyAttestation` bound into
`MaskedClientUpdate` and verified against the outer envelope's own
signing key; central Gaussian noise added once, inside
`SecureAggregationSessionManager::finalize()`, to the decoded ring sum
before the existing divide-by-weight-sum step, reusing the run's
existing OS-CSPRNG-backed `CryptoSecureNoiseProvider`; authoritative
coordinator accounting (`UserLevelAccountant`) that commits exactly
once, gated by the same round-progression idempotency guard that
already made `apply_secure_aggregate_and_advance` safe against retried
RPCs; a non-mutating budget pre-check at session-creation time
(`SECURE_USER_LEVEL_DP_BUDGET_EXHAUSTED` if the projected epsilon would
meet/exceed `epsilon_budget`). A real three-worker Docker Compose
stack (`infra/compose/docker-compose.secure-user-level-dp.yml`,
`scripts/validate_secure_user_level_dp.py`) drove a complete
single-round FedAvg run with a deliberately tiny `clip_norm=0.01`
through the full secure user-level-DP path: real worker-side clipping
genuinely engaged on real training gradients (not a synthetic injected
value), all three signed attestations were accepted, and
`model_version` genuinely advanced `v0 → v1`. 22/22 automated checks
passed. C++: 8/8 gRPC-gated Docker ctest targets, 7/7 local Windows
protobuf-free suites. Python: 454 passed, 1 skipped (up from 413).

**Two real bugs found and fixed by this slice's own testing** (see the
report doc for the full detail) — both in the attestation's
cross-language canonical-hash agreement, both caught because live
Docker validation exercises two independently hand-written
canonicalizers against each other, which no single-language unit test
ever could:

* The hand-written C++ JSON canonicalization emitted `client_id`/
  `clip_norm` in the wrong order (`clip_norm` before `client_id` —
  alphabetically backwards). Python's `json.dumps(sort_keys=True)`
  silently self-corrects any dict-literal ordering mistake at
  serialization time; C++ has no equivalent and simply emits whatever
  order the source code was written in. Every attestation the
  coordinator verified failed with `payload_hash_mismatch` until this
  was found and fixed.
* While adding the cross-language golden-fixture regression test that
  should have caught the above before it ever reached live validation,
  the fixture's own Python `provider` value was a wrong guess (`1`
  instead of the real
  `SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL` value,
  `2`) — found and fixed once the first fix alone didn't make the new
  test pass either.

**Explicitly not built this slice** (bounded/deferred, per the audit
doc's own scope statement, not oversights): Go read-only APIs and web
secure-aggregation observability for user-level-DP session state (no
HTTP/UI surface exists yet — matches the immediately prior slice's own
deferral); a dedicated `SECURE_USER_LEVEL_DP_*` security-event
vocabulary (the existing secure-aggregation event types are reused
instead); new Prometheus metrics; performance benchmarking; a formal
bounded-sample statistical smoke test against the production CSPRNG
provider (covered instead by a deterministic-provider test proving
noise engages, is applied exactly once, and changes the result). Secure
hybrid DP, secure adaptive clipping, threshold secret sharing, dropout
recovery, partial-cohort finalization, malicious-client clipping
verification, variable user weights under secure aggregation,
replace-one adjacency, and random-subsampling amplification all remain
entirely out of scope — the last two are reserved enum values on the
wire (`SECURE_USER_LEVEL_ADJACENCY_MODEL_REPLACE_ONE`,
`SECURE_USER_LEVEL_SAMPLING_ASSUMPTION_RANDOM_SUBSAMPLING`) the
coordinator never produces.

## Secure User-Level DP Operations, Observability, and Release Evidence slice

See [secure-user-level-operations-audit.md](secure-user-level-operations-audit.md)
for the pre-implementation audit and scope statement,
[secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md)
for the documented state machine, and
[secure-user-level-operations-report.md](secure-user-level-operations-report.md)
for the full completion report. This slice adds operational
observability around the previous slice's already-complete privacy
mechanism (`SECAGG_NO_DROPOUT_EXPERIMENTAL`, honest-client-dependent
user-level DP) — it does not change the mechanism itself.

**Implemented and live-validated**: a bounded, representative
`SECURE_USER_LEVEL_DP_*` security-event vocabulary (12 of the
requested ~29, wired at real C++/Python call sites — configuration
accepted/rejected, budget reserved/exhausted, clipping applied,
attestation accepted/rejected, noise applied, accounting committed,
round completed, finalization conflict, health degraded); Go-side
`fl_secure_user_dp_*` Prometheus metrics fed by a new
`GetSecureUserLevelPrivacyHealth` coordinator read RPC (no native C++
metrics endpoint added, preserving the established re-export
architecture); 4 new coordinator read RPCs
(`GetSecureUserLevelPrivacyHealth`/`Budget`,
`ListSecureUserLevelPrivacyRounds`,
`GetSecureUserLevelPrivacyRound`) backing 5 Go coordinator-client
methods and 5 new `GET /api/v1/secure-aggregation/privacy/*` routes,
each gated by its own responsibility-named permission
(`security.secure_user_dp.{status,health,rounds,round,budget}.read`)
with a real ADMIN/RESEARCHER/VIEWER/SERVICE matrix (SERVICE has no
implicit access anywhere; VIEWER reads aggregate status/health but not
per-run round/budget detail) and explicit per-role response types
(ADMIN sees exact epsilon, RESEARCHER sees it rounded to 3 places); a
new Web page (`/security/secure-aggregation/privacy`) with capability,
runtime-health, budget-lookup, and a cursor-paginated Privacy Round
Explorer, plus all 10 mandated trust-limitation warnings rendered as a
real, always-visible list; a real Playwright browser spec
(`secure-user-level-dp-privacy.spec.ts`) covering unauthenticated,
admin, viewer, and service-role behavior against the live backend; a
new bounded statistical smoke test against the real
`CryptoSecureNoiseProvider` (20,000 draws, documented mean/variance/
tolerance, explicitly not described as certification); a documented
publication-boundary state machine with new restart-after-publication
and corrupted-checkpoint-fails-closed C++ tests; a 6-scenario
`secure-aggregation-user-level-dp` runtime-validation harness group
(status/health/access-control/error-handling, real HTTP assertions);
`scripts/validate_secure_user_level_dp.py` extended with real API/
event/metric assertions against the same live 3-worker round the prior
slice already drove; the `secure-aggregation-user-level-dp` group added
to the bounded PR-subset CI gate; an extended artifact-sanitation
pattern for worker private-key/shared-secret/mask-key field leaks.

**Three real bugs found and fixed by this slice's own testing** (see
[secure-user-level-operations-report.md](secure-user-level-operations-report.md)
§2 for full detail), none catchable by inspection or a single
language's own unit tests: (1) the new restart-after-publication C++
test failed on its first run — `UserLevelLedgerEntry`'s new
`committed_at_unix_s` field was added to the struct and set at both
push sites, but the checkpoint's `encode_user_level_entry`/
`parse_user_level_entry` functions were never updated to actually
serialize/parse it, silently resetting to `0.0` on every restore; (2)
the first live Docker validation run's event check missed
`SECURE_USER_LEVEL_DP_CLIPPING_APPLIED` — a worker-emitted event that
reaches the coordinator only via a real 5-second cross-service batch-
flush timer the original single-shot check didn't wait for; (3) the
first live Playwright run found `ListSecureUserLevelPrivacyRounds`
returning 404 for an unknown `run_id` instead of an empty page (the
correct LIST-endpoint behavior, matching every other list endpoint in
this codebase) — the web page's Round Explorer showed a scary error
state instead of a plain empty result for any not-yet-existent run.

**Explicitly not built this slice** (bounded/deferred, per the audit
doc's own scope statement, not oversights): the remaining ~17 of the
~29 named event types (finer-grained sub-steps, e.g. separate encoding/
masking/signing-stage events); the ~31 individually-named metrics the
task specification suggested are not each implemented as a separate
series (4 metric families are implemented instead — route-level
request counts and aggregate runtime health, see
docs/security-metrics.md); a
`kSecureUserLevelDpDropoutAborted`/`kSecureUserLevelDpCheckpointReconciled`
call site (the enum values exist; no real dropout-abort or restart-
reconciliation call site emits them yet — `reconciliation_required`
in the health RPC is therefore always `false` today, since no automated
cross-check between the ledger and model-version state exists); the
literal 24-scenario-ID / 56-item-checklist enumerations (addressed at
representative depth instead); performance benchmarking (the existing
benchmark harness is not extended); new CI *jobs* (new tests land in
the existing broad `cpp-grpc`/`python`/`go`/`web` jobs' full-suite
invocation, plus the `security-runtime-full` workflow's unfiltered
scenario run, which automatically picks up the new harness group and
Playwright spec). Secure hybrid DP, secure adaptive clipping, threshold
secret sharing, and dropout recovery remain entirely out of scope,
unchanged from every prior secure-aggregation slice.

## Secure Transport and Worker Identity Hardening slice (historical)

**Superseded by the two follow-on slices below** — every bullet
previously listed here (C++ mTLS unverified, no live three-way mTLS
session, no worker identity registry, signed capabilities not wired in,
canonical serialization Python-only) has since been resolved. See
[transport-identity-report.md](transport-identity-report.md) for the
itemized status.

* **`CryptoSecureNoiseProvider`'s identity is still not threaded into
  the privacy ledger wire structures** — the one item from this slice
  that remains open. `provider.identity()` is a real, checkable
  accessor at the C++ object level, but
  `UserLevelLedgerEntry`/`AdaptiveClippingLedgerEntry` have no
  `noise_provider_identity` field yet — see
  [secure-random-runtime.md](secure-random-runtime.md).

## Coordinator Transport Verification and Message Authenticity slice

See [transport-identity-report.md](transport-identity-report.md).
Delivered and live-validated: C++ gRPC coordinator compiled and
runtime-validated in Docker; real Go-to-C++ and Python-to-C++ mTLS;
certificate URI SAN identity validation and service/worker-to-certificate
binding; development PKI generation/issuance/inspection/revocation,
automated end-to-end on both bash and PowerShell; a persistent,
filesystem-backed `WorkerIdentityRegistry` with atomic writes and a
full status machine, validated across a real restart; Ed25519 worker
signing identities; canonical capability-statement serialization with
proven Python/C++ parity; live signed-capability verification inside
`RegisterWorker`.

* **Signing-key rotation does not exist.** A `RegisterWorker` or
  `Heartbeat` call presenting a signing key that differs from the one
  already on record for a `worker_id` is unconditionally rejected
  (default-deny) — there is no sanctioned way for a worker to
  legitimately rotate its key yet. See
  [signing-key-management.md](signing-key-management.md).
* **`certificate_serial` is not populated** in `WorkerIdentityRegistry`
  records — only `certificate_fingerprint` (a SHA-256 over the
  AuthContext's PEM text, not a DER-based fingerprint) is. Not a
  security gap; a metadata gap for a future Go/web view.

## Message Authenticity Enforcement and Identity Lifecycle slice (historical)

**Superseded in relevant part by the Signed Client Results and Worker
Lifecycle Enforcement slice below** — signed client results, worker
lifecycle administration RPCs, worker-status enforcement at
`AcquireTask`, and active-lease cancellation on revocation (all listed
below as gaps at the time) are now implemented. See
[message-authenticity-report.md](message-authenticity-report.md) for
this slice's own original itemized status.

* **`RegisterWorker`'s `SignedCapabilityStatement` still has no
  persistent replay protection or sequence validation** — `ReplayProtectionStore`
  covers `SignedWorkerEnvelope` messages (`Heartbeat`, `SubmitClientResult`);
  the capability-statement path still only checks expiry, exactly as
  documented in [signed-capabilities.md](signed-capabilities.md). Still
  open.
* **The in-memory, non-persistent `WorkerRegistry` (runtime scheduling
  state) still does not survive a coordinator restart**, unlike
  `WorkerIdentityRegistry`/`ReplayProtectionStore`. Still open,
  unchanged — a pre-existing architectural characteristic, not
  something either slice attempted to fix.
* **No signing-key persistence beyond `WorkerIdentityRegistry`'s single
  `signing_public_key`/`signing_key_id` fields per worker.** Still
  open — one worker has exactly one signing key on record at a time.

## Signed Client Results and Worker Lifecycle Enforcement slice (historical)

**Superseded in relevant part by the Privacy Record Authenticity,
Signing-Key Lifecycle, and Coordinator-Signed Tasks slice below** —
independently signed sample-level privacy records, accountant
monotonicity, and budget-decision consistency (all listed below as
gaps at the time) are now implemented. Formal Python `pytest` coverage
for signing code was also added (partially — see below). See
[message-authenticity-report.md](message-authenticity-report.md) for
this slice's own original itemized status.

* **`SubmitClientResult`/`ReportTaskProgress` still do not check
  `SUSPENDED`/`REVOKED` status directly.** Only `RegisterWorker`/
  `Heartbeat`/`AcquireTask` do. Still open, unchanged — a revoked
  worker's active lease is proactively canceled by `RevokeWorker`
  itself (cross-run, unconditional), which closes the practical window.
* **`allow_unsigned_client_results`/`allow_unsigned_privacy_records`'s
  development-compatibility modes are not additionally gated on the
  target run's privacy mode being `NONE`** — each is gated only on its
  own coordinator-wide env var, not re-checked per-run. Still open.
* **Signing-key rotation, coordinator-signed tasks, Go security
  administration HTTP APIs, web security administration views, formal
  audit-record persistence, and full Docker Compose authenticated-
  message validation are all still entirely unimplemented.** See the
  new slice section below.

## Privacy Record Authenticity, Signing-Key Lifecycle, and Coordinator-Signed Tasks slice (historical)

**Superseded in relevant part by the Signing-Key Lifecycle slice
below** — signing-key rotation, grace periods, expiry, and revocation
(all listed below as gaps at the time) are now implemented.
Coordinator-signed tasks remain deferred, unchanged. See
[message-authenticity-report.md](message-authenticity-report.md) for
this slice's own original itemized status.

* **`configuration_hash` is still not independently recomputed**
  against the coordinator's own assigned `SampleLevelDPConfig` for a
  task's round. Still open.
* **`AcquireTask` still does not consult budget-decision history.**
  Still open.
* **No RPC exposes `AccountantMonotonicityStore::reset()`.** Still
  open.
* **Coordinator-signed tasks are still entirely unimplemented.** See
  the new slice section below.

## Signing-Key Lifecycle slice

See [message-authenticity-report.md](message-authenticity-report.md)
for the full itemized status. Delivered and live-validated (16/16
checks plus a separately-verified legacy migration, real
`GrpcCoordinatorClient` production code path including
`rotate_signing_key()`, real mTLS, real containerized coordinator): a
persistent, multi-key-per-worker `SigningKeyRegistry`
(`PENDING`/`ACTIVE`/`GRACE_PERIOD`/`REVOKED`/`EXPIRED`, restart-safe,
corruption-detecting, mirroring `WorkerIdentityRegistry`'s exact
persistence pattern); idempotent legacy migration from
`WorkerIdentityRegistry`'s existing single-key data, exercised via a
real coordinator restart with the signing-key registry file deleted;
a signed `WorkerKeyRotationRequest` contract (reusing
`SignedWorkerEnvelope`/`ReplayProtectionStore` via the `KEY_MANAGEMENT`
stream, the same pattern already proven twice); real grace-period
acceptance and a real, elapsed-time expiry (not simulated); immediate
signing-key revocation with automatic worker suspension when the
revoked key was the worker's only valid one; a single shared
enforcement point (`resolve_signing_key`) wired into capability
statements, heartbeats, client results, and privacy records alike;
`AcquireTask` blocking a worker with no valid signing key at all; and
a cross-language golden fixture for the rotation-request payload hash.

* **Coordinator-signed tasks are entirely unimplemented.** Superseded
  by the Coordinator-Signed Tasks slice below — a coordinator signing
  identity, `SignedCoordinatorTask` contract, Python-side verification,
  and worker-side replay/journal are all now implemented and
  live-validated.
* **No signing-key-specific security events, metrics, or audit records**
  beyond structured stderr logging (`WORKER_KEY_ROTATION_ACCEPTED`,
  `WORKER_KEY_REVOKED`, `SIGNING_KEY_MIGRATED`) — no Prometheus
  counters, no durable audit-record store.
* **`GrpcCoordinatorClient.rotate_signing_key()` does not yet persist
  its own rotation state to disk across a worker process restart** —
  `signing_key_rotation.WorkerKeyRotationState`/`load_rotation_state`/
  `save_rotation_state` exist and are unit-tested, but the client only
  tracks "which key is preferred" in memory for the object's lifetime,
  not wired into that state file yet. See [key-rotation.md](key-rotation.md).
* **No default rotation interval, minimum key lifetime, or automated
  background expiry sweep** — a policy choice this pass declined to
  add, not a missing store capability (expiry is still correctly
  enforced lazily at verification time regardless).
* **Old private-key file cleanup after grace-period expiry is not
  automated** — `save_keyed_signing_identity`'s files are never
  deleted by this pass.
* **Formal test coverage covers only the rotation-request hash and the
  `SigningKeyRegistry`/local-key-state modules** — no dedicated
  end-to-end pytest suite exists yet for the full rotate→grace-period→
  expire→revoke lifecycle beyond the live Docker validation script;
  that script is real but is a standalone scratchpad script, not part
  of the `python/tests/` suite CI runs automatically (the same
  disclosed gap every prior slice's live-validation work has carried).
* **Only direct `docker run` scenarios were live-validated**, not the
  full 33-scenario Docker Compose flow the parent specification
  describes (which also requires coordinator-signed tasks and Go/web
  verification, neither of which exist).
* **No performance benchmarking was performed** for signing-key
  registry lookup/persistence, rotation-request canonical serialization,
  hashing, signing, or verification.

## Coordinator-Signed Tasks slice

See [message-authenticity-report.md](message-authenticity-report.md)
and [signed-coordinator-tasks.md](signed-coordinator-tasks.md) for the
full itemized status. Delivered and live-validated in a real Docker
build (real `libgrpc++-dev`/`protobuf-compiler-grpc`, 12/12 `ctest`
suites passing, a live mTLS coordinator/worker round trip): a
persistent coordinator Ed25519 signing identity, separate from the TLS
credential; a `CoordinatorSigningKeyRegistry` mirroring
`SigningKeyRegistry`'s design; a `SignedCoordinatorTask` contract
additively attached to `ClientTrainingTask`; five configuration hashes
plus a task payload hash, each with its own domain-separation prefix;
a coordinator task sequence store; real `AcquireTask` signing (fixing
two pre-existing gaps — `lease_expires_at`/`attempt` were never
populated on the wire before this slice); a trusted-coordinator-key
bundle file (never fetched via RPC); a full Python verification
pipeline with 16 structured rejection reasons; a worker-side task
replay store; an accepted-task execution journal with real
crash-recovery (a genuinely separate journal instance against the same
file) and duplicate-execution rejection; and a real cross-language
golden fixture that caught two genuine bugs (a `std::to_chars`
float-formatting threshold mismatch and a JSON key-ordering bug in the
hand-written C++ privacy-configuration-hash encoder) — see
[task-configuration-hashes.md](task-configuration-hashes.md).

* **No gRPC rotation RPC for the coordinator's own signing key.**
  Superseded by the Security Administration slice below —
  `RotateCoordinatorSigningKey`/`RevokeCoordinatorSigningKey` are now
  real, live-validated RPCs.
* **No signed-coordinator-task-specific security events, metrics, or
  audit records** beyond what the pre-existing structured stderr
  logging already covers — no new Prometheus counters, no durable
  audit-record store.
* **Journal entry retention/cleanup is not implemented** — entries
  accumulate indefinitely in `AcceptedTaskJournal`, no TTL or
  size-based eviction.
* **Time-based nonce expiry is not implemented in
  `CoordinatorTaskReplayStore`** — bounded only by a fixed
  per-track nonce cap (256), not a time window, unlike the
  coordinator-side `ReplayProtectionStore`'s `purge_expired`.
* **`__main__.py`/`configuration.py` were not wired with new env vars**
  for `trusted_coordinator_keys_path`/replay-store/journal paths — the
  live validation constructed `GrpcCoordinatorClient` directly with
  these parameters, the same scope boundary every prior mTLS/signing
  slice's `__main__.py` wiring has also left unaddressed (see
  [docker-runtime.md](docker-runtime.md)'s "Secure Transport and Worker
  Identity Hardening slice" section for the identical precedent).
* **Only direct `docker run`/live-mTLS scenarios were validated**, not
  the full 39-scenario Docker Compose flow the parent specification
  describes.
* **No performance benchmarking was performed** for task signing,
  hashing, or verification.
* **Formal test coverage does not include a dedicated
  `coordinator_service_test.cpp` integration test for the signed-task
  path specifically** — `fl_coordinator_grpc_tests` was confirmed to
  still pass unchanged (proving backward compatibility), but no new
  C++ integration test drives `AcquireTask` with a real
  `CoordinatorSigningIdentity` configured; that path was live-validated
  instead via the real running server and a real Python client.

## Security Administration, Observability, and Runtime Validation slice

See [security-administration-report.md](security-administration-report.md)
for the full itemized status. Delivered and live-validated in a real
Docker build (12/12 `ctest` suites, 18/18 live RPC checks over real
mTLS, a further 5/5 recovery-CLI scenarios validated independently of
any running server): live `RotateCoordinatorSigningKey`/
`RevokeCoordinatorSigningKey` RPCs (real OpenSSL Ed25519 keygen for
each rotation, real grace-period transition, real lazy-evaluated
expiry after a genuine elapsed wait, real revocation with
`production_task_issuance_stopped` reporting); a persisted
`IdempotencyStore` (a retried rotation confirmed to return the
identical previously-minted key rather than generating a second one);
a keyed coordinator private-key directory
(`save_keyed_coordinator_signing_identity`/
`load_keyed_coordinator_signing_identity`, mismatched-key-id
detection) plus a thread-safe `CoordinatorActiveIdentityStore` so
`AcquireTask` signs with whichever key is currently ACTIVE even while
concurrent requests are in flight; a strengthened, versioned,
checksummed trusted-key bundle (`trusted_key_bundle.cpp`) confirmed
cross-language-verifiable (a real C++-written checksum independently
re-derived and confirmed by Python, not merely round-tripped through
one side); a stateful `TrustedCoordinatorKeyBundleReloader` (Python)
enforcing bundle-version monotonicity (a rollback attempt is rejected;
the previous valid bundle is kept on any validation failure), wired
into `GrpcCoordinatorClient.acquire_task` as a "reload before
rejecting an unknown key" step; and a real, independently-tested
recovery CLI (`fl_coordinator_key_admin_cli`) covering every documented
recovery scenario (lost/expired/revoked active key, corrupted bundle)
without needing a running coordinator process at all.

* **Go HTTP security administration APIs, Go security client
  methods, and the web Security Center are entirely unimplemented.**
  Confirmed by direct inspection before starting this slice: zero
  worker-lifecycle-administration RPCs (`SuspendWorker`,
  `GetWorkerIdentity`, `GetWorkerSigningKeys`, etc. — all built across
  three prior C++ slices) are wired into the Go coordinator client at
  all, there are no security-related HTTP routes, and the web app has
  no security-related routes or components (`go/internal/observability/audit.go`
  is a bare 16-line struct with no persistence or querying). Building
  this properly (14+ HTTP endpoints, role-based authorization and
  redaction, a production-shaped Security Center with 6 routes) is a
  full slice of its own scope, explicitly deferred rather than
  attempted shallowly.
* **No comprehensive security-event schema, and no new Prometheus
  security metrics** beyond the structured stderr events this slice
  added for the rotation/revocation/bundle operations specifically
  (`COORDINATOR_KEY_ROTATION_STARTED/COMPLETED/FAILED`,
  `COORDINATOR_KEY_REVOKED`, `TRUSTED_BUNDLE_GENERATED/GENERATION_FAILED`)
  — the full ~50-event schema and low-cardinality Prometheus counters
  the specification describes across C++/Python/Go are not implemented.
* **No durable, queryable security-audit-journal.** The registry files
  themselves are durable and restart-safe, but there is no append-only,
  paginated, filterable audit log distinct from them — Work Packages
  S/T/U (durable audit journal, audit query APIs, role-aware
  redaction) are not implemented.
* **No full 58-scenario Docker Compose security validation matrix.**
  This slice's live validation used real, direct `docker run` +
  `cmake --build` + a live mTLS coordinator/client round trip (the same
  established convention every prior security-focused slice in this
  project has used — see [docker-runtime.md](docker-runtime.md)'s
  "Secure Transport and Worker Identity Hardening slice" section for
  the identical precedent), not the full Go/web/Compose orchestration
  the specification describes.
* **No Go tests, web tests, or security-focused CI gates were added**
  — none of the Go/web surfaces exist yet for tests to cover.
* **The RPC-level recovery path (`RotateCoordinatorSigningKey` with no
  `expected_current_signing_key_id`) does not fall back to registering
  a fresh initial key the way the CLI's `rotate` subcommand does** —
  confirmed live: this is a deliberate difference (an unauthenticated-
  precondition RPC call silently creating a brand-new trust root would
  be a much larger blast-radius mistake to allow accidentally than a
  human operator explicitly invoking a CLI tool with filesystem
  access), not an oversight, but it does mean recovering from a fully
  revoked/lost coordinator identity requires the CLI, not the live RPC
  surface, until a future pass adds an explicit, separately-authorized
  "force initial key" RPC if that is ever judged worthwhile.
* **`coordinator_signing_identity.hpp`'s keyed-storage convention has
  no cleanup for old rotated-out private-key files** — same disclosed
  gap the worker-key rotation slice already carries for
  `save_keyed_signing_identity`'s files.
* **No performance benchmarking was performed** for rotation,
  revocation, bundle regeneration, or reload.

## Security Operations and Administration slice

See [security-operations-report.md](security-operations-report.md) for
the full itemized status. Delivered and live-validated in a real Docker
Compose mTLS run (real `coordinator` + `api` containers, real dev-PKI
certificates, 22/22 live checks): two new C++ RPCs
(`GetTransportSecurityStatus`, `GetSecurityTrustModel`); a full Go
`SecurityClient` (12 methods, real `GrpcClient` + deterministic
`MockClient` implementations); a `go/internal/security` permission
package (14 `security.*` constants, an ADMIN/RESEARCHER/VIEWER/SERVICE
matrix); 13 real HTTP endpoints under `/api/v1/security/...` plus one
honest `501` for the events endpoint; role-aware response redaction
(worker identity, audit records); HTTP-layer mutation idempotency (an
`Idempotency-Key` header, an in-memory cache); real audit logging of
every security mutation into the existing, general-purpose Go
`AuditRepository`; and — new infrastructure — the first Docker Compose
override to ever mount PKI material and run real mTLS between the Go
API and C++ coordinator (`infra/compose/docker-compose.security.yml`).

* **The Web Security Center is entirely unimplemented.** No `/security`
  web routes, no dashboards, no admin forms. This was explicitly
  deferred (the user chose the "Go API + permissions only" scope over
  "Go API + minimal Web Security Center" when asked).
* ~~**No formal, schema-versioned security-event type and no event
  stream.**~~ **Superseded by the Security Events, Metrics, and Durable
  Audit Journal slice below** — a real, schema-versioned event type
  exists in C++/Python/Go, `GET /api/v1/security/events` is real (no
  longer `501`), and a durable event journal exists per service.
* ~~**No Prometheus metrics for this HTTP surface.**~~ **Superseded
  below** — `fl_security_events_total` (Go) and
  `fl_worker_security_events_total` (Python) now exist, with disclosed
  scope limits (see [security-metrics.md](security-metrics.md)).
* ~~**No durable, security-specific audit journal.**~~ **Superseded
  below** — a new `SecurityAuditJournal` exists in Go and C++, with real
  pagination/filtering; the general-purpose `AuditRepository` continues
  to be written to unchanged (additive, not replaced) — see
  [security-audit-journal.md](security-audit-journal.md).
* **SERVICE-role explicit per-user scope grants have no plumbing.**
  `security.HasScope` exists as the intended mechanism, but
  `application.Actor`/`AuthSession.Capabilities` never carry anything
  beyond a role's fixed default (`capabilitiesForRole(role)`
  re-derived on every login) — no live HTTP request path feeds
  `HasScope` a real per-user scope list. Every SERVICE-role HTTP
  request is denied by default as a result — the fail-closed, honest
  outcome, not a caught bug.
* **Redaction is implemented for two response shapes only** (worker
  identity views, audit records) — worker/coordinator signing-key
  listings are all-or-nothing (full access or `403`), not partially
  redacted for VIEWER.
* **The HTTP-layer idempotency cache is in-memory only** (lost on
  process restart) and serializes all cached mutations behind one
  mutex — a deliberate, disclosed correctness-over-throughput choice,
  unlike the C++ coordinator's own file-persisted `IdempotencyStore`
  backing the two coordinator-signing-key RPCs specifically.
* **`docs/mtls.md`'s example `FL_COORDINATOR_SERVER_NAME` value
  (a SPIFFE URI) does not actually work** against Go's standard-library
  `crypto/tls` hostname verification, which only ever checks DNS/IP
  SANs, never URI SANs — discovered live while wiring the first-ever
  Compose mTLS run for this project. The correct value is a DNS name
  actually present on the coordinator's certificate (`coordinator`,
  matching both the cert's DNS SAN and the Compose service-discovery
  hostname) — see [security-api.md](security-api.md). `docs/mtls.md`
  itself was not rewritten this pass; treat its SPIFFE-URI example as
  superseded by this note until it is corrected.
* **Superseded (Security Runtime Completion and Release Evidence
  slice): `python-worker` is now included in the Compose mTLS
  override.** The wiring gap this bullet originally described (no real
  `WorkerTLSConfig`/signing identity ever constructed by the deployed
  container) is closed — `docker-compose.security.yml`'s
  `python-worker` service block now sets real
  `FL_WORKER_TLS_*`/`FL_WORKER_SIGNING_KEY_DIR`/
  `FL_WORKER_SECURITY_EVENT_*` variables, live-validated end to end
  (worker registration, signed capability verification, event
  centralization, coordinator-outage recovery — see
  [security-runtime-validation.md](security-runtime-validation.md)).
  `web` remains excluded from the mTLS override for the original,
  still-accurate reason: it talks to the Go API over plain HTTP/JSON,
  not gRPC, so it has no mTLS surface of its own to configure.
* **The Go API had no CORS handling at all until this slice**, meaning
  every client-side `fetch()` from the web app was silently blocked by
  the browser when the two ran as separate origins (`localhost:3000`
  vs `localhost:8080`, Compose's normal topology) — invisible to every
  non-browser test in this repository (curl, `httptest`, the Python
  runtime-validation harness). Fixed via `withCORS` in
  `go/internal/transport/httpapi/server.go`; see
  [security-runtime-validation.md](security-runtime-validation.md) for
  the full writeup. A single-tenant-dev-platform posture (reflects
  `Origin`, no allowlist, no `Access-Control-Allow-Credentials` since
  auth is Bearer-token only) — revisit if this platform ever serves
  multiple untrusted origins.
* **`event_id` is unique only within its own source's sequence, not
  globally, across `GET /api/v1/security/events`'s merged Go-local +
  coordinator-relayed response** — found live, via a real browser
  session, by this slice's Playwright suite (32 fetched events, only 20
  unique IDs). Caused a React key collision in the Event Explorer and
  worker-detail "recent activity" timeline, rendering the wrong event's
  content in a table row after a filter change; fixed at the rendering
  layer only (`key={`${source_service}-${event_id}`}` in both
  `security-events-console.tsx` and
  `security-worker-detail-console.tsx`). The underlying non-uniqueness
  itself, and its effect on `after_event_id` cursor semantics across a
  page boundary that splits unevenly between sources, is disclosed but
  not fixed at the wire level this pass — see
  `go/internal/transport/httpapi/security_handlers.go`'s
  `handleSecurityEvents` doc comment and
  [security-runtime-completion-report.md](security-runtime-completion-report.md).
  A future slice could make `event_id` globally unique at assignment
  time (e.g. prefixed by `source_service`) if a real consumer needs a
  strictly monotonic cross-source cursor.
* **Coordinator signing-key rotation form defaults previously exceeded
  the server's own enforced maximums** — the web UI defaulted to a
  365-day key lifetime and a 7-day grace period, while
  `coordinator_signing_key_registry.hpp` enforces real maximums of 90
  days and 1 day respectively. A real admin submitting the form
  unmodified always received a real 409. Found live, via a real
  browser rotating a real key; fixed by changing the UI's defaults to
  90/1 days and adding matching `max` attributes to the number inputs
  (`security-coordinator-keys-console.tsx`). The server-side maximums
  themselves were correct and unchanged — this was a UI/backend default
  mismatch, not a security-constraint defect.
* **Worker activation/revocation and worker-signing-key revocation
  were validated live only for the underlying permission-denial case**
  (unit/mock coverage is complete for the operations themselves,
  matching the prior slice's own live coverage of these exact RPCs) —
  this pass's live Docker time was spent proving the *new* Go/HTTP
  surface specifically, not re-proving RPCs already live-validated in
  slice 5.8.
* **No Go tests were added for the C++ side's two new RPCs beyond what
  the Go security-client mock/integration tests exercise** — the C++
  RPCs themselves have no dedicated unit test in
  `coordinator_service_test.cpp` (consistent with every other
  `ADMIN_CONTROL` RPC in that file, which also has none — this
  project's established convention is live-Docker validation for this
  RPC class, not `coordinator_service_test.cpp` unit coverage).
* **No security-focused CI gates were added.**

## Security Events, Metrics, and Durable Audit Journal slice

See [security-events.md](security-events.md), [security-metrics.md](security-metrics.md),
[security-audit-journal.md](security-audit-journal.md), and
[security-runtime-validation.md](security-runtime-validation.md) for
full detail. Implemented and live-validated: a shared, versioned
security-event schema (C++/Python/Go, real cross-language golden-
fixture checksum parity); durable, JSONL, rotating, corruption-
recovering event and audit journals per service; a real (no longer
`501`) `GET /api/v1/security/events`; a new, security-specific,
paginated/filterable `SecurityAuditJournal` backing `GET
/api/v1/security/audit`; role-aware redaction on both endpoints; a
`SECURITY_AUDIT_ACCESSED` meta-audit event; low-cardinality Prometheus
counters in Go and Python; and the project's first committed, reusable
Docker Compose security-validation script (12/12 live checks).

Current limitations (stated honestly):

* **Event/audit emission is wired at a representative subset of call
  sites, not exhaustively at every operation in the ~55-event
  registry.** C++: worker lifecycle (suspend/activate/revoke + lease
  cancellation), worker/coordinator signing-key revocation, coordinator
  signing-key rotation, `ADMIN_CONTROL` permission denials, transport
  startup, and `Heartbeat` (representative for the signed-worker-
  message category — `SubmitClientResult`/privacy-record/
  `RotateWorkerSigningKey` use the identical verification/rejection
  machinery but are not yet wired to event emission). Python: the
  coordinator-task verification pipeline only. Go: every security
  mutation handler, plus permission denial and detailed-audit access
  centrally. See [security-observability-inventory.md](security-observability-inventory.md)
  for the exact per-operation status.
* **RESOLVED in the Web Security Center, Event Centralization, and
  Security CI slice below: Python-worker-originated events are now
  shipped to the coordinator** via a new signed `SubmitWorkerSecurityEvents`
  RPC and persisted into the coordinator's own journal
  (`source_service="python-worker"`). They are still also persisted
  locally (JSONL journal, unchanged) and exposed via Prometheus for the
  worker process's own visibility.
* **No native C++ Prometheus `/metrics` endpoint** (still true, and
  still an intentional decision — see [security-metrics.md](security-metrics.md)'s
  design-decision section). Individual C++-owned security-event counts
  are still not relayed into Go's per-event `fl_security_events_total`
  counter. What the Web Security Center, Event Centralization, and
  Security CI slice below DOES add is a set of **aggregate** event-
  source-health gauges (`fl_security_event_source_*`), fed from the
  existing `GetSecurityEventSourceHealth` RPC on every poll — a
  narrower, already-computed aggregate, not a general per-event relay.
* **Merged event pagination across Go-local and coordinator-relayed
  sources is not a perfectly stable distributed cursor** at a page
  boundary that splits unevenly between the two sources — acceptable
  for this slice's scope, disclosed rather than silently assumed
  correct.
* **The C++ coordinator's own `SecurityAuditJournal` has no dedicated
  gRPC read RPC** — file-only, same posture every coordinator store had
  before its first read RPC existed. Go's and the coordinator's audit
  journals are not merged into one queryable view.
* **The Docker Compose validation script restarts only the `api`
  container**, not `coordinator`, and does not cover `python-worker`/
  `web` (excluded from the mTLS override for the same pre-existing,
  disclosed reason every prior slice's Compose validation excluded
  them).
* **Corruption/rotation/retention tests exist at the unit level in all
  three languages**, not exercised under sustained concurrent write
  load or via a live multi-gigabyte journal in Docker.

## Web Security Center, Event Centralization, and Security CI slice

See [security-event-centralization.md](security-event-centralization.md)
and [security-ui-report.md](security-ui-report.md) for full detail.
Implemented and validated: a complete Web Security Center (6 routes --
overview, worker identities/signing-keys list+detail with admin
lifecycle actions, coordinator signing-key rotate/revoke, security
event explorer, security audit explorer), a typed client API layer with
Idempotency-Key and `AbortSignal` support (new capabilities `lib/api.ts`
does not have), polling-based live updates with a bounded client-side
event buffer; a new `SubmitWorkerSecurityEvents` gRPC RPC reusing the
existing `SignedWorkerEnvelope` pipeline (signature/replay/worker-
binding verification, bounded batch size, per-event skip-not-fatal
validation) plus a Python worker-side persistent queue (built on the
existing `SecurityEventJournal`, not a second store) with at-least-once
delivery and restart-safe cursor persistence; aggregate event-source-
health Prometheus gauges in Go; a real Docker gRPC build/test CI job
(`cpp-grpc`) and a tracked-secret-scan CI job, closing two previously-
disclosed gaps (no CI coverage of the gRPC-gated coordinator; no
secret-scanning job) stated in this file's earlier sections. Fresh test
counts for this slice: 12/12 C++ ctest suites (including the new
`SubmitWorkerSecurityEvents` integration coverage) in a live Docker gRPC
build; Go `go test ./...` all packages passing (6 new tests); Python
`pytest tests python/tests` 336 passed/1 skipped (15 new tests); web
`npm run test` 46 passed (20 new tests).

Current limitations (stated honestly):

* **Critical event coverage is still wired at a representative subset
  of call sites, not exhaustively across the full event-type registry**
  (unchanged scope note from the slice above -- this slice added
  `WORKER_SECURITY_EVENT_BATCH_ACCEPTED`/`_REJECTED` as the two new
  event types the new RPC emits, and journals every accepted worker-
  submitted event under its own event_type, but did not attempt an
  exhaustive pass wiring every remaining unwired event type across
  C++/Python/Go).
* **No Grafana dashboard was added this slice.** `infra/grafana/` still
  provisions only a datasource, no dashboards -- building and
  provisioning a security dashboard against the new metrics
  (`fl_security_events_total`, `fl_security_event_source_*`) is
  deferred, not attempted partially. The metrics themselves are real
  and scrapeable; only the dashboard JSON/provisioning is missing.
* **The security-validation harness (`scripts/security-validation/`)
  was not modularized or expanded into the full enumerated scenario
  matrix this slice.** The existing script (12/12 checks, from the
  prior slice) was not re-run or extended with new
  `SubmitWorkerSecurityEvents`-specific live-Docker-Compose scenarios;
  that coverage today exists only at the `ctest`
  (`coordinator_service_test.cpp`) integration-test level against a
  directly-constructed `CoordinatorServiceImpl`, not a live Compose
  stack with real mTLS between three real processes.
* **No browser end-to-end automation** (no Playwright/Cypress exists in
  this repository). The Web Security Center is verified via component/
  API-layer tests (Vitest + Testing Library), a real `npm run build`,
  and `npm run typecheck`/`npm run lint` -- never a scripted or manual
  click-through of the running dev server in this pass. Explicitly
  BLOCKED, not claimed as passing.
* **Live Docker Compose validation of the new
  `SubmitWorkerSecurityEvents` RPC end-to-end (real Python worker
  process, over real mTLS, submitting to a real running coordinator)
  was not performed this slice.** Validation instead used: (a) a live
  Docker gRPC build directly exercising `CoordinatorServiceImpl` via
  `ctest`, and (b) Python-side unit tests of the queue/signing logic in
  isolation. The two halves are proven correct independently and share
  the identical canonical-JSON/hash-input logic (cross-language golden
  fixture, byte-for-byte identical in both the Python test and the C++
  test), but an actual live process-to-process RPC call was not
  exercised.
* **`docker-compose.security.yml`'s existing mTLS override was not
  extended to the `python-worker` service** (a pre-existing, disclosed
  gap from the prior slice -- see "Security Events, Metrics, and
  Durable Audit Journal slice" above), so a live Docker validation of
  `SubmitWorkerSecurityEvents` over real mTLS was not newly enabled by
  this slice either.

## Secure Hybrid Differential Privacy Runtime slice

See [secure-hybrid-dp-runtime-audit.md](secure-hybrid-dp-runtime-audit.md)
for the pre-implementation audit and scope statement,
[secure-hybrid-dp-semantics.md](secure-hybrid-dp-semantics.md) for the
full mechanism specification (execution order, dual-budget/publication-
boundary semantics, the "two epsilons, never combined" rule), and
[secure-hybrid-dp-runtime-report.md](secure-hybrid-dp-runtime-report.md)
for the full completion report. This slice composes the two already-
built, already-live-validated mechanisms — sample-level DP (Opacus,
worker-side) and secure user-level DP (worker-side clipping + central
aggregate noise, from the immediately prior slice) — under secure
aggregation for the first time. `PrivacyMode::kHybridDp` previously
existed only in its cleartext form (`docs/hybrid-dp.md`, untouched by
this slice); `AcquireTask` unconditionally rejected `kHybridDp` under
secure aggregation before this slice.

**Implemented and live-validated**: an `AcquireTask` hybrid
compatibility gate reusing the existing sample-level and user-level
validation ladders in sequence, `SECURE_HYBRID_DP_*`-prefixed rejection
reasons; no new combined-configuration message (both sub-configurations
were already independently, cryptographically bound into the one
signed task before this slice — see the audit doc); a worker-side
hybrid execution order (sample-level private training → whole-user
delta → user-level clipping → encode → mask → submit both signed
records) reusing every existing pure-math/training function unchanged;
`SubmitMaskedClientUpdateRequest` extended with
`sample_privacy_record_envelope`/`sample_privacy_record_payload`
fields, verified by `SubmitMaskedClientUpdate` via the cleartext path's
exact signature/structural-binding/replay/monotonicity/budget-
contradiction logic, staged and committed only after
`submit_masked_update` itself durably succeeds; three new rejection
reasons (`SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_MISSING`,
`_SAMPLE_RECORD_INVALID_SIGNATURE`, `_SAMPLE_RECORD_BINDING_MISMATCH`);
a bounded, representative `SECURE_HYBRID_DP_*` security-event
vocabulary (8 types); `GetSecureUserLevelPrivacyHealth`/
`GetSecureUserLevelPrivacyBudget` fixed to recognize `kHybridDp` runs
instead of 412-rejecting them, closing an observability gap for the
already-built read RPCs without any new Go/web code; a 3-scenario
`secure-aggregation-hybrid-dp` runtime-validation harness group; a
cross-language golden fixture for the hybrid-mode (`privacy_mode=4`)
sample-record canonical hash, independently verified byte-for-byte and
SHA-256-digest-for-digest between Python and C++. A real three-worker
Docker Compose stack
(`infra/compose/docker-compose.secure-hybrid-dp.yml`,
`scripts/validate_secure_hybrid_dp.py`) drove a complete single-round
FedAvg run with a deliberately tight sample-level `max_grad_norm=0.5`
and a deliberately tiny user-level `initial_clipping_bound=0.01`
through the full hybrid path: both real clipping mechanisms genuinely
engaged, in the correct order, on real training output; all three
workers' dual signed records were accepted; `model_version` genuinely
advanced `v0 → v1`; real positive `epsilon_spent=5.303` was reported
for the user-level layer. **38/38 automated checks passed.**

**Four real bugs found and fixed by this slice's own testing** (see the
report doc for full detail): (1) `apply_secure_aggregate_and_advance`'s
accountant-commit gate checked `privacy_mode == kUserLevelDp` only,
silently skipping every hybrid round's user-level accountant/ledger
commit; (2) `SubmitMaskedClientUpdate`'s finalize block computed
central noise under the identical `kUserLevelDp`-only condition,
meaning hybrid rounds would have finalized with zero central
noise — a real, silent user-level privacy degradation — while still
reporting `HYBRID_DP` as active; both found by direct code re-reading
while wiring the finalize path, both proven fixed by a dedicated new
C++ test block; (3) a `UnicodeDecodeError` crashing the live validation
script's `docker compose build` output capture under Windows' default
`cp1252` codepage, fixed by explicit UTF-8 decoding; (4) a wrong
test assertion in the live validation script itself (checking the
coordinator's stdout log for an event that, like its
`SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED` sibling, is only ever
written to the durable security-event journal) — found live (38/39,
1 failed) and fixed by removing the incorrect assertion, not by adding
a spurious stdout log call purely to satisfy a wrong test.

**Explicitly not built this slice** (bounded/deferred, per the audit
doc's own scope statement, not oversights): new Prometheus metrics
specific to hybrid; Go read-only hybrid-specific API routes and a
dedicated Web hybrid observability page (the existing
`/security/secure-aggregation/privacy` page and its Go API already
correctly report the user-level layer for any run including a hybrid
one, now that the two health/budget RPC fixes above are in place);
performance benchmarking; new CI *job* structure (new tests land in the
existing broad `cpp-grpc`/`python`/`go`/`web` jobs); the formal 8-state
`HYBRID_*` worker state-machine enum (the real state transitions
already exist as ordinary Python control flow with real exception
handling — a parallel, unused enum was judged documentation dressed as
code; the states are documented in prose in the semantics doc
instead); the literal 24-scenario/71-item live-validation enumeration
(addressed at representative depth instead — 3 harness scenarios plus
the 38-check live Docker run). Secure adaptive clipping, secure
aggregation of clipping indicators, variable user weights,
sample-count-weighted hybrid privacy, a single combined epsilon, formal
cross-unit privacy composition, cryptographic verification of Opacus
execution or whole-update clipping, threshold secret sharing, dropout
recovery, and partial-cohort finalization remain entirely out of
scope, unchanged from every prior secure-aggregation slice.
