"""Federated server aggregation for the active root runtime."""

from __future__ import annotations

import copy
import os
from typing import Dict, List

import torch
import torch.nn as nn

from federated.client import assert_finite_state, flat_l2_norm

StateDict = Dict[str, torch.Tensor]
SUPPORTED_ALGORITHMS = ("fedavg", "fedprox", "scaffold")
SUPPORTED_AGGREGATION_WEIGHTING = ("uniform", "sample_count")
_CHECKPOINT_DIR_ENV = "FL_ROOT_CHECKPOINT_DIR"
_CHECKPOINT_ROUNDS_ENV = "FL_ROOT_CHECKPOINT_ROUNDS"


class Server:
    """Holds the global model and applies aggregation and central DP noise."""

    def __init__(
        self,
        model: nn.Module,
        num_clients: int,
        algorithm: str,
        server_lr: float = 1.0,
        device: torch.device = torch.device("cpu"),
        aggregation_weighting: str = "uniform",
        dp_enabled: bool = False,
        noise_multiplier: float = 0.0,
        update_clip_norm: float = 1.0,
        privacy_noise_generator: torch.Generator | None = None,
    ) -> None:
        algorithm = algorithm.lower()
        weighting = aggregation_weighting.lower()
        if algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(
                f"Unknown algorithm '{algorithm}'. Supported: {SUPPORTED_ALGORITHMS}"
            )
        if weighting not in SUPPORTED_AGGREGATION_WEIGHTING:
            raise ValueError(
                f"Unknown aggregation weighting '{aggregation_weighting}'. "
                f"Supported: {SUPPORTED_AGGREGATION_WEIGHTING}"
            )
        if algorithm == "scaffold" and weighting != "uniform":
            raise ValueError("SCAFFOLD only supports uniform aggregation weighting.")
        if algorithm == "scaffold" and bool(dp_enabled):
            raise ValueError(
                "DP-enabled SCAFFOLD is disabled in the active root runtime until "
                "the privacy effect of released/stateful control-variate updates is "
                "formally established. Use FedAvg/FedProx for client-level DP, or "
                "run SCAFFOLD with DP disabled as an optimization baseline."
            )
        self.algorithm = algorithm
        self.num_clients = int(num_clients)
        self.server_lr = float(server_lr)
        self.device = device
        self.model = model.to(device)
        self.aggregation_weighting = weighting
        self.dp_enabled = bool(dp_enabled)
        self.noise_multiplier = float(noise_multiplier)
        self.update_clip_norm = float(update_clip_norm)
        self.privacy_noise_generator = privacy_noise_generator
        self._round_count = 0

        self.c_global: StateDict = {}
        self.c_locals: List[StateDict] = []
        if self.algorithm == "scaffold":
            template = self._float_param_template()
            self.c_global = {k: torch.zeros_like(v) for k, v in template.items()}
            self.c_locals = [
                {k: torch.zeros_like(v) for k, v in template.items()}
                for _ in range(self.num_clients)
            ]

    @property
    def round_count(self) -> int:
        return int(self._round_count)

    def _float_param_template(self) -> StateDict:
        return {
            key: value.detach().cpu().clone()
            for key, value in self.model.state_dict().items()
            if torch.is_floating_point(value)
        }

    def broadcast(self) -> StateDict:
        return {
            key: value.detach().cpu().clone()
            for key, value in self.model.state_dict().items()
        }

    def get_control_variates(self, client_id: int):
        if self.algorithm != "scaffold":
            return None, None
        return self.c_global, self.c_locals[client_id]

    def export_runtime_state(self) -> dict:
        """Return CPU-owned state required for an exact round-boundary resume."""
        model_state = {
            key: value.detach().cpu().clone()
            for key, value in self.model.state_dict().items()
        }
        c_global = {
            key: value.detach().cpu().clone() for key, value in self.c_global.items()
        }
        c_locals = [
            {key: value.detach().cpu().clone() for key, value in state.items()}
            for state in self.c_locals
        ]
        return {
            "schema_version": 1,
            "algorithm": self.algorithm,
            "num_clients": self.num_clients,
            "round_count": self._round_count,
            "model_state": model_state,
            "c_global": c_global,
            "c_locals": c_locals,
        }

    def load_runtime_state(self, state: dict) -> None:
        """Restore a state produced by :meth:`export_runtime_state` fail-closed."""
        if not isinstance(state, dict):
            raise ValueError("server runtime state must be an object")
        if int(state.get("schema_version", 0)) != 1:
            raise ValueError("server runtime state schema_version must be 1")
        if str(state.get("algorithm", "")).lower() != self.algorithm:
            raise ValueError("server runtime state algorithm mismatch")
        if int(state.get("num_clients", -1)) != self.num_clients:
            raise ValueError("server runtime state num_clients mismatch")
        round_count = int(state.get("round_count", -1))
        if round_count < 0:
            raise ValueError("server runtime state round_count must be non-negative")

        model_state = state.get("model_state")
        if not isinstance(model_state, dict):
            raise ValueError("server runtime state model_state is missing")
        assert_finite_state(model_state, context="restored server model")
        self.model.load_state_dict(model_state, strict=True)

        if self.algorithm == "scaffold":
            c_global = state.get("c_global")
            c_locals = state.get("c_locals")
            if not isinstance(c_global, dict) or not isinstance(c_locals, list):
                raise ValueError("SCAFFOLD runtime checkpoint is missing control variates")
            template_keys = set(self._float_param_template())
            if set(c_global) != template_keys:
                raise ValueError("SCAFFOLD global control-variate keys mismatch")
            if len(c_locals) != self.num_clients:
                raise ValueError("SCAFFOLD local control-variate count mismatch")
            restored_locals: List[StateDict] = []
            for client_id, local_state in enumerate(c_locals):
                if not isinstance(local_state, dict) or set(local_state) != template_keys:
                    raise ValueError(
                        f"SCAFFOLD client {client_id} control-variate keys mismatch"
                    )
                assert_finite_state(
                    local_state,
                    context=f"restored SCAFFOLD client {client_id} control variate",
                )
                restored_locals.append(
                    {key: value.detach().cpu().clone() for key, value in local_state.items()}
                )
            assert_finite_state(c_global, context="restored SCAFFOLD global control variate")
            self.c_global = {
                key: value.detach().cpu().clone() for key, value in c_global.items()
            }
            self.c_locals = restored_locals
        else:
            c_global = state.get("c_global", {})
            c_locals = state.get("c_locals", [])
            if c_global or c_locals:
                raise ValueError(
                    "non-SCAFFOLD runtime checkpoint unexpectedly contains control variates"
                )
            self.c_global = {}
            self.c_locals = []

        self._round_count = round_count

    def aggregate(self, client_results: List[dict]) -> dict:
        if self.algorithm in ("fedavg", "fedprox"):
            result = self._aggregate_fedavg_family(client_results)
        else:
            result = self._aggregate_scaffold(client_results)
        self._round_count += 1
        self._persist_root_checkpoint_if_final_round()
        return result

    def _aggregate_fedavg_family(self, client_results: List[dict]) -> dict:
        if self.aggregation_weighting == "sample_count":
            aggregate_delta = self._weighted_average_delta(client_results)
            self._apply_delta(aggregate_delta)
            return {
                "aggregate_noise_norm": 0.0,
                "cohort_size": len(client_results),
            }

        if not client_results:
            return {"aggregate_noise_norm": 0.0, "cohort_size": 0}
        clipped_sum = self._sum_deltas(client_results)
        noise_norm = 0.0
        if self.dp_enabled:
            noise = self._sample_noise_like(clipped_sum)
            noise_norm = flat_l2_norm(noise)
            clipped_sum = {
                name: clipped_sum[name] + noise[name]
                for name in clipped_sum
            }
        aggregate_delta = {
            name: clipped_sum[name] / float(len(client_results))
            for name in clipped_sum
        }
        self._apply_delta(aggregate_delta)
        return {
            "aggregate_noise_norm": noise_norm,
            "cohort_size": len(client_results),
        }

    def _aggregate_scaffold(self, client_results: List[dict]) -> dict:
        if not client_results:
            return {"aggregate_noise_norm": 0.0, "cohort_size": 0}

        cohort_size = float(len(client_results))
        clipped_sum = self._sum_deltas(client_results)
        noise_norm = 0.0
        if self.dp_enabled:
            noise = self._sample_noise_like(clipped_sum)
            noise_norm = flat_l2_norm(noise)
            clipped_sum = {
                name: clipped_sum[name] + noise[name]
                for name in clipped_sum
            }
        aggregate_delta = {
            name: clipped_sum[name] / cohort_size for name in clipped_sum
        }

        aggregate_delta_c: StateDict = {}
        for result in client_results:
            for name, delta_c in result["delta_c"].items():
                if name not in aggregate_delta_c:
                    aggregate_delta_c[name] = torch.zeros_like(delta_c)
                aggregate_delta_c[name] += delta_c / cohort_size
            self.c_locals[result["client_id"]] = {
                key: value.clone() for key, value in result["new_c_local"].items()
            }

        self._apply_delta(aggregate_delta)
        scale = cohort_size / float(self.num_clients)
        for name in self.c_global:
            self.c_global[name] = self.c_global[name] + scale * aggregate_delta_c[name]
        return {
            "aggregate_noise_norm": noise_norm,
            "cohort_size": int(cohort_size),
        }

    def _weighted_average_delta(self, client_results: List[dict]) -> StateDict:
        if not client_results:
            return {}
        total_samples = float(sum(result["num_samples"] for result in client_results))
        aggregate: StateDict = {}
        for result in client_results:
            weight = result["num_samples"] / total_samples
            for name, delta in result["delta"].items():
                if name not in aggregate:
                    aggregate[name] = torch.zeros_like(delta)
                aggregate[name] += weight * delta
        return aggregate

    def _sum_deltas(self, client_results: List[dict]) -> StateDict:
        aggregate: StateDict = {}
        for result in client_results:
            for name, delta in result["delta"].items():
                if name not in aggregate:
                    aggregate[name] = torch.zeros_like(delta)
                aggregate[name] += delta
        assert_finite_state(aggregate, context="aggregated client sum")
        return aggregate

    def _sample_noise_like(self, template: StateDict) -> StateDict:
        std = self.noise_multiplier * self.update_clip_norm
        noise: StateDict = {}
        for name, value in template.items():
            noise[name] = torch.normal(
                mean=0.0,
                std=std,
                size=tuple(value.shape),
                generator=self.privacy_noise_generator,
                dtype=value.dtype,
            )
        assert_finite_state(noise, context="aggregate gaussian noise")
        return noise

    def _apply_delta(self, aggregate_delta: StateDict) -> None:
        if not aggregate_delta:
            return
        assert_finite_state(aggregate_delta, context="server update")
        new_state = copy.deepcopy(self.model.state_dict())
        for name, delta in aggregate_delta.items():
            update = self.server_lr * delta.to(new_state[name].device)
            new_state[name] = new_state[name] + update.to(new_state[name].dtype)
        self.model.load_state_dict(new_state)

    def _persist_root_checkpoint_if_final_round(self) -> None:
        """Write one checkpoint after the configured final aggregation call."""
        output_dir = os.environ.get(_CHECKPOINT_DIR_ENV)
        target_rounds_text = os.environ.get(_CHECKPOINT_ROUNDS_ENV)
        if not output_dir or not target_rounds_text:
            return
        try:
            target_rounds = int(target_rounds_text)
        except ValueError as exc:
            raise ValueError(
                f"{_CHECKPOINT_ROUNDS_ENV} must be an integer"
            ) from exc
        if target_rounds <= 0:
            raise ValueError(f"{_CHECKPOINT_ROUNDS_ENV} must be > 0")
        if self._round_count != target_rounds:
            return

        os.makedirs(output_dir, exist_ok=True)
        target = os.path.join(output_dir, f"global_model_{self.algorithm}.pt")
        temporary = target + ".tmp"
        state = {
            key: value.detach().cpu().clone()
            for key, value in self.model.state_dict().items()
        }
        torch.save(
            {
                "schema_version": 1,
                "algorithm": self.algorithm,
                "rounds_completed": self._round_count,
                "state_dict": state,
            },
            temporary,
        )
        os.replace(temporary, target)
