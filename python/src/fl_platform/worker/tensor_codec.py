"""Shared tensor wire-format codec.

Matches the "name|dtype|shape|values" text encoding used throughout the
C++ core (cpp/core/tools/aggregate_cli.cpp, cpp/coordinator/tools/
coordinator_cli.cpp, and the checkpoint formats) — see
docs/tensor-format.md. Kept separate from
fl_platform.compat.cpp_bridge's (Milestone 2) tensor helpers because this
module targets plain dict[str, torch.Tensor] state (what a worker
naturally works with), not the CppAggregationRequest/Result shapes.
"""

from __future__ import annotations

import torch


def encode_tensor(tensor: torch.Tensor) -> str:
    shape = "-".join(str(dim) for dim in tensor.shape)
    values = ",".join(repr(value) for value in tensor.detach().flatten().tolist())
    return f"f32|{shape}|{values}"


def encode_named_tensor(name: str, tensor: torch.Tensor) -> str:
    return f"{name}|{encode_tensor(tensor)}"


def decode_tensor_field(field_value: str) -> tuple[str, torch.Tensor]:
    name, dtype, shape_str, values_str = field_value.split("|", 3)
    if dtype != "f32":
        raise ValueError(f"unsupported tensor dtype: {dtype}")
    shape = tuple(int(dim) for dim in shape_str.split("-")) if shape_str else ()
    values = [float(v) for v in values_str.split(",")] if values_str else []
    return name, torch.tensor(values, dtype=torch.float32).reshape(shape)


def decode_tensor_dict(field_values: list[str]) -> dict[str, torch.Tensor]:
    result: dict[str, torch.Tensor] = {}
    for field_value in field_values:
        name, tensor = decode_tensor_field(field_value)
        result[name] = tensor
    return result
