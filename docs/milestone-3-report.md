# Milestone 3 Final Report

## 1. Executive summary

Milestone 3 delivers a production-shaped C++ coordinator runtime, real
gRPC contracts and clients across C++/Python/Go, a PyTorch federated
worker, Go control-plane integration, end-to-end synchronous round
execution, checkpoint/crash recovery, per-client SCAFFOLD state
persistence, real-time event streaming, cross-language integration
tests, and Docker Compose runtime validation — all against the explicit
exclusion list (no FedSAM/Ditto/Per-FedAvg production training, no
Opacus, no secure aggregation cryptography, no Ray/Flower, no
asynchronous aggregation, no production Postgres/Redis repositories, no
full live dashboard, no Kubernetes production deployment). Two real bugs
were found and fixed by actually running the full stack in Docker
Compose rather than only unit-testing each language in isolation (a
proto field-name collision that only broke C++ codegen, and a Go
gRPC-streaming poll-window bug) — see §20.

## 2. Repository audit summary (start-of-milestone state)

Milestone 1 (release-gate hardening: Go control plane with
project/experiment/run bookkeeping, auth, audit log, a demo-data-backed
web dashboard) and Milestone 2 (C++ aggregation core: FedAvg/FedProx/
FedOpt/SCAFFOLD math, checkpoint store, `aggregate_cli` process-per-call
bridge, cross-language golden parity tests) were both complete and
passing before this milestone began. `git status` at the start showed a
clean tree on `main`. Existing scaffolds explicitly marked deferred in
`docs/known-limitations.md` (coordinator gRPC service, live dashboard
integration) were the intended starting point for this milestone's
work, not evidence of prior incompleteness.

## 3. Milestone 3 architecture overview

See [milestone-3-architecture.md](milestone-3-architecture.md) for the
full component diagram. Summary: one C++ domain layer
(`RunManager`/`RunInstance`) driven by two front ends (a real gRPC
server, CI/Docker-only-buildable, and a process-per-call CLI bridge for
local development); a Go control plane translating HTTP to gRPC through
a `coordinator.Client` interface with a real and a mock implementation;
a Python PyTorch worker with a real training path and a partially
deferred gRPC client; a web dashboard with an additive coordinator
status panel.

## 4. Protobuf/gRPC contract changes

See [grpc-contracts.md](grpc-contracts.md). `CoordinatorService` (13
RPCs) added to `proto/coordinator/coordinator.proto`; additive fields
and enums to `worker.proto`/`events.proto`; `go_package` options added
repo-wide. All changes additive except one field rename
(`ClientTrainingTask.has_task` → `task_available`) forced by a C++
codegen collision discovered this milestone — see §20.
`scripts/verify_proto_contracts.py` extended to check enums as well as
field numbers.

## 5. C++ coordinator implementation

`cpp/coordinator/` (27 files): domain layer (`worker_registry`,
`task_dispatcher`, `event_bus`, `round_manager`, `scaffold_client_state`,
`run_manager`, `structured_log`), the CLI bridge
(`tools/coordinator_cli.cpp`), the gRPC service adapter
(`src/coordinator_service.cpp`, `main.cpp`), and 7 test files (5/5 CTest
suites passing in both Debug and Release). `cpp/CMakeLists.txt` gates
the real gRPC server target on `find_package(Protobuf)`/`find_package(gRPC)`
succeeding — false on this Windows/MSVC host, true (and now
verified-by-actually-building) inside
`infra/docker/cpp-coordinator.Dockerfile`.

## 6. Python worker implementation

`python/src/fl_platform/worker/` (14 files): real PyTorch training via
`task_runner.py` (reusing `federated.client.Client`), a CLI-bridge
coordinator client (real, fully exercised) and a gRPC coordinator client
(real code, `Health()` only implemented — see
[python-worker.md](python-worker.md) for the explicit reasoning), a
worker execution loop (`service.py`), configuration (CLI/env/TOML
precedence), and a new Docker entrypoint (`__main__.py`) added this
milestone. `python/src/fl_platform/rpc/` added for generated-bindings
path bootstrapping.

## 7. Go coordinator integration implementation

`go/internal/coordinator/` (7 files): `Client` interface,
`GrpcClient` (real, now verified end-to-end against a live coordinator),
`MockClient` (idempotency-accurate, used by this repo's own tests),
error/type mapping. Wired into `go/internal/application/coordinator_service.go`
(new) and `go/internal/transport/httpapi/coordinator_handlers.go` (new)
per the layering in [go-coordinator-integration.md](go-coordinator-integration.md).
19 new tests across the coordinator package and HTTP handler tests, all
passing.

## 8. Web integration implementation

`web/features/runs/coordinator-status-panel.tsx` and
`web/lib/coordinator-events.ts` (new): a live coordinator status/round
panel and a `fetch`-based SSE client (not the native `EventSource` API,
which cannot carry the required `Authorization` header), added
additively to the existing run dashboard without disturbing Milestone
1's local run-bookkeeping UI. 6 new tests in `web/tests/api.test.ts`.

## 9. SCAFFOLD client state persistence

See [scaffold-client-state.md](scaffold-client-state.md). Per-client
control variates persisted via `FilesystemClientAlgorithmStateStore`
(atomic writes, FNV-1a checksums, schema versioning — same pattern as
Milestone 2's `AggregatorCheckpointStore`), loaded/saved by
`RunInstance::scaffold_control_variates_for()` around each SCAFFOLD
client's participation.

## 10. Checkpoint and recovery behavior

See [coordinator-recovery.md](coordinator-recovery.md). Checkpoints are
written from inside `transition()` (every lifecycle change, not just
round boundaries) and cover round/model/optimizer state plus
`round_results_`/`active_leases_`/`failed_clients_`. Recovery is
create-with-original-config then `restore_from_checkpoint()`. Verified
by `recovery_test.cpp` and the `test_coordinator_restart_and_resume`
cross-language integration test (bit-identical final model vs. an
uninterrupted control run).

## 11. Event streaming implementation

See [event-streaming.md](event-streaming.md). `EventBus` (bounded,
per-run, pull-based) → `CoordinatorServiceImpl::StreamRunEvents`
(long-lived server stream) → `GrpcClient.PollEvents` (bounded poll
window, 8s, tuned this milestone after finding a real bug — §20) →
`httpapi`'s SSE handler → `web/lib/coordinator-events.ts`'s
fetch-based client with reconnect and bounded history (25 events).
Verified with real events over the live Docker stack.

## 12. Task leasing and worker lifecycle

See [task-leasing.md](task-leasing.md) and
[worker-lifecycle.md](worker-lifecycle.md). Documents the cross-process
task-ID-collision bug found and fixed during Milestone 3 domain-layer
development (checkpointed `active_leases_`, keyed by `client_id`, as the
authoritative source of truth instead of the dispatcher's own
process-local state).

## 13. Security controls

Local-development grade throughout: gRPC message-size caps (64 MiB),
insecure transport with a named TLS/mTLS configuration hook (unused),
no per-worker auth token, error messages returned verbatim. See
[coordinator-runtime.md](coordinator-runtime.md)'s security section and
[known-limitations.md](known-limitations.md). No change to Milestone 1's
web/API auth (bearer tokens, role-based authorization), which the new
coordinator routes reuse (`withAuth` middleware, same role set).

## 14. Observability

Structured per-event logging added to the C++ coordinator this
milestone (`structured_log.cpp`, called from `RunInstance::emit()`) —
key=value lines to stderr (unbuffered, unlike the previously-buffered
`std::cout`, which made a healthy container look silent under `docker
logs`) carrying `run_id`/`round_id`/`event_type`/`client_id`/
`worker_id`/`model_version`/`trace_id` where applicable. A real
Prometheus `/metrics` endpoint added to the Go API
(`go/internal/observability/telemetry.go`'s `MetricsRecorder.WritePrometheus`,
hand-rolled text-exposition format) — this fixed a previously-silent
gap: `infra/prometheus/prometheus.yml` had been configured to scrape
`api:8080/metrics` since Milestone 1 with no such route ever existing;
confirmed fixed via Prometheus's own target-health API (`go-api` now
reports `up`).

## 15. Docker Compose runtime

See [docker-runtime.md](docker-runtime.md) and
[milestone-3-validation.md](milestone-3-validation.md). `coordinator`
and `python-worker` services added for real (not scaffolds — both
Dockerfiles previously ran a placeholder `echo`/`print` command and now
run the actual gRPC server / worker entrypoint). Full stack brought up,
exercised, and torn down cleanly.

## 16. Files added and changed

34 modified files, 25 new files/directories (including 27 files under
the new `cpp/coordinator/`, 7 under `go/internal/coordinator/`, 14 under
`python/src/fl_platform/worker/`), per `git status --short`. Full list
in [milestone-3-validation.md](milestone-3-validation.md)'s companion
`git status` output; not duplicated here to avoid drift.

## 17. Tests added

C++: 7 coordinator test files (already counted in the 5/5 CTest suite
total, since `fl_coordinator_tests` bundles them). Python: 9
cross-language integration tests
(`tests/baseline/test_coordinator_worker_integration.py`), included in
the 58-test full suite. Go: 11 tests in `internal/coordinator`, 9 in
`coordinator_handlers_test.go`, 1 in `server_test.go` (metrics
endpoint) — 21 new Go tests. Web: 6 new tests in `api.test.ts`.

## 18. Exact validation commands executed (pass/fail/blocked)

See [milestone-3-validation.md](milestone-3-validation.md) for the full
table. Summary: all commands that can run on this machine passed; the
only blocked command is `go test -race` (needs cgo/a C compiler,
documented CI-only since Milestone 1).

## 19. Cross-language integration results

9/9 passing: FedAvg/FedProx/SCAFFOLD two-round runs, duplicate-result
rejection, stale-model-version rejection, worker-failure-and-retry,
pause/resume, cancel, and full crash-recovery — all via the CLI bridge
(the locally-runnable substitute for the gRPC server; see
[coordinator-runtime.md](coordinator-runtime.md)). Additionally, this
milestone performed real HTTP→Go→gRPC→C++ verification against the
*actual* gRPC server for the first time, in Docker (§20, §11) — a
stronger form of cross-language verification than the CLI-bridge tests
alone provide, though not run as an automated test (manual `curl`
sequences documented in
[milestone-3-validation.md](milestone-3-validation.md)).

## 20. Real bugs found and fixed during integration

1. **Proto field collision** (`ClientTrainingTask.has_task` vs. the
   auto-generated `has_task()` presence accessor for field `task`) —
   broke C++ compilation only; invisible to Python/Go codegen and to the
   field-number contract checker. Found by `docker compose build
   coordinator`, the first real compile of the C++ gRPC bindings. Fixed
   by renaming to `task_available`.
2. **Go event-poll blocking forever** — `PollEvents` had no internal
   deadline against a server stream that runs until the client
   disconnects; found by watching `GET .../events` hang with zero output
   through the live `api`+`coordinator` containers. Fixed with a bounded
   window; a second, subtler fix was needed because status-code-based
   "was this my own timeout" detection was racy/inconsistent — see
   [event-streaming.md](event-streaming.md) for the full account,
   including the still-open question of *why* the docker-bridge network
   path needed an 8s window when the host-published-port path did not
   (documented in [known-limitations.md](known-limitations.md) rather
   than left silently unmentioned).
3. Assorted Docker build-config gaps (`go.sum`/`go/generated` not copied
   into the API image, `golang:1.22` too old for the post-`go mod tidy`
   `go 1.25.0` floor, missing torch in the worker image) — see
   [docker-runtime.md](docker-runtime.md).

## 21. Performance measurements

Not a focus of this milestone (no training-algorithm work occurred).
Observed, not benchmarked rigorously: `docker compose build coordinator`
~17s warm / ~1min cold (apt install + full C++ rebuild); `python-worker`
health-check RPC round-trip well under the 8s `pollEventsWindow` once
warm (typically sub-100ms, per the `elapsed` measurements taken while
debugging §20's issue); web `npm run build` ~5s native / ~26s in Docker.
No systematic latency/throughput benchmarking of the coordinator's RPC
paths was performed — flagged as a Milestone 4 candidate in §24.

## 22. Existing regression status

Zero regressions. Every pre-existing C++ (5/5 suites), Python (58
tests), Go (all packages), and web (8 tests, typecheck, lint, build)
check passes at this milestone's end, matching or exceeding
pre-milestone counts.

## 23. Known limitations (summary)

Full detail in [known-limitations.md](known-limitations.md#milestone-3-coordinator-runtime--grpc--docker-compose).
Headline items: Python `GrpcCoordinatorClient` implements only
`Health()`; the coordinator↔Go event-stream poll window's root cause is
not fully pinned down (workaround verified reliable, not explained);
`grafana`/`mlflow` Prometheus targets have pre-existing, unrelated
issues (host port conflict; no `/metrics` route on mlflow); security is
local-development grade throughout.

## 24. Git working-tree summary and recommended Milestone 4 scope

**Working tree**: all changes staged as modifications/additions on
`main`, nothing committed or pushed per the standing instruction not to
commit without explicit request.

**Recommended Milestone 4 scope**, in priority order:

1. Complete `GrpcCoordinatorClient`'s deferred methods
   (`register_worker`/`acquire_task`/`submit_result`/`heartbeat`) now
   that a real gRPC server has been verified reachable from Python —
   this unlocks real federated training through the Docker worker
   container, not just health checks.
2. Root-cause the docker-bridge-network gRPC streaming latency noted in
   §20/§23, since the current 8s window is empirically-tuned, not
   understood.
3. TLS/mTLS for the coordinator↔worker and coordinator↔Go-API channels,
   plus per-worker authentication — the named configuration hooks exist
   but are unused.
4. Production PostgreSQL/Redis-backed repositories for the Go control
   plane's project/experiment/run bookkeeping (currently file/in-memory).
5. Systematic coordinator RPC performance benchmarking (latency
   distributions under concurrent workers, not just the ad hoc
   measurements in §21).
6. Live training-metric data (accuracy/loss/privacy-budget) on the web
   dashboard's `RunDashboard`, once real training runs (via item 1) exist
   to source them from — the `RunMetrics` projection added this
   milestone deliberately does not fabricate these.

This milestone's explicit exclusions (FedSAM/Ditto/Per-FedAvg,
Opacus, secure aggregation, Ray/Flower, async aggregation,
Kubernetes production deployment) remain out of scope for Milestone 4
recommendation unless the user directs otherwise.
