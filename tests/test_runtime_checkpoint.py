from __future__ import annotations

import csv
import random
from pathlib import Path

import numpy as np
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
from utils.logger import CSVLogger


def checkpoint_config(tmp_path: Path) -> dict:
    return {
        "system": {"seed": 17, "device": "cpu", "results_dir": str(tmp_path)},
        "data": {"dataset": "MNIST", "partition": "iid"},
        "model": {"name": "cnn", "group_norm_groups": 8},
        "algorithm": {"name": "fedavg", "mu": 0.0},
        "optimizer": {"lr": 0.01},
        "federated": {
            "num_clients": 4,
            "rounds": 5,
            "sample_rate": 0.5,
            "sampling_strategy": "poisson",
            "aggregation_weighting": "uniform",
        },
        "dp": {"enabled": True, "noise_multiplier": 1.2, "target_delta": 1e-5},
        "evaluation": {"eval_batch_size": 32},
        "execution_control": {"resume": False, "checkpoint_path": "ignored"},
    }


def new_fedavg_server(generator: torch.Generator) -> Server:
    return Server(
        model=nn.Linear(2, 1),
        num_clients=4,
        algorithm="fedavg",
        device=torch.device("cpu"),
        dp_enabled=True,
        noise_multiplier=1.2,
        update_clip_norm=1.0,
        privacy_noise_generator=generator,
    )


def test_checkpoint_restores_next_random_draws_and_accountant(tmp_path: Path) -> None:
    random.seed(101)
    np.random.seed(202)
    torch.manual_seed(303)
    privacy_generator = torch.Generator(device="cpu").manual_seed(404)
    sampler = random.Random(505)
    accountant = MomentsAccountant(
        noise_multiplier=1.2,
        sample_rate=0.5,
        target_delta=1e-5,
    )
    server = new_fedavg_server(privacy_generator)
    for _ in range(3):
        server.aggregate([])
        accountant.step()
    with torch.no_grad():
        server.model.weight.fill_(2.5)
        server.model.bias.fill_(-0.75)

    checkpoint = tmp_path / "runtime-checkpoint.pt"
    history = [{"round": index} for index in range(1, 4)]
    save_runtime_checkpoint(
        checkpoint,
        config=checkpoint_config(tmp_path),
        algorithm="fedavg",
        rounds_completed=3,
        history=history,
        elapsed_sec=12.5,
        server=server,
        sampler=sampler,
        privacy_generator=privacy_generator,
        accountant=accountant,
    )

    expected_sampler = sampler.random()
    expected_python = random.random()
    expected_numpy = float(np.random.random())
    expected_torch = torch.rand(3)
    expected_privacy = torch.rand(3, generator=privacy_generator)

    restored_privacy = torch.Generator(device="cpu").manual_seed(999)
    restored_server = new_fedavg_server(restored_privacy)
    restored_sampler = random.Random(999)
    restored_accountant = MomentsAccountant(
        noise_multiplier=1.2,
        sample_rate=0.5,
        target_delta=1e-5,
    )
    restored = restore_runtime_checkpoint(
        checkpoint,
        config=checkpoint_config(tmp_path),
        algorithm="fedavg",
        server=restored_server,
        sampler=restored_sampler,
        privacy_generator=restored_privacy,
        accountant=restored_accountant,
    )

    assert restored.rounds_completed == 3
    assert restored.history == history
    assert restored.elapsed_sec == pytest.approx(12.5)
    assert restored_server.round_count == 3
    assert restored_accountant.steps == 3
    assert torch.allclose(restored_server.model.weight, torch.full_like(restored_server.model.weight, 2.5))
    assert torch.allclose(restored_server.model.bias, torch.full_like(restored_server.model.bias, -0.75))
    assert restored_sampler.random() == pytest.approx(expected_sampler)
    assert random.random() == pytest.approx(expected_python)
    assert float(np.random.random()) == pytest.approx(expected_numpy)
    assert torch.equal(torch.rand(3), expected_torch)
    assert torch.equal(torch.rand(3, generator=restored_privacy), expected_privacy)


def test_checkpoint_rejects_configuration_mismatch(tmp_path: Path) -> None:
    privacy_generator = torch.Generator(device="cpu").manual_seed(1)
    server = new_fedavg_server(privacy_generator)
    sampler = random.Random(2)
    accountant = MomentsAccountant(1.2, 0.5, 1e-5)
    checkpoint = tmp_path / "runtime-checkpoint.pt"
    save_runtime_checkpoint(
        checkpoint,
        config=checkpoint_config(tmp_path),
        algorithm="fedavg",
        rounds_completed=0,
        history=[],
        elapsed_sec=0.0,
        server=server,
        sampler=sampler,
        privacy_generator=privacy_generator,
        accountant=accountant,
    )
    changed = checkpoint_config(tmp_path)
    changed["optimizer"]["lr"] = 0.02
    with pytest.raises(CheckpointError, match="fingerprint"):
        restore_runtime_checkpoint(
            checkpoint,
            config=changed,
            algorithm="fedavg",
            server=new_fedavg_server(torch.Generator(device="cpu").manual_seed(3)),
            sampler=random.Random(4),
            privacy_generator=torch.Generator(device="cpu").manual_seed(5),
            accountant=MomentsAccountant(1.2, 0.5, 1e-5),
        )


def test_scaffold_server_runtime_state_restores_control_variates() -> None:
    server = Server(
        model=nn.Linear(2, 1),
        num_clients=2,
        algorithm="scaffold",
        device=torch.device("cpu"),
    )
    server.aggregate([])
    for value in server.c_global.values():
        value.add_(1.25)
    for client_index, state in enumerate(server.c_locals):
        for value in state.values():
            value.add_(float(client_index + 1))

    exported = server.export_runtime_state()
    restored = Server(
        model=nn.Linear(2, 1),
        num_clients=2,
        algorithm="scaffold",
        device=torch.device("cpu"),
    )
    restored.load_runtime_state(exported)

    assert restored.round_count == 1
    for name, value in server.c_global.items():
        assert torch.equal(restored.c_global[name], value)
    for client_index, state in enumerate(server.c_locals):
        for name, value in state.items():
            assert torch.equal(restored.c_locals[client_index][name], value)


def test_csv_logger_append_preserves_existing_rounds(tmp_path: Path) -> None:
    path = tmp_path / "run.csv"
    with CSVLogger(str(path)) as logger:
        logger.log({"round": 1, "algorithm": "fedavg", "test_acc": 0.5})
    with CSVLogger(str(path), append=True) as logger:
        logger.log({"round": 2, "algorithm": "fedavg", "test_acc": 0.6})

    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [int(row["round"]) for row in rows] == [1, 2]
    assert [row["algorithm"] for row in rows] == ["fedavg", "fedavg"]
