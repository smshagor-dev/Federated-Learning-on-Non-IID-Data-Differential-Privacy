"""Real Sharpness-Aware Minimization (SAM) local training (Foret et al.,
2021), applied per-batch during federated local training — FedSAM. See
docs/fedsam.md.

Per batch: (1) forward/backward at current weights, (2) compute the
gradient-norm-scaled perturbation and apply it, (3) forward/backward
again at the perturbed weights, (4) restore the original weights
(try/finally — a failure in the second pass must never leave the model
perturbed), (5) step the base optimizer using the *second* pass's
gradients (standard SAM; this is what actually seeks a flat minimum).

Global aggregation mapping: FedSAM submits a FedAvg-shaped delta (the
base optimizer's parameter update) — see fl_core/aggregation.hpp's
AggregationAlgorithm::kFedSam comment and docs/aggregation-manifests.md.
No claim is made here that FedSAM converges faster or better than
FedAvg; see docs/algorithm-expansion-report.md's benchmarking section — SAM's
two passes make it strictly more expensive per batch, not less.
"""

from __future__ import annotations

import math
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
class FedSamConfig:
    enabled: bool = True
    rho: float = 0.05
    adaptive: bool = False
    base_optimizer: str = "sgd"
    learning_rate: float = 0.01
    momentum: float = 0.0
    weight_decay: float = 0.0
    local_epochs: int = 1
    grad_clip_norm: float = 0.0
    mixed_precision: bool = False
    max_perturbation_norm: float = 0.0
    fail_on_non_finite: bool = True
    batch_size: int = 32

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> FedSamConfig:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in known})


def _grad_norm(parameters: list[torch.nn.Parameter]) -> float:
    total = 0.0
    for p in parameters:
        if p.grad is not None:
            total += float(p.grad.detach().pow(2).sum().item())
    return math.sqrt(total)


class FedSamAlgorithm:
    name = "fedsam"

    def validate_task(self, context: LocalTrainingContext) -> None:
        config = FedSamConfig.from_dict(context.algorithm_config)
        if config.rho <= 0:
            raise ValueError("FedSAM requires rho > 0")
        if config.local_epochs <= 0:
            raise ValueError("FedSAM requires local_epochs > 0")

    def train(self, context: LocalTrainingContext) -> LocalTrainingResult:
        started = time.time()
        config = FedSamConfig.from_dict(context.algorithm_config)
        model = context.global_model.to(context.device)
        model.train()
        global_state = {
            name: p.detach().clone() for name, p in model.named_parameters()
        }

        dataset, indices = load_partition(context.dataset_partition)
        loader = DataLoader(
            Subset(dataset, indices),
            batch_size=min(config.batch_size, max(1, len(indices))),
            shuffle=True,
            num_workers=0,
            drop_last=False,
        )
        optimizer = torch.optim.SGD(
            model.parameters(),
            lr=config.learning_rate,
            momentum=config.momentum,
            weight_decay=config.weight_decay,
        )

        use_autocast = config.mixed_precision and context.device.type == "cuda"
        first_pass_losses: list[float] = []
        second_pass_losses: list[float] = []
        perturbation_norms: list[float] = []
        gradient_norms: list[float] = []
        non_finite_batches = 0
        skipped_batches = 0
        sample_count = 0
        step_count = 0
        is_non_finite_overall = False

        for _epoch in range(config.local_epochs):
            for inputs, labels in loader:
                client_id = context.client_id
                if context.is_cancelled():
                    raise TaskCancelled(f"FedSAM task cancelled: client={client_id}")
                if context.deadline_exceeded():
                    raise TaskDeadlineExceeded(
                        f"FedSAM task deadline exceeded: client={client_id}"
                    )

                inputs = inputs.to(context.device)
                labels = labels.to(context.device)

                # --- Pass 1: standard forward/backward at current weights ---
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(device_type="cuda", enabled=use_autocast):
                    outputs: torch.Tensor = model(inputs)
                    loss1: torch.Tensor = functional.cross_entropy(outputs, labels)
                # torch's bundled stubs leave Tensor.backward() itself
                # untyped regardless of the receiver's declared type.
                loss1.backward()  # type: ignore[no-untyped-call]

                trainable = [p for p in model.parameters() if p.requires_grad]
                params_with_grad = [p for p in trainable if p.grad is not None]
                gradient_norm = _grad_norm(params_with_grad)

                if not math.isfinite(gradient_norm):
                    non_finite_batches += 1
                    if config.fail_on_non_finite:
                        is_non_finite_overall = True
                        break
                    skipped_batches += 1
                    continue
                if not params_with_grad:
                    # Empty-gradient case (e.g. a batch that touches only
                    # frozen parameters) — nothing to perturb; just skip.
                    skipped_batches += 1
                    continue

                first_pass_losses.append(float(loss1.item()))
                gradient_norms.append(gradient_norm)

                # --- Perturbation: w_adv = w + rho * g / ||g|| (or, for
                # adaptive SAM, scaled additionally by |w| per-element) ---
                scale = config.rho / (gradient_norm + 1e-12)
                applied_perturbation: dict[int, torch.Tensor] = {}
                perturbation_sq_sum = 0.0
                try:
                    with torch.no_grad():
                        for p in params_with_grad:
                            grad = p.grad
                            assert (
                                grad is not None
                            )  # params_with_grad was filtered above
                            direction = grad * scale
                            if config.adaptive:
                                direction = direction * p.detach().abs()
                            if config.max_perturbation_norm > 0:
                                direction_norm = float(direction.norm().item())
                                if direction_norm > config.max_perturbation_norm:
                                    direction = direction * (
                                        config.max_perturbation_norm
                                        / (direction_norm + 1e-12)
                                    )
                            p.add_(direction)
                            applied_perturbation[id(p)] = direction
                            perturbation_sq_sum += float(direction.pow(2).sum().item())

                    # --- Pass 2: forward/backward at the perturbed weights ---
                    optimizer.zero_grad(set_to_none=True)
                    with torch.autocast(device_type="cuda", enabled=use_autocast):
                        outputs2: torch.Tensor = model(inputs)
                        loss2: torch.Tensor = functional.cross_entropy(outputs2, labels)
                    loss2.backward()  # type: ignore[no-untyped-call]
                finally:
                    # Restoration must happen even if pass 2 raised —
                    # otherwise the model is left permanently perturbed.
                    with torch.no_grad():
                        for p in params_with_grad:
                            applied = applied_perturbation.get(id(p))
                            if applied is not None:
                                p.sub_(applied)

                second_pass_losses.append(float(loss2.item()))
                perturbation_norms.append(math.sqrt(perturbation_sq_sum))

                if config.grad_clip_norm > 0:
                    torch.nn.utils.clip_grad_norm_(
                        model.parameters(), config.grad_clip_norm
                    )
                optimizer.step()
                step_count += 1
                sample_count += inputs.shape[0]

            if is_non_finite_overall:
                break

        local_state = {
            name: p.detach().cpu().clone() for name, p in model.named_parameters()
        }
        delta = {
            name: local_state[name] - global_state[name].cpu() for name in local_state
        }

        avg_first = (
            sum(first_pass_losses) / len(first_pass_losses)
            if first_pass_losses
            else 0.0
        )
        avg_second = (
            sum(second_pass_losses) / len(second_pass_losses)
            if second_pass_losses
            else 0.0
        )

        return LocalTrainingResult(
            global_update=delta,
            sample_count=sample_count,
            step_count=step_count,
            training_loss=avg_second or avg_first,
            is_non_finite=is_non_finite_overall,
            duration_seconds=time.time() - started,
            algorithm_metrics={
                "first_pass_loss": avg_first,
                "second_pass_loss": avg_second,
                "perturbation_norm": sum(perturbation_norms) / len(perturbation_norms)
                if perturbation_norms
                else 0.0,
                "gradient_norm": sum(gradient_norms) / len(gradient_norms)
                if gradient_norms
                else 0.0,
                "sharpness_proxy": avg_second - avg_first,
                "non_finite_batch_count": float(non_finite_batches),
                "skipped_batch_count": float(skipped_batches),
            },
        )

    def evaluate(self, context: LocalEvaluationContext) -> LocalEvaluationResult:
        from fl_platform.evaluation import evaluate_model_on_partition

        accuracy, loss, sample_count = evaluate_model_on_partition(
            context.global_model, context.dataset_partition, context.device
        )
        return LocalEvaluationResult(
            global_model_local_accuracy=accuracy,
            global_model_local_loss=loss,
            sample_count=sample_count,
        )
