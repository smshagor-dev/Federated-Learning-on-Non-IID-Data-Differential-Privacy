"""Execution admission and metrics for realistic v3 system heterogeneity."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from fl_platform.execution.multiprocessing_orchestrator import TaskAdmissionDecision
from fl_platform.v3.heterogeneity import ClientSystemProfile, EdgeRequirements
from fl_platform.workers import TrainingTask


@dataclass(frozen=True)
class HeterogeneityEvaluation:
    client_id: str
    admitted: bool
    reason: str
    estimated_round_seconds: float


@dataclass(frozen=True)
class HeterogeneityRoundMetrics:
    selected_clients: int
    admitted_clients: int
    unavailable_clients: int
    resource_ineligible_clients: int
    deadline_miss_clients: int
    mean_estimated_round_seconds: float
    max_estimated_round_seconds: float
    estimated_communication_bytes: int


class HeterogeneityAdmissionPolicy:
    """Deterministic pre-training admission for heterogeneous clients."""

    def __init__(
        self,
        profiles: tuple[ClientSystemProfile, ...],
        *,
        requirements: EdgeRequirements | None = None,
        baseline_training_seconds: float,
        payload_bytes: int,
        round_deadline_seconds: float | None = None,
        seed: int = 0,
    ) -> None:
        if not profiles:
            raise ValueError("at least one client system profile is required")
        if baseline_training_seconds < 0.0 or not math.isfinite(
            baseline_training_seconds
        ):
            raise ValueError(
                "baseline_training_seconds must be finite and non-negative"
            )
        if payload_bytes < 0:
            raise ValueError("payload_bytes must be non-negative")
        if round_deadline_seconds is not None and (
            round_deadline_seconds < 0.0 or not math.isfinite(round_deadline_seconds)
        ):
            raise ValueError("round_deadline_seconds must be finite and non-negative")
        profile_map: dict[str, ClientSystemProfile] = {}
        for profile in profiles:
            profile.validate()
            if profile.client_id in profile_map:
                raise ValueError(f"duplicate client profile: {profile.client_id}")
            profile_map[profile.client_id] = profile
        self._profiles = profile_map
        self._requirements = requirements or EdgeRequirements()
        self._baseline_training_seconds = baseline_training_seconds
        self._payload_bytes = payload_bytes
        self._round_deadline_seconds = round_deadline_seconds
        self._seed = seed

    def _availability_draw(self, task: TrainingTask) -> float:
        digest = hashlib.sha256()
        digest.update(b"fl-platform-heterogeneity-v1\x00")
        digest.update(str(self._seed).encode("ascii"))
        digest.update(b"\x00")
        digest.update(str(task.round_id).encode("ascii"))
        digest.update(b"\x00")
        digest.update(task.client_id.encode("utf-8"))
        value = int.from_bytes(digest.digest()[:8], "big")
        return value / float(1 << 64)

    def evaluate_detail(self, task: TrainingTask) -> HeterogeneityEvaluation:
        profile = self._profiles.get(task.client_id)
        if profile is None:
            return HeterogeneityEvaluation(
                client_id=task.client_id,
                admitted=False,
                reason="missing_system_profile",
                estimated_round_seconds=0.0,
            )
        estimated = profile.estimated_round_seconds(
            baseline_training_seconds=self._baseline_training_seconds,
            payload_bytes=self._payload_bytes,
        )
        if profile.memory_mb < self._requirements.min_memory_mb or (
            profile.cpu_cores < self._requirements.min_cpu_cores
        ):
            return HeterogeneityEvaluation(
                client_id=task.client_id,
                admitted=False,
                reason="resource_ineligible",
                estimated_round_seconds=estimated,
            )
        if self._requirements.max_round_seconds is not None and (
            estimated > self._requirements.max_round_seconds
        ):
            return HeterogeneityEvaluation(
                client_id=task.client_id,
                admitted=False,
                reason="resource_deadline_exceeded",
                estimated_round_seconds=estimated,
            )
        if self._round_deadline_seconds is not None and (
            estimated > self._round_deadline_seconds
        ):
            return HeterogeneityEvaluation(
                client_id=task.client_id,
                admitted=False,
                reason="round_deadline_exceeded",
                estimated_round_seconds=estimated,
            )
        if self._availability_draw(task) >= profile.availability:
            return HeterogeneityEvaluation(
                client_id=task.client_id,
                admitted=False,
                reason="client_unavailable",
                estimated_round_seconds=estimated,
            )
        return HeterogeneityEvaluation(
            client_id=task.client_id,
            admitted=True,
            reason="",
            estimated_round_seconds=estimated,
        )

    def evaluate(self, task: TrainingTask) -> TaskAdmissionDecision:
        detail = self.evaluate_detail(task)
        return TaskAdmissionDecision(admitted=detail.admitted, reason=detail.reason)

    def summarize(
        self,
        tasks: tuple[TrainingTask, ...],
    ) -> HeterogeneityRoundMetrics:
        evaluations = tuple(self.evaluate_detail(task) for task in tasks)
        admitted = tuple(item for item in evaluations if item.admitted)
        estimates = tuple(item.estimated_round_seconds for item in admitted)
        return HeterogeneityRoundMetrics(
            selected_clients=len(tasks),
            admitted_clients=len(admitted),
            unavailable_clients=sum(
                item.reason == "client_unavailable" for item in evaluations
            ),
            resource_ineligible_clients=sum(
                item.reason in {"resource_ineligible", "resource_deadline_exceeded"}
                for item in evaluations
            ),
            deadline_miss_clients=sum(
                item.reason == "round_deadline_exceeded" for item in evaluations
            ),
            mean_estimated_round_seconds=(
                sum(estimates) / len(estimates) if estimates else 0.0
            ),
            max_estimated_round_seconds=max(estimates, default=0.0),
            estimated_communication_bytes=len(admitted) * self._payload_bytes * 2,
        )


__all__ = [
    "HeterogeneityAdmissionPolicy",
    "HeterogeneityEvaluation",
    "HeterogeneityRoundMetrics",
]
