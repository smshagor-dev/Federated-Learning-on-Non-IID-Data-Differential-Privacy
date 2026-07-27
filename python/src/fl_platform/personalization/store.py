"""Persistent per-client personalized model store (the Algorithm Expansion phase, Work
Package G). See docs/personalized-model-store.md.

Filesystem layout under `root`:
    {root}/{run_id}/{client_id}/{algorithm}.json    -- metadata (this file)
    {root}/{run_id}/{client_id}/{algorithm}.v{N}.pt -- tensor artifact, N = version

Atomicity follows the same pattern as the Aggregation Core phase's
AggregatorCheckpointStore (C++) and the Coordinator Runtime phase's
FilesystemClientAlgorithmStateStore: write to a temporary sibling file,
then atomically replace the real path — a crash mid-write never leaves a
partially-written artifact where the real path is expected to be.

Artifacts are loaded with `torch.load(..., weights_only=True)`, which
restricts unpickling to tensor data (no arbitrary object graphs / code
execution) — see docs/personalized-model-store.md's security section and
Work Package P's "no unsafe pickle loading" requirement.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import torch

_VALID_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class PersonalizedModelStoreError(RuntimeError):
    pass


class PersonalizedModelCorruptionError(PersonalizedModelStoreError):
    """Checksum mismatch or a truncated/unreadable artifact file."""


class PersonalizedModelOwnershipError(PersonalizedModelStoreError):
    """The stored record's run_id/client_id does not match what was
    requested — either a path-traversal attempt or a corrupted/tampered
    metadata file, never returned to the caller as if it were valid."""


class PersonalizedModelSchemaError(PersonalizedModelStoreError):
    """Architecture name or state-dict schema hash mismatch: the
    checkpoint was produced by a different model than the caller is
    about to load it into."""


def _validate_id(value: str, label: str) -> None:
    if not value or not _VALID_ID.match(value):
        raise PersonalizedModelStoreError(
            f"invalid {label} '{value}': must be non-empty and contain only "
            "letters, digits, '_', '-', '.'  (path-traversal defense — see "
            "docs/personalized-model-store.md)"
        )


@dataclass(slots=True)
class PersonalizedModelRecord:
    schema_version: int
    run_id: str
    client_id: str
    algorithm: str
    global_model_version: str
    personalized_model_version: int
    architecture_name: str
    state_dict_schema_hash: str
    state_dict: dict[str, torch.Tensor]
    training_metrics: dict[str, float] = field(default_factory=dict)
    parent_checkpoint_reference: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    artifact_checksum: str = ""

    @property
    def state_dict_manifest(self) -> list[str]:
        return sorted(self.state_dict)


CURRENT_SCHEMA_VERSION = 1


class FilesystemPersonalizedModelStore:
    def __init__(self, root: str | Path, *, max_retained_versions: int = 3) -> None:
        self._root = Path(root)
        self._max_retained_versions = max_retained_versions
        self._root.mkdir(parents=True, exist_ok=True)
        self._cleanup_stale_temp_files()

    def _cleanup_stale_temp_files(self) -> None:
        # best-effort; a concurrent writer may own one of these files
        for temp_path in self._root.rglob("*.tmp"):
            with contextlib.suppress(OSError):
                temp_path.unlink()

    def _client_dir(self, run_id: str, client_id: str) -> Path:
        _validate_id(run_id, "run_id")
        _validate_id(client_id, "client_id")
        return self._root / run_id / client_id

    def _metadata_path(self, run_id: str, client_id: str, algorithm: str) -> Path:
        _validate_id(algorithm, "algorithm")
        return self._client_dir(run_id, client_id) / f"{algorithm}.json"

    def _artifact_path(
        self, run_id: str, client_id: str, algorithm: str, version: int
    ) -> Path:
        _validate_id(algorithm, "algorithm")
        return self._client_dir(run_id, client_id) / f"{algorithm}.v{version}.pt"

    def load(
        self,
        run_id: str,
        client_id: str,
        algorithm: str,
        *,
        expected_architecture: str | None = None,
        expected_schema_hash: str | None = None,
    ) -> PersonalizedModelRecord | None:
        metadata_path = self._metadata_path(run_id, client_id, algorithm)
        if not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise PersonalizedModelCorruptionError(
                f"unreadable metadata at {metadata_path}: {error}"
            ) from error

        stored_run_id, stored_client_id = (
            metadata.get("run_id"),
            metadata.get("client_id"),
        )
        if stored_run_id != run_id or stored_client_id != client_id:
            claimed = f"run_id={stored_run_id!r} client_id={stored_client_id!r}"
            wanted = f"run_id={run_id!r} client_id={client_id!r}"
            message = f"metadata claims {claimed}, expected {wanted}"
            raise PersonalizedModelOwnershipError(message)
        got_arch = metadata.get("architecture_name")
        want_arch = expected_architecture
        if want_arch is not None and got_arch != want_arch:
            raise PersonalizedModelSchemaError(
                f"architecture mismatch: got {got_arch!r}, expected {want_arch!r}"
            )
        got_hash = metadata.get("state_dict_schema_hash")
        want_hash = expected_schema_hash
        if want_hash is not None and got_hash != want_hash:
            raise PersonalizedModelSchemaError(
                f"schema hash mismatch: got {got_hash}, expected {want_hash}"
            )

        version = int(metadata["personalized_model_version"])
        artifact_path = self._artifact_path(run_id, client_id, algorithm, version)
        if not artifact_path.exists():
            raise PersonalizedModelCorruptionError(
                f"metadata references missing artifact: {artifact_path}"
            )
        artifact_bytes = artifact_path.read_bytes()
        actual_checksum = _sha256_hex(artifact_bytes)
        if actual_checksum != metadata.get("artifact_checksum"):
            raise PersonalizedModelCorruptionError(
                f"checksum mismatch for {artifact_path}: truncated or corrupted"
            )
        state_dict = torch.load(artifact_path, weights_only=True, map_location="cpu")

        return PersonalizedModelRecord(
            schema_version=metadata["schema_version"],
            run_id=run_id,
            client_id=client_id,
            algorithm=algorithm,
            global_model_version=metadata["global_model_version"],
            personalized_model_version=version,
            architecture_name=metadata["architecture_name"],
            state_dict_schema_hash=metadata["state_dict_schema_hash"],
            state_dict=state_dict,
            training_metrics=metadata.get("training_metrics", {}),
            parent_checkpoint_reference=metadata.get("parent_checkpoint_reference", ""),
            created_at=metadata.get("created_at", 0.0),
            updated_at=metadata.get("updated_at", 0.0),
            artifact_checksum=actual_checksum,
        )

    def save(self, record: PersonalizedModelRecord) -> None:
        client_dir = self._client_dir(record.run_id, record.client_id)
        client_dir.mkdir(parents=True, exist_ok=True)
        _validate_id(record.algorithm, "algorithm")

        artifact_path = self._artifact_path(
            record.run_id,
            record.client_id,
            record.algorithm,
            record.personalized_model_version,
        )
        temp_artifact_path = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
        torch.save(record.state_dict, temp_artifact_path)
        os.replace(temp_artifact_path, artifact_path)
        artifact_checksum = _sha256_hex(artifact_path.read_bytes())

        now = time.time()
        metadata = {
            "schema_version": record.schema_version,
            "run_id": record.run_id,
            "client_id": record.client_id,
            "algorithm": record.algorithm,
            "global_model_version": record.global_model_version,
            "personalized_model_version": record.personalized_model_version,
            "architecture_name": record.architecture_name,
            "state_dict_manifest": record.state_dict_manifest,
            "state_dict_schema_hash": record.state_dict_schema_hash,
            "artifact_checksum": artifact_checksum,
            "created_at": record.created_at or now,
            "updated_at": now,
            "training_metrics": record.training_metrics,
            "parent_checkpoint_reference": record.parent_checkpoint_reference,
        }
        metadata_path = self._metadata_path(
            record.run_id, record.client_id, record.algorithm
        )
        temp_metadata_path = metadata_path.with_suffix(".json.tmp")
        temp_metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        os.replace(temp_metadata_path, metadata_path)

        self._enforce_retention(
            record.run_id,
            record.client_id,
            record.algorithm,
            record.personalized_model_version,
        )

    def _enforce_retention(
        self, run_id: str, client_id: str, algorithm: str, current_version: int
    ) -> None:
        if self._max_retained_versions <= 0:
            return
        client_dir = self._client_dir(run_id, client_id)
        oldest_kept = current_version - self._max_retained_versions + 1
        for candidate in client_dir.glob(f"{algorithm}.v*.pt"):
            match = re.match(rf"^{re.escape(algorithm)}\.v(\d+)\.pt$", candidate.name)
            if not match:
                continue
            version = int(match.group(1))
            if version < oldest_kept:
                with contextlib.suppress(OSError):
                    candidate.unlink()


def _sha256_hex(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()


class PersonalizedModelCache:
    """Bounded worker-local LRU cache of loaded PersonalizedModelRecords —
    the store above always hits disk; this avoids re-reading/re-loading a
    client's checkpoint on every task when the same worker serves that
    client repeatedly across rounds. Never grows past `max_entries`."""

    def __init__(self, max_entries: int = 8) -> None:
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: OrderedDict[tuple[str, str, str], PersonalizedModelRecord] = (
            OrderedDict()
        )
        self.hits = 0
        self.misses = 0

    def get(
        self, run_id: str, client_id: str, algorithm: str
    ) -> PersonalizedModelRecord | None:
        key = (run_id, client_id, algorithm)
        if key not in self._entries:
            self.misses += 1
            return None
        self.hits += 1
        self._entries.move_to_end(key)
        return self._entries[key]

    def put(self, record: PersonalizedModelRecord) -> None:
        key = (record.run_id, record.client_id, record.algorithm)
        self._entries[key] = record
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
