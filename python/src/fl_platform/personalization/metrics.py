"""Personalization/fairness metric formulas.

Canonical definitions (mirrored, not re-derived, in Go — see
go/internal/application/fairness.go and docs/fairness-metrics.md — since
the Go control plane serves these numbers from live coordinator data
without a synchronous dependency on this Python module).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import median, pstdev


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty sequence")
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def _jain_fairness_index(values: list[float]) -> float | None:
    """Jain's fairness index: (sum x_i)^2 / (n * sum x_i^2), in (0, 1].

    Only mathematically valid for non-negative values (accuracy in [0,1]
    qualifies) and undefined when every value is exactly 0 — returns
    None rather than raising or returning a misleading 0/0 -> nan.
    """
    if not values:
        return None
    if any(value < 0 for value in values):
        return None
    sum_sq = sum(value * value for value in values)
    if sum_sq == 0:
        return None
    return (sum(values) ** 2) / (len(values) * sum_sq)


@dataclass(slots=True)
class PersonalizationMetrics:
    global_accuracy: float
    mean_personalized_accuracy: float
    median_personalized_accuracy: float
    p10_personalized_accuracy: float
    p90_personalized_accuracy: float
    worst_client_accuracy: float
    fairness_gap: float
    mean_improvement_over_global: float
    # Algorithm Expansion phase additions (additive — existing fields/
    # values above are unchanged, so the original foundation test's
    # exact expected values still hold).
    p25_personalized_accuracy: float = 0.0
    p75_personalized_accuracy: float = 0.0
    best_client_accuracy: float = 0.0
    std_dev_personalized_accuracy: float = 0.0
    median_improvement_over_global: float = 0.0
    fraction_clients_improved: float = 0.0
    coefficient_of_variation: float | None = None
    jain_fairness_index: float | None = None
    client_count: int = 0
    excluded_client_count: int = 0
    excluded_reasons: list[str] = field(default_factory=list)


def summarize_personalization(
    global_accuracy: float,
    personalized_accuracies: list[float],
) -> PersonalizationMetrics:
    if not personalized_accuracies:
        raise ValueError("personalized_accuracies must not be empty")
    ordered = sorted(personalized_accuracies)
    mean_accuracy = sum(ordered) / len(ordered)
    improvements = [value - global_accuracy for value in ordered]
    std_dev = pstdev(ordered) if len(ordered) > 1 else 0.0
    return PersonalizationMetrics(
        global_accuracy=global_accuracy,
        mean_personalized_accuracy=mean_accuracy,
        median_personalized_accuracy=median(ordered),
        p10_personalized_accuracy=_percentile(ordered, 0.10),
        p90_personalized_accuracy=_percentile(ordered, 0.90),
        worst_client_accuracy=min(ordered),
        fairness_gap=max(ordered) - min(ordered),
        mean_improvement_over_global=sum(improvements) / len(improvements),
        p25_personalized_accuracy=_percentile(ordered, 0.25),
        p75_personalized_accuracy=_percentile(ordered, 0.75),
        best_client_accuracy=max(ordered),
        std_dev_personalized_accuracy=std_dev,
        median_improvement_over_global=median(improvements),
        fraction_clients_improved=sum(1 for value in improvements if value > 0)
        / len(improvements),
        coefficient_of_variation=(std_dev / mean_accuracy)
        if mean_accuracy != 0
        else None,
        jain_fairness_index=_jain_fairness_index(ordered),
        client_count=len(ordered),
    )


@dataclass(slots=True)
class PerClientEvaluationRecord:
    """One client's evaluation outcome for one round — the unit
    `compute_aggregated_personalization_metrics` consumes. Excluded
    clients (see its docstring) still appear here with a reason, rather
    than being silently dropped before this point."""

    client_id: str
    global_local_accuracy: float
    personalized_local_accuracy: float | None
    sample_count: int
    excluded: bool = False
    excluded_reason: str = ""


def compute_aggregated_personalization_metrics(
    records: list[PerClientEvaluationRecord],
) -> PersonalizationMetrics:
    """Aggregates per-client records into fairness statistics.

    Handles, per docs/fairness-metrics.md:
    - empty client sets: raises ValueError (there is no meaningful
      "fairness gap" over zero clients; callers should check
      `records` themselves and render an empty state rather than call
      this).
    - missing personalized models (personalized_local_accuracy is None):
      excluded from the personalized-accuracy statistics, counted in
      `excluded_client_count`/`excluded_reasons`.
    - zero-sample clients: excluded the same way (sample_count == 0
      cannot support a meaningful accuracy).
    - non-finite metrics (NaN/inf): excluded the same way.
    - partially evaluated cohorts: the statistics are computed over
      whatever clients remain after exclusion; `client_count` reports
      how many that actually was, so callers can tell "5 of 5" from
      "5 of 20."
    """
    if not records:
        raise ValueError("records must not be empty")

    global_accuracies = [
        record.global_local_accuracy for record in records if not record.excluded
    ]
    if not global_accuracies:
        raise ValueError("no non-excluded records to compute global accuracy from")
    global_accuracy = sum(global_accuracies) / len(global_accuracies)

    included_personalized: list[float] = []
    excluded_reasons: list[str] = []
    excluded_count = 0
    for record in records:
        if record.excluded:
            excluded_count += 1
            if record.excluded_reason:
                excluded_reasons.append(f"{record.client_id}: {record.excluded_reason}")
            continue
        if record.sample_count <= 0:
            excluded_count += 1
            excluded_reasons.append(f"{record.client_id}: zero evaluation samples")
            continue
        if record.personalized_local_accuracy is None:
            excluded_count += 1
            excluded_reasons.append(f"{record.client_id}: no personalized model")
            continue
        value = record.personalized_local_accuracy
        if math.isnan(value) or math.isinf(value):
            excluded_count += 1
            excluded_reasons.append(f"{record.client_id}: non-finite accuracy")
            continue
        included_personalized.append(value)

    if not included_personalized:
        # Every client lacks a personalized model (e.g. FedAvg/FedProx/
        # SCAFFOLD runs) or every one was otherwise excluded — there is
        # no personalized-accuracy distribution to summarize. Report the
        # global accuracy alone with clearly-zeroed personalization
        # fields rather than raising, since "this run has no
        # personalization" is a valid, common case (see
        # docs/fairness-metrics.md), not an error.
        return PersonalizationMetrics(
            global_accuracy=global_accuracy,
            mean_personalized_accuracy=0.0,
            median_personalized_accuracy=0.0,
            p10_personalized_accuracy=0.0,
            p90_personalized_accuracy=0.0,
            worst_client_accuracy=0.0,
            fairness_gap=0.0,
            mean_improvement_over_global=0.0,
            client_count=0,
            excluded_client_count=excluded_count,
            excluded_reasons=excluded_reasons,
        )

    metrics = summarize_personalization(global_accuracy, included_personalized)
    metrics.excluded_client_count = excluded_count
    metrics.excluded_reasons = excluded_reasons
    return metrics
