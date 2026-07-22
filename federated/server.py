"""Federated server: broadcasting and aggregation for FedAvg, FedProx,
and SCAFFOLD.

Aggregation rules
-----------------
FedAvg / FedProx (identical server-side; FedProx differs only in the client
objective):
    x <- x + server_lr * sum_k (n_k / n) * delta_k

SCAFFOLD (Karimireddy et al., 2020):
    x <- x + server_lr * (1/|S|) * sum_{i in S} delta_i
    c <- c + (|S| / N)  * (1/|S|) * sum_{i in S} (c_i^+ - c_i)
where c is the global control variate and c_i are per-client control
variates stored server-side between sampling events.
"""

from __future__ import annotations

import copy
from typing import Dict, List

import torch
import torch.nn as nn

StateDict = Dict[str, torch.Tensor]

SUPPORTED_ALGORITHMS = ("fedavg", "fedprox", "scaffold")


class Server:
    """Holds the global model and applies algorithm-specific aggregation."""

    def __init__(
        self,
        model: nn.Module,
        num_clients: int,
        algorithm: str,
        server_lr: float = 1.0,
        device: torch.device = torch.device("cpu"),
    ) -> None:
        algorithm = algorithm.lower()
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"Unknown algorithm '{algorithm}'. Supported: {SUPPORTED_ALGORITHMS}"
            )
        self.algorithm = algorithm
        self.num_clients = int(num_clients)
        self.server_lr = float(server_lr)
        self.device = device
        self.model = model.to(device)

        # SCAFFOLD state: global control variate c and one c_i per client,
        # all initialized to zero (standard initialization).
        self.c_global: StateDict = {}
        self.c_locals: List[StateDict] = []
        if self.algorithm == "scaffold":
            template = self._float_param_template()
            self.c_global = {k: torch.zeros_like(v) for k, v in template.items()}
            self.c_locals = [
                {k: torch.zeros_like(v) for k, v in template.items()}
                for _ in range(self.num_clients)
            ]

    # ------------------------------------------------------------------ #
    def _float_param_template(self) -> StateDict:
        """CPU copies of all floating-point tensors in the global state."""
        return {
            k: v.detach().cpu().clone()
            for k, v in self.model.state_dict().items()
            if torch.is_floating_point(v)
        }

    def broadcast(self) -> StateDict:
        """CPU snapshot of the full global state dict sent to clients."""
        return {
            k: v.detach().cpu().clone() for k, v in self.model.state_dict().items()
        }

    def get_control_variates(self, client_id: int):
        """(c_global, c_local_i) for a sampled SCAFFOLD client."""
        if self.algorithm != "scaffold":
            return None, None
        return self.c_global, self.c_locals[client_id]

    # ------------------------------------------------------------------ #
    def aggregate(self, client_results: List[dict]) -> None:
        """Fold a round of client results into the global model.

        Args:
            client_results: outputs of ``Client.train`` for the sampled cohort.
        """
        if not client_results:
            raise ValueError("aggregate() called with an empty cohort.")

        if self.algorithm in ("fedavg", "fedprox"):
            self._aggregate_weighted(client_results)
        else:
            self._aggregate_scaffold(client_results)

    def _aggregate_weighted(self, client_results: List[dict]) -> None:
        """Sample-size-weighted delta averaging (FedAvg / FedProx)."""
        total_samples = float(sum(r["num_samples"] for r in client_results))
        agg_delta: StateDict = {}
        for r in client_results:
            weight = r["num_samples"] / total_samples
            for name, d in r["delta"].items():
                if name not in agg_delta:
                    agg_delta[name] = torch.zeros_like(d)
                agg_delta[name] += weight * d
        self._apply_delta(agg_delta)

    def _aggregate_scaffold(self, client_results: List[dict]) -> None:
        """Uniform delta averaging + control-variate bookkeeping."""
        cohort = float(len(client_results))
        agg_delta: StateDict = {}
        agg_dc: StateDict = {}

        for r in client_results:
            for name, d in r["delta"].items():
                if name not in agg_delta:
                    agg_delta[name] = torch.zeros_like(d)
                agg_delta[name] += d / cohort
            for name, dc in r["delta_c"].items():
                if name not in agg_dc:
                    agg_dc[name] = torch.zeros_like(dc)
                agg_dc[name] += dc / cohort
            # Persist the client's refreshed control variate.
            self.c_locals[r["client_id"]] = {
                k: v.clone() for k, v in r["new_c_local"].items()
            }

        self._apply_delta(agg_delta)

        scale = cohort / float(self.num_clients)
        for name in self.c_global:
            self.c_global[name] = self.c_global[name] + scale * agg_dc[name]

    def _apply_delta(self, agg_delta: StateDict) -> None:
        """x <- x + server_lr * agg_delta (non-float buffers untouched)."""
        new_state = copy.deepcopy(self.model.state_dict())
        for name, d in agg_delta.items():
            update = self.server_lr * d.to(new_state[name].device)
            new_state[name] = new_state[name] + update.to(new_state[name].dtype)
        self.model.load_state_dict(new_state)
