# Algorithm Expansion Phase Validation Log

Command-by-command record of what was actually run and what it actually
returned. No number in this document is invented — anything not
directly observed is stated as such.

## C++

```bash
cmake -S cpp -B build/cpp-debug
cmake --build build/cpp-debug
ctest --test-dir build/cpp-debug
```

Result: **8/8 test suites pass**, including the two added this
phase (`aggregation_manifest_test`, `personalization_summary_test`).
Also verified via `docker compose build coordinator` (real gRPC
compilation on Ubuntu/apt, the only environment with a working C++ gRPC
toolchain available — see [docker-runtime.md](docker-runtime.md)).

## Python

```bash
python -m pytest -q
```

Result: **71/71 tests pass**, run repeatedly throughout this phase
(after every algorithm implementation, after every ruff pass, after
protobuf stub regeneration, after every Go/web change that could
plausibly have side effects — none did). Includes:

* 15 new unit tests (`FedSamTests`, `DittoTests`, `PerFedAvgTests`,
  `ModelRegistryTests`, `DatasetRegistryTests`, `PersonalizationMetricsTests`
  in `test_algorithm_expansion_foundations.py`).
* 4 new cross-language integration tests
  (`test_algorithm_expansion_integration.py`): FedSAM two rounds/four
  clients, Ditto two rounds with personalized-checkpoint persistence
  across a simulated worker restart, Per-FedAvg two rounds/four clients,
  local-head tensor rejected by the coordinator's aggregation manifest.

`ruff check .` and `ruff format --check .`: **all checks passed**, zero
errors, at the end of this phase's work.

## Go

```bash
go build ./...
go vet ./...
go test ./...
```

Result: **clean build, clean vet, all tests pass** — 60+ tests across
`internal/algorithms` (12), `internal/application` (fairness: 9, model
service: 6, dataset service: 8, experiment algorithm-config validation: 4,
plus all pre-existing Foundation-through-Coordinator-Runtime tests), `internal/models` (4),
`internal/datasets` (4), `internal/transport/httpapi` (personalization/
fairness endpoints: 5, registry endpoints: 6, plus all pre-existing
tests), `internal/coordinator` (pre-existing, unaffected).

**Protobuf stubs had to be regenerated** for both Go and Python before
any of this compiled against the new `AggregationManifest`/
`PersonalizationMetricRecord`/`GetPersonalizationSummary` messages —
`protoc` is not installed on this machine, so both were regenerated via
a throwaway Docker container (`golang:1.25` for Go,
`python:3.12-slim` for Python), writing output back into the
bind-mounted, gitignored `go/generated`/`python/src/fl_platform/generated`
directories. `go build ./...` and `python -m pytest` both stayed green
immediately after.

Also verified via `docker build -f infra/docker/go-api.Dockerfile .`
(real compilation in the actual deployment container image).

## Web

```bash
npm run typecheck   # tsc --noEmit
npm run lint        # eslint
npm run test        # vitest
npm run build       # next build
```

Result: **all four clean**. 21 vitest tests pass (13 new the Algorithm Expansion phase API
helper tests + 8 pre-existing). Production build succeeds for all 8
routes including the 3 new pages (`/models`, `/datasets`, `/compare`).

**Real contract bug caught and fixed during this work**: the experiment
builder was already sending `config.algorithm = {name, ...fields}` (a
nested object) — the Go-side algorithm-config validation had initially
been written assuming a flat `algorithm`/`algorithm_config` shape, which
would have silently never fired against the real frontend payload. Fixed
by matching the existing, already-shipped frontend contract rather than
inventing a second one. See `go/internal/application/services.go`'s
`validateExperimentAlgorithmConfig`.

## Benchmarking

```bash
python scripts/benchmark_algorithms.py
```

Real, locally-measured wall-clock numbers for FedAvg/FedSAM/Ditto/
Per-FedAvg at two model sizes — see [benchmarking.md](benchmarking.md)'s
the Algorithm Expansion phase section for the full table and interpretation. Headline:
FedSAM measures ~1.86× FedAvg's per-batch cost at `GroupNormCNN` scale
(two forward/backward passes vs. one) — the expected result, not a
regression.

## Docker runtime

See [docker-runtime.md](docker-runtime.md)'s the Algorithm Expansion phase section for the
full log: all four project-owned images (`coordinator`, `api`, `web`,
`python-worker`) build clean; the full stack (`postgres`, `redis`,
`coordinator`, `api`, `web`, 2× `python-worker`, `prometheus`) starts
healthy; every new Go API endpoint (`/algorithms`, `/models`,
`/datasets`, `/coordinator/runs/{id}/{personalization,fairness,
algorithm-summary}`) verified against the *live* stack with real HTTP
calls (not mocked); the new `GetPersonalizationSummary` gRPC RPC
confirmed reaching the live C++ coordinator (via
`fl_coordinator_rpc_total{method="GetPersonalizationSummary"}` in
`/metrics`); Prometheus's `go-api` scrape target confirmed `up`; clean
`docker compose down -v` with zero containers left afterward.

**Real environment issue found and worked around**: host port 8080 is
already bound by an unrelated Apache/XAMPP instance on this machine —
validation was performed via a `curlimages/curl` container attached
directly to the compose network instead of the host-published port. Not
a regression (same class of pre-existing issue as the documented
grafana/port-3001 conflict).

**One check not performed, and why**: a full live-gRPC distributed
training round (to verify personalized-checkpoint persistence across a
*worker container* restart specifically) was not possible — `CreateRun`'s
gRPC wire mapping doesn't yet populate `client_ids`/a real
`ModelManifest` (a pre-existing the Coordinator Runtime phase gap, not introduced by
the Algorithm Expansion phase). The underlying persistence property was verified instead
via the C++ unit test (fresh `RunManager`/`RunInstance`) and the Python
cross-language integration test (simulated worker restart via the
CLI-bridge) — both real, both passing. See
[docker-runtime.md](docker-runtime.md) for the full reasoning.

## Security audit

See [algorithm-expansion-security-audit.md](algorithm-expansion-security-audit.md) for
the full pass (path traversal, unsafe deserialization, tamper detection,
RBAC, sensitive-data exposure, injection, audit trail). No new
vulnerability class introduced; one new risk surface (personalized
models may memorize client-specific data) documented explicitly in
[known-limitations.md](known-limitations.md).

## Summary

| Layer | Command | Result |
|---|---|---|
| C++ | `ctest` | 8/8 suites pass |
| C++ (Docker) | `docker compose build coordinator` | real gRPC compile succeeds |
| Python | `pytest -q` | 71/71 pass |
| Python | `ruff check . && ruff format --check .` | clean |
| Go | `go build ./... && go vet ./... && go test ./...` | clean, all pass |
| Go (Docker) | `docker build -f infra/docker/go-api.Dockerfile .` | real compile succeeds |
| Web | `typecheck && lint && test && build` | all four clean |
| Docker stack | `docker compose up -d` (7 services) | all healthy, new endpoints verified live |
| Docker teardown | `docker compose down -v` | clean, 0 containers left |
| Benchmarks | `scripts/benchmark_algorithms.py` | real numbers captured, documented |
