# Coordinator Runtime

## Two front ends, one domain layer

`cpp/coordinator/include/fl_coordinator/run_manager.hpp` defines
`RunManager` (owns every run, the worker registry, and the event bus) and
`RunInstance` (one run's full state machine — lifecycle, round lifecycle,
task dispatch, checkpointing). Neither type knows anything about gRPC or
processes; two separate front ends drive them:

1. **`fl_coordinator_grpc_server`** (`cpp/coordinator/main.cpp`,
   `coordinator_service.cpp`) — a real, long-lived gRPC server
   implementing all 13 `CoordinatorService` RPCs. Only configured when
   CMake finds Protobuf *and* gRPC (`cpp/CMakeLists.txt`), which is not
   the case on this Windows/MSVC development machine. As of this
   milestone it has been built and run for real — see
   [docker-runtime.md](docker-runtime.md) — using
   `infra/docker/cpp-coordinator.Dockerfile`'s Ubuntu base (apt has
   `libgrpc++-dev`/`protobuf-compiler-grpc`; MSVC has neither locally).

2. **`fl_coordinator_cli`** (`cpp/coordinator/tools/coordinator_cli.cpp`)
   — a process-per-call CLI, extending Milestone 2's `aggregate_cli.cpp`
   precedent. Each invocation constructs a fresh `RunManager`, restores
   state from checkpoint files on disk, performs one action
   (create-run/start-run/acquire-task/submit-result/...), and exits. This
   is what the Milestone 3 cross-language integration tests
   (`tests/baseline/test_coordinator_worker_integration.py`) actually
   drive, and what runs natively on this development machine.

## Why the CLI bridge exists

Real, production-shaped gRPC code was written either way (the server
adapter is not a stub — see `coordinator_service.cpp`). The CLI bridge
exists because this repository must be developed and tested on a machine
without a local gRPC C++ toolchain. State continuity across the CLI
bridge's separate process invocations is what most of the coordinator's
internal complexity is actually for:

* `RunInstance::active_leases_` (keyed by `client_id`, not by the
  dispatcher-local `task_id`/`lease_id`, which reset to 0 in every fresh
  process) is the authoritative, checkpointed source of truth for "which
  client currently holds a lease" — see the task-ID-collision bug
  described in [task-leasing.md](task-leasing.md).
* `save_checkpoint()` is called from inside `transition()` itself (every
  lifecycle state change), not just at round boundaries, so that
  start/pause/resume/cancel survive a process boundary.

## Recovery

See [coordinator-recovery.md](coordinator-recovery.md).

## Security posture (local-development grade)

* gRPC message size caps: `SetMaxReceiveMessageSize`/`SetMaxSendMessageSize`
  at 64 MiB (`main.cpp`).
* Transport: `grpc::InsecureServerCredentials()` — no TLS. `GrpcClient`
  (Go) and `GrpcCoordinatorClient` (Python) both have an `insecure` field
  as the named configuration hook; neither implements certificate
  loading. Not appropriate for anything beyond local development or a
  network-isolated Compose stack.
* No per-worker authentication token and no request-rate limiting beyond
  the message-size caps.
* `RejectedError`/`RunManagerError` messages are returned verbatim to
  callers (useful for debugging a local dev stack; would need review
  before ever being exposed to an untrusted caller).
