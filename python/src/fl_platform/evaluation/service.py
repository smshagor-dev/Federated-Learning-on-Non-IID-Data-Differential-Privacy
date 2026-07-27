"""Algorithm Expansion phase evaluation layer: global and per-client
model evaluation.

Aggregated fairness statistics live in
fl_platform.personalization.metrics (compute_aggregated_personalization_metrics)
— this module produces the per-client records that feed it, plus a plain
global-only evaluation path for algorithms with no personalized model.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import torch
import torch.nn.functional as functional

from fl_platform.worker.dataset_loader import PartitionManifest, load_partition


@dataclass(slots=True)
class GlobalEvaluationResult:
    test_loss: float
    test_accuracy: float
    top_k_accuracy: float | None
    model_version: str
    sample_count: int
    duration_seconds: float


@torch.no_grad()
def evaluate_model_on_partition(
    model: torch.nn.Module,
    partition: PartitionManifest,
    device: torch.device,
) -> tuple[float, float, int]:
    """Runs `model` in eval mode over `partition`'s synthetic samples.

    Returns (accuracy, avg_loss, sample_count). Used for both "global
    model, local data" and "personalized model, local data" evaluation —
    callers pass whichever model they mean; this function has no opinion
    on which one it is.
    """
    dataset, indices = load_partition(partition)
    if not indices:
        return 0.0, 0.0, 0
    model = model.to(device)
    model.eval()
    inputs = torch.stack([dataset[index][0] for index in indices]).to(device)
    targets = torch.stack([dataset[index][1] for index in indices]).to(device)
    logits = model(inputs)
    loss = functional.cross_entropy(logits, targets)
    predictions = logits.argmax(dim=1)
    accuracy = (predictions == targets).float().mean().item()
    return float(accuracy), float(loss.item()), len(indices)


@torch.no_grad()
def _top_k_accuracy(logits: torch.Tensor, targets: torch.Tensor, k: int) -> float:
    k = min(k, logits.shape[1])
    top_k_predictions = logits.topk(k, dim=1).indices
    hits = (top_k_predictions == targets.unsqueeze(1)).any(dim=1)
    return float(hits.float().mean().item())


def evaluate_global_model(
    model: torch.nn.Module,
    partition: PartitionManifest,
    device: torch.device,
    model_version: str,
    top_k: int | None = None,
) -> GlobalEvaluationResult:
    """`top_k` is only meaningful when the model has more classes than
    `top_k` — for this phase's small synthetic/registry models that
    is often not the case, so `top_k_accuracy` stays None unless a
    caller explicitly requests a `top_k` smaller than the number of
    classes the model's final layer produces."""
    started = time.time()
    dataset, indices = load_partition(partition)
    top_k_accuracy: float | None = None
    if not indices:
        return GlobalEvaluationResult(
            test_loss=0.0,
            test_accuracy=0.0,
            top_k_accuracy=None,
            model_version=model_version,
            sample_count=0,
            duration_seconds=time.time() - started,
        )
    model = model.to(device)
    model.eval()
    with torch.no_grad():
        inputs = torch.stack([dataset[index][0] for index in indices]).to(device)
        targets = torch.stack([dataset[index][1] for index in indices]).to(device)
        logits = model(inputs)
        loss = functional.cross_entropy(logits, targets)
        accuracy = (logits.argmax(dim=1) == targets).float().mean().item()
        if top_k is not None and 1 < top_k < logits.shape[1]:
            top_k_accuracy = _top_k_accuracy(logits, targets, top_k)
    return GlobalEvaluationResult(
        test_loss=float(loss.item()),
        test_accuracy=float(accuracy),
        top_k_accuracy=top_k_accuracy,
        model_version=model_version,
        sample_count=len(indices),
        duration_seconds=time.time() - started,
    )
