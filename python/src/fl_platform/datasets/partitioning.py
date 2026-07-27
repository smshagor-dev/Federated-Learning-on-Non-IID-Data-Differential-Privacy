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
from dataclasses import dataclass, field

import numpy as np


class PartitionError(ValueError):
    pass


@dataclass(slots=True)
class PartitionManifestRecord:
    partition_id: str
    dataset_id: str
    strategy: str
    seed: int
    num_clients: int
    client_sample_counts: dict[str, int]
    client_indices: dict[str, list[int]]
    manifest_checksum: str
    alpha: float | None = None
    classes_per_client: int | None = None
    minimum_client_samples: int = 1
    label_distribution_summary: dict[str, dict[int, int]] = field(default_factory=dict)


def _client_ids(num_clients: int) -> list[str]:
    return [f"client-{i}" for i in range(num_clients)]


def _labels_for(sample_count: int, num_classes: int) -> np.ndarray:
    return np.array([index % num_classes for index in range(sample_count)])


def _manifest_checksum(client_indices: dict[str, list[int]]) -> str:
    canonical = json.dumps(client_indices, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


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


def create_iid_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
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
    return PartitionManifestRecord(
        partition_id=partition_id,
        dataset_id=dataset_id,
        strategy="iid",
        seed=seed,
        num_clients=num_clients,
        client_sample_counts={k: len(v) for k, v in client_indices.items()},
        client_indices=client_indices,
        manifest_checksum=_manifest_checksum(client_indices),
        minimum_client_samples=minimum_client_samples,
        label_distribution_summary=_label_summary(labels, client_indices),
    )


def create_dirichlet_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
    alpha: float,
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
    return PartitionManifestRecord(
        partition_id=partition_id,
        dataset_id=dataset_id,
        strategy="dirichlet",
        seed=seed,
        num_clients=num_clients,
        alpha=alpha,
        client_sample_counts={k: len(v) for k, v in client_indices.items()},
        client_indices=client_indices,
        manifest_checksum=_manifest_checksum(client_indices),
        minimum_client_samples=minimum_client_samples,
        label_distribution_summary=_label_summary(labels, client_indices),
    )


def create_pathological_partition(
    dataset_id: str,
    partition_id: str,
    sample_count: int,
    num_classes: int,
    num_clients: int,
    seed: int,
    classes_per_client: int,
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
    return PartitionManifestRecord(
        partition_id=partition_id,
        dataset_id=dataset_id,
        strategy="pathological",
        seed=seed,
        num_clients=num_clients,
        classes_per_client=classes_per_client,
        client_sample_counts={k: len(v) for k, v in client_indices.items()},
        client_indices=client_indices,
        manifest_checksum=_manifest_checksum(client_indices),
        minimum_client_samples=minimum_client_samples,
        label_distribution_summary=_label_summary(labels, client_indices),
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
