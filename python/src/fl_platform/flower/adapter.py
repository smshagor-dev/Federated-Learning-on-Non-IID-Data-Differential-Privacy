from __future__ import annotations

from dataclasses import dataclass

from fl_platform.execution import SchedulingConfig


@dataclass(slots=True)
class FlowerFederationPlan:
    client_count: int
    fraction_fit: float
    min_available_clients: int
    scheduling_mode: str


class FlowerSimulationAdapter:
    """Planning-only Flower adapter scaffold for simulation compatibility."""

    def build_plan(
        self,
        client_count: int,
        scheduling: SchedulingConfig,
    ) -> FlowerFederationPlan:
        if client_count <= 0:
            raise ValueError("client_count must be positive")
        fraction_fit = min(1.0, scheduling.target_clients / client_count)
        return FlowerFederationPlan(
            client_count=client_count,
            fraction_fit=fraction_fit,
            min_available_clients=scheduling.minimum_clients,
            scheduling_mode=scheduling.mode.value,
        )
