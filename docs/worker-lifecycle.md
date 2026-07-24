# Worker Lifecycle

## Coordinator-side: `WorkerRegistry`

`cpp/coordinator/include/fl_coordinator/worker_registry.hpp`:

* `WorkerStatus`: `kIdle`/`kBusy`/`kUnhealthy`/... (mirrors the proto
  `WorkerStatus` enum in `proto/worker/worker.proto`).
* `register_worker()` is idempotent — re-registering an already-known
  `worker_id` updates its capability/status rather than erroring.
* `set_current_task()` / `clear_current_task()` throw for an unregistered
  `worker_id` — callers (task dispatch) must register first. This is
  why every coordinator CLI/gRPC entry point auto-registers the calling
  `worker_id` before acting on it.
* `sweep_unhealthy()` marks workers that haven't heartbeated within the
  configured interval as unhealthy, without deregistering them (a
  worker that comes back is the same worker, not a new registration).

## Worker-side: `WorkerService.run()`

`python/src/fl_platform/worker/service.py` — one iteration:

1. `acquire_task` — on `CoordinatorUnavailableError`, log and retry next
   loop iteration (does not crash the worker process).
2. If no task available: for a bounded test run
   (`max_iterations` set), stop; for a real long-running worker
   (`max_iterations=None`), sleep `poll_interval_seconds` and retry.
3. Train (`run_local_training`) with a fresh `CancellationToken` per
   task. `TaskCancelled`/`TaskDeadlineExceeded`/`RuntimeError` (covers
   CUDA-unavailable/OOM/other torch failures) are all caught individually
   — one bad task increments `tasks_failed` and the loop continues; it
   does not crash the worker.
4. `_submit_with_retry` — up to `submission_retry_attempts` (default 3)
   with `submission_retry_backoff_seconds` backoff, on
   `CoordinatorUnavailableError` specifically. A `CoordinatorRejectedError`
   (e.g. stale model version) is not retried — logged and counted as
   failed, since retrying an already-rejected submission cannot succeed.

`install_signal_handlers()` wires `SIGINT`/`SIGTERM` to
`request_shutdown()`, checked at the top of every loop iteration —
a real worker process run via `python -m fl_platform.worker` (see
[python-worker.md](python-worker.md)) shuts down cleanly on
`docker compose down`, not via a forced kill.

## What the Docker worker container actually exercises

The container's health-check-only loop (`__main__.py`) exercises
registration-adjacent connectivity (`Health()`) but not the
acquire/train/submit cycle above, since `GrpcCoordinatorClient` only
implements `health()` — see [python-worker.md](python-worker.md). The
full lifecycle above is exercised by the cross-language integration
tests via the CLI bridge, not by the Docker container.
