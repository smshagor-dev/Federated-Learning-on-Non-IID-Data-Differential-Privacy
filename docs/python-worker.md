# Python Worker

`python/src/fl_platform/worker/` is a real PyTorch federated worker that
reuses (not reimplements) the legacy prototype's proven training code —
`federated.client.Client`, wrapped by `task_runner.py`'s
`run_local_training()`.

## Layout

* `coordinator_client.py` — the `CoordinatorClient` Protocol, plus two
  implementations:
  * `CliBridgeCoordinatorClient` — real and fully exercised, shelling out
    to `fl_coordinator_cli` per call. This is what the cross-language
    integration tests use.
  * `GrpcCoordinatorClient` — real gRPC client code. As of the Privacy
    Engineering phase, every `CoordinatorClient` method is implemented
    for real (`register_worker`/`acquire_task`/`submit_result`/
    `create_run`/etc., not just `health()`) — see
    [create-run-wire-mapping.md](create-run-wire-mapping.md). Validated
    with a live two-round FedAvg run through Docker Compose.
* `task_runner.py` — `run_local_training()`, and `BridgeCompatibleModel`,
  a custom `nn.Module` holding one flat 1-D `weight` parameter (reshaped
  internally for the linear op) to match the CLI bridge's single-tensor
  manifest limit.
* `dataset_loader.py` — `SyntheticImageDataset`, `PartitionManifest`, and
  a custom FNV-1a `_stable_hash()` (Python's built-in `hash()` on strings
  is salted per-process, which would silently break reproducible
  partition assignment across worker processes).
* `service.py` — `WorkerService`: register → heartbeat → acquire task →
  train → submit → repeat, with retry/backoff on submission and graceful
  handling of coordinator-unavailable, cancellation, and shutdown
  signals.
* `__main__.py` — the Docker container's actual entrypoint
  (`python -m fl_platform.worker`), added this phase. See below.
* `configuration.py` — `WorkerConfig`: CLI args override environment
  variables (`FL_WORKER_*`) override a TOML file override defaults.

## What the Docker worker container actually does

`infra/docker/python-worker.Dockerfile`'s `CMD` runs
`python -m fl_platform.worker`, which loads `WorkerConfig`. When
`FL_WORKER_RUN_ID` is set, it runs the real `WorkerService` loop
(register → acquire task → train → submit → repeat) against the live
gRPC coordinator — the same loop already validated against the
CLI-bridge transport by
`tests/baseline/test_coordinator_worker_integration.py`. With no
`run_id` configured it falls back to polling
`GrpcCoordinatorClient.health()` in a loop (connectivity proof only, no
training) — the previous behavior, still useful when no run exists yet.

The training path was validated end-to-end through Docker Compose: a
run created via the Go API (`fedavg`, 2 clients, 2 rounds) was picked up
by a `python-worker` container, which registered, acquired each client's
task, trained for real with PyTorch, and submitted real delta tensors;
the C++ coordinator aggregated them, advanced `model_version` v0→v1→v2,
and reached `RUN_COMPLETED` — confirmed via `GET
/api/v1/coordinator/runs/{runId}`. See
[create-run-wire-mapping.md](create-run-wire-mapping.md) for what was
fixed to make this possible (the wire-mapping gap and the tensor
transport gap), and
[known-limitations.md](known-limitations.md) for a caveat found during
this validation: the SSE event-stream endpoint did not reliably relay
every event through to `RUN_COMPLETED`, even though the coordinator's
own event log was complete and correct.

Note also that the `python-worker.Dockerfile` image needed two additions
during this validation: the top-level `federated/` package (which
`task_runner.py` depends on for its real FedAvg/FedProx/SCAFFOLD training
code, and which was previously only ever available via the repo root
being on `sys.path` in local/test runs, never copied into the container)
and `scipy` (a transitive dependency of `federated/dp_accountant.py`).
Neither had been needed before because the container only ran the
`health()`-only loop.

## Testing

* `python -m pytest` (58 tests, including the worker unit tests and the
  9 cross-language integration tests) — real, not mocked at the training
  layer; `task_runner.py`'s tests train an actual small model.
* `ruff check .` / `ruff format --check .` — clean.
* `mypy --exclude 'generated' --follow-imports=silent python/src` — clean
  (43 source files; the CLI-flag form is required — see
  [coordinator-runtime-validation.md](coordinator-runtime-validation.md) for why the
  equivalent `pyproject.toml` config didn't work).
