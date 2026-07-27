"""Real first-order Per-FedAvg (Fallah et al., 2020). See docs/per-fedavg.md.

Per round, per client:
1. Start from the global model.
2. Run `inner_steps` of SGD on a *support* split of the client's local
   data (the "adaptation" steps).
3. Evaluate the meta-objective (cross-entropy loss) on a disjoint
   *query* split, using the adapted weights.
4. Backpropagate the query loss and apply `meta_steps` first-order
   meta-updates (first-order: gradients are taken directly at the
   adapted weights, without differentiating through the inner-loop
   adaptation itself — i.e. no second-order/Hessian term, per Fallah et
   al.'s FO-MAML variant. Full second-order MAML is explicitly out of
   scope this phase).
5. The resulting meta-updated weights' delta from the global model is
   what's submitted for aggregation (FedAvg-shaped — see
   fl_core/aggregation.hpp's AggregationAlgorithm::kPerFedAvg comment).
6. Post-adaptation personalized performance is evaluated by re-running
   the same inner-adaptation steps (this is what "personalization" means
   for Per-FedAvg — there is no persistent personalized checkpoint,
   unlike Ditto; adaptation is repeated fresh from whatever the global
   model is at evaluation time).
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
class PerFedAvgConfig:
    inner_learning_rate: float = 0.01
    outer_learning_rate: float = 0.01
    inner_steps: int = 1
    meta_steps: int = 1
    first_order_mode: bool = True
    adaptation_steps_eval: int = 1
    support_query_split_ratio: float = 0.5
    minimum_samples_required: int = 4
    fallback_behavior: str = "skip"  # "skip" | "support_only"
    batch_size: int = 32

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> PerFedAvgConfig:
        known = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in values.items() if key in known})


class InsufficientSamplesError(RuntimeError):
    """Raised when a client has too few samples for a support/query
    split and `fallback_behavior` is not able to compensate."""


def _support_query_split(
    indices: list[int], split_ratio: float, seed: int
) -> tuple[list[int], list[int]]:
    """Deterministic given (indices, split_ratio, seed) — same seed and
    inputs always produce the same split, required for reproducible
    cross-language integration tests."""
    generator = torch.Generator().manual_seed(seed)
    shuffled = torch.randperm(len(indices), generator=generator).tolist()
    split_point = max(1, int(len(indices) * split_ratio))
    support = [indices[i] for i in shuffled[:split_point]]
    query = [indices[i] for i in shuffled[split_point:]]
    return support, query


def _adapt(
    model: torch.nn.Module,
    loader: DataLoader[Any],
    lr: float,
    steps: int,
    device: torch.device,
) -> float:
    """Runs up to `steps` SGD steps over `loader` (cycling if the loader
    has fewer batches than `steps`), returning the average support loss."""
    optimizer = torch.optim.SGD(model.parameters(), lr=lr)
    total_loss = 0.0
    taken = 0
    while taken < steps:
        for inputs, labels in loader:
            if taken >= steps:
                break
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = functional.cross_entropy(model(inputs), labels)
            loss.backward()  # type: ignore[no-untyped-call]
            optimizer.step()
            total_loss += float(loss.item())
            taken += 1
    return total_loss / max(1, taken)


class PerFedAvgAlgorithm:
    name = "per_fedavg"

    def validate_task(self, context: LocalTrainingContext) -> None:
        config = PerFedAvgConfig.from_dict(context.algorithm_config)
        if not config.first_order_mode:
            raise ValueError("only first-order Per-FedAvg is supported this phase")
        if not (0.0 < config.support_query_split_ratio < 1.0):
            raise ValueError("support_query_split_ratio must be in (0, 1)")
        if config.minimum_samples_required < 2:
            raise ValueError(
                "minimum_samples_required must be at least 2 (one support, one query)"
            )

    def train(self, context: LocalTrainingContext) -> LocalTrainingResult:
        started = time.time()
        config = PerFedAvgConfig.from_dict(context.algorithm_config)
        device = context.device

        dataset, indices = load_partition(context.dataset_partition)
        if len(indices) < config.minimum_samples_required:
            if config.fallback_behavior == "support_only":
                support, query = indices, indices
            else:
                return LocalTrainingResult(
                    global_update={},
                    sample_count=0,
                    step_count=0,
                    training_loss=0.0,
                    is_non_finite=False,
                    duration_seconds=time.time() - started,
                    algorithm_metrics={
                        "support_loss": 0.0,
                        "query_loss": 0.0,
                        "inner_steps": 0.0,
                        "meta_steps": 0.0,
                        "skipped_client": 1.0,
                    },
                )
        else:
            support, query = _support_query_split(
                indices, config.support_query_split_ratio, context.seed
            )
            if not query:
                query = support

        global_state = {
            name: p.detach().clone()
            for name, p in context.global_model.named_parameters()
        }
        batch_size = min(config.batch_size, max(1, len(support)))
        support_loader = DataLoader(
            Subset(dataset, support), batch_size=batch_size, shuffle=True, num_workers=0
        )
        query_loader = DataLoader(
            Subset(dataset, query),
            batch_size=min(config.batch_size, max(1, len(query))),
            shuffle=True,
            num_workers=0,
        )

        model = copy.deepcopy(context.global_model).to(device)
        model.train()

        meta_optimizer = torch.optim.SGD(
            model.parameters(), lr=config.outer_learning_rate
        )
        query_losses: list[float] = []
        support_losses: list[float] = []

        for _meta_step in range(config.meta_steps):
            client_id = context.client_id
            if context.is_cancelled():
                raise TaskCancelled(f"Per-FedAvg task cancelled: client={client_id}")
            if context.deadline_exceeded():
                raise TaskDeadlineExceeded(
                    f"Per-FedAvg task deadline exceeded: client={client_id}"
                )

            # Inner loop: adapt a *copy* so the meta-update below is taken
            # with respect to the pre-adaptation weights' gradient at the
            # adapted point (first-order approximation — see module
            # docstring), not by differentiating through the inner loop.
            adapted_model = copy.deepcopy(model).to(device)
            support_loss = _adapt(
                adapted_model,
                support_loader,
                config.inner_learning_rate,
                config.inner_steps,
                device,
            )
            support_losses.append(support_loss)

            meta_optimizer.zero_grad(set_to_none=True)
            query_batch_loss = 0.0
            query_batches = 0
            for inputs, labels in query_loader:
                inputs, labels = inputs.to(device), labels.to(device)
                loss = functional.cross_entropy(adapted_model(inputs), labels)
                query_batch_loss += float(loss.item())
                query_batches += 1
                # First-order meta-gradient: gradient of the query loss
                # w.r.t. the *adapted* parameters, copied onto the
                # original model's parameters (first-order approximation
                # — see Fallah et al., 2020, Section 3).
                grads = torch.autograd.grad(
                    loss, list(adapted_model.parameters()), allow_unused=True
                )
                for param, grad in zip(model.parameters(), grads, strict=True):
                    if grad is not None:
                        param.grad = (
                            grad.detach().clone()
                            if param.grad is None
                            else param.grad + grad.detach()
                        )
            if query_batches > 0:
                query_losses.append(query_batch_loss / query_batches)
            meta_optimizer.step()

        local_state = {
            name: p.detach().cpu().clone() for name, p in model.named_parameters()
        }
        delta = {
            name: local_state[name] - global_state[name].cpu() for name in local_state
        }
        is_non_finite = not all(
            torch.isfinite(tensor).all() for tensor in delta.values()
        )

        avg_support_loss = (
            sum(support_losses) / len(support_losses) if support_losses else 0.0
        )
        avg_query_loss = sum(query_losses) / len(query_losses) if query_losses else 0.0

        return LocalTrainingResult(
            global_update=delta,
            sample_count=len(support) + len(query),
            step_count=config.meta_steps * config.inner_steps,
            training_loss=avg_query_loss or avg_support_loss,
            is_non_finite=is_non_finite,
            duration_seconds=time.time() - started,
            algorithm_metrics={
                "support_loss": avg_support_loss,
                "query_loss": avg_query_loss,
                "inner_steps": float(config.inner_steps),
                "meta_steps": float(config.meta_steps),
                "skipped_client": 0.0,
            },
        )

    def evaluate(self, context: LocalEvaluationContext) -> LocalEvaluationResult:
        """Pre-adaptation accuracy uses the global model as-is;
        post-adaptation ("personalized") accuracy re-runs
        `adaptation_steps_eval` steps of local SGD starting from the
        global model, then evaluates the adapted copy — Per-FedAvg has
        no persistent personalized checkpoint (see module docstring)."""
        from fl_platform.evaluation import evaluate_model_on_partition

        config = PerFedAvgConfig.from_dict(context.evaluation_config)
        pre_accuracy, pre_loss, sample_count = evaluate_model_on_partition(
            context.global_model, context.dataset_partition, context.device
        )

        dataset, indices = load_partition(context.dataset_partition)
        post_accuracy: float | None = None
        post_loss: float | None = None
        if indices and config.adaptation_steps_eval > 0:
            adapted_model = copy.deepcopy(context.global_model).to(context.device)
            adapted_model.train()
            loader = DataLoader(
                Subset(dataset, indices),
                batch_size=min(config.batch_size, max(1, len(indices))),
                shuffle=True,
                num_workers=0,
            )
            _adapt(
                adapted_model,
                loader,
                config.inner_learning_rate,
                config.adaptation_steps_eval,
                context.device,
            )
            post_accuracy, post_loss, _ = evaluate_model_on_partition(
                adapted_model, context.dataset_partition, context.device
            )

        return LocalEvaluationResult(
            global_model_local_accuracy=pre_accuracy,
            global_model_local_loss=pre_loss,
            sample_count=sample_count,
            personalized_model_local_accuracy=post_accuracy,
            personalized_model_local_loss=post_loss,
        )
