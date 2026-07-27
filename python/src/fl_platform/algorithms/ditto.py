"""Real Ditto personalized federated learning (Li et al., 2021). See
docs/ditto.md.

Each participating client trains TWO models per round:
1. A global-training model (initialized from the current global model),
   trained with plain local SGD — its delta is what gets submitted to
   the coordinator for aggregation, exactly like FedAvg.
2. A persistent personalized model, trained to minimize
   `local_loss(personalized) + (lambda/2) * ||personalized - global||^2`
   — regularized to stay near the *global reference* (the model this
   round started from), not near the global-training model's post-training
   state. The personalized model is never aggregated and never leaves
   the worker except as scalar metrics (see
   fl.coordinator.v1.PersonalizationMetricRecord).
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

import torch
import torch.nn.functional as functional
from torch.utils.data import DataLoader, Subset

from fl_platform.algorithms.base import (
    LocalEvaluationContext,
    LocalEvaluationResult,
    LocalTrainingContext,
    LocalTrainingResult,
    TaskCancelled,
    TaskDeadlineExceeded,
)
from fl_platform.worker.dataset_loader import load_partition


@dataclass(slots=True)
class DittoConfig:
    personalized_learning_rate: float = 0.01
    global_learning_rate: float = 0.01
    regularization_coefficient: float = 0.1
    personalized_local_epochs: int = 1
    global_local_epochs: int = 1
    personalized_optimizer: str = "sgd"
    global_optimizer: str = "sgd"
    personalized_checkpoint_policy: str = "every_round"
    evaluation_frequency: int = 1
    warm_start_policy: str = "warm"  # "warm" | "cold"
    batch_size: int = 32

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> DittoConfig:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in known})


def _train_plain(
    model: torch.nn.Module,
    loader: DataLoader[Any],
    device: torch.device,
    lr: float,
    epochs: int,
    context: LocalTrainingContext,
) -> float:
    """Plain local SGD, no regularization — used for the global-training
    model. Returns average loss."""
    optimizer = torch.optim.SGD(model.parameters(), lr=lr)
    total_loss, batches = 0.0, 0
    for _epoch in range(epochs):
        for inputs, labels in loader:
            client_id = context.client_id
            if context.is_cancelled():
                raise TaskCancelled(
                    f"Ditto global training cancelled: client={client_id}"
                )
            if context.deadline_exceeded():
                raise TaskDeadlineExceeded(
                    f"Ditto global training deadline: client={client_id}"
                )
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = functional.cross_entropy(model(inputs), labels)
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            total_loss += float(loss.item())
            batches += 1
    return total_loss / max(1, batches)


def _train_personalized(
    personalized_model: torch.nn.Module,
    global_reference: dict[str, torch.Tensor],
    loader: DataLoader[Any],
    device: torch.device,
    lr: float,
    epochs: int,
    regularization_coefficient: float,
    context: LocalTrainingContext,
) -> tuple[float, float]:
    """Ditto personalized objective:
    local_loss + (lambda/2) * ||personalized - global_reference||^2.
    Returns (avg_task_loss, avg_regularization_loss)."""
    optimizer = torch.optim.SGD(personalized_model.parameters(), lr=lr)
    total_task_loss, total_reg_loss, batches = 0.0, 0.0, 0
    for _epoch in range(epochs):
        for inputs, labels in loader:
            client_id = context.client_id
            if context.is_cancelled():
                raise TaskCancelled(
                    f"Ditto personalized training cancelled: client={client_id}"
                )
            if context.deadline_exceeded():
                raise TaskDeadlineExceeded(
                    f"Ditto personalized training deadline: client={client_id}"
                )
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            task_loss = functional.cross_entropy(personalized_model(inputs), labels)
            reg_loss = torch.zeros((), device=device)
            for name, p in personalized_model.named_parameters():
                reg_loss = reg_loss + (p - global_reference[name]).pow(2).sum()
            loss = task_loss + 0.5 * regularization_coefficient * reg_loss
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            total_task_loss += float(task_loss.item())
            total_reg_loss += float(reg_loss.item())
            batches += 1
    return total_task_loss / max(1, batches), total_reg_loss / max(1, batches)


class DittoAlgorithm:
    name = "ditto"

    def validate_task(self, context: LocalTrainingContext) -> None:
        config = DittoConfig.from_dict(context.algorithm_config)
        if config.regularization_coefficient <= 0:
            raise ValueError("Ditto requires a positive regularization_coefficient")
        if config.personalized_local_epochs <= 0 or config.global_local_epochs <= 0:
            raise ValueError(
                "Ditto requires positive local epoch counts for both models"
            )

    def train(self, context: LocalTrainingContext) -> LocalTrainingResult:
        started = time.time()
        config = DittoConfig.from_dict(context.algorithm_config)
        device = context.device

        global_reference = {
            name: p.detach().clone()
            for name, p in context.global_model.named_parameters()
        }

        dataset, indices = load_partition(context.dataset_partition)
        batch_size = min(config.batch_size, max(1, len(indices)))
        loader = DataLoader(
            Subset(dataset, indices), batch_size=batch_size, shuffle=True, num_workers=0
        )

        # --- Global-training model: plain local SGD from the global reference ---
        global_training_model = copy.deepcopy(context.global_model).to(device)
        global_training_model.train()
        global_training_started = time.time()
        global_loss = _train_plain(
            global_training_model,
            loader,
            device,
            config.global_learning_rate,
            config.global_local_epochs,
            context,
        )
        global_training_duration = time.time() - global_training_started

        global_local_state = {
            name: p.detach().cpu().clone()
            for name, p in global_training_model.named_parameters()
        }
        delta = {
            name: global_local_state[name] - global_reference[name].cpu()
            for name in global_local_state
        }

        # --- Personalized model: warm-start from the previous checkpoint
        # if provided (context.personalized_model), else cold-start from
        # the global reference. ---
        if (
            context.personalized_model is not None
            and config.warm_start_policy == "warm"
        ):
            personalized_model = context.personalized_model.to(device)
        else:
            personalized_model = copy.deepcopy(context.global_model).to(device)
        personalized_model.train()

        personalized_started = time.time()
        personalized_loss, regularization_loss = _train_personalized(
            personalized_model,
            global_reference,
            loader,
            device,
            config.personalized_learning_rate,
            config.personalized_local_epochs,
            config.regularization_coefficient,
            context,
        )
        personalized_duration = time.time() - personalized_started

        personalized_checkpoint = {
            name: tensor.detach().cpu().clone()
            for name, tensor in personalized_model.state_dict().items()
        }

        is_non_finite = not (
            all(torch.isfinite(tensor).all() for tensor in delta.values())
            and all(
                torch.isfinite(tensor).all()
                for tensor in personalized_checkpoint.values()
            )
        )

        return LocalTrainingResult(
            global_update=delta,
            sample_count=len(indices),
            step_count=config.global_local_epochs * max(1, len(loader)),
            training_loss=global_loss,
            is_non_finite=is_non_finite,
            duration_seconds=time.time() - started,
            personalized_checkpoint=personalized_checkpoint,
            algorithm_metrics={
                "global_loss": global_loss,
                "personalized_loss": personalized_loss,
                "regularization_loss": regularization_loss,
                "personalized_training_duration_seconds": personalized_duration,
                "global_training_duration_seconds": global_training_duration,
            },
        )

    def evaluate(self, context: LocalEvaluationContext) -> LocalEvaluationResult:
        from fl_platform.evaluation import evaluate_model_on_partition

        global_accuracy, global_loss, sample_count = evaluate_model_on_partition(
            context.global_model, context.dataset_partition, context.device
        )
        personalized_accuracy = None
        personalized_loss = None
        if context.personalized_model is not None:
            personalized_accuracy, personalized_loss, _ = evaluate_model_on_partition(
                context.personalized_model, context.dataset_partition, context.device
            )
        return LocalEvaluationResult(
            global_model_local_accuracy=global_accuracy,
            global_model_local_loss=global_loss,
            sample_count=sample_count,
            personalized_model_local_accuracy=personalized_accuracy,
            personalized_model_local_loss=personalized_loss,
        )
