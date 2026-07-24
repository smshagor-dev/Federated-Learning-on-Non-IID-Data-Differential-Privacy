# gRPC Contracts

## Proto files touched this milestone

* `proto/coordinator/coordinator.proto` — new `CoordinatorService` (13
  RPCs: CreateRun, StartRun, PauseRun, ResumeRun, CancelRun, GetRun,
  GetRound, GetModelManifest, RegisterWorker, Heartbeat, AcquireTask,
  ReportTaskProgress, SubmitClientResult, StreamRunEvents, Health), plus
  ~20 new message types.
* `proto/worker/worker.proto` — `WorkerStatus` enum, `WorkerCapability`,
  `RegisterWorkerRequest/Response`, `WorkerHeartbeatRequest/Response`
  added; existing `TensorManifest`/`ClientTask`/`ClientMetric`/
  `ClientResult` field numbers unchanged.
* `proto/events/events.proto` — additive fields 8–12 on
  `CoordinatorEvent` (`client_id`, `worker_id`, `model_version`, `type`,
  `metadata`), new `CoordinatorEventType` enum.
* `proto/experiment/experiment.proto`, `proto/common/artifact.proto`,
  `proto/privacy/privacy.proto`, `proto/metrics/metrics.proto` —
  `option go_package` only.

All changes are additive (new fields/messages/RPCs, or new options); no
existing field number was renumbered or removed, with one exception
discovered by actually compiling the C++ bindings for the first time
(see "A real bug this caught" below).

## Generation

`scripts/generate_protos.sh` / `.ps1` generate:

* C++ → `cpp/generated/` (message types always; gRPC service stubs only
  if `grpc_cpp_plugin` is on `PATH` — true inside
  `infra/docker/cpp-coordinator.Dockerfile`, false on this Windows
  machine).
* Python → `python/src/fl_platform/generated/` (via `grpcio-tools` if
  installed, else message types only via plain `protoc`).
* Go → `go/generated/` (via `protoc-gen-go`/`protoc-gen-go-grpc` if
  installed).

All three output directories are gitignored (`generated/` in
`.gitignore` matches at any depth) and regenerated on demand — see
`scripts/generate_protos.sh`'s header comment for the full policy.
`python/src/fl_platform/rpc.ensure_generated_on_path()` inserts the
Python output directory onto `sys.path` at runtime (protoc's Python
cross-file imports use bare top-level names).

## Contract compatibility checking

`scripts/verify_proto_contracts.py` parses the `.proto` source directly
(no `protoc` required) and asserts every expected message's field names
still map to their expected field numbers, plus (new this milestone)
that expected enums still exist with their expected members. Run via
`make proto-check`; passes on this machine without `protoc` installed.

## A real bug this caught

`ClientTrainingTask.has_task` (a `bool`, field 1) collided with the
`has_task()` presence-check accessor that protoc auto-generates for the
singular message field `task` (field 2) — a duplicate-method compile
error in C++ codegen only, invisible to Python/Go codegen and invisible
to `verify_proto_contracts.py`'s field-number check (the field number
itself was never wrong). Only surfaced once this milestone's Docker build
actually compiled the C++ bindings for the first time — see
[docker-runtime.md](docker-runtime.md). Fixed by renaming the field to
`task_available`; the one C++ call site
(`coordinator_service.cpp:229`) and `verify_proto_contracts.py`'s
expected-field-name entry were updated to match.
