"""Executes one ClientTrainingTask.

Deliberately reuses federated.client.Client (the legacy prototype's
proven FedAvg/FedProx/SCAFFOLD local-training implementation) rather than
reimplementing the training math — Milestone 3's job is wiring real
PyTorch training into the coordinator/worker loop, not re-deriving
algorithms already implemented and tested in the legacy studio.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn

from federated.client import Client as LegacyClient
from fl_platform.worker.coordinator_client import ClientTrainingTask
from fl_platform.worker.dataset_loader import load_partition, manifest_for_client


class TaskCancelled(RuntimeError):
    """Raised when a cancellation token is set mid-training."""


class TaskDeadlineExceeded(RuntimeError):
    """Raised when the wall-clock deadline passes mid-training."""


@dataclass(slots=True)
class TrainingOutcome:
    delta: dict[str, torch.Tensor]
    sample_count: int
    avg_loss: float
    control_delta: dict[str, torch.Tensor] | None = None
    refreshed_client_control_variate: dict[str, torch.Tensor] | None = None


class BridgeCompatibleModel(nn.Module):
    """A model with exactly one parameter tensor, named "weight", and no
    bias — matching the coordinator CLI bridge's current hard-coded
    single-tensor manifest (see run_local_training's docstring and
    docs/known-limitations.md). Two additional constraints follow from
    that manifest being 1-D (``ModelManifest`` in coordinator_cli.cpp
    only supports a flat ``{tensor_elements}`` shape today, not arbitrary
    rank): the weight is kept and validated as a flat 1-D tensor (an
    nn.Sequential(Flatten, Linear) would both register its weight under
    "1.weight" instead of "weight", *and* be 2-D), reshaped internally
    for the actual linear op.
    """

    def __init__(
        self, num_classes: int = 2, in_channels: int = 1, image_size: int = 4
    ) -> None:
        super().__init__()
        self._num_classes = num_classes
        self._in_features = in_channels * image_size * image_size
        self.weight = nn.Parameter(torch.empty(num_classes * self._in_features))
        nn.init.uniform_(self.weight, -0.1, 0.1)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        weight_matrix = self.weight.view(self._num_classes, self._in_features)
        return torch.nn.functional.linear(x.flatten(1), weight_matrix)


def build_bridge_compatible_model(
    num_classes: int = 2, in_channels: int = 1, image_size: int = 4
) -> nn.Module:
    return BridgeCompatibleModel(
        num_classes=num_classes, in_channels=in_channels, image_size=image_size
    )


def _legacy_config(task: ClientTrainingTask) -> dict:
    return {
        "federated": {"batch_size": task.batch_size, "local_epochs": task.local_epochs},
        "optimizer": {"lr": task.learning_rate, "momentum": 0.0, "weight_decay": 0.0},
        "algorithm": {"mu": task.fedprox_mu},
        "dp": {"enabled": False, "max_grad_norm": 1.0, "noise_multiplier": 0.0},
    }


def run_local_training(
    task: ClientTrainingTask,
    global_state: dict[str, torch.Tensor],
    model: torch.nn.Module,
    *,
    device: torch.device | None = None,
    seed: int = 0,
    sample_count: int = 32,
    num_classes: int = 4,
    in_channels: int = 3,
    image_size: int = 32,
    deadline_unix_s: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> TrainingOutcome:
    """Runs task.local_epochs of local SGD, returning the model update delta.

    `model` is constructed by the caller (dependency injection) rather
    than hardcoded here: the coordinator's current wire transport (both
    the CLI bridge and, eventually, the gRPC ModelManifest) determines
    what tensor shapes can actually flow end-to-end, and that is a
    transport-layer concern, not a training-loop one. Real end-to-end
    coordinator runs in this milestone use a small model whose tensors
    match what the transport supports today; docs/known-limitations.md
    states plainly that the full CNN is not yet proven through the live
    transport path, while python/tests exercises real CNN training
    directly (bypassing the coordinator) to prove the training loop
    itself is correct at that model's scale.

    Cancellation and deadline are checked once before training starts
    (a full pass here is fast enough with the synthetic dataset that
    per-batch checks are not implemented; see docs/known-limitations.md
    for the honest scope of what's enforced today).
    """
    if is_cancelled is not None and is_cancelled():
        raise TaskCancelled(
            f"task for client '{task.client_id}' was cancelled before training started"
        )
    if deadline_unix_s is not None and time.time() > deadline_unix_s:
        raise TaskDeadlineExceeded(
            f"task for client '{task.client_id}' missed its deadline before training"
        )
    device = device or torch.device("cpu")

    manifest = manifest_for_client(
        f"synthetic:{task.client_id}", task.client_id, seed, sample_count=sample_count
    )
    manifest.num_classes = num_classes
    manifest.in_channels = in_channels
    manifest.image_size = image_size
    dataset, indices = load_partition(manifest)

    client = LegacyClient(
        client_id=0,
        dataset=dataset,
        indices=np.array(indices),
        config=_legacy_config(task),
        device=device,
    )

    c_global = task.global_control_variate if task.algorithm == "scaffold" else None
    c_local = task.client_control_variate if task.algorithm == "scaffold" else None
    result = client.train(
        model, global_state, task.algorithm, c_global=c_global, c_local=c_local
    )

    return TrainingOutcome(
        delta=result["delta"],
        sample_count=int(result["num_samples"]),
        avg_loss=float(result["avg_loss"]),
        control_delta=result.get("delta_c"),
        refreshed_client_control_variate=result.get("new_c_local"),
    )
