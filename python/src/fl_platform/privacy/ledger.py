"""Privacy ledger — mirrors proto/privacy/privacy.proto's
SampleLevelLedgerEntry/UserLevelLedgerEntry/AdaptiveClippingLedgerEntry.

Per the Critical Privacy Rule (docs/privacy-mathematics.md), each entry
type carries exactly one mechanism's epsilon/delta. There is no
"combined" entry type and no function anywhere in this module that sums
epsilon across entry types — that is a deliberate omission, not an
oversight.

Authority split (see docs/privacy-ledger.md): Python workers append
SampleLevelLedgerEntry (this module, used worker-side); the C++
coordinator appends UserLevelLedgerEntry/AdaptiveClippingLedgerEntry
(mirrored in cpp/coordinator's own ledger, not this Python module); Go
only persists/exposes what it receives from the coordinator and must
never recompute epsilon itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class SampleLevelLedgerEntry:
    run_id: str
    round_id: int
    client_id: str
    epsilon: float
    delta: float
    noise_multiplier: float
    sample_rate: float
    steps: int
    accountant: str
    recorded_at: str = ""
    entry_id: str = ""


@dataclass(slots=True, frozen=True)
class UserLevelLedgerEntry:
    run_id: str
    round_id: int
    epsilon: float
    delta: float
    noise_multiplier: float
    clipping_bound: float
    num_clients: int
    accountant: str
    recorded_at: str = ""
    entry_id: str = ""


@dataclass(slots=True, frozen=True)
class AdaptiveClippingLedgerEntry:
    run_id: str
    round_id: int
    epsilon: float
    delta: float
    clip_value: float
    observed_over_threshold_fraction: float
    count_noise_multiplier: float
    recorded_at: str = ""
    entry_id: str = ""


@dataclass(slots=True)
class MechanismProjection:
    """Budget projection for exactly one mechanism. Never combined with
    another mechanism's projection — see PrivacyProjection below, which
    keeps three of these entirely separate."""

    current_epsilon: float
    projected_next_epsilon: float
    budget_remaining: float | None


@dataclass(slots=True)
class PrivacyProjection:
    sample_level: MechanismProjection | None = None
    user_level: MechanismProjection | None = None
    clipping: MechanismProjection | None = None


def _project(
    entries: list[float], epsilon_budget: float | None
) -> MechanismProjection | None:
    if not entries:
        return None
    latest = entries[-1]
    if len(entries) == 1:
        increment = latest
    else:
        increments = [
            current - previous
            for previous, current in zip(entries[:-1], entries[1:], strict=True)
        ]
        increment = sum(increments) / len(increments)
    projected = latest + increment
    remaining = None if not epsilon_budget else epsilon_budget - latest
    return MechanismProjection(
        current_epsilon=latest,
        projected_next_epsilon=projected,
        budget_remaining=remaining,
    )


@dataclass(slots=True)
class PrivacyLedger:
    """Sample-level entries only (this class is worker-facing — see
    module docstring for the authority split). The C++ coordinator keeps
    its own equivalent structure for user-level/clipping entries."""

    run_id: str
    sample_level_entries: list[SampleLevelLedgerEntry] = field(default_factory=list)

    def append_sample_level(self, entry: SampleLevelLedgerEntry) -> None:
        if entry.run_id != self.run_id:
            raise ValueError(
                f"entry run_id '{entry.run_id}' does not match ledger run_id "
                f"'{self.run_id}'"
            )
        if (
            self.sample_level_entries
            and entry.round_id < self.sample_level_entries[-1].round_id
        ):
            raise ValueError("privacy ledger round_id must be non-decreasing")
        self.sample_level_entries.append(entry)

    def latest_sample_level(self) -> SampleLevelLedgerEntry | None:
        return self.sample_level_entries[-1] if self.sample_level_entries else None

    def project(self, sample_epsilon_budget: float | None = None) -> PrivacyProjection:
        sample_epsilons = [e.epsilon for e in self.sample_level_entries]
        return PrivacyProjection(
            sample_level=_project(sample_epsilons, sample_epsilon_budget)
        )
