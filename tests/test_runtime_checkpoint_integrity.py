from __future__ import annotations

import random
from pathlib import Path

import pytest
import torch
import torch.nn as nn

from federated.dp_accountant import MomentsAccountant
from federated.runtime_checkpoint import (
    CheckpointError,
    restore_runtime_checkpoint,
    save_runtime_checkpoint,
)
from federated.server import Server


def _config(tmp_path: Path) -> dict:
    return {
        "system": {"seed": 11, "device": "cpu", "results_dir": str(tmp_path)},
        "data": {"dataset": "MNIST", "partition": "iid"},
        "model": {"name": "cnn", "group_norm_groups": 8},
        "algorithm": {"name": "fedavg", "mu": 0.0},
        "optimizer": {"lr": 0.01},
        "federated": {
            "num_clients": 2,
            "rounds": 2,
            "sample_rate": 0.5,
            "sampling_strategy": "poisson",
            "aggregation_weighting": "uniform",
        },
        "dp": {
            "enabled": True,
            "noise_multiplier": 1.0,
            "target_delta": 1e-5,
        },
        "evaluation": {"eval_batch_size": 16},
        "execution_control": {"resume": False},
    }


def _server(generator: torch.Generator) -> Server:
    return Server(
        model=nn.Linear(2, 1),
        num_clients=2,
        algorithm="fedavg",
        device=torch.device("cpu"),
        dp_enabled=True,
        noise_multiplier=1.0,
        update_clip_norm=1.0,
        privacy_noise_generator=generator,
    )


def test_tampered_checkpoint_is_rejected_before_restore(tmp_path: Path) -> None:
    checkpoint = tmp_path / "runtime-checkpoint.pt"
    privacy_generator = torch.Generator(device="cpu").manual_seed(7)
    save_runtime_checkpoint(
        checkpoint,
        config=_config(tmp_path),
        algorithm="fedavg",
        rounds_completed=0,
        history=[],
        elapsed_sec=0.0,
        server=_server(privacy_generator),
        sampler=random.Random(9),
        privacy_generator=privacy_generator,
        accountant=MomentsAccountant(1.0, 0.5, 1e-5),
    )

    checkpoint.write_bytes(checkpoint.read_bytes() + b"tampered")

    restored_privacy = torch.Generator(device="cpu").manual_seed(17)
    with pytest.raises(CheckpointError, match="SHA-256 digest mismatch"):
        restore_runtime_checkpoint(
            checkpoint,
            config=_config(tmp_path),
            algorithm="fedavg",
            server=_server(restored_privacy),
            sampler=random.Random(19),
            privacy_generator=restored_privacy,
            accountant=MomentsAccountant(1.0, 0.5, 1e-5),
        )
