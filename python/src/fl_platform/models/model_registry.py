"""Production-shaped model registry (the Algorithm Expansion phase, Work Package H). See
docs/model-registry.md.

Filesystem-backed (JSON metadata, one file per model name+version) rather
than PostgreSQL-backed, per this phase's explicit scope (production
Postgres repositories remain deferred — see docs/known-limitations.md).
Never stores tensor values; `checkpoint_reference` points at wherever the
actual weights live (a PersonalizedModelStore artifact path, for
instance), matching the "no large model tensors in Go application JSON"
constraint one layer up.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

_VALID_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class ModelRegistryError(RuntimeError):
    pass


class ModelStatus:
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"

    ALL = (DRAFT, VALIDATED, ACTIVE, DEPRECATED, ARCHIVED)


_ALLOWED_TRANSITIONS = {
    ModelStatus.DRAFT: {ModelStatus.VALIDATED},
    ModelStatus.VALIDATED: {ModelStatus.ACTIVE},
    ModelStatus.ACTIVE: {ModelStatus.DEPRECATED},
    ModelStatus.DEPRECATED: {ModelStatus.ARCHIVED},
    ModelStatus.ARCHIVED: set(),
}


@dataclass(slots=True)
class ModelRegistryEntry:
    name: str
    version: str
    architecture_name: str
    input_channels: int
    num_classes: int
    normalization: str
    parameter_count: int
    state_dict_schema_hash: str
    aggregatable_parameter_names: list[str] = field(default_factory=list)
    personalizable_parameter_names: list[str] = field(default_factory=list)
    supported_datasets: list[str] = field(default_factory=list)
    supported_algorithms: list[str] = field(default_factory=list)
    checkpoint_reference: str = ""
    checksum: str = ""
    status: str = ModelStatus.DRAFT
    created_at: float = 0.0
    updated_at: float = 0.0


class FilesystemModelRegistry:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str, version: str) -> Path:
        if not _VALID_ID.match(name) or not _VALID_ID.match(version):
            raise ModelRegistryError(f"invalid model name/version: {name}/{version}")
        return self._root / f"{name}__{version}.json"

    def register(self, entry: ModelRegistryEntry) -> ModelRegistryEntry:
        path = self._path(entry.name, entry.version)
        if path.exists():
            raise ModelRegistryError(
                f"model '{entry.name}' version '{entry.version}' already registered"
            )
        now = time.time()
        entry.created_at = now
        entry.updated_at = now
        entry.status = ModelStatus.DRAFT
        self._write(path, entry)
        return entry

    def list_models(self) -> list[ModelRegistryEntry]:
        return sorted(
            (self._read(path) for path in self._root.glob("*.json")),
            key=lambda entry: (entry.name, entry.version),
        )

    def get(self, name: str, version: str) -> ModelRegistryEntry:
        path = self._path(name, version)
        if not path.exists():
            raise ModelRegistryError(f"unknown model '{name}' version '{version}'")
        return self._read(path)

    def validate(
        self, name: str, version: str, *, actual_schema_hash: str
    ) -> ModelRegistryEntry:
        """Transitions DRAFT -> VALIDATED only if `actual_schema_hash`
        (computed from a real constructed model instance — see
        models/personalization.py's compute_schema_hash) matches the
        registered hash; a mismatch means the registered metadata does
        not describe a model that can actually be built, and is rejected
        rather than silently marked valid."""
        entry = self.get(name, version)
        registered = entry.state_dict_schema_hash
        actual = actual_schema_hash
        if registered != actual:
            raise ModelRegistryError(f"{name} v{version}: {registered} != {actual}")
        return self._transition(entry, ModelStatus.VALIDATED)

    def activate(self, name: str, version: str) -> ModelRegistryEntry:
        return self._transition(self.get(name, version), ModelStatus.ACTIVE)

    def deprecate(self, name: str, version: str) -> ModelRegistryEntry:
        return self._transition(self.get(name, version), ModelStatus.DEPRECATED)

    def resolve_for_task(self, name: str, algorithm: str) -> ModelRegistryEntry:
        """Finds the ACTIVE version of `name` that supports `algorithm`.
        Raises rather than silently falling back to a DRAFT/DEPRECATED
        version, or to a version that never declared support for this
        algorithm."""
        candidates = [
            entry
            for entry in self.list_models()
            if entry.name == name
            and entry.status == ModelStatus.ACTIVE
            and algorithm in entry.supported_algorithms
        ]
        if not candidates:
            raise ModelRegistryError(
                f"no ACTIVE version of model '{name}' supports algorithm '{algorithm}'"
            )
        # Highest version string wins if more than one is somehow ACTIVE
        # (activate() does not itself enforce single-active-per-name,
        # since a registry might deliberately run two active model
        # variants side by side for a comparison run).
        return sorted(candidates, key=lambda entry: entry.version)[-1]

    def verify_schema(self, name: str, version: str, actual_schema_hash: str) -> bool:
        return self.get(name, version).state_dict_schema_hash == actual_schema_hash

    def _transition(
        self, entry: ModelRegistryEntry, next_status: str
    ) -> ModelRegistryEntry:
        allowed = _ALLOWED_TRANSITIONS.get(entry.status, set())
        if next_status not in allowed:
            raise ModelRegistryError(
                f"model '{entry.name}' version '{entry.version}': cannot transition "
                f"{entry.status} -> {next_status}"
            )
        entry.status = next_status
        entry.updated_at = time.time()
        self._write(self._path(entry.name, entry.version), entry)
        return entry

    def _write(self, path: Path, entry: ModelRegistryEntry) -> None:
        payload = {
            "name": entry.name,
            "version": entry.version,
            "architecture_name": entry.architecture_name,
            "input_channels": entry.input_channels,
            "num_classes": entry.num_classes,
            "normalization": entry.normalization,
            "parameter_count": entry.parameter_count,
            "state_dict_schema_hash": entry.state_dict_schema_hash,
            "aggregatable_parameter_names": entry.aggregatable_parameter_names,
            "personalizable_parameter_names": entry.personalizable_parameter_names,
            "supported_datasets": entry.supported_datasets,
            "supported_algorithms": entry.supported_algorithms,
            "checkpoint_reference": entry.checkpoint_reference,
            "checksum": entry.checksum,
            "status": entry.status,
            "created_at": entry.created_at,
            "updated_at": entry.updated_at,
        }
        temp_path = path.with_suffix(".json.tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temp_path, path)

    def _read(self, path: Path) -> ModelRegistryEntry:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ModelRegistryError(
                f"unreadable model registry entry at {path}: {error}"
            ) from error
        return ModelRegistryEntry(**payload)
