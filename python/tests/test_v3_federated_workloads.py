from __future__ import annotations

import json
from pathlib import Path

import pytest

from fl_platform.datasets.federated_loaders import (
    DatasetShardSpec,
    FederatedDatasetError,
    FederatedDatasetManifest,
    load_femnist_leaf,
    load_sent140_leaf,
    load_shakespeare_leaf,
    sha256_file,
)


def _write_shard(
    root: Path,
    name: str,
    *,
    users: dict[str, tuple[list[object], list[object]]],
) -> DatasetShardSpec:
    path = root / name
    payload = {
        "users": list(users),
        "num_samples": [len(features) for features, _ in users.values()],
        "user_data": {
            user_id: {"x": features, "y": labels}
            for user_id, (features, labels) in users.items()
        },
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return DatasetShardSpec(relative_path=name, sha256=sha256_file(path))


def _manifest(
    dataset_name: str,
    shards: tuple[DatasetShardSpec, ...],
    *,
    license_status: str = "verified",
) -> FederatedDatasetManifest:
    return FederatedDatasetManifest(
        dataset_name=dataset_name,
        source="local-test-fixture",
        license_status=license_status,
        license_note="fixture data generated for tests",
        shards=shards,
    )


def test_femnist_loader_merges_verified_local_shards_by_user(tmp_path: Path) -> None:
    first = _write_shard(
        tmp_path,
        "part-1.json",
        users={"writer-a": ([[0, 1], [1, 0]], [1, 2])},
    )
    second = _write_shard(
        tmp_path,
        "part-2.json",
        users={"writer-b": ([[2, 2]], [3])},
    )
    bundle = load_femnist_leaf(
        tmp_path,
        _manifest("femnist", (first, second)),
    )

    assert bundle.dataset_name == "femnist"
    assert bundle.user_count == 2
    assert bundle.sample_count == 3
    assert bundle.user("writer-a").sample_count == 2
    assert bundle.verified_shards == ("part-1.json", "part-2.json")


def test_shakespeare_and_sent140_wrappers_bind_dataset_name(tmp_path: Path) -> None:
    shard = _write_shard(
        tmp_path,
        "text.json",
        users={"speaker": (["to be"], ["next-token"])},
    )
    shakespeare = load_shakespeare_leaf(
        tmp_path,
        _manifest("shakespeare", (shard,)),
    )
    assert shakespeare.user("speaker").features == ("to be",)

    sent140 = load_sent140_leaf(
        tmp_path,
        _manifest("sent140", (shard,)),
    )
    assert sent140.sample_count == 1

    with pytest.raises(FederatedDatasetError, match="dataset_name"):
        load_femnist_leaf(tmp_path, _manifest("sent140", (shard,)))


def test_integrity_mismatch_fails_before_dataset_is_exposed(tmp_path: Path) -> None:
    shard = _write_shard(
        tmp_path,
        "part.json",
        users={"u": ([[1]], [1])},
    )
    path = tmp_path / shard.relative_path
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    with pytest.raises(FederatedDatasetError, match="integrity mismatch"):
        load_femnist_leaf(tmp_path, _manifest("femnist", (shard,)))


def test_unverified_license_fails_closed_by_default(tmp_path: Path) -> None:
    shard = _write_shard(
        tmp_path,
        "part.json",
        users={"u": ([[1]], [1])},
    )
    manifest = _manifest(
        "femnist",
        (shard,),
        license_status="review-required",
    )
    with pytest.raises(FederatedDatasetError, match="license status"):
        load_femnist_leaf(tmp_path, manifest)

    experimental = load_femnist_leaf(
        tmp_path,
        manifest,
        require_verified_license=False,
    )
    assert experimental.license_status == "review-required"


def test_duplicate_user_across_shards_is_rejected(tmp_path: Path) -> None:
    first = _write_shard(
        tmp_path,
        "one.json",
        users={"same-user": ([[1]], [1])},
    )
    second = _write_shard(
        tmp_path,
        "two.json",
        users={"same-user": ([[2]], [2])},
    )
    with pytest.raises(FederatedDatasetError, match="duplicate federated user"):
        load_femnist_leaf(
            tmp_path,
            _manifest("femnist", (first, second)),
        )


def test_malformed_declared_sample_count_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(
        json.dumps(
            {
                "users": ["u"],
                "num_samples": [3],
                "user_data": {"u": {"x": [[1]], "y": [1]}},
            }
        ),
        encoding="utf-8",
    )
    shard = DatasetShardSpec(relative_path="bad.json", sha256=sha256_file(path))
    with pytest.raises(FederatedDatasetError, match="declared sample count"):
        load_femnist_leaf(tmp_path, _manifest("femnist", (shard,)))


def test_unsafe_paths_and_duplicate_shard_specs_are_rejected(tmp_path: Path) -> None:
    shard = _write_shard(
        tmp_path,
        "safe.json",
        users={"u": ([[1]], [1])},
    )
    unsafe = DatasetShardSpec(relative_path="../escape.json", sha256="0" * 64)
    with pytest.raises(FederatedDatasetError, match="safe and relative"):
        load_femnist_leaf(tmp_path, _manifest("femnist", (unsafe,)))

    with pytest.raises(FederatedDatasetError, match="duplicate dataset shard"):
        load_femnist_leaf(
            tmp_path,
            _manifest("femnist", (shard, shard)),
        )
