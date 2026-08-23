# Coordinator Runtime

## Two front ends, one domain layer

`cpp/coordinator/include/fl_coordinator/run_manager.hpp` defines
`RunManager` (owns every run, the worker registry, and the event bus) and
`RunInstance` (one run's full state machine — lifecycle, round lifecycle,
task dispatch, checkpointing). Neither type knows anything about gRPC or
processes; two separate front ends drive them:

1. **`fl_coordinator_grpc_server`** (`cpp/coordinator/main.cpp`,
   `coordinator_service.cpp`) — a real, long-lived gRPC server
   implementing the coordinator RPC surface. It is configured when CMake
   finds Protobuf and gRPC and is built in the coordinator container.

2. **`fl_coordinator_cli`** (`cpp/coordinator/tools/coordinator_cli.cpp`)
   — a process-per-call CLI. Each invocation constructs a fresh
   `RunManager`, restores state from checkpoint files on disk, performs
   one action, and exits. The same coordinator domain layer is used by
   both front ends.

## Why the CLI bridge exists

The gRPC adapter and the CLI bridge share one coordinator domain layer.
State continuity across the CLI bridge's separate process invocations is
also useful as a strict restart/recovery test for the long-lived server:

* `RunInstance::active_leases_` is checkpointed and keyed by `client_id`,
  so a process restart does not make a new dispatcher-local task ID the
  authority for an older lease.
* `save_checkpoint()` is called from `transition()` itself, so lifecycle
  actions such as start, pause, resume, and cancel survive a process
  boundary.

## Distributed round fault tolerance

Cleartext distributed rounds use `round_timeout_seconds` as an absolute
wall-clock deadline. `minimum_valid_results` is a settlement/deadline
quorum; it is **not** a fastest-client early-release target. Before the
deadline the coordinator waits for the full selected cohort unless every
remaining client has permanently exhausted its retry budget. This avoids
systematically selecting only the fastest clients when the configured
quorum is smaller than the selected cohort.

The coordinator persists a checksummed `*.round-runtime` sidecar next to
the normal coordinator checkpoint. It records the current round's
absolute start/deadline, requested minimum result count, lease-attempt
counters, restart-deferred leases, and timeout classification. Therefore:

* a coordinator restart does not extend an already-started round's
  deadline;
* a restart does not reset a client's `max_task_retries` budget;
* a lease that was still valid during restart is not duplicated, and is
  re-queued with the next attempt only after its original expiry;
* at the deadline, unresolved clients are classified as timed out;
* a partial cohort is released only when `minimum_valid_results` is met;
  otherwise the run fails without publishing a new model version.

For user-level differential privacy, deadline/retry-driven cleartext
partial cohorts use the number of **accepted updates** as the central
Gaussian-noise sensitivity denominator. The privacy accountant's sampling
probability remains the run's originally configured client-selection
probability; those two quantities must not be conflated.

Old coordinator checkpoints written before the round-runtime sidecar
existed are migrated additively on the first hardened tick. Existing
checkpointed active leases are treated as having consumed at least one
attempt, and that first hardened tick establishes the deadline for that
already-running old-format round. New rounds always persist the deadline
at dispatch time.

The long-lived coordinator watchdog is enabled when
`FL_ROUND_WATCHDOG_INTERVAL_MS` is set to an integer from `1` to `60000`.
The default Compose stack sets it to `1000` ms. This lets rounds progress,
expire leases, and enforce deadlines even when no worker is currently
polling.

This watchdog currently governs the **cleartext client-result path**.
Secure aggregation has a separate masked-update/session state machine;
dropout recovery for that protocol must be based on secure-aggregation
participation evidence, not `round_results_`, and remains a separate
hardening gate.

## Recovery

See [coordinator-recovery.md](coordinator-recovery.md).

## Security posture

Transport, worker identity, signed task/result handling, replay
protection, signing-key lifecycle, and secure-aggregation controls are
documented in the dedicated security/runtime documents under `docs/`.
Local Compose may explicitly enable development transport settings; those
settings must not be confused with the production security posture.
