"""Deterministic chaos/reliability primitives for worker-round validation.

This module is deliberately bounded to reproducible fault injection around the
Python worker execution shell. It does not claim distributed-process crash or
network-partition coverage; those remain release evidence requirements.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from enum import StrEnum

from fl_platform.workers import TrainingResult, TrainingTask, WorkerService


class ChaosFault(StrEnum):
    NONE = "none"
    DROP_BEFORE_TRAIN = "drop_before_train"
    TRANSIENT_CRASH = "transient_crash"
    PERMANENT_CRASH = "permanent_crash"
    RESULT_DELAY = "result_delay"
    DUPLICATE_REPLAY = "duplicate_replay"


@dataclass(frozen=True)
class ChaosProfile:
    drop_probability: float = 0.05
    transient_crash_probability: float = 0.05
    permanent_crash_probability: float = 0.02
    delay_probability: float = 0.08
    duplicate_replay_probability: float = 0.05

    def validate(self) -> None:
        probabilities = (
            self.drop_probability,
            self.transient_crash_probability,
            self.permanent_crash_probability,
            self.delay_probability,
            self.duplicate_replay_probability,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in probabilities):
            raise ValueError("chaos probabilities must be finite and non-negative")
        if sum(probabilities) > 1.0:
            raise ValueError("chaos probabilities must sum to at most 1")


@dataclass(frozen=True)
class ChaosDecision:
    client_id: str
    fault: ChaosFault


@dataclass(frozen=True)
class ChaosRoundResult:
    selected_clients: int
    results: tuple[TrainingResult, ...]
    decisions: tuple[ChaosDecision, ...]
    dropped_clients: tuple[str, ...]
    failed_clients: tuple[str, ...]
    delayed_clients: tuple[str, ...]
    retry_attempts: int
    replay_rejections: int

    @property
    def recovered_clients(self) -> int:
        return len(self.results)

    @property
    def recovery_rate(self) -> float:
        if self.selected_clients == 0:
            return 0.0
        return self.recovered_clients / self.selected_clients

    def validate_invariants(self) -> None:
        result_clients = tuple(result.client_id for result in self.results)
        if len(set(result_clients)) != len(result_clients):
            raise RuntimeError("chaos round accepted duplicate client results")
        if set(result_clients) & set(self.dropped_clients):
            raise RuntimeError("dropped client produced a result")
        if set(result_clients) & set(self.failed_clients):
            raise RuntimeError("failed client produced a result")
        accounted = (
            len(result_clients)
            + len(self.dropped_clients)
            + len(self.failed_clients)
        )
        if accounted != self.selected_clients:
            raise RuntimeError("chaos round client accounting mismatch")
        if not set(self.delayed_clients) <= set(result_clients):
            raise RuntimeError("delayed client is missing from recovered results")
        if self.retry_attempts < 0 or self.replay_rejections < 0:
            raise RuntimeError("chaos counters must be non-negative")


class DeterministicChaosPlan:
    """Map seed/run/round/client identity to a stable fault decision."""

    def __init__(self, *, seed: int, profile: ChaosProfile | None = None) -> None:
        resolved_profile = profile or ChaosProfile()
        resolved_profile.validate()
        self._seed = seed
        self._profile = resolved_profile

    def decide(self, task: TrainingTask) -> ChaosDecision:
        digest = hashlib.sha256()
        digest.update(b"fl-platform-chaos-v1\x00")
        digest.update(str(self._seed).encode("ascii"))
        digest.update(b"\x00")
        digest.update(task.run_id.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(task.round_id).encode("ascii"))
        digest.update(b"\x00")
        digest.update(task.client_id.encode("utf-8"))
        draw = int.from_bytes(digest.digest()[:8], "big") / float(1 << 64)

        thresholds = (
            (self._profile.drop_probability, ChaosFault.DROP_BEFORE_TRAIN),
            (
                self._profile.transient_crash_probability,
                ChaosFault.TRANSIENT_CRASH,
            ),
            (
                self._profile.permanent_crash_probability,
                ChaosFault.PERMANENT_CRASH,
            ),
            (self._profile.delay_probability, ChaosFault.RESULT_DELAY),
            (
                self._profile.duplicate_replay_probability,
                ChaosFault.DUPLICATE_REPLAY,
            ),
        )
        cumulative = 0.0
        for probability, fault in thresholds:
            cumulative += probability
            if draw < cumulative:
                return ChaosDecision(task.client_id, fault)
        return ChaosDecision(task.client_id, ChaosFault.NONE)


class ChaosRoundExecutor:
    """Execute a round while injecting deterministic worker faults."""

    def __init__(
        self,
        service: WorkerService,
        plan: DeterministicChaosPlan,
        *,
        max_retries: int = 1,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self._service = service
        self._plan = plan
        self._max_retries = max_retries

    def run(self, tasks: tuple[TrainingTask, ...]) -> ChaosRoundResult:
        if len({task.client_id for task in tasks}) != len(tasks):
            raise ValueError("chaos round tasks must have unique client ids")

        decisions: list[ChaosDecision] = []
        on_time: list[TrainingResult] = []
        delayed: list[TrainingResult] = []
        dropped: list[str] = []
        failed: list[str] = []
        retry_attempts = 0
        replay_rejections = 0

        for task in tasks:
            decision = self._plan.decide(task)
            decisions.append(decision)
            if decision.fault == ChaosFault.DROP_BEFORE_TRAIN:
                dropped.append(task.client_id)
                continue
            if decision.fault == ChaosFault.PERMANENT_CRASH:
                retry_attempts += self._max_retries
                failed.append(task.client_id)
                continue
            if decision.fault == ChaosFault.TRANSIENT_CRASH:
                if self._max_retries == 0:
                    failed.append(task.client_id)
                    continue
                retry_attempts += 1

            result = self._service.handle_task(task)
            if result.client_id != task.client_id:
                raise RuntimeError("worker result client identity mismatch")
            if decision.fault == ChaosFault.RESULT_DELAY:
                delayed.append(result)
            else:
                on_time.append(result)
            if decision.fault == ChaosFault.DUPLICATE_REPLAY:
                replay_rejections += 1

        result = ChaosRoundResult(
            selected_clients=len(tasks),
            results=(*on_time, *delayed),
            decisions=tuple(decisions),
            dropped_clients=tuple(dropped),
            failed_clients=tuple(failed),
            delayed_clients=tuple(item.client_id for item in delayed),
            retry_attempts=retry_attempts,
            replay_rejections=replay_rejections,
        )
        result.validate_invariants()
        return result


__all__ = [
    "ChaosDecision",
    "ChaosFault",
    "ChaosProfile",
    "ChaosRoundExecutor",
    "ChaosRoundResult",
    "DeterministicChaosPlan",
]
