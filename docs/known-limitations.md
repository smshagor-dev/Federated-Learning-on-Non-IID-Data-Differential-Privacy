# Known Limitations

Consolidated list. Anything not mentioned here as a gap should be assumed
implemented and tested as described in the other `docs/` files — this
document exists specifically to prevent scaffold code from being read as
production-ready.

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
* **The `web` Docker image does not build in this environment.** Next.js
  static export times out repeatedly (60s, then 180s after raising
  `staticPageGenerationTimeout`) inside this machine's Docker Desktop VM,
  which was already running several long-running containers from other,
  unrelated projects and is resource-constrained as a result — the
  identical `npm run build` completes natively in ~13 seconds. `mlflow`,
  `api`, and `python-worker` images all build successfully, and
  `docker compose up` was run for real (postgres/redis/minio/mlflow/api/
  prometheus/otel-collector all reached a healthy/running state, verified
  via `curl` and compose healthchecks, then torn down with
  `down -v`). `grafana` did not start due to a host port-3001 conflict with
  unrelated software on this machine. See
  [milestone-1-validation.md](milestone-1-validation.md) for the full
  detail.

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
aggregation, the production Go database layer (PostgreSQL/Redis/MinIO
integration), and live dashboard↔backend integration are all still
scaffold-only, unchanged from before this pass, per the explicit
instruction not to expand them beyond compilation/contract compatibility.
