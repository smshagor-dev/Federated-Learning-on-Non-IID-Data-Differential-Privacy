"""Round-boundary checkpointing for resumable root federated executions."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import torch

SCHEMA_VERSION = 1


class CheckpointError(RuntimeError):
    """Raised when a runtime checkpoint cannot be safely restored."""


@dataclass(slots=True)
class RestoredRuntime:
    rounds_completed: int
    history: list[dict[str, Any]]
    elapsed_sec: float


def config_fingerprint(config: dict[str, Any]) -> str:
    """Hash the execution semantics that a checkpoint is allowed to resume."""
    semantic = copy.deepcopy(config)
    semantic.pop("execution_control", None)
    encoded = json.dumps(
        semantic,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def save_runtime_checkpoint(
    path: str | os.PathLike[str],
    *,
    config: dict[str, Any],
    algorithm: str,
    rounds_completed: int,
    history: list[dict[str, Any]],
    elapsed_sec: float,
    server,
    sampler: random.Random,
    privacy_generator: torch.Generator | None,
    accountant,
) -> str:
    """Atomically persist all state required to continue at the next round."""
    if rounds_completed < 0:
        raise ValueError("rounds_completed must be non-negative")
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "config_fingerprint": config_fingerprint(config),
        "algorithm": str(algorithm).lower(),
        "rounds_completed": int(rounds_completed),
        "history": copy.deepcopy(history),
        "elapsed_sec": float(elapsed_sec),
        "server": server.export_runtime_state(),
        "sampler_state": sampler.getstate(),
        "python_random_state": random.getstate(),
        "numpy_random_state": np.random.get_state(),
        "torch_rng_state": torch.get_rng_state(),
        "cuda_rng_states": torch.cuda.get_rng_state_all()
        if torch.cuda.is_available()
        else [],
        "privacy_generator_state": privacy_generator.get_state()
        if privacy_generator is not None
        else None,
        "accountant_steps": int(accountant.steps) if accountant is not None else None,
    }
    torch.save(payload, temporary)
    os.replace(temporary, target)
    return str(target)


def restore_runtime_checkpoint(
    path: str | os.PathLike[str],
    *,
    config: dict[str, Any],
    algorithm: str,
    server,
    sampler: random.Random,
    privacy_generator: torch.Generator | None,
    accountant,
) -> RestoredRuntime:
    """Restore checkpoint state and reject any semantic/configuration mismatch."""
    target = Path(path).resolve()
    if not target.is_file():
        raise CheckpointError(f"runtime checkpoint does not exist: {target}")
    try:
        payload = torch.load(target, map_location="cpu", weights_only=False)
    except Exception as exc:  # torch surfaces several deserialization exception types
        raise CheckpointError(f"load runtime checkpoint {target}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CheckpointError("runtime checkpoint payload must be an object")
    if int(payload.get("schema_version", 0)) != SCHEMA_VERSION:
        raise CheckpointError(
            f"runtime checkpoint schema_version must be {SCHEMA_VERSION}"
        )
    expected_fingerprint = config_fingerprint(config)
    if payload.get("config_fingerprint") != expected_fingerprint:
        raise CheckpointError(
            "runtime checkpoint configuration fingerprint does not match the current execution"
        )
    if str(payload.get("algorithm", "")).lower() != str(algorithm).lower():
        raise CheckpointError("runtime checkpoint algorithm does not match")

    rounds_completed = int(payload.get("rounds_completed", -1))
    configured_rounds = int(config["federated"]["rounds"])
    if rounds_completed < 0 or rounds_completed > configured_rounds:
        raise CheckpointError("runtime checkpoint rounds_completed is invalid")
    history = payload.get("history")
    if not isinstance(history, list) or len(history) != rounds_completed:
        raise CheckpointError(
            "runtime checkpoint history length does not match rounds_completed"
        )

    server_state = payload.get("server")
    if not isinstance(server_state, dict):
        raise CheckpointError("runtime checkpoint server state is missing")
    try:
        server.load_runtime_state(server_state)
    except (TypeError, ValueError, RuntimeError) as exc:
        raise CheckpointError(f"restore server runtime state: {exc}") from exc
    if int(server.round_count) != rounds_completed:
        raise CheckpointError(
            "runtime checkpoint server round count does not match rounds_completed"
        )

    try:
        sampler.setstate(payload["sampler_state"])
        random.setstate(payload["python_random_state"])
        np.random.set_state(payload["numpy_random_state"])
        torch.set_rng_state(payload["torch_rng_state"])
        cuda_states = payload.get("cuda_rng_states", [])
        if torch.cuda.is_available() and cuda_states:
            if len(cuda_states) != torch.cuda.device_count():
                raise CheckpointError(
                    "runtime checkpoint CUDA RNG state count does not match available devices"
                )
            torch.cuda.set_rng_state_all(cuda_states)
    except (KeyError, TypeError, ValueError) as exc:
        raise CheckpointError(f"restore runtime RNG state: {exc}") from exc

    privacy_state = payload.get("privacy_generator_state")
    if privacy_generator is None:
        if privacy_state is not None:
            raise CheckpointError(
                "checkpoint contains privacy RNG state but current execution has no privacy generator"
            )
    else:
        if privacy_state is None:
            raise CheckpointError("checkpoint is missing privacy RNG state")
        privacy_generator.set_state(privacy_state)

    accountant_steps = payload.get("accountant_steps")
    if accountant is None:
        if accountant_steps is not None:
            raise CheckpointError(
                "checkpoint contains privacy accountant state but current execution has no accountant"
            )
    else:
        if accountant_steps is None:
            raise CheckpointError("checkpoint is missing privacy accountant state")
        accountant.steps = int(accountant_steps)
        if accountant.steps != rounds_completed:
            raise CheckpointError(
                "checkpoint accountant steps do not match rounds_completed"
            )

    elapsed_sec = float(payload.get("elapsed_sec", 0.0))
    if elapsed_sec < 0.0:
        raise CheckpointError("runtime checkpoint elapsed_sec must be non-negative")
    return RestoredRuntime(
        rounds_completed=rounds_completed,
        history=copy.deepcopy(history),
        elapsed_sec=elapsed_sec,
    )
