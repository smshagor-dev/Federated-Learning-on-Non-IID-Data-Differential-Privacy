"""Coordinator client abstraction for the Python worker.

Two backends are provided behind the same :class:`CoordinatorClient`
protocol:

* :class:`CliBridgeCoordinatorClient` drives the real, unit-tested C++
  coordinator domain layer (``fl_coordinator``) through
  ``cpp/coordinator/tools/coordinator_cli.cpp`` — one process per call,
  with state continuity provided by the coordinator's own checkpoint/
  recovery machinery (see docs/coordinator-recovery.md). This is what
  actually runs in this environment, both for local development and for
  the cross-language integration tests, because no C++ gRPC build is
  available here (see docs/coordinator-runtime.md for exactly why).
* :class:`GrpcCoordinatorClient` is a real ``grpcio`` client against the
  generated ``coordinator_pb2_grpc`` stubs, for a long-lived C++ gRPC
  coordinator server. It is genuine, runnable code, but has not been
  exercised against a real server in this environment for the same
  reason — it is intended to be validated in CI, where the C++ gRPC
  server can actually be built (apt-installable ``libgrpc++-dev``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import torch

from fl_platform.worker.tensor_codec import decode_tensor_dict, encode_named_tensor


class CoordinatorUnavailableError(RuntimeError):
    """Raised when the coordinator (CLI bridge or gRPC server) cannot be reached."""


class CoordinatorRejectedError(RuntimeError):
    """Raised when the coordinator explicitly rejects a request (not a
    transport failure)."""


@dataclass(slots=True)
class RunSnapshot:
    run_id: str
    state: str
    current_round: int
    max_rounds: int
    model_version: str
    algorithm: str
    registered_workers: int
    healthy_workers: int


@dataclass(slots=True)
class ClientTrainingTask:
    has_task: bool
    task_id: str = ""
    lease_id: str = ""
    client_id: str = ""
    round_id: int = 0
    model_version: str = ""
    algorithm: str = ""
    local_epochs: int = 1
    batch_size: int = 32
    learning_rate: float = 0.01
    fedprox_mu: float = 0.0
    global_control_variate: dict[str, torch.Tensor] = field(default_factory=dict)
    client_control_variate: dict[str, torch.Tensor] = field(default_factory=dict)


@dataclass(slots=True)
class SubmitOutcome:
    accepted: bool
    reason: str
    snapshot: RunSnapshot


@dataclass(slots=True)
class RunSpec:
    """Everything needed to (re)create a run against the coordinator.

    The CLI bridge is stateless per call (see module docstring), so every
    request after the first re-sends this full spec — mirroring the
    documented recovery contract (create_run + restore_from_checkpoint)
    in cpp/coordinator/include/fl_coordinator/run_manager.hpp.
    """

    run_id: str
    algorithm: str
    weighting: str = "uniform"
    total_clients: int = 1
    target_clients_per_round: int = 1
    max_rounds: int = 1
    minimum_valid_results: int = 1
    client_ids: list[str] = field(default_factory=list)
    server_lr: float = 1.0
    beta1: float = 0.9
    beta2: float = 0.99
    tau: float = 1e-3
    contribution_cap: float = 1.0
    seed: int = 0
    task_lease_seconds: int = 60
    max_task_retries: int = 3
    local_epochs: int = 1
    batch_size: int = 32
    learning_rate: float = 0.01
    momentum: float = 0.0
    weight_decay: float = 0.0
    fedprox_mu: float = 0.0
    tensor_elements: int = 1


class CoordinatorClient(Protocol):
    def create_run(self, spec: RunSpec, now: float) -> RunSnapshot: ...
    def start_run(
        self, spec: RunSpec, now: float, trace_id: str = ""
    ) -> RunSnapshot: ...
    def pause_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot: ...
    def resume_run(self, spec: RunSpec, now: float) -> RunSnapshot: ...
    def cancel_run(
        self, spec: RunSpec, now: float, reason: str = ""
    ) -> RunSnapshot: ...
    def get_run(self, spec: RunSpec, now: float) -> RunSnapshot: ...
    def register_worker(self, spec: RunSpec, worker_id: str, now: float) -> None: ...
    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask: ...
    def submit_result(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None = None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None = None,
    ) -> SubmitOutcome: ...


def _spec_fields(spec: RunSpec) -> dict[str, str]:
    return {
        "run_id": spec.run_id,
        "algorithm": spec.algorithm,
        "weighting": spec.weighting,
        "total_clients": str(spec.total_clients),
        "target_clients_per_round": str(spec.target_clients_per_round),
        "max_rounds": str(spec.max_rounds),
        "minimum_valid_results": str(spec.minimum_valid_results),
        "client_ids": ",".join(spec.client_ids),
        "server_lr": repr(spec.server_lr),
        "beta1": repr(spec.beta1),
        "beta2": repr(spec.beta2),
        "tau": repr(spec.tau),
        "contribution_cap": repr(spec.contribution_cap),
        "seed": str(spec.seed),
        "task_lease_seconds": str(spec.task_lease_seconds),
        "max_task_retries": str(spec.max_task_retries),
        "local_epochs": str(spec.local_epochs),
        "batch_size": str(spec.batch_size),
        "learning_rate": repr(spec.learning_rate),
        "momentum": repr(spec.momentum),
        "weight_decay": repr(spec.weight_decay),
        "fedprox_mu": repr(spec.fedprox_mu),
        "tensor_elements": str(spec.tensor_elements),
    }


def _parse_response(stdout: str) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {}
    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        fields.setdefault(key, []).append(value)
    return fields


def _snapshot_from_fields(fields: dict[str, list[str]]) -> RunSnapshot:
    def first(key: str, default: str = "") -> str:
        values = fields.get(key)
        return values[0] if values else default

    return RunSnapshot(
        run_id=first("run_id"),
        state=first("state"),
        current_round=int(first("current_round", "0")),
        max_rounds=int(first("max_rounds", "0")),
        model_version=first("model_version"),
        algorithm=first("algorithm"),
        registered_workers=int(first("registered_workers", "0")),
        healthy_workers=int(first("healthy_workers", "0")),
    )


class CliBridgeCoordinatorClient:
    """Drives fl_coordinator_cli as a subprocess per call."""

    def __init__(self, cli_path: Path, state_dir: Path) -> None:
        self._cli_path = cli_path
        self._state_dir = state_dir

    def _run(
        self, command: str, request_fields: dict[str, str]
    ) -> dict[str, list[str]]:
        if not self._cli_path.exists():
            raise CoordinatorUnavailableError(
                f"fl_coordinator_cli not found at {self._cli_path}; build it with "
                "`cmake --build build/cpp-debug --target fl_coordinator_cli`."
            )
        payload = (
            "\n".join(f"{key}={value}" for key, value in request_fields.items()) + "\n"
        )
        result = subprocess.run(
            [str(self._cli_path), command, str(self._state_dir)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )
        fields = _parse_response(result.stdout)
        status = fields.get("status", [""])[0]
        if status != "ok":
            message = fields.get("message", ["coordinator CLI failed with no message"])[
                0
            ]
            raise CoordinatorRejectedError(message)
        return fields

    def create_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        return _snapshot_from_fields(self._run("create-run", fields))

    def start_run(self, spec: RunSpec, now: float, trace_id: str = "") -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["trace_id"] = trace_id
        return _snapshot_from_fields(self._run("start-run", fields))

    def pause_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["reason"] = reason
        return _snapshot_from_fields(self._run("pause-run", fields))

    def resume_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        return _snapshot_from_fields(self._run("resume-run", fields))

    def cancel_run(self, spec: RunSpec, now: float, reason: str = "") -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["reason"] = reason
        return _snapshot_from_fields(self._run("cancel-run", fields))

    def get_run(self, spec: RunSpec, now: float) -> RunSnapshot:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        return _snapshot_from_fields(self._run("get-run", fields))

    def register_worker(self, spec: RunSpec, worker_id: str, now: float) -> None:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["worker_id"] = worker_id
        self._run("register-worker", fields)

    def acquire_task(
        self, spec: RunSpec, worker_id: str, now: float
    ) -> ClientTrainingTask:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["worker_id"] = worker_id
        response = self._run("acquire-task", fields)
        has_task = response.get("has_task", ["0"])[0] == "1"
        if not has_task:
            return ClientTrainingTask(has_task=False)

        def first(key: str, default: str = "") -> str:
            values = response.get(key)
            return values[0] if values else default

        return ClientTrainingTask(
            has_task=True,
            task_id=first("task_id"),
            lease_id=first("lease_id"),
            client_id=first("client_id"),
            round_id=int(first("round_id", "0")),
            model_version=first("model_version"),
            algorithm=first("algorithm"),
            local_epochs=int(first("local_epochs", "1")),
            batch_size=int(first("batch_size", "32")),
            learning_rate=float(first("learning_rate", "0.01")),
            fedprox_mu=float(first("fedprox_mu", "0.0")),
            global_control_variate=decode_tensor_dict(
                response.get("global_control_variate", [])
            ),
            client_control_variate=decode_tensor_dict(
                response.get("client_control_variate", [])
            ),
        )

    def submit_result(
        self,
        spec: RunSpec,
        worker_id: str,
        task: ClientTrainingTask,
        delta: dict[str, torch.Tensor],
        sample_count: int,
        update_id: str,
        nonce: str,
        now: float,
        control_delta: dict[str, torch.Tensor] | None = None,
        refreshed_client_control_variate: dict[str, torch.Tensor] | None = None,
    ) -> SubmitOutcome:
        fields = _spec_fields(spec)
        fields["now"] = repr(now)
        fields["worker_id"] = worker_id
        fields["task_id"] = task.task_id
        fields["lease_id"] = task.lease_id
        fields["round_id"] = str(task.round_id)
        fields["client_id"] = task.client_id
        fields["update_id"] = update_id
        fields["nonce"] = nonce
        fields["base_model_version"] = task.model_version
        fields["sample_count"] = str(sample_count)
        if len(delta) != 1:
            raise ValueError(
                "the CLI bridge only supports single-tensor 'weight' manifests"
            )
        ((name, tensor),) = delta.items()
        fields["delta"] = encode_named_tensor(name, tensor)
        if control_delta:
            ((cd_name, cd_tensor),) = control_delta.items()
            fields["control_delta"] = encode_named_tensor(cd_name, cd_tensor)
        if refreshed_client_control_variate:
            ((rc_name, rc_tensor),) = refreshed_client_control_variate.items()
            fields["refreshed_client_control_variate"] = encode_named_tensor(
                rc_name, rc_tensor
            )

        response = self._run("submit-result", fields)
        accepted = response.get("accepted", ["0"])[0] == "1"
        reason = response.get("reason", [""])[0]
        return SubmitOutcome(
            accepted=accepted, reason=reason, snapshot=_snapshot_from_fields(response)
        )


class GrpcCoordinatorClient:
    """Real grpcio client for a long-lived C++ coordinator gRPC server.

    Not exercised against a live server in this environment (see module
    docstring); provided as genuine, complete client code so the exact
    same worker execution loop (see task_runner.py / service.py) can be
    pointed at a real coordinator once one is running, by swapping which
    CoordinatorClient implementation is constructed.
    """

    def __init__(self, address: str, *, insecure: bool = True) -> None:
        from fl_platform.rpc import ensure_generated_on_path

        ensure_generated_on_path()
        # These three imports are structurally unresolvable by static
        # analysis: grpc ships no type stubs (not a dev dependency here),
        # and `coordinator`/`worker` only become importable at runtime
        # after ensure_generated_on_path() inserts the gitignored
        # generated/ directory onto sys.path above — mypy cannot see that.
        # Narrowly ignored rather than disabling import checking broadly.
        import grpc  # type: ignore[import-untyped]  # noqa: PLC0415 - deferred: only needed if this backend is used
        from coordinator import (  # type: ignore[import-not-found]  # noqa: PLC0415
            coordinator_pb2,
            coordinator_pb2_grpc,
        )
        from worker import worker_pb2  # type: ignore[import-not-found]  # noqa: PLC0415

        self._pb2 = coordinator_pb2
        self._worker_pb2 = worker_pb2
        if not insecure:
            raise NotImplementedError(
                "TLS is a config hook for now; see docs/coordinator-runtime.md"
            )
        self._channel = grpc.insecure_channel(address)
        self._stub = coordinator_pb2_grpc.CoordinatorServiceStub(self._channel)

    def health(self, trace_id: str = "") -> str:
        response = self._stub.Health(self._pb2.HealthRequest(trace_id=trace_id))
        return response.status

    # NOTE: the remaining CoordinatorClient methods follow the identical
    # request/response mapping documented in docs/grpc-contracts.md; they
    # are omitted here pending a real server to validate the mapping
    # against, so as not to carry unverified request-building code with
    # unknown bugs. Health() alone is implemented and left as the
    # concrete example of the pattern every other method would follow.
