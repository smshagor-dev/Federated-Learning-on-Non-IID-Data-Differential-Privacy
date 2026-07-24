# Known Limitations

Consolidated list. Anything not mentioned here as a gap should be assumed
implemented and tested as described in the other `docs/` files — this
document exists specifically to prevent scaffold code from being read as
production-ready.

## Milestone 3 (coordinator runtime / gRPC / Docker Compose)

* **The C++ gRPC coordinator server (`fl_coordinator_grpc_server`) is real
  and, as of this milestone, has actually been compiled and run** — see
  [docker-runtime.md](docker-runtime.md) and
  [coordinator-runtime.md](coordinator-runtime.md). It still cannot be
  built on this Windows/MSVC development machine (no local gRPC C++
  install); `infra/docker/cpp-coordinator.Dockerfile` builds it for real
  on Ubuntu via apt, and that image was run, exercised end-to-end over
  HTTP→Go→gRPC→C++, and torn down as part of this milestone's validation.
* **Python `GrpcCoordinatorClient` implements only `Health()`.**
  `register_worker`, `acquire_task`, `submit_result`, and `heartbeat`
  remain unimplemented by design (see
  `python/src/fl_platform/worker/coordinator_client.py`'s module
  docstring) — carrying unverified request-building code for RPCs with no
  real server to validate the mapping against was judged worse than
  leaving them explicitly deferred. `python -m fl_platform.worker` (the
  Docker worker entrypoint) only performs a real, repeated `Health()`
  poll against the coordinator; it does not execute federated training
  rounds. See [python-worker.md](python-worker.md).
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
* **`grafana` does not start via `docker compose up` on this machine** —
  host port 3001 is held by an unrelated process outside Docker (`netstat`
  confirms a non-Docker PID). Every other service in `docker-compose.yml`,
  including the two new Milestone 3 services (`coordinator`,
  `python-worker`), started and reached a healthy/running state in the
  same run. Not a regression from this milestone; carried over from
  Milestone 1's identical note.
* **`mlflow`'s Prometheus scrape target is unhealthy (404 on `/metrics`)**
  — mlflow does not expose a Prometheus endpoint by default. Pre-existing
  from Milestone 1's `infra/prometheus/prometheus.yml`, unrelated to this
  milestone's work; the *other* previously-broken scrape target
  (`go-api`, which had no `/metrics` route registered at all before this
  milestone) is now fixed and scraping successfully — see
  [milestone-3-validation.md](milestone-3-validation.md).
* **Coordinator security is local-development-grade.** `insecure` gRPC
  credentials, no TLS/mTLS, no per-worker auth token, no request-rate
  limiting beyond gRPC's own message-size caps. TLS/mTLS has a named
  configuration hook (`GrpcClient`'s `Insecure` field;
  `grpc::InsecureServerCredentials()` in `main.cpp`) but no actual
  certificate handling. See [coordinator-runtime.md](coordinator-runtime.md).
* **No secure aggregation, no per-round differential privacy application,
  no asynchronous/semi-synchronous round execution** — explicitly out of
  scope for this milestone by instruction (see the "explicitly out of
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
* **RESOLVED in Milestone 3: the `web` Docker image now builds
  (~17s).** The Milestone 1 note below described the symptom
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
  not a full model); nothing in this milestone owns a persistent global
  model snapshot end-to-end, so introducing the type now would have no
  real behavior behind it. See
  [cpp-aggregation-architecture.md](cpp-aggregation-architecture.md).
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

FedSAM, Ditto, Per-FedAvg, Opacus/sample-level DP training, secure
aggregation cryptography, Ray/Flower execution, asynchronous/buffered
aggregation, and the production Go database layer (PostgreSQL/Redis/MinIO
integration for the project/experiment/run bookkeeping repositories,
which remain file/in-memory-backed) are all still scaffold-only, per the
explicit instruction not to expand them beyond compilation/contract
compatibility. Live dashboard↔backend integration is now partial: the Go
API's own project/experiment/run/audit data and the new coordinator-run
endpoints are both real and live (see
[go-coordinator-integration.md](go-coordinator-integration.md)); training
metrics like accuracy/loss remain out of scope since no training
algorithm work happened this milestone (see
[milestone-3-report.md](milestone-3-report.md)'s recommended Milestone 4
scope).
