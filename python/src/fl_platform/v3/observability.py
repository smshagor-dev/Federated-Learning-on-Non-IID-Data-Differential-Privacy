"""Machine-readable observability and benchmark records for v3."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RoundMetrics:
    round_id: int
    cohort_size: int
    accepted_updates: int
    dropped_clients: int
    round_latency_seconds: float
    aggregation_seconds: float
    upload_bytes: int
    download_bytes: int
    privacy_epsilon: float | None = None

    def validate(self) -> None:
        if self.round_id < 0:
            raise ValueError("round_id must be non-negative")
        counts = (self.cohort_size, self.accepted_updates, self.dropped_clients)
        if any(value < 0 for value in counts):
            raise ValueError("client counters must be non-negative")
        durations = (self.round_latency_seconds, self.aggregation_seconds)
        if any(value < 0.0 or not math.isfinite(value) for value in durations):
            raise ValueError("durations must be finite and non-negative")
        if self.upload_bytes < 0 or self.download_bytes < 0:
            raise ValueError("communication counters must be non-negative")
        if self.privacy_epsilon is not None and (
            self.privacy_epsilon < 0.0 or not math.isfinite(self.privacy_epsilon)
        ):
            raise ValueError("privacy_epsilon must be finite and non-negative")

    @property
    def communication_bytes(self) -> int:
        return self.upload_bytes + self.download_bytes

    def to_record(self) -> dict[str, Any]:
        self.validate()
        record = asdict(self)
        record["communication_bytes"] = self.communication_bytes
        return record


@dataclass(frozen=True)
class RobustnessMetrics:
    attack_name: str
    malicious_clients: int
    attack_success_rate: float
    clean_accuracy: float
    attacked_accuracy: float

    def validate(self) -> None:
        if not self.attack_name:
            raise ValueError("attack_name must not be empty")
        if self.malicious_clients < 0:
            raise ValueError("malicious_clients must be non-negative")
        for name, value in (
            ("attack_success_rate", self.attack_success_rate),
            ("clean_accuracy", self.clean_accuracy),
            ("attacked_accuracy", self.attacked_accuracy),
        ):
            if not 0.0 <= value <= 1.0 or not math.isfinite(value):
                raise ValueError(f"{name} must be finite and in [0, 1]")

    @property
    def accuracy_degradation(self) -> float:
        return self.clean_accuracy - self.attacked_accuracy


__all__ = ["RobustnessMetrics", "RoundMetrics"]
