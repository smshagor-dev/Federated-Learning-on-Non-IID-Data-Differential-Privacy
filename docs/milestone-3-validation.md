# Milestone 3 Validation

Every command below was actually run in this environment during this
milestone. Status reflects the final run after all fixes described in
[docker-runtime.md](docker-runtime.md) and [event-streaming.md](event-streaming.md).

## C++

| Command | Result |
|---|---|
| `cmake -S cpp -B build/cpp-debug -DCMAKE_BUILD_TYPE=Debug` | OK (gRPC not found locally → `fl_coordinator_grpc_server` skipped, as designed) |
| `cmake --build build/cpp-debug --config Debug` | OK, all targets |
| `ctest -C Debug` (in `build/cpp-debug`) | **5/5 passed**: fl_core_smoke, fl_aggregator_golden, fl_validation_tests, fl_checkpoint_tests, fl_coordinator_tests |
| `cmake -S cpp -B build/cpp-release -DCMAKE_BUILD_TYPE=Release` + build | OK |
| `ctest -C Release` | **5/5 passed** |
| `docker compose build coordinator` | OK — first-ever real build of `fl_coordinator_grpc_server` (see [grpc-contracts.md](grpc-contracts.md) for the proto bug this surfaced and fixed) |

## Python

| Command | Result |
|---|---|
| `python -m pytest -q` | **58 passed** |
| `python -m pytest tests/ -q` | **30 passed** (baseline + cross-language integration subset) |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 55 files already formatted |
| `mypy --exclude 'generated' --follow-imports=silent python/src` | Success: no issues found in 44 source files |
| `scripts/verify_proto_contracts.py` | protobuf contract compatibility checks passed |

## Go

| Command | Result |
|---|---|
| `gofmt -l .` | clean |
| `go vet ./...` | clean |
| `go build ./...` | OK |
| `go test ./...` | **all packages ok** — `internal/coordinator` (11 tests), `internal/transport/httpapi` (24 tests incl. 8 new coordinator handler tests + 1 metrics test), `internal/application`, `internal/bootstrap`, `internal/observability` |
| `go test -race ./...` | Not run locally — requires cgo, no C compiler on this Windows machine (documented Milestone 1/2 precedent, CI-only) |

## Web

| Command | Result |
|---|---|
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `npm run test` | **8 passed** (2 test files) |
| `npm run build` | OK, ~5s, all 6 routes (2 static, 4 dynamic) |

## Docker Compose

| Command | Result |
|---|---|
| `docker compose config --quiet` | valid |
| `docker compose build coordinator` | OK (~17s after the proto fix) |
| `docker compose build python-worker` | OK |
| `docker compose build api` | OK |
| `docker compose build web` | OK (~26s) |
| `docker compose up -d` (all 10 services) | 9/10 healthy/running; `grafana` blocked by unrelated host port-3001 conflict (verified via `netstat`, non-Docker PID) |
| `docker compose ps` | coordinator, api, postgres, redis, minio, mlflow, prometheus, otel-collector, python-worker, web all up |
| `docker compose down` / `down -v` | clean shutdown, containers and network removed, verified via `docker ps -a` |

## End-to-end coordinator functional verification (real HTTP→Go→gRPC→C++ chain)

All performed against live containers, not mocks:

1. `POST /api/v1/coordinator/runs` → 201, real `RunSnapshot` from the C++ coordinator.
2. `POST .../start`, `.../pause`, `.../resume`, `.../cancel` → each 200 with correct state transitions; double-cancel → 200 (idempotent, matching the C++ coordinator's own idempotency rules).
3. `GET .../rounds/current`, `GET .../metrics` → correct projections.
4. `GET .../events` (SSE) → real events (`RUN_CREATED`, `RUN_VALIDATED`, `RUN_STARTED`) streamed with correct `id:`/`event:`/`data:` framing and JSON field names.
5. `python-worker` container: 145 consecutive successful `Health()` RPCs to the coordinator container over 24+ minutes, confirmed via container logs.
6. `GET /metrics` on the API → real Prometheus exposition format; confirmed Prometheus's `go-api` scrape target reports `health: up` (previously unhealthy — no `/metrics` route existed before this milestone).

## Regression status

No pre-existing test in any language regressed. C++ CTest, Python
pytest, Go test, and web vitest/typecheck/lint all pass at the same or
higher count than before this milestone's changes.
