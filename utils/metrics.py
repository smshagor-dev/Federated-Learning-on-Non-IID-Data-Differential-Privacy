"""Evaluation and heterogeneity diagnostics for the root runtime."""

from __future__ import annotations

from typing import Dict, List, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader

StateDict = Dict[str, torch.Tensor]


@torch.no_grad()
def evaluate_global(
    model: nn.Module, test_loader: DataLoader, device: torch.device
) -> Tuple[float, float]:
    model.eval()
    model.to(device)

    total_loss, total_correct, total_samples = 0.0, 0, 0
    for inputs, labels in test_loader:
        inputs = inputs.to(device)
        labels = labels.to(device)
        outputs = model(inputs)
        loss = F.cross_entropy(outputs, labels, reduction="sum")
        total_loss += float(loss.item())
        total_correct += int((outputs.argmax(dim=1) == labels).sum().item())
        total_samples += int(labels.size(0))

    if total_samples == 0:
        return 0.0, 0.0
    return total_loss / total_samples, total_correct / total_samples


def _flatten_state(state: StateDict) -> np.ndarray:
    parts = [
        value.detach().cpu().reshape(-1).numpy()
        for _, value in sorted(state.items())
        if torch.is_floating_point(value)
    ]
    if not parts:
        return np.zeros(0, dtype=np.float64)
    return np.concatenate(parts).astype(np.float64)


def compute_weight_variance(client_states: List[StateDict]) -> float:
    if len(client_states) < 2:
        return 0.0
    stacked = np.stack([_flatten_state(state) for state in client_states], axis=0)
    return float(stacked.var(axis=0, ddof=0).mean())


def compute_mean_update_norm(client_deltas: List[StateDict]) -> float:
    if not client_deltas:
        return 0.0
    norms = [float(np.linalg.norm(_flatten_state(delta), ord=2)) for delta in client_deltas]
    return float(np.mean(norms))


def compute_client_drift(client_deltas: List[StateDict]) -> float:
    if len(client_deltas) < 2:
        return 0.0
    stacked = np.stack([_flatten_state(delta) for delta in client_deltas], axis=0)
    mean_delta = stacked.mean(axis=0, keepdims=True)
    distances = np.linalg.norm(stacked - mean_delta, axis=1)
    return float(distances.mean())
