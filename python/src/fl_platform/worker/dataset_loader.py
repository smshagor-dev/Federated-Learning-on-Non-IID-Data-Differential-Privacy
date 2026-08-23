"""Deterministic synthetic dataset access for distributed workers.

The distributed worker intentionally keeps using a download-free synthetic
integration dataset, but the shard it reconstructs now follows the canonical
partition contract carried by the coordinator's signed ``dataset_reference``.
The reference format is versioned as ``fl-partition-v1://synthetic?...`` and
supports the same four strategies accepted by the execution specification:
IID, Dirichlet label skew, pathological class restriction, and quantity skew.

Raw samples still never traverse the coordinator. A verified task only selects
deterministic shard semantics; the worker reconstructs its samples locally from
the signed partition parameters, client id, and seed.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import numpy as np
import torch
from torch.utils.data import Dataset

_CANONICAL_REFERENCE_PREFIX = "fl-partition-v1://"
_SUPPORTED_STRATEGIES = frozenset(
    {"iid", "dirichlet", "pathological", "quantity_skew"}
)
_ALLOWED_QUERY_KEYS = frozenset(
    {
        "dataset",
        "strategy",
        "alpha",
        "classes_per_client",
        "quantity_skew_sigma",
        "min_client_size",
        "seed",
    }
)
_REFERENCE_LOCK = threading.RLock()
_VERIFIED_REFERENCE_BY_CLIENT: dict[str, str] = {}


def _stable_hash(value: str) -> int:
    """FNV-1a over UTF-8 bytes, stable across Python processes."""
    hash_value = 0xCBF29CE484222325
    for byte in value.encode("utf-8"):
        hash_value ^= byte
        hash_value = (hash_value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return hash_value


def register_verified_partition_reference(
    client_id: str, dataset_reference: str
) -> None:
    """Publish a coordinator-accepted partition reference for one client.

    ``PartitionAwareGrpcCoordinatorClient`` calls this only after the base gRPC
    client has completed its existing task acceptance pipeline. When coordinator
    signing is configured, that pipeline includes signature, trust-bundle,
    replay, and accepted-task-journal verification before this function runs.
    """
    client_id = client_id.strip()
    dataset_reference = dataset_reference.strip()
    if not client_id:
        raise ValueError("client_id is required for a partition reference")
    if not dataset_reference:
        raise ValueError("dataset_reference is required")
    with _REFERENCE_LOCK:
        _VERIFIED_REFERENCE_BY_CLIENT[client_id] = dataset_reference


def clear_verified_partition_references() -> None:
    """Test helper: clear process-local accepted partition bindings."""
    with _REFERENCE_LOCK:
        _VERIFIED_REFERENCE_BY_CLIENT.clear()


def _effective_reference(dataset_reference: str, client_id: str) -> str:
    with _REFERENCE_LOCK:
        return _VERIFIED_REFERENCE_BY_CLIENT.get(client_id, dataset_reference)


@dataclass(slots=True)
class PartitionManifest:
    """Deterministic local shard description reconstructed by a worker."""

    dataset_id: str
    partition_id: str
    client_id: str
    sample_count: int
    seed: int
    partition_strategy: str = "iid"
    alpha: float = 0.0
    classes_per_client: int = 0
    quantity_skew_sigma: float = 0.0
    minimum_client_size: int = 0
    num_classes: int = 4
    in_channels: int = 3
    image_size: int = 32


def _single(
    query: dict[str, list[str]], name: str, *, default: str | None = None
) -> str:
    values = query.get(name)
    if values is None:
        if default is None:
            raise ValueError(f"canonical partition reference is missing '{name}'")
        return default
    if len(values) != 1:
        raise ValueError(f"canonical partition reference repeats '{name}'")
    return values[0]


def _parse_nonnegative_int(value: str, name: str) -> int:
    try:
        parsed = int(value, 10)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if parsed < 0:
        raise ValueError(f"{name} must be non-negative")
    return parsed


def _parse_finite_float(value: str, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(parsed):
        raise ValueError(f"{name} must be finite")
    return parsed


@dataclass(slots=True, frozen=True)
class _PartitionReference:
    dataset_id: str
    strategy: str
    alpha: float
    classes_per_client: int
    quantity_skew_sigma: float
    minimum_client_size: int
    seed: int | None


def _parse_partition_reference(dataset_reference: str) -> _PartitionReference:
    if not dataset_reference.startswith(_CANONICAL_REFERENCE_PREFIX):
        return _PartitionReference(
            dataset_id="synthetic-cifar-like",
            strategy="iid",
            alpha=0.0,
            classes_per_client=0,
            quantity_skew_sigma=0.0,
            minimum_client_size=0,
            seed=None,
        )

    parsed = urlparse(dataset_reference)
    if parsed.scheme != "fl-partition-v1" or parsed.netloc != "synthetic":
        raise ValueError("unsupported canonical partition reference authority")
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    unknown = sorted(set(query) - _ALLOWED_QUERY_KEYS)
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"canonical partition reference has unknown fields: {names}")

    dataset_id = _single(query, "dataset").strip()
    strategy = _single(query, "strategy").strip().lower()
    if not dataset_id:
        raise ValueError("canonical partition dataset must be non-empty")
    if strategy not in _SUPPORTED_STRATEGIES:
        raise ValueError(f"unsupported partition strategy '{strategy}'")

    alpha = _parse_finite_float(_single(query, "alpha", default="0"), "alpha")
    classes_per_client = _parse_nonnegative_int(
        _single(query, "classes_per_client", default="0"), "classes_per_client"
    )
    quantity_skew_sigma = _parse_finite_float(
        _single(query, "quantity_skew_sigma", default="0"), "quantity_skew_sigma"
    )
    minimum_client_size = _parse_nonnegative_int(
        _single(query, "min_client_size", default="0"), "min_client_size"
    )
    seed = _parse_nonnegative_int(_single(query, "seed"), "seed")
    if seed > 0xFFFFFFFFFFFFFFFF:
        raise ValueError("seed must fit in uint64")

    if strategy == "dirichlet" and alpha <= 0.0:
        raise ValueError("dirichlet partition requires alpha > 0")
    if strategy == "pathological" and classes_per_client <= 0:
        raise ValueError("pathological partition requires classes_per_client > 0")
    if strategy == "quantity_skew" and quantity_skew_sigma <= 0.0:
        raise ValueError("quantity_skew partition requires quantity_skew_sigma > 0")

    return _PartitionReference(
        dataset_id=dataset_id,
        strategy=strategy,
        alpha=alpha,
        classes_per_client=classes_per_client,
        quantity_skew_sigma=quantity_skew_sigma,
        minimum_client_size=minimum_client_size,
        seed=seed,
    )


def _quantity_skew_count(
    base_sample_count: int,
    client_seed: int,
    sigma: float,
    minimum_client_size: int,
) -> int:
    """Return a deterministic bounded log-normal synthetic shard size."""
    rng = np.random.default_rng(client_seed)
    factor = float(rng.lognormal(mean=-0.5 * sigma * sigma, sigma=sigma))
    count = max(1, int(round(base_sample_count * factor)))
    count = max(count, minimum_client_size)
    safe_ceiling = max(minimum_client_size, base_sample_count * 32, 1)
    return min(count, safe_ceiling)


class SyntheticImageDataset(Dataset[tuple[torch.Tensor, torch.Tensor]]):
    """Deterministic synthetic images whose labels follow the partition spec."""

    def __init__(self, manifest: PartitionManifest) -> None:
        self._manifest = manifest
        if manifest.sample_count <= 0:
            raise ValueError("sample_count must be positive")
        if manifest.num_classes <= 0:
            raise ValueError("num_classes must be positive")
        if manifest.in_channels <= 0 or manifest.image_size <= 0:
            raise ValueError("synthetic image dimensions must be positive")

        torch_seed = manifest.seed & 0x7FFFFFFFFFFFFFFF
        generator = torch.Generator().manual_seed(torch_seed)
        self._data = torch.randn(
            manifest.sample_count,
            manifest.in_channels,
            manifest.image_size,
            manifest.image_size,
            generator=generator,
        )

        label_rng = np.random.default_rng(manifest.seed ^ 0x9E3779B97F4A7C15)
        if manifest.partition_strategy == "dirichlet":
            concentration = np.full(
                manifest.num_classes, manifest.alpha, dtype=np.float64
            )
            probabilities = label_rng.dirichlet(concentration)
            labels = label_rng.choice(
                manifest.num_classes,
                size=manifest.sample_count,
                p=probabilities,
            )
        elif manifest.partition_strategy == "pathological":
            class_count = min(manifest.classes_per_client, manifest.num_classes)
            allowed = label_rng.choice(
                manifest.num_classes,
                size=class_count,
                replace=False,
            )
            labels = label_rng.choice(allowed, size=manifest.sample_count)
        else:
            # Preserve the original worker dataset exactly for legacy/IID
            # tasks: labels were index % num_classes before partition parity.
            # Quantity skew changes only the shard size, not this label rule.
            labels = np.arange(manifest.sample_count) % manifest.num_classes
        self._targets = torch.as_tensor(labels, dtype=torch.long)

    def __len__(self) -> int:
        return int(self._targets.numel())

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self._data[index], self._targets[index]


def load_partition(
    manifest: PartitionManifest,
) -> tuple[Dataset[tuple[torch.Tensor, torch.Tensor]], list[int]]:
    """Return ``(dataset, indices)`` for the existing legacy client trainer."""
    dataset = SyntheticImageDataset(manifest)
    return dataset, list(range(manifest.sample_count))


def manifest_for_client(
    dataset_reference: str,
    client_id: str,
    seed: int,
    sample_count: int = 32,
) -> PartitionManifest:
    """Build a deterministic shard manifest from the accepted task contract.

    Legacy ``synthetic:<client>`` references remain backward-compatible IID
    shards. When a canonical reference has been accepted for the client, it
    takes precedence and supplies the execution seed and partition parameters.
    """
    if sample_count <= 0:
        raise ValueError("sample_count must be positive")
    effective_reference = _effective_reference(dataset_reference, client_id)
    partition = _parse_partition_reference(effective_reference)
    base_seed = seed if partition.seed is None else partition.seed
    client_seed = (base_seed ^ _stable_hash(client_id)) & 0xFFFFFFFFFFFFFFFF
    local_sample_count = sample_count
    if partition.strategy == "quantity_skew":
        local_sample_count = _quantity_skew_count(
            sample_count,
            client_seed,
            partition.quantity_skew_sigma,
            partition.minimum_client_size,
        )

    return PartitionManifest(
        dataset_id=partition.dataset_id,
        partition_id=f"{partition.strategy}-{client_id}",
        client_id=client_id,
        sample_count=local_sample_count,
        seed=client_seed,
        partition_strategy=partition.strategy,
        alpha=partition.alpha,
        classes_per_client=partition.classes_per_client,
        quantity_skew_sigma=partition.quantity_skew_sigma,
        minimum_client_size=partition.minimum_client_size,
    )
