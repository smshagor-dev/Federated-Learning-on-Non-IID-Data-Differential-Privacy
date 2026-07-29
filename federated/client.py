"""Federated client local training for the active root runtime."""

from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset

StateDict = Dict[str, torch.Tensor]


def float_state_keys(state: StateDict) -> list[str]:
    return [key for key, value in state.items() if torch.is_floating_point(value)]


def flat_l2_norm(state: StateDict) -> float:
    total = 0.0
    for value in state.values():
        total += float(value.detach().pow(2).sum().item())
    return float(np.sqrt(total))


def assert_finite_state(state: StateDict, *, context: str) -> None:
    for name, value in state.items():
        if not torch.isfinite(value).all():
            raise ValueError(f"Non-finite tensor detected in {context}: {name}")


def clip_state_update(delta: StateDict, clip_norm: float) -> tuple[StateDict, float, float, bool]:
    if clip_norm <= 0.0:
        raise ValueError("clip_norm must be > 0.")
    update_norm = flat_l2_norm(delta)
    clip_factor = min(1.0, clip_norm / (update_norm + 1e-12))
    clipped = {name: value * clip_factor for name, value in delta.items()}
    assert_finite_state(clipped, context="clipped client update")
    return clipped, update_norm, clip_factor, clip_factor < 1.0


class Client:
    """A single simulated FL participant."""

    def __init__(
        self,
        client_id: int,
        dataset: torch.utils.data.Dataset,
        indices: np.ndarray,
        config: dict,
        device: torch.device,
    ) -> None:
        self.client_id = client_id
        self.device = device
        self.cfg = config
        self.num_samples = int(len(indices))
        batch_size = int(config["federated"]["batch_size"])
        self.loader = DataLoader(
            Subset(dataset, indices.tolist()),
            batch_size=max(1, min(batch_size, self.num_samples)),
            shuffle=True,
            num_workers=0,
            drop_last=False,
        )

    def train(
        self,
        model: nn.Module,
        global_state: StateDict,
        algorithm: str,
        c_global: Optional[StateDict] = None,
        c_local: Optional[StateDict] = None,
    ) -> dict:
        algorithm = algorithm.lower()
        fed_cfg = self.cfg["federated"]
        opt_cfg = self.cfg["optimizer"]
        dp_cfg = self.cfg["dp"]

        local_epochs = int(fed_cfg["local_epochs"])
        lr = float(opt_cfg["lr"])
        momentum = 0.0 if algorithm == "scaffold" else float(opt_cfg["momentum"])
        weight_decay = float(opt_cfg["weight_decay"])
        grad_clip_norm = opt_cfg.get("grad_clip_norm")
        mu = float(self.cfg["algorithm"]["mu"])
        dp_enabled = bool(dp_cfg["enabled"])
        update_clip_value = dp_cfg.get("update_clip_norm", dp_cfg.get("max_grad_norm", 1.0))
        update_clip_norm = float(update_clip_value)

        model.load_state_dict(global_state)
        model.to(self.device)
        model.train()

        global_params = {
            name: param.detach().clone()
            for name, param in model.named_parameters()
        }
        if algorithm == "scaffold":
            if c_global is None or c_local is None:
                raise ValueError("SCAFFOLD requires c_global and c_local.")
            c_global_dev = {name: value.to(self.device) for name, value in c_global.items()}
            c_local_dev = {name: value.to(self.device) for name, value in c_local.items()}

        optimizer = torch.optim.SGD(
            model.parameters(),
            lr=lr,
            momentum=momentum,
            weight_decay=weight_decay,
        )

        local_steps = 0
        loss_sum = 0.0
        loss_batches = 0
        for _ in range(local_epochs):
            for inputs, labels in self.loader:
                inputs = inputs.to(self.device)
                labels = labels.to(self.device)
                optimizer.zero_grad(set_to_none=True)
                outputs = model(inputs)
                loss = F.cross_entropy(outputs, labels)
                if algorithm == "fedprox" and mu > 0.0:
                    prox = torch.tensor(0.0, device=self.device)
                    for name, param in model.named_parameters():
                        prox = prox + (param - global_params[name]).pow(2).sum()
                    loss = loss + 0.5 * mu * prox
                loss.backward()
                if grad_clip_norm is not None:
                    torch.nn.utils.clip_grad_norm_(
                        model.parameters(),
                        float(grad_clip_norm),
                    )
                if algorithm == "scaffold":
                    for name, param in model.named_parameters():
                        if param.grad is not None:
                            param.grad.add_(c_global_dev[name] - c_local_dev[name])
                optimizer.step()
                local_steps += 1
                loss_sum += float(loss.item())
                loss_batches += 1

        avg_loss = loss_sum / max(1, loss_batches)
        local_state = {
            key: value.detach().cpu().clone()
            for key, value in model.state_dict().items()
        }
        raw_delta = {
            name: local_state[name] - global_state[name].cpu()
            for name in float_state_keys(local_state)
        }
        assert_finite_state(raw_delta, context="raw client update")
        clipped_delta = raw_delta
        unclipped_update_norm = flat_l2_norm(raw_delta)
        clipping_factor = 1.0
        was_clipped = False
        if dp_enabled:
            clipped_delta, unclipped_update_norm, clipping_factor, was_clipped = clip_state_update(
                raw_delta,
                update_clip_norm,
            )
        transmitted_delta = {
            name: tensor.detach().clone() for name, tensor in clipped_delta.items()
        }

        result = {
            "client_id": self.client_id,
            "delta": transmitted_delta,
            "raw_delta": raw_delta,
            "clipped_delta": clipped_delta,
            "num_samples": self.num_samples,
            "avg_loss": avg_loss,
            "local_state": local_state,
            "unclipped_update_norm": unclipped_update_norm,
            "clipping_factor": clipping_factor,
            "was_clipped": was_clipped,
            "local_steps": max(1, local_steps),
        }

        if algorithm == "scaffold":
            new_c_local: StateDict = {}
            delta_c: StateDict = {}
            tau_k = max(1, local_steps)
            for name in transmitted_delta:
                c_i = c_local[name].cpu()
                c_g = c_global[name].cpu()
                c_plus = c_i - c_g - transmitted_delta[name] / (tau_k * lr)
                new_c_local[name] = c_plus
                delta_c[name] = c_plus - c_i
            result["new_c_local"] = new_c_local
            result["delta_c"] = delta_c

        return result
