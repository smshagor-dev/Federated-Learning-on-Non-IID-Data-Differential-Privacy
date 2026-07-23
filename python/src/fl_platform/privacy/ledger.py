from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from .config import PrivacyMode


@dataclass(slots=True)
class PrivacyLedgerEntry:
    round_id: int
    mode: PrivacyMode
    epsilon: float
    delta: float
    noise_multiplier: float
    clipping_bound: float


@dataclass(slots=True)
class PrivacyProjection:
    current_epsilon: float
    projected_next_epsilon: float
    budget_remaining: float | None


@dataclass(slots=True)
class PrivacyLedger:
    run_id: str
    entries: list[PrivacyLedgerEntry] = field(default_factory=list)

    def append(self, entry: PrivacyLedgerEntry) -> None:
        if self.entries and entry.round_id <= self.entries[-1].round_id:
            raise ValueError("privacy ledger round_id must be strictly increasing")
        self.entries.append(entry)

    def latest(self) -> PrivacyLedgerEntry | None:
        return self.entries[-1] if self.entries else None

    def separate_by_mode(self) -> dict[PrivacyMode, list[PrivacyLedgerEntry]]:
        grouped: dict[PrivacyMode, list[PrivacyLedgerEntry]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.mode, []).append(entry)
        return grouped

    def project_next(self, epsilon_budget: float | None = None) -> PrivacyProjection:
        latest = self.latest()
        if latest is None:
            return PrivacyProjection(0.0, 0.0, epsilon_budget)
        if len(self.entries) == 1:
            increment = latest.epsilon
        else:
            deltas = [
                current.epsilon - previous.epsilon
                for previous, current in zip(
                    self.entries[:-1], self.entries[1:], strict=True
                )
            ]
            increment = mean(deltas)
        projected = latest.epsilon + increment
        remaining = None if epsilon_budget is None else epsilon_budget - latest.epsilon
        return PrivacyProjection(
            current_epsilon=latest.epsilon,
            projected_next_epsilon=projected,
            budget_remaining=remaining,
        )
