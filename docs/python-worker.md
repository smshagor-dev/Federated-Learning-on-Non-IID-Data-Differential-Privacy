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
  * `GrpcCoordinatorClient` — real gRPC client code, but **only
    `health()` is implemented**. `register_worker`/`acquire_task`/
    `submit_result`/`heartbeat` are deliberately not written yet: the
    module docstring explains this is to avoid carrying unverified
    request-building code for RPCs with no real server available to
    validate the mapping against, mirroring the pattern already accepted
    for `docs/known-limitations.md` scaffolds in earlier milestones. Now
    that a real gRPC coordinator server has actually been run (see
    [docker-runtime.md](docker-runtime.md)), completing these is the
    natural next step for a future milestone — see
    [milestone-3-report.md](milestone-3-report.md).
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
  (`python -m fl_platform.worker`), added this milestone. See below.
* `configuration.py` — `WorkerConfig`: CLI args override environment
  variables (`FL_WORKER_*`) override a TOML file override defaults.

## What the Docker worker container actually does

`infra/docker/python-worker.Dockerfile`'s `CMD` runs
`python -m fl_platform.worker`, which loads `WorkerConfig` and polls
`GrpcCoordinatorClient.health()` in a loop, logging
`worker_id`/`status`/`attempt` each time. This is a real, repeated,
successful gRPC call to the live coordinator container — verified over a
sustained 24+ minute, 145-attempt run in this milestone's Docker Compose
validation (see [milestone-3-validation.md](milestone-3-validation.md))
— but it is connectivity proof, not federated training. Full round
execution needs the deferred `GrpcCoordinatorClient` methods above.

## Testing

* `python -m pytest` (58 tests, including the worker unit tests and the
  9 cross-language integration tests) — real, not mocked at the training
  layer; `task_runner.py`'s tests train an actual small model.
* `ruff check .` / `ruff format --check .` — clean.
* `mypy --exclude 'generated' --follow-imports=silent python/src` — clean
  (43 source files; the CLI-flag form is required — see
  [milestone-3-validation.md](milestone-3-validation.md) for why the
  equivalent `pyproject.toml` config didn't work).
