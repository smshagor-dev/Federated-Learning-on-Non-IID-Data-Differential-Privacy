"""Evaluation and heterogeneity diagnostics.

  * evaluate_global        -- loss / accuracy on the held-out global test set
  * compute_weight_variance -- mean parameter variance across client models
  * compute_client_drift    -- mean L2 deviation of client updates from the
                               cohort-average update (classic drift measure)
"""

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
    """Return (avg_cross_entropy_loss, top1_accuracy) on the test set."""
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
    """Concatenate all floating-point tensors of a state dict into one vector."""
    parts = [
        v.detach().cpu().reshape(-1).numpy()
        for k, v in sorted(state.items())
        if torch.is_floating_point(v)
    ]
    if not parts:
        return np.zeros(0, dtype=np.float64)
    return np.concatenate(parts).astype(np.float64)


def compute_weight_variance(client_states: List[StateDict]) -> float:
    """Mean per-coordinate variance of client model weights.

    A direct proxy for how far the sampled clients' local optima have
    diverged from each other during the round (higher = more drift).
    """
    if len(client_states) < 2:
        return 0.0
    stacked = np.stack([_flatten_state(s) for s in client_states], axis=0)
    return float(stacked.var(axis=0, ddof=0).mean())


def compute_client_drift(client_deltas: List[StateDict]) -> float:
    """Average L2 distance between each client update and the mean update.

    drift = (1/m) * sum_i || delta_i - mean_delta ||_2
    """
    if len(client_deltas) < 2:
        return 0.0
    stacked = np.stack([_flatten_state(d) for d in client_deltas], axis=0)
    mean_delta = stacked.mean(axis=0, keepdims=True)
    distances = np.linalg.norm(stacked - mean_delta, axis=1)
    return float(distances.mean())
