"""Executes one ClientTrainingTask.

Deliberately reuses federated.client.Client (the legacy prototype's
proven FedAvg/FedProx/SCAFFOLD local-training implementation) rather than
reimplementing the training math — the Coordinator Runtime phase's job is wiring real
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
from fl_platform.privacy import SampleLevelDPConfig, is_usable, sample_level_status
from fl_platform.privacy.budget_enforcement import (
    BLOCKING_OUTCOMES,
    SampleBudgetEnforcer,
    SampleLevelBudgetExceededError,
    accountant_state_hash,
)
from fl_platform.privacy.secure_random import require_opacus_secure_mode
from fl_platform.worker.coordinator_client import ClientTrainingTask
from fl_platform.worker.dataset_loader import load_partition, manifest_for_client


class TaskCancelled(RuntimeError):
    """Raised when a cancellation token is set mid-training."""


class TaskDeadlineExceeded(RuntimeError):
    """Raised when the wall-clock deadline passes mid-training."""


class UnsupportedPrivacyCombinationError(RuntimeError):
    """Raised when a task's algorithm is not usable with the requested
    privacy mechanism (see privacy/compatibility.py). Never silently
    execute non-private training when private training was requested —
    this is raised, not swallowed, before any training happens."""


@dataclass(slots=True)
class TrainingOutcome:
    delta: dict[str, torch.Tensor]
    sample_count: int
    avg_loss: float
    control_delta: dict[str, torch.Tensor] | None = None
    refreshed_client_control_variate: dict[str, torch.Tensor] | None = None


@dataclass(slots=True, frozen=True)
class SampleLevelPrivacyResult:
    """Raw accounting facts from one private training call — the caller
    (service.py) fills in run_id/recorded_at/entry_id to build a real
    SampleLevelLedgerEntry, since task_runner.py doesn't know the run_id
    (it lives on RunSpec, not ClientTrainingTask).

    budget_decision_outcome/budget_policy/accountant_state_hash are the
    sample-level budget enforcement facts for this call (see
    fl_platform.privacy.budget_enforcement) — present even when no
    budget is configured (outcome is "allowed", reason states so),
    never silently omitted.
    """

    epsilon: float
    delta: float
    noise_multiplier: float
    sample_rate: float
    steps: int
    accountant: str
    budget_decision_outcome: str
    budget_policy: str
    accountant_state_hash: str


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


def _legacy_config(task: ClientTrainingTask) -> dict[str, dict[str, object]]:
    return {
        "federated": {"batch_size": task.batch_size, "local_epochs": task.local_epochs},
        "optimizer": {
            "lr": task.learning_rate,
            "momentum": 0.0,
            "weight_decay": 0.0,
            "grad_clip_norm": None,
        },
        "algorithm": {"mu": task.fedprox_mu},
        "dp": {
            "enabled": False,
            "update_clip_norm": 1.0,
            "max_grad_norm": 1.0,
            "noise_multiplier": 0.0,
            "target_delta": 1e-5,
            "deterministic_noise_for_testing": False,
            "test_noise_seed": None,
        },
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
    coordinator runs in this phase use a small model whose tensors
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


def run_private_local_training(
    task: ClientTrainingTask,
    global_state: dict[str, torch.Tensor],
    model: torch.nn.Module,
    privacy_config: SampleLevelDPConfig,
    *,
    device: torch.device | None = None,
    seed: int = 0,
    sample_count: int = 32,
    num_classes: int = 2,
    in_channels: int = 1,
    image_size: int = 4,
    deadline_unix_s: float | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    budget_enforcer: SampleBudgetEnforcer | None = None,
) -> tuple[TrainingOutcome, SampleLevelPrivacyResult]:
    """Runs task.local_epochs of real DP-SGD (Opacus) local training,
    returning the model update delta and the real accounting facts for
    this training call. See docs/privacy-mathematics.md.

    Does not reuse ``federated.client.Client`` (unlike
    ``run_local_training``): Opacus needs to wrap the model, optimizer,
    and data loader itself via ``PrivacyEngine.make_private`` — bolting
    that onto ``LegacyClient``'s self-contained training loop would mean
    either reimplementing Opacus's clip/noise math inside it (exactly
    the "unvalidated custom DP mathematics" the privacy engineering
    task's spec forbids) or fighting its internal optimizer stepping.
    Only ``fedavg``/``fedprox`` (and the server-optimizer-only variants
    that share fedavg's local loop) are SUPPORTED for sample-level DP —
    see ``privacy/compatibility.py``; anything else raises
    :class:`UnsupportedPrivacyCombinationError` before any training
    happens, never falling back to non-private training.

    ``budget_enforcer``, if given, enforces
    ``privacy_config.epsilon_budget`` per its configured
    ``sample_budget_policy`` (see fl_platform.privacy.budget_enforcement):
    STOP_BEFORE_EXCEEDING refuses individual optimizer steps whose
    projected epsilon would meet/exceed budget (keeping whatever steps
    already completed safely); FAIL_TASK raises immediately once the
    post-step epsilon meets/exceeds budget (no partial delta returned);
    STOP_AFTER_CURRENT_TASK lets this task finish but flags the client
    so a later call refuses to start training at all. WARN_ONLY, or no
    enforcer, never blocks anything.
    """
    status = sample_level_status(task.algorithm)
    if not is_usable(status.status):
        raise UnsupportedPrivacyCombinationError(
            f"sample-level DP is {status.status.value} for algorithm "
            f"'{task.algorithm}': {status.reason}"
        )
    if privacy_config.secure_random_required:
        # Checked before any training work starts (dataset load, model
        # setup) -- never speculatively construct PrivacyEngine(secure_mode=True)
        # and catch the resulting ImportError, which would both waste
        # work and report Opacus's generic message instead of this
        # project's structured SecureRandomTaskRejectedError.
        require_opacus_secure_mode(client_id=task.client_id)
    if is_cancelled is not None and is_cancelled():
        raise TaskCancelled(
            f"task for client '{task.client_id}' was cancelled before training started"
        )
    if deadline_unix_s is not None and time.time() > deadline_unix_s:
        raise TaskDeadlineExceeded(
            f"task for client '{task.client_id}' missed its deadline before training"
        )
    device = device or torch.device("cpu")

    # Deferred: Opacus/torch.utils.data are heavy imports only needed on
    # this private-training path.
    from opacus import PrivacyEngine  # noqa: PLC0415
    from torch.utils.data import DataLoader, Subset  # noqa: PLC0415

    manifest = manifest_for_client(
        f"synthetic:{task.client_id}", task.client_id, seed, sample_count=sample_count
    )
    manifest.num_classes = num_classes
    manifest.in_channels = in_channels
    manifest.image_size = image_size
    dataset, indices = load_partition(manifest)

    model.load_state_dict(global_state)
    model.to(device)
    global_params = [p.detach().clone() for p in model.parameters()]

    loader = DataLoader(
        Subset(dataset, indices), batch_size=task.batch_size, shuffle=True
    )
    optimizer = torch.optim.SGD(model.parameters(), lr=task.learning_rate)

    # Real, pre-existing bug found while adding budget enforcement (which
    # calls get_epsilon() far more often, at low step counts, than the
    # prior single end-of-training call): PrivacyEngine()'s own default
    # accountant is "prv", not "rdp" -- without passing accountant=
    # explicitly here, every private-training call silently used PRV
    # accounting internally regardless of privacy_config.accountant
    # (which defaults to "rdp" and is what SampleLevelPrivacyResult.accountant
    # reports), and PRV's domain computation returns NaN at low step
    # counts besides. Fixed by actually passing the configured mechanism
    # through, so the reported accountant type matches what really ran.
    privacy_engine = PrivacyEngine(
        accountant=privacy_config.accountant,
        secure_mode=privacy_config.secure_random_required,
    )
    private_model, private_optimizer, private_loader = privacy_engine.make_private(
        module=model,
        optimizer=optimizer,
        data_loader=loader,
        noise_multiplier=privacy_config.noise_multiplier,
        max_grad_norm=privacy_config.max_grad_norm,
        poisson_sampling=privacy_config.poisson_sampling,
    )

    # Opacus computes its own internal effective sample rate from
    # batch_size/dataset_size regardless of poisson_sampling (the
    # physical sampler differs; the RDP-accounting "sample_rate" it
    # feeds accountant.step() does not) — mirrored here only for
    # pre-step *projection* purposes (see check_before_step); the real,
    # authoritative sample_rate used for accounting always comes from
    # privacy_engine.accountant.history after a step actually happens.
    effective_sample_rate = task.batch_size / max(1, len(indices))

    criterion = nn.CrossEntropyLoss()
    total_loss = 0.0
    total_samples = 0
    stopped_before_step = False
    steps_completed = 0
    for _epoch in range(task.local_epochs):
        if stopped_before_step:
            break
        for inputs, targets in private_loader:
            if budget_enforcer is not None:
                decision = budget_enforcer.check_before_step(
                    privacy_engine.accountant,
                    noise_multiplier=privacy_config.noise_multiplier,
                    sample_rate=effective_sample_rate,
                )
                if decision.outcome in BLOCKING_OUTCOMES:
                    stopped_before_step = True
                    break
            inputs, targets = inputs.to(device), targets.to(device)
            private_optimizer.zero_grad()
            outputs = private_model(inputs)
            loss = criterion(outputs, targets)
            if task.algorithm == "fedprox" and task.fedprox_mu > 0:
                proximal_term = torch.zeros((), device=device)
                for local_param, global_param in zip(
                    private_model.parameters(), global_params, strict=True
                ):
                    proximal_term = proximal_term + torch.sum(
                        (local_param - global_param) ** 2
                    )
                loss = loss + (task.fedprox_mu / 2.0) * proximal_term
            loss.backward()
            private_optimizer.step()
            steps_completed += 1
            total_loss += float(loss.detach()) * inputs.shape[0]
            total_samples += inputs.shape[0]
            if budget_enforcer is not None:
                # May raise SampleLevelBudgetExceededError (FAIL_TASK, or
                # STOP_BEFORE_EXCEEDING as defense-in-depth) — propagates
                # out of this function uncaught, exactly like
                # TaskCancelled/TaskDeadlineExceeded above.
                budget_enforcer.check_after_step(privacy_engine.accountant)

    if budget_enforcer is not None and stopped_before_step and steps_completed == 0:
        # Nothing was safely trained under budget at all — do not
        # submit a zero-progress "successful" result, per "do not
        # submit an update when hard policy forbids it".
        assert budget_enforcer.last_decision is not None
        raise SampleLevelBudgetExceededError(budget_enforcer.last_decision)

    trained_state = private_model._module.state_dict()
    delta = {
        name: (trained_state[name].detach().cpu() - global_state[name].detach().cpu())
        for name in trained_state
    }

    epsilon = float(privacy_engine.get_epsilon(delta=privacy_config.target_delta))
    history = privacy_engine.accountant.history
    steps = sum(entry_steps for (_, _, entry_steps) in history)
    sample_rate = history[-1][1] if history else 0.0
    if budget_enforcer is not None:
        final_decision = budget_enforcer.last_decision
        assert final_decision is not None
        budget_decision_outcome = final_decision.outcome.value
        budget_policy = final_decision.policy.value
        state_hash = final_decision.accountant_state_hash
    else:
        budget_decision_outcome = "no_enforcer"
        budget_policy = privacy_config.sample_budget_policy.value
        state_hash = accountant_state_hash(privacy_engine.accountant)

    outcome = TrainingOutcome(
        delta=delta,
        sample_count=total_samples,
        avg_loss=total_loss / total_samples if total_samples else 0.0,
    )
    privacy_result = SampleLevelPrivacyResult(
        epsilon=epsilon,
        delta=privacy_config.target_delta,
        noise_multiplier=privacy_config.noise_multiplier,
        sample_rate=float(sample_rate),
        steps=int(steps),
        accountant=privacy_config.accountant,
        budget_decision_outcome=budget_decision_outcome,
        budget_policy=budget_policy,
        accountant_state_hash=state_hash,
    )
    return outcome, privacy_result
