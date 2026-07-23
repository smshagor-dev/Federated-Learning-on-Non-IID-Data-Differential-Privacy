"""Compatibility bridge to the C++ aggregation core.

This module builds deterministic PyTorch state dicts and client updates,
serializes them into the line-based protocol understood by the
``fl_aggregate_cli`` binary (see ``cpp/core/tools/aggregate_cli.cpp`` and
``docs/tensor-format.md``), invokes it as a subprocess, and reconstructs
PyTorch tensors from the response. It exists so Milestone 2 golden-parity
tests can compare the C++ aggregation core against the legacy Python
server implementation without requiring gRPC or pybind11 bindings.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import torch

REPO_ROOT = Path(__file__).resolve().parents[4]

_CANDIDATE_CLI_PATHS = [
    REPO_ROOT / "build" / "cpp-debug" / "Debug" / "fl_aggregate_cli.exe",
    REPO_ROOT / "build" / "cpp-release" / "Release" / "fl_aggregate_cli.exe",
    REPO_ROOT / "build" / "cpp-debug" / "fl_aggregate_cli",
    REPO_ROOT / "build" / "cpp-release" / "fl_aggregate_cli",
]


class CppAggregationUnavailable(RuntimeError):
    """Raised when the compiled fl_aggregate_cli binary cannot be found."""


class CppAggregationError(RuntimeError):
    """Raised when the C++ aggregation core rejects a request."""


def find_cli() -> Path:
    for candidate in _CANDIDATE_CLI_PATHS:
        if candidate.exists():
            return candidate
    raise CppAggregationUnavailable(
        "fl_aggregate_cli has not been built. Run "
        "`cmake --build build/cpp-debug` (see Makefile target cpp-debug) first."
    )


@dataclass
class CppClientUpdate:
    client_id: str
    update_id: str
    nonce: str
    worker_id: str
    base_model_version: str
    sample_count: int
    delta: Mapping[str, torch.Tensor]
    control_delta: Mapping[str, torch.Tensor] = field(default_factory=dict)


@dataclass
class CppAggregationRequest:
    algorithm: str
    updates: Sequence[CppClientUpdate]
    weighting: str = "sample_count"
    run_id: str = "run-1"
    round_id: int = 1
    total_clients: int = 1
    contribution_cap: float = 1.0
    minimum_weight: float = 0.0
    maximum_weight: float = 1.0
    server_lr: float = 1.0
    beta1: float = 0.9
    beta2: float = 0.99
    tau: float = 1e-3
    model_id: str = "model"
    model_version: str = "v1"
    previous_step: int = 0
    previous_first_moment: Mapping[str, torch.Tensor] = field(default_factory=dict)
    previous_second_moment: Mapping[str, torch.Tensor] = field(default_factory=dict)


@dataclass
class CppAggregationResult:
    model_delta: dict[str, torch.Tensor]
    control_delta: dict[str, torch.Tensor]
    optimizer_step: int
    first_moment: dict[str, torch.Tensor]
    second_moment: dict[str, torch.Tensor]


def _encode_tensor(tensor: torch.Tensor) -> str:
    shape = "-".join(str(dim) for dim in tensor.shape)
    values = ",".join(repr(value) for value in tensor.detach().flatten().tolist())
    return f"f32|{shape}|{values}"


def _encode_descriptor(tensor: torch.Tensor) -> str:
    shape = "-".join(str(dim) for dim in tensor.shape)
    return f"f32|{shape}|"


def _decode_tensor_field(field_value: str) -> tuple[str, torch.Tensor]:
    name, dtype, shape_str, values_str = field_value.split("|", 3)
    if dtype != "f32":
        raise CppAggregationError(f"unsupported dtype in C++ response: {dtype}")
    shape = tuple(int(dim) for dim in shape_str.split("-")) if shape_str else ()
    values = [float(v) for v in values_str.split(",")] if values_str else []
    tensor = torch.tensor(values, dtype=torch.float32).reshape(shape)
    return name, tensor


def _encode_request(request: CppAggregationRequest) -> str:
    if not request.updates:
        raise ValueError("request must include at least one client update")

    lines = [
        f"algorithm={request.algorithm}",
        f"weighting={request.weighting}",
        f"run_id={request.run_id}",
        f"round_id={request.round_id}",
        f"total_clients={request.total_clients}",
        f"contribution_cap={request.contribution_cap!r}",
        f"minimum_weight={request.minimum_weight!r}",
        f"maximum_weight={request.maximum_weight!r}",
        f"server_lr={request.server_lr!r}",
        f"beta1={request.beta1!r}",
        f"beta2={request.beta2!r}",
        f"tau={request.tau!r}",
        f"model_id={request.model_id}",
        f"model_version={request.model_version}",
    ]

    for name, tensor in request.updates[0].delta.items():
        lines.append(f"manifest_tensor={name}|{_encode_descriptor(tensor)}")

    lines.append(f"prev_step={request.previous_step}")
    for name, tensor in request.previous_first_moment.items():
        lines.append(f"prev_first={name}|{_encode_tensor(tensor)}")
    for name, tensor in request.previous_second_moment.items():
        lines.append(f"prev_second={name}|{_encode_tensor(tensor)}")

    for update in request.updates:
        lines.append("update_begin")
        lines.append(f"client_id={update.client_id}")
        lines.append(f"update_id={update.update_id}")
        lines.append(f"nonce={update.nonce}")
        lines.append(f"worker_id={update.worker_id}")
        lines.append(f"base_model_version={update.base_model_version}")
        lines.append(f"run_id={request.run_id}")
        lines.append(f"round_id={request.round_id}")
        lines.append(f"algorithm={request.algorithm}")
        lines.append(f"sample_count={update.sample_count}")
        for name, tensor in update.delta.items():
            lines.append(f"delta={name}|{_encode_tensor(tensor)}")
        for name, tensor in update.control_delta.items():
            lines.append(f"control_delta={name}|{_encode_tensor(tensor)}")
        lines.append("update_end")

    return "\n".join(lines) + "\n"


def _parse_response(stdout: str) -> CppAggregationResult:
    status: str | None = None
    message: str | None = None
    model_delta: dict[str, torch.Tensor] = {}
    control_delta: dict[str, torch.Tensor] = {}
    first_moment: dict[str, torch.Tensor] = {}
    second_moment: dict[str, torch.Tensor] = {}
    optimizer_step = 0

    for line in stdout.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key == "status":
            status = value
        elif key == "message":
            message = value
        elif key == "model_delta":
            name, tensor = _decode_tensor_field(value)
            model_delta[name] = tensor
        elif key == "control_delta":
            name, tensor = _decode_tensor_field(value)
            control_delta[name] = tensor
        elif key == "first_moment":
            name, tensor = _decode_tensor_field(value)
            first_moment[name] = tensor
        elif key == "second_moment":
            name, tensor = _decode_tensor_field(value)
            second_moment[name] = tensor
        elif key == "optimizer_step":
            optimizer_step = int(value)

    if status != "ok":
        raise CppAggregationError(message or "C++ aggregation failed with no status")

    return CppAggregationResult(
        model_delta=model_delta,
        control_delta=control_delta,
        optimizer_step=optimizer_step,
        first_moment=first_moment,
        second_moment=second_moment,
    )


def run_cpp_aggregate(
    request: CppAggregationRequest, *, cli_path: Path | None = None
) -> CppAggregationResult:
    """Invoke fl_aggregate_cli and return the parsed aggregation result."""
    resolved_cli = cli_path or find_cli()
    payload = _encode_request(request)
    result = subprocess.run(
        [str(resolved_cli)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    return _parse_response(result.stdout)


def apply_delta_to_state_dict(
    state_dict: Mapping[str, torch.Tensor], delta: Mapping[str, torch.Tensor]
) -> dict[str, torch.Tensor]:
    """x <- x + delta, matching Server._apply_delta's server_lr=1 case."""
    new_state = {name: tensor.clone() for name, tensor in state_dict.items()}
    for name, tensor in delta.items():
        new_state[name] = new_state[name] + tensor.to(new_state[name].dtype)
    return new_state
