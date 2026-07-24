"""Deterministic synthetic dataset access for the worker.

Milestone 3 scope is integration correctness, not real-dataset training:
this module provides a synthetic image dataset keyed by a
(dataset_id, client_id, seed) partition manifest, so tests never require
a download (per the task's explicit "do not require downloads in
automated tests" instruction). Real MNIST/CIFAR-10 loading is a natural
follow-up using the same interface — see docs/known-limitations.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import torch
from torch.utils.data import Dataset


def _stable_hash(value: str) -> int:
    """FNV-1a over UTF-8 bytes.

    Python's built-in hash() is salted per-process (hash randomization)
    and would silently break reproducibility across separate worker
    process invocations — exactly the scenario this module exists to
    support deterministically.
    """
    hash_value = 0xCBF29CE484222325
    for byte in value.encode("utf-8"):
        hash_value ^= byte
        hash_value = (hash_value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return hash_value


@dataclass(slots=True)
class PartitionManifest:
    """What a client task references instead of raw samples.

    Never sends raw samples through the coordinator — only this
    manifest (dataset id, partition id, client id, sample count, seed,
    transform config), from which the worker deterministically
    reconstructs its local shard.
    """

    dataset_id: str
    partition_id: str
    client_id: str
    sample_count: int
    seed: int
    num_classes: int = 4
    in_channels: int = 3
    image_size: int = 32


class SyntheticImageDataset(Dataset):
    """Deterministic, seeded synthetic image/label pairs.

    Given the same PartitionManifest, always produces the same tensors —
    required for reproducible integration tests and for the checkpoint/
    recovery test to observe identical results before and after a
    simulated crash.
    """

    def __init__(self, manifest: PartitionManifest) -> None:
        self._manifest = manifest
        generator = torch.Generator().manual_seed(manifest.seed)
        self._data = torch.randn(
            manifest.sample_count,
            manifest.in_channels,
            manifest.image_size,
            manifest.image_size,
            generator=generator,
        )
        self._targets = torch.tensor(
            [index % manifest.num_classes for index in range(manifest.sample_count)]
        )

    def __len__(self) -> int:
        return int(self._targets.numel())

    def __getitem__(self, index: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self._data[index], self._targets[index]


def load_partition(manifest: PartitionManifest) -> tuple[Dataset, list[int]]:
    """Returns (dataset, indices) matching federated.client.Client's constructor."""
    dataset = SyntheticImageDataset(manifest)
    return dataset, list(range(manifest.sample_count))


def manifest_for_client(
    dataset_reference: str, client_id: str, seed: int, sample_count: int = 32
) -> PartitionManifest:
    """Builds a deterministic manifest from a ClientTask's dataset_reference.

    dataset_reference is expected in the "synthetic:<client_id>" form
    produced by the coordinator's task dispatch (see
    cpp/coordinator/src/run_manager.cpp's begin_round); the client_id
    argument is used directly rather than re-parsed from the string, to
    keep this robust to that format changing.
    """
    del dataset_reference  # documented above: not parsed, kept for signature symmetry
    # Mix the client_id into the seed deterministically so different
    # clients get different (but each individually reproducible) shards.
    client_seed = seed ^ (_stable_hash(client_id) & 0xFFFFFFFF)
    return PartitionManifest(
        dataset_id="synthetic-cifar-like",
        partition_id=f"partition-{client_id}",
        client_id=client_id,
        sample_count=sample_count,
        seed=client_seed,
    )
