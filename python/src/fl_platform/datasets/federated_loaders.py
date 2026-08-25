"""Verified local loaders for naturally federated LEAF-style datasets.

The loader never downloads data. A caller supplies local JSON shards plus an
explicit provenance manifest. Integrity and license status are checked before
any sample is exposed, making release use fail closed while keeping CI fully
offline and deterministic.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast


class FederatedDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class DatasetShardSpec:
    relative_path: str
    sha256: str

    def validate(self) -> None:
        path = Path(self.relative_path)
        if not self.relative_path or path.is_absolute() or ".." in path.parts:
            raise FederatedDatasetError("dataset shard path must be safe and relative")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise FederatedDatasetError("dataset shard sha256 must be 64 lowercase hex characters")


@dataclass(frozen=True)
class FederatedDatasetManifest:
    dataset_name: str
    source: str
    license_status: str
    license_note: str
    shards: tuple[DatasetShardSpec, ...]
    preprocessing: str = "leaf-json"

    def validate(self, *, require_verified_license: bool = True) -> None:
        if not self.dataset_name:
            raise FederatedDatasetError("dataset_name must not be empty")
        if not self.source:
            raise FederatedDatasetError("dataset source must not be empty")
        if not self.license_note:
            raise FederatedDatasetError("license_note must not be empty")
        if require_verified_license and self.license_status != "verified":
            raise FederatedDatasetError(
                f"dataset license status is not verified: {self.license_status}"
            )
        if not self.shards:
            raise FederatedDatasetError("at least one dataset shard is required")
        seen: set[str] = set()
        for shard in self.shards:
            shard.validate()
            if shard.relative_path in seen:
                raise FederatedDatasetError(
                    f"duplicate dataset shard path: {shard.relative_path}"
                )
            seen.add(shard.relative_path)


@dataclass(frozen=True)
class FederatedUserPartition:
    user_id: str
    features: tuple[object, ...]
    labels: tuple[object, ...]

    @property
    def sample_count(self) -> int:
        return len(self.features)


@dataclass(frozen=True)
class FederatedDatasetBundle:
    dataset_name: str
    source: str
    license_status: str
    preprocessing: str
    users: tuple[FederatedUserPartition, ...]
    verified_shards: tuple[str, ...]

    @property
    def user_count(self) -> int:
        return len(self.users)

    @property
    def sample_count(self) -> int:
        return sum(user.sample_count for user in self.users)

    def user(self, user_id: str) -> FederatedUserPartition:
        for partition in self.users:
            if partition.user_id == user_id:
                return partition
        raise KeyError(user_id)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_mapping(value: object, *, field: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise FederatedDatasetError(f"{field} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise FederatedDatasetError(f"{field} keys must be strings")
    return cast(dict[str, object], value)


def _as_list(value: object, *, field: str) -> list[object]:
    if not isinstance(value, list):
        raise FederatedDatasetError(f"{field} must be a list")
    return value


def _load_leaf_shard(path: Path) -> tuple[FederatedUserPartition, ...]:
    try:
        raw = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FederatedDatasetError(f"failed to parse dataset shard {path.name}") from exc

    root = _as_mapping(raw, field="root")
    users_raw = _as_list(root.get("users"), field="users")
    counts_raw = _as_list(root.get("num_samples"), field="num_samples")
    user_data = _as_mapping(root.get("user_data"), field="user_data")

    if len(users_raw) != len(counts_raw):
        raise FederatedDatasetError("users and num_samples lengths must match")

    users: list[FederatedUserPartition] = []
    for position, (user_value, count_value) in enumerate(
        zip(users_raw, counts_raw, strict=True)
    ):
        if not isinstance(user_value, str) or not user_value:
            raise FederatedDatasetError(f"users[{position}] must be a non-empty string")
        if isinstance(count_value, bool) or not isinstance(count_value, int) or count_value < 0:
            raise FederatedDatasetError(
                f"num_samples[{position}] must be a non-negative integer"
            )
        if user_value not in user_data:
            raise FederatedDatasetError(f"missing user_data for user {user_value}")
        record = _as_mapping(user_data[user_value], field=f"user_data.{user_value}")
        features = _as_list(record.get("x"), field=f"user_data.{user_value}.x")
        labels = _as_list(record.get("y"), field=f"user_data.{user_value}.y")
        if len(features) != len(labels):
            raise FederatedDatasetError(
                f"feature/label length mismatch for user {user_value}"
            )
        if len(features) != count_value:
            raise FederatedDatasetError(
                f"declared sample count mismatch for user {user_value}"
            )
        users.append(
            FederatedUserPartition(
                user_id=user_value,
                features=tuple(features),
                labels=tuple(labels),
            )
        )

    listed = {user.user_id for user in users}
    extra = sorted(set(user_data).difference(listed))
    if extra:
        raise FederatedDatasetError(
            f"user_data contains users not declared in users: {extra}"
        )
    return tuple(users)


def load_federated_leaf_dataset(
    root: str | Path,
    manifest: FederatedDatasetManifest,
    *,
    require_verified_license: bool = True,
) -> FederatedDatasetBundle:
    """Load verified local LEAF JSON shards without network access."""
    manifest.validate(require_verified_license=require_verified_license)
    root_path = Path(root)
    if not root_path.is_dir():
        raise FederatedDatasetError("federated dataset root must be an existing directory")

    users: list[FederatedUserPartition] = []
    seen_users: set[str] = set()
    verified: list[str] = []
    for shard in manifest.shards:
        path = root_path / shard.relative_path
        if not path.is_file():
            raise FederatedDatasetError(
                f"dataset shard does not exist: {shard.relative_path}"
            )
        actual_digest = sha256_file(path)
        if actual_digest != shard.sha256.lower():
            raise FederatedDatasetError(
                f"dataset shard integrity mismatch: {shard.relative_path}"
            )
        for user in _load_leaf_shard(path):
            if user.user_id in seen_users:
                raise FederatedDatasetError(
                    f"duplicate federated user across shards: {user.user_id}"
                )
            seen_users.add(user.user_id)
            users.append(user)
        verified.append(shard.relative_path)

    if not users:
        raise FederatedDatasetError("federated dataset contains no users")
    return FederatedDatasetBundle(
        dataset_name=manifest.dataset_name,
        source=manifest.source,
        license_status=manifest.license_status,
        preprocessing=manifest.preprocessing,
        users=tuple(users),
        verified_shards=tuple(verified),
    )


def _load_named_workload(
    name: str,
    root: str | Path,
    manifest: FederatedDatasetManifest,
    *,
    require_verified_license: bool,
) -> FederatedDatasetBundle:
    if manifest.dataset_name.lower() != name:
        raise FederatedDatasetError(
            f"manifest dataset_name must be {name}, got {manifest.dataset_name}"
        )
    return load_federated_leaf_dataset(
        root,
        manifest,
        require_verified_license=require_verified_license,
    )


def load_femnist_leaf(
    root: str | Path,
    manifest: FederatedDatasetManifest,
    *,
    require_verified_license: bool = True,
) -> FederatedDatasetBundle:
    return _load_named_workload(
        "femnist",
        root,
        manifest,
        require_verified_license=require_verified_license,
    )


def load_shakespeare_leaf(
    root: str | Path,
    manifest: FederatedDatasetManifest,
    *,
    require_verified_license: bool = True,
) -> FederatedDatasetBundle:
    return _load_named_workload(
        "shakespeare",
        root,
        manifest,
        require_verified_license=require_verified_license,
    )


def load_sent140_leaf(
    root: str | Path,
    manifest: FederatedDatasetManifest,
    *,
    require_verified_license: bool = True,
) -> FederatedDatasetBundle:
    return _load_named_workload(
        "sent140",
        root,
        manifest,
        require_verified_license=require_verified_license,
    )


__all__ = [
    "DatasetShardSpec",
    "FederatedDatasetBundle",
    "FederatedDatasetError",
    "FederatedDatasetManifest",
    "FederatedUserPartition",
    "load_federated_leaf_dataset",
    "load_femnist_leaf",
    "load_sent140_leaf",
    "load_shakespeare_leaf",
    "sha256_file",
]
