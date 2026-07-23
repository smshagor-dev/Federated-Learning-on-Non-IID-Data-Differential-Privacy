from __future__ import annotations

from dataclasses import dataclass
from statistics import median


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty sequence")
    position = (len(sorted_values) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


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


def summarize_personalization(
    global_accuracy: float,
    personalized_accuracies: list[float],
) -> PersonalizationMetrics:
    if not personalized_accuracies:
        raise ValueError("personalized_accuracies must not be empty")
    ordered = sorted(personalized_accuracies)
    mean_accuracy = sum(ordered) / len(ordered)
    improvements = [value - global_accuracy for value in ordered]
    return PersonalizationMetrics(
        global_accuracy=global_accuracy,
        mean_personalized_accuracy=mean_accuracy,
        median_personalized_accuracy=median(ordered),
        p10_personalized_accuracy=_percentile(ordered, 0.10),
        p90_personalized_accuracy=_percentile(ordered, 0.90),
        worst_client_accuracy=min(ordered),
        fairness_gap=max(ordered) - min(ordered),
        mean_improvement_over_global=sum(improvements) / len(improvements),
    )
