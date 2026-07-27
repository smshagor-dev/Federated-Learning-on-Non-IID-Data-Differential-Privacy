"""Production-shaped dataset registry (Algorithm Expansion phase, Work
Package I). See docs/dataset-registry.md. Same filesystem-backed
pattern as models/model_registry.py — see that module's docstring for
why."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fl_platform.datasets.partitioning import (
    PartitionManifestRecord,
    create_dirichlet_partition,
    create_iid_partition,
    create_pathological_partition,
)

_VALID_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class DatasetRegistryError(RuntimeError):
    pass


class DatasetStatus:
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"


_ALLOWED_TRANSITIONS = {
    DatasetStatus.DRAFT: {DatasetStatus.VALIDATED},
    DatasetStatus.VALIDATED: {DatasetStatus.ACTIVE},
    DatasetStatus.ACTIVE: {DatasetStatus.DEPRECATED},
    DatasetStatus.DEPRECATED: {DatasetStatus.ARCHIVED},
    DatasetStatus.ARCHIVED: set(),
}


@dataclass(slots=True)
class DatasetRegistryEntry:
    dataset_id: str
    name: str
    version: str
    task_type: str
    num_classes: int
    input_shape: list[int]
    train_sample_count: int
    eval_sample_count: int
    normalization: str
    storage_reference: str
    checksum: str = ""
    license_metadata: str = ""
    status: str = DatasetStatus.DRAFT
    created_at: float = 0.0
    updated_at: float = 0.0


_STRATEGIES: dict[str, Callable[..., PartitionManifestRecord]] = {
    "iid": create_iid_partition,
    "dirichlet": create_dirichlet_partition,
    "pathological": create_pathological_partition,
}


class FilesystemDatasetRegistry:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._datasets_dir = self._root / "datasets"
        self._partitions_dir = self._root / "partitions"
        self._datasets_dir.mkdir(parents=True, exist_ok=True)
        self._partitions_dir.mkdir(parents=True, exist_ok=True)

    def _dataset_path(self, dataset_id: str) -> Path:
        if not _VALID_ID.match(dataset_id):
            raise DatasetRegistryError(f"invalid dataset_id: {dataset_id}")
        return self._datasets_dir / f"{dataset_id}.json"

    def register(self, entry: DatasetRegistryEntry) -> DatasetRegistryEntry:
        path = self._dataset_path(entry.dataset_id)
        if path.exists():
            raise DatasetRegistryError(
                f"dataset '{entry.dataset_id}' already registered"
            )
        now = time.time()
        entry.created_at = now
        entry.updated_at = now
        entry.status = DatasetStatus.DRAFT
        self._write_dataset(path, entry)
        return entry

    def list_datasets(self) -> list[DatasetRegistryEntry]:
        return sorted(
            (self._read_dataset(path) for path in self._datasets_dir.glob("*.json")),
            key=lambda entry: entry.dataset_id,
        )

    def get(self, dataset_id: str) -> DatasetRegistryEntry:
        path = self._dataset_path(dataset_id)
        if not path.exists():
            raise DatasetRegistryError(f"unknown dataset '{dataset_id}'")
        return self._read_dataset(path)

    def validate(self, dataset_id: str) -> DatasetRegistryEntry:
        entry = self.get(dataset_id)
        if entry.train_sample_count <= 0:
            raise DatasetRegistryError(
                f"dataset '{dataset_id}' has no training samples; cannot validate"
            )
        if entry.num_classes <= 0:
            raise DatasetRegistryError(
                f"dataset '{dataset_id}' has non-positive num_classes; cannot validate"
            )
        return self._transition(entry, DatasetStatus.VALIDATED)

    def activate(self, dataset_id: str) -> DatasetRegistryEntry:
        return self._transition(self.get(dataset_id), DatasetStatus.ACTIVE)

    def deprecate(self, dataset_id: str) -> DatasetRegistryEntry:
        return self._transition(self.get(dataset_id), DatasetStatus.DEPRECATED)

    def create_partition(
        self,
        dataset_id: str,
        partition_id: str,
        strategy: str,
        seed: int,
        num_clients: int,
        *,
        alpha: float | None = None,
        classes_per_client: int | None = None,
        minimum_client_samples: int = 1,
    ) -> PartitionManifestRecord:
        dataset = self.get(dataset_id)
        builder = _STRATEGIES.get(strategy)
        if builder is None:
            available = sorted(_STRATEGIES)
            raise DatasetRegistryError(
                f"unsupported partition strategy '{strategy}': {available}"
            )

        kwargs: dict[str, float | int] = {}
        if strategy == "dirichlet":
            if alpha is None:
                raise DatasetRegistryError("dirichlet partitioning requires alpha")
            kwargs["alpha"] = alpha
        if strategy == "pathological":
            if classes_per_client is None:
                raise DatasetRegistryError(
                    "pathological partitioning requires classes_per_client"
                )
            kwargs["classes_per_client"] = classes_per_client

        record = builder(
            dataset_id=dataset_id,
            partition_id=partition_id,
            sample_count=dataset.train_sample_count,
            num_classes=dataset.num_classes,
            num_clients=num_clients,
            seed=seed,
            minimum_client_samples=minimum_client_samples,
            **kwargs,
        )
        self._write_partition(record)
        return record

    def get_partition(self, partition_id: str) -> PartitionManifestRecord:
        path = self._partition_path(partition_id)
        if not path.exists():
            raise DatasetRegistryError(f"unknown partition '{partition_id}'")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["label_distribution_summary"] = {
            client_id: {int(label): count for label, count in counts.items()}
            for client_id, counts in payload["label_distribution_summary"].items()
        }
        return PartitionManifestRecord(**payload)

    def list_partitions(
        self, dataset_id: str | None = None
    ) -> list[PartitionManifestRecord]:
        records = [
            self.get_partition(path.stem)
            for path in self._partitions_dir.glob("*.json")
        ]
        if dataset_id is not None:
            records = [record for record in records if record.dataset_id == dataset_id]
        return sorted(records, key=lambda record: record.partition_id)

    def _partition_path(self, partition_id: str) -> Path:
        if not _VALID_ID.match(partition_id):
            raise DatasetRegistryError(f"invalid partition_id: {partition_id}")
        return self._partitions_dir / f"{partition_id}.json"

    def _write_partition(self, record: PartitionManifestRecord) -> None:
        path = self._partition_path(record.partition_id)
        if path.exists():
            raise DatasetRegistryError(
                f"partition '{record.partition_id}' already exists"
            )
        payload = {
            "partition_id": record.partition_id,
            "dataset_id": record.dataset_id,
            "strategy": record.strategy,
            "seed": record.seed,
            "num_clients": record.num_clients,
            "alpha": record.alpha,
            "classes_per_client": record.classes_per_client,
            "minimum_client_samples": record.minimum_client_samples,
            "client_sample_counts": record.client_sample_counts,
            "client_indices": record.client_indices,
            "manifest_checksum": record.manifest_checksum,
            "label_distribution_summary": record.label_distribution_summary,
        }
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(temp_path, path)

    def _transition(
        self, entry: DatasetRegistryEntry, next_status: str
    ) -> DatasetRegistryEntry:
        allowed = _ALLOWED_TRANSITIONS.get(entry.status, set())
        if next_status not in allowed:
            dataset_id = entry.dataset_id
            raise DatasetRegistryError(
                f"dataset '{dataset_id}': cannot go {entry.status} -> {next_status}"
            )
        entry.status = next_status
        entry.updated_at = time.time()
        self._write_dataset(self._dataset_path(entry.dataset_id), entry)
        return entry

    def _write_dataset(self, path: Path, entry: DatasetRegistryEntry) -> None:
        payload = {
            "dataset_id": entry.dataset_id,
            "name": entry.name,
            "version": entry.version,
            "task_type": entry.task_type,
            "num_classes": entry.num_classes,
            "input_shape": entry.input_shape,
            "train_sample_count": entry.train_sample_count,
            "eval_sample_count": entry.eval_sample_count,
            "normalization": entry.normalization,
            "storage_reference": entry.storage_reference,
            "checksum": entry.checksum,
            "license_metadata": entry.license_metadata,
            "status": entry.status,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp_path, path)

    def _read_dataset(self, path: Path) -> DatasetRegistryEntry:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DatasetRegistryError(
                f"unreadable dataset registry entry at {path}: {error}"
            ) from error
        return DatasetRegistryEntry(**payload)
