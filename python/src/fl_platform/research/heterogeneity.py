"""Realized heterogeneity metrics for federated dataset partitions.

Partition configuration (for example Dirichlet alpha) describes how a split
was generated, but it does not uniquely describe the split that was actually
realized. This module derives a reproducible vector of label and quantity
heterogeneity metrics from a partition manifest's real client histograms.

No single scalar "heterogeneity score" is produced: different forms of
heterogeneity are scientifically distinct and should remain visible.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class HeterogeneityVector:
    client_count: int
    total_samples: int
    label_count: int
    mean_client_samples: float
    sample_count_std: float
    quantity_coefficient_of_variation: float
    minimum_client_samples: int
    maximum_client_samples: int
    mean_normalized_label_entropy: float
    minimum_normalized_label_entropy: float
    mean_js_divergence_to_global: float
    maximum_js_divergence_to_global: float
    mean_class_coverage: float
    minimum_class_coverage: float
    mean_effective_label_count: float
    fingerprint_sha256: str


def _normalize_histogram(
    histogram: Mapping[int, int], *, labels: tuple[int, ...]
) -> tuple[float, ...]:
    counts = tuple(float(histogram.get(label, 0)) for label in labels)
    if any(count < 0.0 for count in counts):
        raise ValueError("label counts must be non-negative")
    total = sum(counts)
    if total <= 0.0:
        return tuple(0.0 for _ in labels)
    return tuple(count / total for count in counts)


def _entropy(probabilities: tuple[float, ...]) -> float:
    return -sum(value * math.log2(value) for value in probabilities if value > 0.0)


def _kl_divergence(
    p: tuple[float, ...], q: tuple[float, ...]
) -> float:
    total = 0.0
    for p_value, q_value in zip(p, q, strict=True):
        if p_value <= 0.0:
            continue
        if q_value <= 0.0:
            raise ValueError("KL reference distribution has zero mass where p is positive")
        total += p_value * math.log2(p_value / q_value)
    return total


def _js_divergence(
    p: tuple[float, ...], q: tuple[float, ...]
) -> float:
    mixture = tuple(
        0.5 * (p_value + q_value)
        for p_value, q_value in zip(p, q, strict=True)
    )
    return 0.5 * _kl_divergence(p, mixture) + 0.5 * _kl_divergence(q, mixture)


def _canonical_histograms(
    client_label_histograms: Mapping[str, Mapping[int, int]],
) -> dict[str, dict[int, int]]:
    canonical: dict[str, dict[int, int]] = {}
    for client_id in sorted(client_label_histograms):
        if not str(client_id).strip():
            raise ValueError("client identifiers must be non-empty")
        histogram = {
            int(label): int(count)
            for label, count in sorted(client_label_histograms[client_id].items())
        }
        if any(count < 0 for count in histogram.values()):
            raise ValueError(f"client {client_id!r} contains a negative label count")
        canonical[str(client_id)] = histogram
    return canonical


def compute_heterogeneity_vector(
    client_label_histograms: Mapping[str, Mapping[int, int]],
) -> HeterogeneityVector:
    """Measure the realized label/quantity heterogeneity of a partition."""
    canonical = _canonical_histograms(client_label_histograms)
    if not canonical:
        raise ValueError("at least one client histogram is required")

    label_space = tuple(
        sorted({label for histogram in canonical.values() for label in histogram})
    )
    if not label_space:
        raise ValueError("at least one label must be present in the partition")

    sample_counts = {
        client_id: sum(histogram.values())
        for client_id, histogram in canonical.items()
    }
    if any(count <= 0 for count in sample_counts.values()):
        raise ValueError("every client must have at least one sample")

    global_histogram = {label: 0 for label in label_space}
    for histogram in canonical.values():
        for label, count in histogram.items():
            global_histogram[label] += count
    global_probabilities = _normalize_histogram(global_histogram, labels=label_space)

    max_entropy = math.log2(len(label_space)) if len(label_space) > 1 else 1.0
    entropies: list[float] = []
    divergences: list[float] = []
    coverages: list[float] = []
    effective_labels: list[float] = []
    for histogram in canonical.values():
        probabilities = _normalize_histogram(histogram, labels=label_space)
        raw_entropy = _entropy(probabilities)
        normalized_entropy = raw_entropy / max_entropy if len(label_space) > 1 else 1.0
        entropies.append(normalized_entropy)
        divergences.append(_js_divergence(probabilities, global_probabilities))
        active_labels = sum(1 for probability in probabilities if probability > 0.0)
        coverages.append(active_labels / len(label_space))
        effective_labels.append(2.0**raw_entropy)

    counts = tuple(sample_counts.values())
    mean_samples = statistics.fmean(counts)
    count_std = statistics.pstdev(counts) if len(counts) > 1 else 0.0
    quantity_cv = count_std / mean_samples if mean_samples > 0.0 else 0.0

    fingerprint_payload = {
        "client_label_histograms": canonical,
        "global_label_histogram": global_histogram,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            fingerprint_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return HeterogeneityVector(
        client_count=len(canonical),
        total_samples=sum(counts),
        label_count=len(label_space),
        mean_client_samples=float(mean_samples),
        sample_count_std=float(count_std),
        quantity_coefficient_of_variation=float(quantity_cv),
        minimum_client_samples=min(counts),
        maximum_client_samples=max(counts),
        mean_normalized_label_entropy=float(statistics.fmean(entropies)),
        minimum_normalized_label_entropy=float(min(entropies)),
        mean_js_divergence_to_global=float(statistics.fmean(divergences)),
        maximum_js_divergence_to_global=float(max(divergences)),
        mean_class_coverage=float(statistics.fmean(coverages)),
        minimum_class_coverage=float(min(coverages)),
        mean_effective_label_count=float(statistics.fmean(effective_labels)),
        fingerprint_sha256=fingerprint,
    )


def heterogeneity_vector_dict(vector: HeterogeneityVector) -> dict[str, object]:
    """Return a JSON-ready representation with a stable field order."""
    return dict(asdict(vector))
