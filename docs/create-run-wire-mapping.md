# CreateRun Wire Mapping

## Problem

`RunConfig` (the C++ coordinator's domain type, `cpp/coordinator/include/fl_coordinator/run_manager.hpp`)
has always carried every field needed for a real distributed training run:
`client_ids`, `local_epochs`, `batch_size`, `learning_rate`, `momentum`,
`weight_decay`, `fedprox_mu`, `task_lease_seconds`, `max_task_retries`,
`manifest`, `aggregation_manifest`. The CLI-bridge transport
(`coordinator_cli.cpp`'s `parse_run_config`) already mapped all of them
from its text-based request format.

The gRPC transport did not. `coordinator_service.cpp`'s
`config_from_request` only ever mapped a subset of `CreateRunRequest`
(`run_id`, `algorithm`, `weighting`, optimizer scalars, client/round
counts) — `client_ids` was never read at all, so `config.client_ids`
stayed empty for every run created over gRPC. `RunInstance::acquire_task`
selects a client from `RunConfig.client_ids`; with that list empty, no
worker could ever be assigned a real task through the live gRPC path,
regardless of how many workers registered.

Separately, `SubmitClientResult` never decoded `ClientResult.tensor_manifest`
into `submission.update.delta` at all — a client's submitted training
delta was silently discarded no matter what a worker sent, and
`config_from_request` never populated `config.manifest` (the model
manifest defining the global model's tensors) either, since
`CreateRunRequest` had no field to carry one.

Both gaps were silent: `CreateRun` returned success, `SubmitClientResult`
returned `accepted`, and nothing in existing tests (which exercise the
CLI-bridge transport, not the gRPC one) caught it.

## Fix

### CreateRunRequest fields (proto/coordinator/coordinator.proto)

`CreateRunRequest` gained ten new fields (10–20), all additive —
existing field numbers 1–9 are unchanged:

| Field | Purpose |
|---|---|
| `client_ids` | The client pool `AcquireTask` selects from. |
| `local_epochs`, `batch_size`, `learning_rate`, `momentum`, `weight_decay`, `fedprox_mu` | Per-round training hyperparameters, threaded into every `ClientTaskDescriptor`. |
| `task_lease_seconds`, `max_task_retries` | Task-dispatch tuning, previously only settable via the CLI bridge. |
| `model_manifest` | The initial global model's tensor manifest plus its `AggregationManifest` (shared/personalized/frozen parameter names — see [aggregation-manifests.md](aggregation-manifests.md)). |
| `request_id` | Idempotency key for retried `CreateRun` calls. Not yet enforced by the coordinator — see "Known non-goals" below. |

`coordinator_service.cpp`'s `config_from_request` now maps all of these
into `RunConfig`, and validates before doing so: empty `run_id`, zero
`total_clients`, and `target_clients_per_round` outside `[1, total_clients]`
are all rejected with `std::invalid_argument` (mapped to gRPC
`FAILED_PRECONDITION`). `weighting_from_wire` was also tightened to
reject unrecognized weighting strategies instead of silently defaulting
to uniform, matching `algorithm_from_wire`'s existing strict behavior.

### Tensor transport (proto/worker/worker.proto)

The wire contracts had no field anywhere for actual tensor *values* —
`TensorManifest` carried only `name`/`shape`/`dtype`/`checksum`, and
`fl.common.v1.ArtifactReference` is a bare URI/checksum pointer with no
implemented upload/download client on either side. This meant real
model weights and client deltas had no way to cross the live gRPC wire
at all, independent of the `CreateRun` gap above.

`TensorManifest` gained one additive field:

```proto
message TensorManifest {
  string name = 1;
  repeated uint64 shape = 2;
  string dtype = 3;
  uint64 byte_length = 4;
  string checksum = 5;
  repeated double values = 6;  // NEW
}
```

This was chosen over implementing a real MinIO-backed artifact-store
upload/download path (matching the existing but unused
`ArtifactReference` design more faithfully) because the coordinator does
not currently implement any artifact-store client in any language, and
building one is a substantially larger, separate piece of work. Inlining
values directly on the message unblocks real tensor transport with a
single additive field. Revisiting artifact-store transport (e.g. for
large models where inlining every value is impractical) is out of scope
here.

`coordinator_service.cpp::SubmitClientResult` now decodes
`result.tensor_manifest()`, `client_control_variate_delta()`, and
`refreshed_client_control_variate()` into real `TensorCollection`s via
`tensor_collection_from_wire`/`tensor_buffer_from_wire`, instead of
leaving `submission.update.delta` permanently empty.

`GrpcCoordinatorClient` (`python/src/fl_platform/worker/coordinator_client.py`)
now implements every `CoordinatorClient` method for real — previously
only `health()` was implemented, so a Python worker container talking to
a live gRPC coordinator could do nothing but poll `Health()`. It builds
`ClientResult.tensor_manifest` entries from a trained delta's real
tensor values (`tensor.detach().flatten().tolist()`), mirroring
`tensor_codec.py`'s existing flatten/reshape convention used by the
CLI-bridge transport.

## Verification

- `cpp/coordinator/tests/coordinator_service_test.cpp` (gRPC-gated, built
  alongside `fl_coordinator_grpc_server`): drives `CoordinatorServiceImpl`
  through real `CreateRun` → `RegisterWorker` → `StartRun` → `AcquireTask`
  → `SubmitClientResult` with real wire messages (no mocks). Asserts a
  task is actually returned (previously impossible), that it carries the
  hyperparameters from `CreateRunRequest`, that a real delta tensor is
  accepted, and — as a positive proof that `tensor_manifest` decoding is
  real rather than the previous always-empty no-op — that a delta tensor
  the aggregation manifest marks personalized-only is *rejected* (this
  rejection path could never trigger before the fix, since the delta
  never actually reached the personalized/frozen name check no matter
  what a worker submitted). Also covers the new required-field/enum
  validation. Passing in a real Ubuntu + libgrpc++-dev + protobuf-compiler-grpc
  environment (this repo's Windows/MSVC dev machine has no local C++ gRPC
  toolchain — see [coordinator-runtime.md](coordinator-runtime.md)).
- `go/internal/coordinator/grpc_client_test.go`: substitutes a fake
  `CoordinatorServiceClient` stub and asserts `GrpcClient.CreateRun` maps
  every new `CreateRunRequest` field (client IDs, hyperparameters, model
  manifest, aggregation manifest, request ID) onto the wire message.
- `python/tests/test_grpc_coordinator_client.py`: same pattern for
  `GrpcCoordinatorClient.create_run`/`submit_result`/`acquire_task`,
  including asserting real tensor values reach `TensorManifest.values`.
  Skips (not fails) if the generated Python protobuf bindings or `grpcio`
  aren't available — the CI `python` job doesn't run `make proto` (that's
  the separate `protobuf` job); run `make proto` locally first.
- `tests/baseline/test_coordinator_worker_integration.py`: the
  pre-existing CLI-bridge integration suite (9 scenarios — FedAvg/FedProx/
  SCAFFOLD two-round runs, multiple workers, worker failure + retry,
  coordinator restart + resume, duplicate/stale-result rejection, cancel,
  pause/resume) continues to pass unchanged, confirming the gRPC-side
  fixes didn't regress the transport actually exercised end-to-end today.

## Live validation (Docker Compose)

Beyond the unit/integration tests above, a real FedAvg run was created
and driven to completion entirely through Docker Compose, with no
subprocess bridging: `postgres`, `redis`, `coordinator`
(`fl_coordinator_grpc_server`, built for real via
`infra/docker/cpp-coordinator.Dockerfile`), `api` (the Go control
plane), and `python-worker` all built and started via
`docker compose up -d --build`.

Sequence exercised, matching the required test sequence exactly:

1. Logged in via `POST /api/v1/auth/login` (researcher role) and created
   a run via `POST /api/v1/coordinator/runs` with `client_ids: [client-a,
   client-b]`, `algorithm: fedavg`, `total_clients: 2`,
   `target_clients_per_round: 2`, `max_rounds: 2`,
   `minimum_valid_results: 2`, and a `model_manifest` with one `weight`
   tensor.
2. `POST /api/v1/coordinator/runs/{runId}/start`.
3. Started a `python-worker` container with `FL_WORKER_RUN_ID` set,
   running the real `WorkerService` loop against the live gRPC
   coordinator.
4. The worker registered, acquired client-a's and client-b's tasks,
   trained for real with PyTorch, and submitted real delta tensors.
5. The coordinator accepted both results, aggregated them, advanced
   `model_version` v0→v1, checkpointed, and started round 2.
6. Round 2 repeated the same sequence, advancing to `model_version` v2
   and reaching `RUN_COMPLETED`.
7. `GET /api/v1/coordinator/runs/{runId}` confirmed
   `state: "COMPLETED", current_round: 2, model_version: "v2"`.
8. `docker compose down -v` — no containers left running.

Confirmed via the coordinator's own structured stdout log (24 events,
in order: `RUN_CREATED` → `RUN_VALIDATED` → `RUN_STARTED` →
(`ROUND_STARTED` → `COHORT_SELECTED` → `TASK_ASSIGNED` ×2 →
`CLIENT_RESULT_ACCEPTED` ×2 → `AGGREGATION_STARTED` →
`AGGREGATION_COMPLETED` → `MODEL_VERSION_UPDATED` →
`CHECKPOINT_COMPLETED`) ×2 → `RUN_COMPLETED`).

Two real bugs surfaced and were fixed during this validation, beyond the
wire-mapping and tensor-transport fixes already described above:

- The `python-worker` Docker image was missing the top-level `federated/`
  package (`task_runner.py`'s real training path depends on
  `federated.client.Client`) and `scipy` (a transitive dependency of
  `federated/dp_accountant.py`, imported by `federated/__init__.py`).
  Neither had ever been exercised in the container before, since it
  previously only ran the `Health()`-only poll loop. Both are now copied/
  installed in `infra/docker/python-worker.Dockerfile`.
- A test-authoring mistake, not a code bug: the `model_manifest`'s
  declared tensor shape must match what the real model actually
  produces. `BridgeCompatibleModel` (`task_runner.py`) with its default
  constructor arguments (`num_classes=2, in_channels=1, image_size=4`)
  produces one flat `weight` tensor of `2 * 1 * 4 * 4 = 32` elements —
  not an arbitrary size. A `CreateRun` declaring the wrong shape (`[4]`
  in an early attempt) let both individual `SubmitClientResult` calls
  through (shape isn't checked at submission time — only tensor
  *names* are, against the aggregation manifest), then crashed the
  *next* `AcquireTask` call with `FAILED_PRECONDITION: client tensor
  shape does not match manifest`, because that next call's
  `RunInstance::advance()` is what actually triggers
  `aggregate()`'s shape validation
  (`cpp/core/src/aggregation.cpp`). The crash is correct, defensive
  behavior — the fix was correcting the test's declared shape, not the
  coordinator.

One gap was found and not fixed in this pass: the SSE event-stream
endpoint (`GET /api/v1/coordinator/runs/{runId}/events`) did not reliably
relay every event through to `RUN_COMPLETED` in this test, even though
the coordinator's own event log was complete and correct — see
[known-limitations.md](known-limitations.md) for the full description.
Run completion was confirmed via `GetRun`/`GET .../runs/{runId}`
instead.

## Known non-goals

- `request_id` is carried on the wire but not yet enforced as an
  idempotency key — a retried `CreateRun` with the same `request_id`
  is not deduplicated. Documented here rather than silently implied.
- Global model weights are not sent to workers at `AcquireTask` time in
  either transport (CLI-bridge or gRPC): both `coordinator_cli.cpp`'s
  `print_task` and `CoordinatorServiceImpl::AcquireTask` omit
  `ClientTrainingTask.task.model_manifest`'s tensor values. Workers build
  a fresh local model each round (see `task_runner.py`'s
  `build_bridge_compatible_model`) rather than warm-starting from the
  coordinator's actual current global weights; only the trained delta
  matters for aggregation. This is a pre-existing, already-tested design
  (not something this fix changed) — extending it to genuine weight
  synchronization is a separate, larger change.
- Large tensors: inlining every value as `repeated double` is not
  bandwidth-efficient for large models. Fine for this project's synthetic
  test models; an artifact-store-backed path remains the documented
  future option if that becomes a real constraint.
