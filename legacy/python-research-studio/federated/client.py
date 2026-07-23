"""Federated client: local SGD, FedProx proximal term, SCAFFOLD variance
reduction, and client-level differential privacy (clip + Gaussian noise).

DP mechanism (DP-FedAvg style, McMahan et al., 2018):
  1. During local SGD every per-batch gradient is clipped to ``max_grad_norm``
     for optimization stability.
  2. The *model update* delta = w_local - w_global is clipped to L2 norm
     C = ``max_grad_norm`` (bounding each client's sensitivity), then Gaussian
     noise N(0, (sigma * C)^2 I) is added before the update leaves the client.
  Privacy is accounted at client level by the subsampled-Gaussian moments
  accountant in ``dp_accountant.py`` (sampling rate = clients/round / total).
"""

from __future__ import annotations

import copy
from typing import Dict, Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Subset

StateDict = Dict[str, torch.Tensor]


def _float_keys(state: StateDict):
    """Keys of floating-point tensors (the aggregatable parameters)."""
    return [k for k, v in state.items() if torch.is_floating_point(v)]


def _flat_norm(delta: StateDict) -> float:
    """Global L2 norm of a (possibly multi-tensor) update."""
    total = 0.0
    for v in delta.values():
        total += float(v.pow(2).sum().item())
    return float(np.sqrt(total))


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
        # drop_last=False so tiny non-IID shards still produce batches.
        self.loader = DataLoader(
            Subset(dataset, indices.tolist()),
            batch_size=min(batch_size, self.num_samples),
            shuffle=True,
            num_workers=0,
            drop_last=False,
        )

    # ------------------------------------------------------------------ #
    # Local training                                                     #
    # ------------------------------------------------------------------ #
    def train(
        self,
        model: nn.Module,
        global_state: StateDict,
        algorithm: str,
        c_global: Optional[StateDict] = None,
        c_local: Optional[StateDict] = None,
    ) -> dict:
        """Run E local epochs starting from ``global_state``.

        Args:
            model: scratch model instance (architecture only; weights are
                overwritten with the global state).
            global_state: server parameters at the start of the round (CPU).
            algorithm: "fedavg" | "fedprox" | "scaffold".
            c_global / c_local: SCAFFOLD control variates (CPU state dicts,
                required iff algorithm == "scaffold").

        Returns:
            dict with keys:
                delta        -- clipped/noised update (CPU state dict)
                num_samples  -- local dataset size (aggregation weight)
                avg_loss     -- mean local training loss
                local_state  -- final local weights (CPU, pre-noise; used for
                                the weight-variance / drift diagnostics)
                new_c_local  -- updated control variate (SCAFFOLD only)
                delta_c      -- c_i^+ - c_i (SCAFFOLD only)
        """
        algorithm = algorithm.lower()
        fed_cfg = self.cfg["federated"]
        opt_cfg = self.cfg["optimizer"]
        dp_cfg = self.cfg["dp"]

        local_epochs = int(fed_cfg["local_epochs"])
        lr = float(opt_cfg["lr"])
        # SCAFFOLD's control-variate correction assumes plain SGD steps.
        momentum = 0.0 if algorithm == "scaffold" else float(opt_cfg["momentum"])
        weight_decay = float(opt_cfg["weight_decay"])
        mu = float(self.cfg["algorithm"]["mu"])
        dp_enabled = bool(dp_cfg["enabled"])
        clip_bound = float(dp_cfg["max_grad_norm"])
        noise_multiplier = float(dp_cfg["noise_multiplier"])

        model.load_state_dict(global_state)
        model.to(self.device)
        model.train()

        # Snapshot of global trainable params for FedProx / SCAFFOLD math.
        global_params = {
            name: p.detach().clone()
            for name, p in model.named_parameters()
        }

        if algorithm == "scaffold":
            if c_global is None or c_local is None:
                raise ValueError("SCAFFOLD requires c_global and c_local.")
            c_global_dev = {k: v.to(self.device) for k, v in c_global.items()}
            c_local_dev = {k: v.to(self.device) for k, v in c_local.items()}

        optimizer = torch.optim.SGD(
            model.parameters(), lr=lr, momentum=momentum, weight_decay=weight_decay
        )

        step_count = 0
        loss_sum, loss_batches = 0.0, 0

        for _ in range(local_epochs):
            for inputs, labels in self.loader:
                inputs = inputs.to(self.device)
                labels = labels.to(self.device)

                optimizer.zero_grad(set_to_none=True)
                outputs = model(inputs)
                loss = F.cross_entropy(outputs, labels)

                if algorithm == "fedprox" and mu > 0.0:
                    prox = torch.tensor(0.0, device=self.device)
                    for name, p in model.named_parameters():
                        prox = prox + (p - global_params[name]).pow(2).sum()
                    loss = loss + 0.5 * mu * prox

                loss.backward()

                if dp_enabled:
                    # Stability clipping of the per-batch gradient.
                    torch.nn.utils.clip_grad_norm_(model.parameters(), clip_bound)

                if algorithm == "scaffold":
                    # Variance-reduced gradient: g <- g + c - c_i
                    for name, p in model.named_parameters():
                        if p.grad is not None:
                            p.grad.add_(c_global_dev[name] - c_local_dev[name])

                optimizer.step()
                step_count += 1
                loss_sum += float(loss.item())
                loss_batches += 1

        avg_loss = loss_sum / max(1, loss_batches)

        # ------------------------------------------------------------------
        # Build the model update delta = w_local - w_global (trainable params)
        # ------------------------------------------------------------------
        local_state = {
            k: v.detach().cpu().clone() for k, v in model.state_dict().items()
        }
        delta: StateDict = {}
        for name in _float_keys(local_state):
            delta[name] = local_state[name] - global_state[name].cpu()

        # ------------------------------------------------------------------
        # Differential privacy: clip update sensitivity, then add noise
        # ------------------------------------------------------------------
        if dp_enabled:
            update_norm = _flat_norm(delta)
            clip_factor = min(1.0, clip_bound / (update_norm + 1e-12))
            for name in delta:
                delta[name] = delta[name] * clip_factor
                noise = torch.normal(
                    mean=0.0,
                    std=noise_multiplier * clip_bound,
                    size=delta[name].shape,
                    generator=None,
                )
                delta[name] = delta[name] + noise.to(delta[name].dtype)

        result = {
            "client_id": self.client_id,
            "delta": delta,
            "num_samples": self.num_samples,
            "avg_loss": avg_loss,
            "local_state": local_state,
        }

        # ------------------------------------------------------------------
        # SCAFFOLD control-variate update (Option II, Karimireddy et al. 2020)
        #   c_i^+ = c_i - c + (x - y_i) / (K * eta_l)
        # We use the transmitted (clipped+noised) delta so the control variate
        # leaks no additional information beyond the DP-protected update.
        # ------------------------------------------------------------------
        if algorithm == "scaffold":
            K = max(1, step_count)
            new_c_local: StateDict = {}
            delta_c: StateDict = {}
            for name in delta:
                c_i = c_local[name].cpu()
                c_g = c_global[name].cpu()
                c_plus = c_i - c_g - delta[name] / (K * lr)
                new_c_local[name] = c_plus
                delta_c[name] = c_plus - c_i
            result["new_c_local"] = new_c_local
            result["delta_c"] = delta_c

        return result
