"""Deterministic client partition manifests (the Algorithm Expansion phase, Work Package
I). See docs/dataset-registry.md.

Partitioning operates on label *counts* (a synthetic label assignment
matching dataset_loader.py's `index % num_classes` scheme), not on real
image tensors — this phase's automated tests must not download real
datasets (MNIST/CIFAR-10 loading is real code, in loaders.py, but is
never exercised by the test suite). The partition math itself (which
sample indices go to which client) is identical either way.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


class PartitionError(ValueError):
    pass


@dataclass(slots=True, kw_only=True)
class PartitionManifestRecord:
    schema_version: int = 2
    partition_id: str
    dataset_id: str
    dataset_version: str = ""
    dataset_checksum: str = ""
    strategy: str
    seed: int
    num_clients: int
    client_sample_counts: dict[str, int]
    client_indices: dict[str, list[int]]
    manifest_checksum: str
    manifest_hash: str = ""
    partition_configuration: dict[str, Any] = field(default_factory=dict)
    alpha: float | None = None
    classes_per_client: int | None = None
    quantity_skew_sigma: float | None = None
    minimum_client_samples: int = 1
    label_distribution_summary: dict[str, dict[int, int]] = field(default_factory=dict)
    global_label_histogram: dict[int, int] = field(default_factory=dict)
    heterogeneity_metrics: dict[str, float] = field(default_factory=dict)


def _client_ids(num_clients: int) -> list[str]:
    return [f"client-{i}" for i in range(num_clients)]


def _labels_for(sample_count: int, num_classes: int) -> np.ndarray:
    return np.array([index % num_classes for index in range(sample_count)])


def _manifest_checksum(client_indices: dict[str, list[int]]) -> str:
    canonical = json.dumps(client_indices, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _manifest_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _label_summary(
    labels: np.ndarray, client_indices: dict[str, list[int]]
) -> dict[str, dict[int, int]]:
    summary: dict[str, dict[int, int]] = {}
    for client_id, indices in client_indices.items():
        client_labels = labels[indices] if indices else np.array([], dtype=int)
        unique, counts = np.unique(client_labels, return_counts=True)
        summary[client_id] = {
            int(label): int(count) for label, count in zip(unique, counts, strict=True)
        }
    return summary


def _global_label_histogram(labels: np.ndarray) -> dict[int, int]:
    unique, counts = np.unique(labels, return_counts=True)
    return {int(label): int(count) for label, count in zip(unique, counts, strict=True)}


def _probabilities_from_histogram(
    histogram: dict[int, int], *, label_space: list[int]
) -> np.ndarray:
    counts = np.array(
        [histogram.get(label, 0) for label in label_space], dtype=np.float64
    )
    total = float(np.sum(counts))
    if total <= 0:
        return np.zeros(len(label_space), dtype=np.float64)
    return counts / total


def _shannon_entropy(probabilities: np.ndarray) -> float:
    non_zero = probabilities[probabilities > 0]
    if non_zero.size == 0:
        return 0.0
    return float(-np.sum(non_zero * np.log2(non_zero)))


def _jensen_shannon_divergence(p: np.ndarray, q: np.ndarray) -> float:
    mixture = 0.5 * (p + q)

    def kl_divergence(a: np.ndarray, b: np.ndarray) -> float:
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / b[mask])))

    return 0.5 * kl_divergence(p, mixture) + 0.5 * kl_divergence(q, mixture)


def _heterogeneity_metrics(
    label_summary: dict[str, dict[int, int]],
    client_sample_counts: dict[str, int],
    global_histogram: dict[int, int],
) -> dict[str, float]:
    if not client_sample_counts:
        return {}
    label_space = sorted(global_histogram)
    global_probabilities = _probabilities_from_histogram(
        global_histogram, label_space=label_space
    )
    sample_counts = np.array(list(client_sample_counts.values()), dtype=np.float64)
    entropies: list[float] = []
    effective_labels: list[float] = []
    js_divergences: list[float] = []
    class_coverages: list[float] = []
    for histogram in label_summary.values():
        probabilities = _probabilities_from_histogram(
            histogram, label_space=label_space
        )
        entropies.append(_shannon_entropy(probabilities))
        effective_labels.append(float(np.sum(np.array(list(histogram.values())) > 0)))
        js_divergences.append(
            _jensen_shannon_divergence(probabilities, global_probabilities)
        )
        class_coverages.append(
            float(np.sum(np.array(list(histogram.values())) > 0))
            / float(max(1, len(label_space)))
        )
    mean_count = float(np.mean(sample_counts))
    std_count = float(np.std(sample_counts))
    return {
        "client_count": float(sample_counts.size),
        "mean_client_samples": mean_count,
        "std_client_samples": std_count,
        "min_client_samples": float(np.min(sample_counts)),
        "max_client_samples": float(np.max(sample_counts)),
        "quantity_skew_coefficient": 0.0 if mean_count <= 0 else std_count / mean_count,
        "mean_client_label_entropy_bits": float(
            np.mean(np.array(entropies, dtype=np.float64))
        ),
        "mean_global_label_js_divergence_bits": float(
            np.mean(np.array(js_divergences, dtype=np.float64))
        ),
        "mean_effective_label_count": float(
            np.mean(np.array(effective_labels, dtype=np.float64))
        ),
        "mean_class_coverage_fraction": float(
            np.mean(np.array(class_coverages, dtype=np.float64))
        ),
    }


def _build_partition_record(
    *,
    partition_id: str,
    dataset_id: str,
    dataset_version: str,
    dataset_checksum: str,
    strategy: str,
    seed: int,
    num_clients: int,
    client_indices: dict[str, list[int]],
    labels: np.ndarray,
    partition_configuration: dict[str, Any],
    alpha: float | None = None,
    classes_per_client: int | None = None,
    quantity_skew_sigma: float | None = None,
    minimum_client_samples: int = 1,
) -> PartitionManifestRecord:
    client_sample_counts = {k: len(v) for k, v in client_indices.items()}
    label_distribution_summary = _label_summary(labels, client_indices)
    global_label_histogram = _global_label_histogram(labels)
    manifest_checksum = _manifest_checksum(client_indices)
    heterogeneity_metrics = _heterogeneity_metrics(
        label_distribution_summary, client_sample_counts, global_label_histogram
    )
    payload = {
        "schema_version": 2,
        "partition_id": partition_id,
        "dataset_id": dataset_id,
        "dataset_version": dataset_version,
        "dataset_checksum": dataset_checksum,
        "strategy": strategy,
        "seed": seed,
        "num_clients": num_clients,
        "client_sample_counts": client_sample_counts,
        "client_indices": client_indices,
        "manifest_checksum": manifest_checksum,
        "partition_configuration": partition_configuration,
        "alpha": alpha,
        "classes_per_client": classes_per_client,
        "quantity_skew_sigma": quantity_skew_sigma,
        "minimum_client_samples": minimum_client_samples,
        "label_distribution_summary": label_distribution_summary,
        "global_label_histogram": global_label_histogram,
        "heterogeneity_metrics": heterogeneity_metrics,
    }
    return PartitionManifestRecord(
        schema_version=2,
        partition_id=partition_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        strategy=strategy,
        seed=seed,
        num_clients=num_clients,
        client_sample_counts=client_sample_counts,
        client_indices=client_indices,
        manifest_checksum=manifest_checksum,
        manifest_hash=_manifest_hash(payload),
        partition_configuration=partition_configuration,
        alpha=alpha,
        classes_per_client=classes_per_client,
        quantity_skew_sigma=quantity_skew_sigma,
        minimum_client_samples=minimum_client_samples,
        label_distribution_summary=label_distribution_summary,
        global_label_histogram=global_label_histogram,
        heterogeneity_metrics=heterogeneity_metrics,
    )


def create_iid_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
    dataset_version: str = "",
    dataset_checksum: str = "",
    minimum_client_samples: int = 1,
) -> PartitionManifestRecord:
    if num_clients <= 0:
        raise PartitionError("num_clients must be positive")
    labels = _labels_for(sample_count, num_classes)
    rng = np.random.RandomState(seed)
    shuffled = rng.permutation(sample_count)
    client_ids = _client_ids(num_clients)
    splits = np.array_split(shuffled, num_clients)
    client_indices = {
        client_id: split.tolist()
        for client_id, split in zip(client_ids, splits, strict=True)
    }
    _reject_below_minimum(client_indices, minimum_client_samples, "iid")
    return _build_partition_record(
        partition_id=partition_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        strategy="iid",
        seed=seed,
        num_clients=num_clients,
        client_indices=client_indices,
        labels=labels,
        partition_configuration={
            "strategy": "iid",
            "seed": seed,
            "num_clients": num_clients,
            "minimum_client_samples": minimum_client_samples,
        },
        minimum_client_samples=minimum_client_samples,
    )


def create_dirichlet_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
    alpha: float,
    dataset_version: str = "",
    dataset_checksum: str = "",
    minimum_client_samples: int = 1,
) -> PartitionManifestRecord:
    """Standard label-skew Dirichlet partition (Hsu et al., 2019): for
    each class, split that class's sample indices across clients
    according to proportions drawn from Dirichlet(alpha). Smaller alpha
    -> more skewed (more non-IID); alpha -> infinity approaches IID."""
    if alpha <= 0:
        raise PartitionError("alpha must be positive")
    if num_clients <= 0:
        raise PartitionError("num_clients must be positive")
    labels = _labels_for(sample_count, num_classes)
    rng = np.random.RandomState(seed)
    client_ids = _client_ids(num_clients)
    client_indices: dict[str, list[int]] = {client_id: [] for client_id in client_ids}

    for class_label in range(num_classes):
        class_indices = np.where(labels == class_label)[0]
        rng.shuffle(class_indices)
        proportions = rng.dirichlet(np.full(num_clients, alpha))
        split_points = (np.cumsum(proportions) * len(class_indices)).astype(int)[:-1]
        for client_id, shard in zip(
            client_ids, np.split(class_indices, split_points), strict=True
        ):
            client_indices[client_id].extend(shard.tolist())

    for indices in client_indices.values():
        rng.shuffle(indices)
    _reject_below_minimum(client_indices, minimum_client_samples, "dirichlet")
    return _build_partition_record(
        partition_id=partition_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        strategy="dirichlet",
        seed=seed,
        num_clients=num_clients,
        client_indices=client_indices,
        labels=labels,
        partition_configuration={
            "strategy": "dirichlet",
            "seed": seed,
            "num_clients": num_clients,
            "alpha": alpha,
            "minimum_client_samples": minimum_client_samples,
        },
        alpha=alpha,
        minimum_client_samples=minimum_client_samples,
    )


def create_pathological_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
    classes_per_client: int,
    dataset_version: str = "",
    dataset_checksum: str = "",
    minimum_client_samples: int = 1,
) -> PartitionManifestRecord:
    """Each client is assigned exactly `classes_per_client` classes
    (McMahan et al., 2017's pathological non-IID split) and only ever
    sees samples from those classes."""
    if not (0 < classes_per_client <= num_classes):
        raise PartitionError("classes_per_client must be in (0, num_classes]")
    if num_clients <= 0:
        raise PartitionError("num_clients must be positive")
    labels = _labels_for(sample_count, num_classes)
    rng = np.random.RandomState(seed)
    client_ids = _client_ids(num_clients)

    # Each client draws classes_per_client classes (with replacement
    # across clients, so the same class can and usually will serve
    # multiple clients — otherwise num_clients * classes_per_client would
    # be capped at num_classes).
    client_classes = {
        client_id: rng.choice(
            num_classes, size=classes_per_client, replace=False
        ).tolist()
        for client_id in client_ids
    }

    class_to_clients: dict[int, list[str]] = {label: [] for label in range(num_classes)}
    for client_id, classes in client_classes.items():
        for label in classes:
            class_to_clients[label].append(client_id)

    client_indices: dict[str, list[int]] = {client_id: [] for client_id in client_ids}
    for class_label in range(num_classes):
        class_indices = np.where(labels == class_label)[0]
        rng.shuffle(class_indices)
        assigned_clients = class_to_clients[class_label]
        if not assigned_clients:
            continue
        shards = np.array_split(class_indices, len(assigned_clients))
        for client_id, shard in zip(assigned_clients, shards, strict=True):
            client_indices[client_id].extend(shard.tolist())

    for indices in client_indices.values():
        rng.shuffle(indices)
    _reject_below_minimum(client_indices, minimum_client_samples, "pathological")
    return _build_partition_record(
        partition_id=partition_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        strategy="pathological",
        seed=seed,
        num_clients=num_clients,
        client_indices=client_indices,
        labels=labels,
        partition_configuration={
            "strategy": "pathological",
            "seed": seed,
            "num_clients": num_clients,
            "classes_per_client": classes_per_client,
            "minimum_client_samples": minimum_client_samples,
        },
        classes_per_client=classes_per_client,
        minimum_client_samples=minimum_client_samples,
    )


def create_quantity_skew_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
    quantity_skew_sigma: float,
    dataset_version: str = "",
    dataset_checksum: str = "",
    minimum_client_samples: int = 1,
) -> PartitionManifestRecord:
    if num_clients <= 0:
        raise PartitionError("num_clients must be positive")
    if not math.isfinite(quantity_skew_sigma) or quantity_skew_sigma < 0.0:
        raise PartitionError("quantity_skew_sigma must be finite and non-negative")
    labels = _labels_for(sample_count, num_classes)
    rng = np.random.RandomState(seed)
    shuffled = rng.permutation(sample_count)
    if quantity_skew_sigma == 0.0:
        raw_weights = np.ones(num_clients, dtype=np.float64)
    else:
        raw_weights = rng.lognormal(
            mean=0.0, sigma=quantity_skew_sigma, size=num_clients
        )
    probabilities = raw_weights / np.sum(raw_weights)
    expected_counts = probabilities * sample_count
    counts = np.floor(expected_counts).astype(int)
    remainder = sample_count - int(np.sum(counts))
    if remainder > 0:
        fractional_order = np.argsort(-(expected_counts - counts))
        counts[fractional_order[:remainder]] += 1
    client_indices: dict[str, list[int]] = {}
    offset = 0
    for client_id, count in zip(_client_ids(num_clients), counts.tolist(), strict=True):
        client_indices[client_id] = shuffled[offset : offset + count].tolist()
        offset += count
    _reject_below_minimum(client_indices, minimum_client_samples, "quantity_skew")
    return _build_partition_record(
        partition_id=partition_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        dataset_checksum=dataset_checksum,
        strategy="quantity_skew",
        seed=seed,
        num_clients=num_clients,
        client_indices=client_indices,
        labels=labels,
        partition_configuration={
            "strategy": "quantity_skew",
            "seed": seed,
            "num_clients": num_clients,
            "quantity_skew_sigma": quantity_skew_sigma,
            "minimum_client_samples": minimum_client_samples,
        },
        quantity_skew_sigma=quantity_skew_sigma,
        minimum_client_samples=minimum_client_samples,
    )


def _reject_below_minimum(
    client_indices: dict[str, list[int]], minimum: int, strategy: str
) -> None:
    starved = [
        client_id
        for client_id, indices in client_indices.items()
        if len(indices) < minimum
    ]
    if starved:
        raise PartitionError(
            f"{strategy} partition leaves {len(starved)} client(s) below the minimum "
            f"{minimum} samples: {starved[:5]}{'...' if len(starved) > 5 else ''}"
        )
