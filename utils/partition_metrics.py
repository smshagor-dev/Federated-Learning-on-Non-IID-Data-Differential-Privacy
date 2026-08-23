"""Partition provenance and realized heterogeneity metrics for the root runtime."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
from typing import Mapping

import numpy as np
import torch


def _targets(dataset: torch.utils.data.Dataset) -> np.ndarray:
    values = dataset.targets
    if isinstance(values, torch.Tensor):
        values = values.numpy()
    return np.asarray(values, dtype=np.int64)


def client_label_histograms(
    client_dict: Mapping[int, np.ndarray], dataset: torch.utils.data.Dataset
) -> dict[str, dict[int, int]]:
    labels = _targets(dataset)
    result: dict[str, dict[int, int]] = {}
    for client_id, indices in sorted(client_dict.items()):
        client_labels = labels[np.asarray(indices, dtype=np.int64)]
        unique, counts = np.unique(client_labels, return_counts=True)
        result[f"client-{client_id}"] = {
            int(label): int(count)
            for label, count in zip(unique, counts, strict=True)
        }
    return result


def _probabilities(histogram: Mapping[int, int], labels: tuple[int, ...]) -> tuple[float, ...]:
    counts = tuple(float(histogram.get(label, 0)) for label in labels)
    total = sum(counts)
    if total <= 0.0:
        return tuple(0.0 for _ in labels)
    return tuple(count / total for count in counts)


def _entropy(probabilities: tuple[float, ...]) -> float:
    return -sum(value * math.log2(value) for value in probabilities if value > 0.0)


def _js_divergence(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    mixture = tuple(0.5 * (a + b) for a, b in zip(p, q, strict=True))

    def kl(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        return sum(
            av * math.log2(av / bv)
            for av, bv in zip(a, b, strict=True)
            if av > 0.0
        )

    return 0.5 * kl(p, mixture) + 0.5 * kl(q, mixture)


def compute_partition_metrics(
    histograms: Mapping[str, Mapping[int, int]],
) -> dict[str, float | int]:
    if not histograms:
        raise ValueError("at least one client histogram is required")
    labels = tuple(sorted({label for histogram in histograms.values() for label in histogram}))
    if not labels:
        raise ValueError("partition contains no labels")

    sample_counts = [sum(histogram.values()) for histogram in histograms.values()]
    if any(count <= 0 for count in sample_counts):
        raise ValueError("every client must contain at least one sample")

    global_histogram = {label: 0 for label in labels}
    for histogram in histograms.values():
        for label, count in histogram.items():
            global_histogram[int(label)] += int(count)
    global_probabilities = _probabilities(global_histogram, labels)

    max_entropy = math.log2(len(labels)) if len(labels) > 1 else 1.0
    entropies: list[float] = []
    divergences: list[float] = []
    coverages: list[float] = []
    effective_labels: list[float] = []
    for histogram in histograms.values():
        probabilities = _probabilities(histogram, labels)
        entropy = _entropy(probabilities)
        entropies.append(entropy / max_entropy if len(labels) > 1 else 1.0)
        divergences.append(_js_divergence(probabilities, global_probabilities))
        active = sum(1 for value in probabilities if value > 0.0)
        coverages.append(active / len(labels))
        effective_labels.append(2.0**entropy)

    mean_samples = statistics.fmean(sample_counts)
    sample_std = statistics.pstdev(sample_counts) if len(sample_counts) > 1 else 0.0
    return {
        "client_count": len(sample_counts),
        "total_samples": int(sum(sample_counts)),
        "label_count": len(labels),
        "mean_client_samples": float(mean_samples),
        "sample_count_std": float(sample_std),
        "quantity_coefficient_of_variation": float(sample_std / mean_samples),
        "minimum_client_samples": int(min(sample_counts)),
        "maximum_client_samples": int(max(sample_counts)),
        "mean_normalized_label_entropy": float(statistics.fmean(entropies)),
        "minimum_normalized_label_entropy": float(min(entropies)),
        "mean_js_divergence_to_global": float(statistics.fmean(divergences)),
        "maximum_js_divergence_to_global": float(max(divergences)),
        "mean_class_coverage": float(statistics.fmean(coverages)),
        "minimum_class_coverage": float(min(coverages)),
        "mean_effective_label_count": float(statistics.fmean(effective_labels)),
    }


def partition_hash(client_dict: Mapping[int, np.ndarray]) -> str:
    payload = {
        str(client_id): [int(index) for index in np.asarray(indices, dtype=np.int64)]
        for client_id, indices in sorted(client_dict.items())
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def write_partition_artifacts(
    *,
    client_dict: Mapping[int, np.ndarray],
    dataset: torch.utils.data.Dataset,
    dataset_name: str,
    strategy: str,
    seed: int,
    parameters: Mapping[str, object],
    output_dir: str,
) -> tuple[str, str, dict[str, object]]:
    """Persist exact indices plus a JSON manifest and return both paths."""
    os.makedirs(output_dir, exist_ok=True)
    histograms = client_label_histograms(client_dict, dataset)
    metrics = compute_partition_metrics(histograms)
    fingerprint = partition_hash(client_dict)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "dataset": dataset_name,
        "strategy": strategy,
        "seed": int(seed),
        "parameters": dict(parameters),
        "partition_hash": fingerprint,
        "client_sample_counts": {
            str(client_id): int(len(indices))
            for client_id, indices in sorted(client_dict.items())
        },
        "client_label_histograms": histograms,
        "heterogeneity": metrics,
    }

    indices_path = os.path.join(output_dir, "partition_indices.npz")
    np.savez_compressed(
        indices_path,
        **{
            f"client_{client_id}": np.asarray(indices, dtype=np.int64)
            for client_id, indices in sorted(client_dict.items())
        },
    )
    manifest_path = os.path.join(output_dir, "partition_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return manifest_path, indices_path, manifest
