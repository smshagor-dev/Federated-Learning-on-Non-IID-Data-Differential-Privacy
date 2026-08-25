"""Deterministic system-heterogeneity and edge-client modeling for v3."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class ClientSystemProfile:
    client_id: str
    compute_multiplier: float
    bandwidth_mbps: float
    latency_ms: float
    availability: float
    memory_mb: int
    cpu_cores: int

    def validate(self) -> None:
        if not self.client_id:
            raise ValueError("client_id must not be empty")
        if self.compute_multiplier <= 0.0 or not math.isfinite(self.compute_multiplier):
            raise ValueError("compute_multiplier must be finite and positive")
        if self.bandwidth_mbps <= 0.0 or not math.isfinite(self.bandwidth_mbps):
            raise ValueError("bandwidth_mbps must be finite and positive")
        if self.latency_ms < 0.0 or not math.isfinite(self.latency_ms):
            raise ValueError("latency_ms must be finite and non-negative")
        if not 0.0 <= self.availability <= 1.0:
            raise ValueError("availability must be in [0, 1]")
        if self.memory_mb <= 0 or self.cpu_cores <= 0:
            raise ValueError("memory_mb and cpu_cores must be positive")

    def estimated_round_seconds(
        self, *, baseline_training_seconds: float, payload_bytes: int
    ) -> float:
        self.validate()
        if baseline_training_seconds < 0.0 or payload_bytes < 0:
            raise ValueError("training time and payload size must be non-negative")
        training = baseline_training_seconds * self.compute_multiplier
        transfer = (payload_bytes * 8.0) / (self.bandwidth_mbps * 1_000_000.0)
        return training + transfer + self.latency_ms / 1000.0


@dataclass(frozen=True)
class EdgeRequirements:
    min_memory_mb: int = 256
    min_cpu_cores: int = 1
    max_round_seconds: float | None = None


def generate_profiles(count: int, *, seed: int) -> tuple[ClientSystemProfile, ...]:
    """Generate a reproducible heterogeneous client population."""
    if count <= 0:
        raise ValueError("count must be positive")
    rng = random.Random(seed)
    profiles: list[ClientSystemProfile] = []
    for index in range(count):
        profiles.append(
            ClientSystemProfile(
                client_id=f"client-{index:04d}",
                compute_multiplier=rng.uniform(0.5, 3.0),
                bandwidth_mbps=rng.uniform(1.0, 100.0),
                latency_ms=rng.uniform(5.0, 250.0),
                availability=rng.uniform(0.55, 1.0),
                memory_mb=rng.choice((256, 512, 1024, 2048, 4096)),
                cpu_cores=rng.choice((1, 2, 4, 8)),
            )
        )
    return tuple(profiles)


def eligible_for_edge_training(
    profile: ClientSystemProfile,
    requirements: EdgeRequirements,
    *,
    baseline_training_seconds: float = 0.0,
    payload_bytes: int = 0,
) -> bool:
    profile.validate()
    if profile.memory_mb < requirements.min_memory_mb:
        return False
    if profile.cpu_cores < requirements.min_cpu_cores:
        return False
    if requirements.max_round_seconds is not None:
        return (
            profile.estimated_round_seconds(
                baseline_training_seconds=baseline_training_seconds,
                payload_bytes=payload_bytes,
            )
            <= requirements.max_round_seconds
        )
    return True


__all__ = [
    "ClientSystemProfile",
    "EdgeRequirements",
    "eligible_for_edge_training",
    "generate_profiles",
]
