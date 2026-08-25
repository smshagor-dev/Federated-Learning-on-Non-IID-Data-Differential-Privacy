"""Privacy validation metrics and restart-consistency checks for v3.

These helpers evaluate empirical attack signals and accounting continuity. They
do not convert attack results into a formal differential-privacy guarantee;
formal epsilon/delta accounting remains owned by the existing privacy modules.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from fl_platform.privacy.ledger import SampleLevelLedgerEntry

Vector = tuple[float, ...]


@dataclass(frozen=True)
class MembershipInferenceResult:
    auc: float
    advantage: float
    member_count: int
    nonmember_count: int

    def validate(self) -> None:
        if not 0.0 <= self.auc <= 1.0 or not math.isfinite(self.auc):
            raise ValueError("membership-inference AUC must be finite and in [0, 1]")
        if not 0.0 <= self.advantage <= 1.0 or not math.isfinite(self.advantage):
            raise ValueError("membership advantage must be finite and in [0, 1]")
        if self.member_count <= 0 or self.nonmember_count <= 0:
            raise ValueError("membership groups must be non-empty")


@dataclass(frozen=True)
class GradientLeakageResult:
    cosine_similarity: float
    normalized_l2_error: float

    def validate(self) -> None:
        if not -1.0 <= self.cosine_similarity <= 1.0:
            raise ValueError("cosine_similarity must be in [-1, 1]")
        if not math.isfinite(self.cosine_similarity):
            raise ValueError("cosine_similarity must be finite")
        if self.normalized_l2_error < 0.0 or not math.isfinite(
            self.normalized_l2_error
        ):
            raise ValueError("normalized_l2_error must be finite and non-negative")


@dataclass(frozen=True)
class PrivacyUtilityPoint:
    epsilon: float
    delta: float
    utility: float
    attack_auc: float | None = None

    def validate(self) -> None:
        if self.epsilon < 0.0 or not math.isfinite(self.epsilon):
            raise ValueError("epsilon must be finite and non-negative")
        if not 0.0 <= self.delta < 1.0 or not math.isfinite(self.delta):
            raise ValueError("delta must be finite and in [0, 1)")
        if not 0.0 <= self.utility <= 1.0 or not math.isfinite(self.utility):
            raise ValueError("utility must be finite and in [0, 1]")
        if self.attack_auc is not None and (
            not 0.0 <= self.attack_auc <= 1.0 or not math.isfinite(self.attack_auc)
        ):
            raise ValueError("attack_auc must be finite and in [0, 1]")


@dataclass(frozen=True)
class LedgerResumeValidation:
    valid: bool
    entries_before_restart: int
    entries_after_resume: int
    latest_epsilon: float | None
    reason: str | None = None


@dataclass(frozen=True)
class PrivacyValidationReport:
    membership_inference: MembershipInferenceResult | None = None
    gradient_leakage: GradientLeakageResult | None = None
    utility_curve: tuple[PrivacyUtilityPoint, ...] = ()
    ledger_resume: LedgerResumeValidation | None = None

    def validate(self) -> None:
        if self.membership_inference is not None:
            self.membership_inference.validate()
        if self.gradient_leakage is not None:
            self.gradient_leakage.validate()
        for point in self.utility_curve:
            point.validate()
        if self.ledger_resume is not None and not self.ledger_resume.valid:
            raise ValueError(
                "privacy ledger resume validation failed: "
                + (self.ledger_resume.reason or "unknown reason")
            )

    def to_record(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)


def _finite_scores(values: Sequence[float], *, name: str) -> tuple[float, ...]:
    scores = tuple(float(value) for value in values)
    if not scores:
        raise ValueError(f"{name} must not be empty")
    if not all(math.isfinite(value) for value in scores):
        raise ValueError(f"{name} must contain only finite values")
    return scores


def membership_inference_auc(
    member_scores: Sequence[float],
    nonmember_scores: Sequence[float],
    *,
    higher_means_member: bool = True,
) -> MembershipInferenceResult:
    """Compute exact empirical ROC AUC using pairwise ranking.

    Scores may be confidence, negative loss, or another scalar attack signal.
    Ties contribute one half, matching the Mann-Whitney interpretation of AUC.
    """
    members = _finite_scores(member_scores, name="member_scores")
    nonmembers = _finite_scores(nonmember_scores, name="nonmember_scores")
    wins = 0.0
    for member in members:
        for nonmember in nonmembers:
            if member == nonmember:
                wins += 0.5
            elif (member > nonmember) == higher_means_member:
                wins += 1.0
    auc = wins / (len(members) * len(nonmembers))
    advantage = abs(2.0 * auc - 1.0)
    return MembershipInferenceResult(
        auc=auc,
        advantage=advantage,
        member_count=len(members),
        nonmember_count=len(nonmembers),
    )


def gradient_leakage_similarity(
    original: Sequence[float], reconstructed: Sequence[float]
) -> GradientLeakageResult:
    """Measure similarity between a reference and reconstructed gradient."""
    left = _finite_scores(original, name="original")
    right = _finite_scores(reconstructed, name="reconstructed")
    if len(left) != len(right):
        raise ValueError("gradient vectors must have the same dimension")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise ValueError("gradient vectors must have non-zero norm")
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    cosine = max(-1.0, min(1.0, dot / (left_norm * right_norm)))
    error = (
        math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))
        / left_norm
    )
    return GradientLeakageResult(cosine, error)


def validate_sample_ledger_resume(
    before_restart: Sequence[SampleLevelLedgerEntry],
    after_resume: Sequence[SampleLevelLedgerEntry],
) -> LedgerResumeValidation:
    """Validate that a resumed ledger preserves its exact prefix and monotonicity."""
    before = tuple(before_restart)
    after = tuple(after_resume)
    if len(after) < len(before):
        return LedgerResumeValidation(
            False,
            len(before),
            len(after),
            after[-1].epsilon if after else None,
            "resumed ledger lost pre-restart entries",
        )
    if after[: len(before)] != before:
        return LedgerResumeValidation(
            False,
            len(before),
            len(after),
            after[-1].epsilon if after else None,
            "resumed ledger prefix differs from persisted ledger",
        )
    if not after:
        return LedgerResumeValidation(True, 0, 0, None)

    run_id = after[0].run_id
    previous_round = -1
    previous_epsilon = -1.0
    entry_ids: set[str] = set()
    for entry in after:
        if entry.run_id != run_id:
            return LedgerResumeValidation(
                False,
                len(before),
                len(after),
                entry.epsilon,
                "run_id changed after resume",
            )
        if entry.round_id < previous_round:
            return LedgerResumeValidation(
                False,
                len(before),
                len(after),
                entry.epsilon,
                "round_id regressed after resume",
            )
        if entry.epsilon + 1e-12 < previous_epsilon:
            return LedgerResumeValidation(
                False,
                len(before),
                len(after),
                entry.epsilon,
                "epsilon regressed after resume",
            )
        if entry.entry_id:
            if entry.entry_id in entry_ids:
                return LedgerResumeValidation(
                    False,
                    len(before),
                    len(after),
                    entry.epsilon,
                    "duplicate privacy ledger entry_id",
                )
            entry_ids.add(entry.entry_id)
        previous_round = entry.round_id
        previous_epsilon = entry.epsilon
    return LedgerResumeValidation(
        True,
        len(before),
        len(after),
        after[-1].epsilon,
    )


def build_privacy_utility_curve(
    points: Sequence[PrivacyUtilityPoint],
) -> tuple[PrivacyUtilityPoint, ...]:
    """Validate and order privacy/utility measurements by epsilon."""
    normalized = tuple(points)
    if not normalized:
        raise ValueError("privacy utility curve must contain at least one point")
    for point in normalized:
        point.validate()
    return tuple(sorted(normalized, key=lambda point: (point.epsilon, point.delta)))


__all__ = [
    "GradientLeakageResult",
    "LedgerResumeValidation",
    "MembershipInferenceResult",
    "PrivacyUtilityPoint",
    "PrivacyValidationReport",
    "build_privacy_utility_curve",
    "gradient_leakage_similarity",
    "membership_inference_auc",
    "validate_sample_ledger_resume",
]
