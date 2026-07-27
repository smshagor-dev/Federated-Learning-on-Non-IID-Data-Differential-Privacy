"""Adapts the Coordinator Runtime phase's fedavg/fedprox/scaffold path
(task_runner.py's run_local_training, which itself reuses
federated.client.Client) to the Algorithm Expansion phase's
FederatedLocalAlgorithm interface, without changing that
proven code — see docs/algorithm-expansion-architecture.md for why fedsam/ditto/
per_fedavg are new implementations instead of extensions of this path:
none of the three fit LegacyClient.train()'s single forward/backward-pass
shape (FedSAM needs two passes with a perturb/restore in between, Ditto
trains two separate models, Per-FedAvg needs a support/query split and a
meta-update).
"""

from __future__ import annotations

import time

from fl_platform.algorithms.base import (
    LocalEvaluationContext,
    LocalEvaluationResult,
    LocalTrainingContext,
    LocalTrainingResult,
)
from fl_platform.worker.coordinator_client import ClientTrainingTask
from fl_platform.worker.task_runner import run_local_training


class LegacyAlgorithmAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    def validate_task(self, context: LocalTrainingContext) -> None:
        if context.algorithm != self.name:
            raise ValueError(
                f"{self.name} adapter received task for algorithm '{context.algorithm}'"
            )

    def train(self, context: LocalTrainingContext) -> LocalTrainingResult:
        manifest = context.dataset_partition
        task = ClientTrainingTask(
            has_task=True,
            task_id=context.task_id,
            client_id=context.client_id,
            round_id=context.round_id,
            model_version=context.model_version,
            algorithm=context.algorithm,
            local_epochs=int(context.algorithm_config.get("local_epochs", 1)),
            batch_size=int(
                context.optimizer_config.get("batch_size", manifest.sample_count)
            ),
            learning_rate=float(context.optimizer_config.get("learning_rate", 0.01)),
            fedprox_mu=float(context.algorithm_config.get("mu", 0.0)),
            global_control_variate=context.algorithm_config.get(
                "global_control_variate", {}
            ),
            client_control_variate=context.algorithm_config.get(
                "client_control_variate", {}
            ),
        )
        global_state = {
            name: tensor.clone()
            for name, tensor in context.global_model.state_dict().items()
        }
        started = time.time()
        outcome = run_local_training(
            task,
            global_state,
            context.global_model,
            device=context.device,
            seed=context.seed,
            sample_count=manifest.sample_count,
            num_classes=manifest.num_classes,
            in_channels=manifest.in_channels,
            image_size=manifest.image_size,
            deadline_unix_s=context.deadline_unix_s,
            is_cancelled=context.cancellation_token.is_cancelled
            if context.cancellation_token
            else None,
        )
        return LocalTrainingResult(
            global_update=outcome.delta,
            sample_count=outcome.sample_count,
            step_count=0,
            training_loss=outcome.avg_loss,
            is_non_finite=False,
            duration_seconds=time.time() - started,
            control_delta=outcome.control_delta,
            refreshed_client_control_variate=outcome.refreshed_client_control_variate,
        )

    def evaluate(self, context: LocalEvaluationContext) -> LocalEvaluationResult:
        # fedavg/fedprox/scaffold have no personalized model; global-only
        # local evaluation reuses the same synthetic partition the worker
        # already trains against (see dataset_loader.py).
        from fl_platform.evaluation import evaluate_model_on_partition

        global_accuracy, global_loss, sample_count = evaluate_model_on_partition(
            context.global_model, context.dataset_partition, context.device
        )
        return LocalEvaluationResult(
            global_model_local_accuracy=global_accuracy,
            global_model_local_loss=global_loss,
            sample_count=sample_count,
        )
