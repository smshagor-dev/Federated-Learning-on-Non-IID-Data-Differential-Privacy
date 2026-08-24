"""Statistical primitives for repeated federated-learning benchmarks.

The module uses only the Python standard library so result aggregation remains
lightweight and deterministic. It provides percentile-bootstrap confidence
intervals, matched-seed comparisons, Cohen's dz, paired sign-flip tests, and
Holm correction for multiple comparisons.
"""

from __future__ import annotations

import hashlib
import math
import random
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

DEFAULT_MINIMUM_REPLICATES = 5
DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_RANDOMIZATION_SAMPLES = 20_000


@dataclass(frozen=True, slots=True)
class MetricSummary:
    n: int
    mean: float
    sample_std: float
    median: float
    minimum: float
    maximum: float
    confidence: float
    ci_low: float
    ci_high: float
    interval_method: str


@dataclass(frozen=True, slots=True)
class PairedComparison:
    n: int
    baseline_name: str
    candidate_name: str
    mean_difference: float
    sample_std_difference: float
    cohen_dz: float
    win_rate: float
    confidence: float
    difference_ci_low: float
    difference_ci_high: float
    p_value: float
    p_value_method: str


def _validated_values(
    values: Iterable[float],
    *,
    minimum_replicates: int,
    label: str,
) -> tuple[float, ...]:
    normalized = tuple(float(value) for value in values)
    if minimum_replicates < 1:
        raise ValueError("minimum_replicates must be >= 1")
    if len(normalized) < minimum_replicates:
        raise ValueError(
            f"{label} requires at least {minimum_replicates} observations; "
            f"received {len(normalized)}"
        )
    if any(not math.isfinite(value) for value in normalized):
        raise ValueError(f"{label} contains a non-finite value")
    return normalized


def _validate_confidence(confidence: float) -> None:
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must lie in (0, 1)")


def _stable_seed(*parts: object) -> int:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False)


def _linear_quantile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute a quantile of an empty sequence")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must lie in [0, 1]")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = probability * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    fraction = position - lower
    return float(
        sorted_values[lower]
        + fraction * (sorted_values[upper] - sorted_values[lower])
    )


def bootstrap_mean_interval(
    values: Iterable[float],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = 0,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> tuple[float, float]:
    """Return a deterministic percentile-bootstrap interval for the mean."""
    _validate_confidence(confidence)
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be >= 100")
    normalized = _validated_values(
        values,
        minimum_replicates=minimum_replicates,
        label="bootstrap interval",
    )
    canonical_values = tuple(sorted(normalized))
    rng = random.Random(_stable_seed("bootstrap-mean", seed, *canonical_values))
    n = len(canonical_values)
    bootstrap_means: list[float] = []
    for _ in range(bootstrap_samples):
        total = 0.0
        for _ in range(n):
            total += canonical_values[rng.randrange(n)]
        bootstrap_means.append(total / n)
    bootstrap_means.sort()
    alpha = 1.0 - confidence
    return (
        _linear_quantile(bootstrap_means, alpha / 2.0),
        _linear_quantile(bootstrap_means, 1.0 - alpha / 2.0),
    )


def summarize_metric(
    values: Iterable[float],
    *,
    confidence: float = 0.95,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    seed: int = 0,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> MetricSummary:
    """Summarize one benchmark condition while preserving replicate variance."""
    normalized = _validated_values(
        values,
        minimum_replicates=minimum_replicates,
        label="metric summary",
    )
    ci_low, ci_high = bootstrap_mean_interval(
        normalized,
        confidence=confidence,
        bootstrap_samples=bootstrap_samples,
        seed=seed,
        minimum_replicates=minimum_replicates,
    )
    return MetricSummary(
        n=len(normalized),
        mean=float(statistics.fmean(normalized)),
        sample_std=float(statistics.stdev(normalized)) if len(normalized) > 1 else 0.0,
        median=float(statistics.median(normalized)),
        minimum=float(min(normalized)),
        maximum=float(max(normalized)),
        confidence=confidence,
        ci_low=ci_low,
        ci_high=ci_high,
        interval_method=f"percentile_bootstrap_{bootstrap_samples}",
    )


def _paired_differences(
    baseline: Mapping[int, float], candidate: Mapping[int, float]
) -> tuple[tuple[int, ...], tuple[float, ...]]:
    baseline_seeds = set(baseline)
    candidate_seeds = set(candidate)
    if baseline_seeds != candidate_seeds:
        raise ValueError(
            "paired comparison requires identical seed sets; "
            f"missing_from_candidate={sorted(baseline_seeds - candidate_seeds)}, "
            f"missing_from_baseline={sorted(candidate_seeds - baseline_seeds)}"
        )
    seeds = tuple(sorted(baseline_seeds))
    differences = tuple(
        float(candidate[seed]) - float(baseline[seed]) for seed in seeds
    )
    if any(not math.isfinite(value) for value in differences):
        raise ValueError("paired comparison contains a non-finite value")
    return seeds, differences


def _sign_flip_p_value(
    differences: Sequence[float],
    *,
    randomization_samples: int,
    seed: int,
) -> tuple[float, str]:
    observed = abs(statistics.fmean(differences))
    n = len(differences)
    tolerance = 1e-15
    if n <= 16:
        extreme = 0
        total = 1 << n
        for mask in range(total):
            signed_total = 0.0
            for index, difference in enumerate(differences):
                sign = -1.0 if (mask >> index) & 1 else 1.0
                signed_total += sign * difference
            if abs(signed_total / n) + tolerance >= observed:
                extreme += 1
        return extreme / total, "exact_paired_sign_flip"

    if randomization_samples < 1_000:
        raise ValueError("randomization_samples must be >= 1000")
    rng = random.Random(_stable_seed("paired-sign-flip", seed, *differences))
    extreme = 0
    for _ in range(randomization_samples):
        signed_total = sum(
            difference if rng.getrandbits(1) else -difference
            for difference in differences
        )
        if abs(signed_total / n) + tolerance >= observed:
            extreme += 1
    return (
        (extreme + 1.0) / (randomization_samples + 1.0),
        f"monte_carlo_paired_sign_flip_{randomization_samples}",
    )


def compare_paired_metrics(
    baseline: Mapping[int, float],
    candidate: Mapping[int, float],
    *,
    baseline_name: str,
    candidate_name: str,
    confidence: float = 0.95,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    randomization_samples: int = DEFAULT_RANDOMIZATION_SAMPLES,
    seed: int = 0,
    minimum_replicates: int = DEFAULT_MINIMUM_REPLICATES,
) -> PairedComparison:
    """Compare two algorithms on exactly matched seeds and conditions."""
    if not baseline_name.strip() or not candidate_name.strip():
        raise ValueError("baseline_name and candidate_name must be non-empty")
    _validate_confidence(confidence)
    seeds, differences = _paired_differences(baseline, candidate)
    _validated_values(
        differences,
        minimum_replicates=minimum_replicates,
        label="paired comparison",
    )
    difference_summary = summarize_metric(
        differences,
        confidence=confidence,
        bootstrap_samples=bootstrap_samples,
        seed=_stable_seed(seed, baseline_name, candidate_name, *seeds),
        minimum_replicates=minimum_replicates,
    )
    std_difference = difference_summary.sample_std
    if std_difference == 0.0:
        cohen_dz = (
            0.0
            if difference_summary.mean == 0.0
            else math.copysign(math.inf, difference_summary.mean)
        )
    else:
        cohen_dz = difference_summary.mean / std_difference
    p_value, method = _sign_flip_p_value(
        differences,
        randomization_samples=randomization_samples,
        seed=seed,
    )
    wins = sum(1 for difference in differences if difference > 0.0)
    ties = sum(1 for difference in differences if difference == 0.0)
    win_rate = (wins + 0.5 * ties) / len(differences)
    return PairedComparison(
        n=len(differences),
        baseline_name=baseline_name,
        candidate_name=candidate_name,
        mean_difference=difference_summary.mean,
        sample_std_difference=std_difference,
        cohen_dz=cohen_dz,
        win_rate=win_rate,
        confidence=confidence,
        difference_ci_low=difference_summary.ci_low,
        difference_ci_high=difference_summary.ci_high,
        p_value=p_value,
        p_value_method=method,
    )


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return Holm-Bonferroni adjusted p-values with monotonic correction."""
    if any(not 0.0 <= float(value) <= 1.0 for value in p_values.values()):
        raise ValueError("all p-values must lie in [0, 1]")
    ordered = sorted((float(value), key) for key, value in p_values.items())
    m = len(ordered)
    adjusted: dict[str, float] = {}
    running_max = 0.0
    for rank, (p_value, key) in enumerate(ordered):
        raw_adjusted = min(1.0, (m - rank) * p_value)
        running_max = max(running_max, raw_adjusted)
        adjusted[key] = running_max
    return adjusted
