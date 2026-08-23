"""Evaluation and heterogeneity diagnostics for the root runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Mapping, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset

StateDict = Dict[str, torch.Tensor]


@dataclass(frozen=True, slots=True)
class ClientEvaluation:
    client_id: int
    sample_count: int
    loss: float
    accuracy: float


@dataclass(frozen=True, slots=True)
class ClientEvaluationSummary:
    client_count: int
    total_samples: int
    mean_client_accuracy: float
    weighted_client_accuracy: float
    median_client_accuracy: float
    p10_client_accuracy: float
    worst_client_accuracy: float
    best_client_accuracy: float
    client_accuracy_std: float
    client_accuracy_range: float
    jain_accuracy_index: float
    mean_client_loss: float
    weighted_client_loss: float
    p90_client_loss: float
    worst_client_loss: float


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


@torch.no_grad()
def evaluate_client_partitions(
    model: nn.Module,
    dataset: torch.utils.data.Dataset,
    client_dict: Mapping[int, np.ndarray],
    *,
    batch_size: int,
    device: torch.device,
) -> tuple[list[ClientEvaluation], ClientEvaluationSummary]:
    """Evaluate one global model on every client's held-out partition."""
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    if not client_dict:
        raise ValueError("client_dict must contain at least one client")

    model.eval()
    model.to(device)
    rows: list[ClientEvaluation] = []

    for client_id, indices in sorted(client_dict.items()):
        normalized_indices = np.asarray(indices, dtype=np.int64)
        if len(normalized_indices) == 0:
            raise ValueError(f"client {client_id} has no held-out evaluation samples")
        loader = DataLoader(
            Subset(dataset, normalized_indices.tolist()),
            batch_size=batch_size,
            shuffle=False,
            num_workers=0,
        )
        loss, accuracy = evaluate_global(model, loader, device)
        rows.append(
            ClientEvaluation(
                client_id=int(client_id),
                sample_count=len(normalized_indices),
                loss=float(loss),
                accuracy=float(accuracy),
            )
        )

    accuracies = np.asarray([row.accuracy for row in rows], dtype=np.float64)
    losses = np.asarray([row.loss for row in rows], dtype=np.float64)
    counts = np.asarray([row.sample_count for row in rows], dtype=np.float64)
    total_samples = int(counts.sum())
    if total_samples <= 0:
        raise RuntimeError("client evaluation produced no samples")

    weighted_accuracy = float(np.average(accuracies, weights=counts))
    weighted_loss = float(np.average(losses, weights=counts))
    denominator = float(len(accuracies) * np.square(accuracies).sum())
    jain_index = (
        1.0
        if denominator == 0.0
        else float(np.square(accuracies.sum()) / denominator)
    )

    summary = ClientEvaluationSummary(
        client_count=len(rows),
        total_samples=total_samples,
        mean_client_accuracy=float(np.mean(accuracies)),
        weighted_client_accuracy=weighted_accuracy,
        median_client_accuracy=float(np.median(accuracies)),
        p10_client_accuracy=float(np.percentile(accuracies, 10)),
        worst_client_accuracy=float(np.min(accuracies)),
        best_client_accuracy=float(np.max(accuracies)),
        client_accuracy_std=float(np.std(accuracies, ddof=0)),
        client_accuracy_range=float(np.max(accuracies) - np.min(accuracies)),
        jain_accuracy_index=jain_index,
        mean_client_loss=float(np.mean(losses)),
        weighted_client_loss=weighted_loss,
        p90_client_loss=float(np.percentile(losses, 90)),
        worst_client_loss=float(np.max(losses)),
    )
    return rows, summary


def client_evaluation_dict(row: ClientEvaluation) -> dict[str, object]:
    return dict(asdict(row))


def client_evaluation_summary_dict(
    summary: ClientEvaluationSummary,
) -> dict[str, object]:
    return dict(asdict(summary))


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
    norms = [
        float(np.linalg.norm(_flatten_state(delta), ord=2))
        for delta in client_deltas
    ]
    return float(np.mean(norms))


def compute_client_drift(client_deltas: List[StateDict]) -> float:
    if len(client_deltas) < 2:
        return 0.0
    stacked = np.stack([_flatten_state(delta) for delta in client_deltas], axis=0)
    mean_delta = stacked.mean(axis=0, keepdims=True)
    distances = np.linalg.norm(stacked - mean_delta, axis=1)
    return float(distances.mean())
