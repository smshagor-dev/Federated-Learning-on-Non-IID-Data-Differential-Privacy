from __future__ import annotations

import json
from pathlib import Path

import pytest

from fl_platform.v3.release_security import (
    build_release_artifact_manifest,
    discover_kubernetes_images,
    render_immutable_kubernetes_images,
    validate_immutable_image_reference,
)


def _digest(name: str) -> str:
    hex_value = (name.encode("utf-8").hex() * 64)[:64]
    return f"registry.example/fl/{name}@sha256:{hex_value}"


def test_release_images_require_full_sha256_digest() -> None:
    for mutable in (
        "fl-platform/api:latest",
        "fl-platform/api:3.0.0",
        "fl-platform/api@sha256:abcd",
    ):
        with pytest.raises(ValueError, match="immutable"):
            validate_immutable_image_reference(mutable)

    validate_immutable_image_reference(_digest("api"))


def test_kubernetes_renderer_requires_complete_exact_image_lock(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "app.yaml").write_text(
        "containers:\n  - image: fl-platform/api:latest\n  - image: postgres:16\n",
        encoding="utf-8",
    )
    assert discover_kubernetes_images(source) == (
        "fl-platform/api:latest",
        "postgres:16",
    )

    with pytest.raises(ValueError, match="missing"):
        render_immutable_kubernetes_images(
            source,
            tmp_path / "out",
            {"fl-platform/api:latest": _digest("api")},
        )

    lock = {
        "fl-platform/api:latest": _digest("api"),
        "postgres:16": _digest("postgres"),
    }
    rendered = render_immutable_kubernetes_images(source, tmp_path / "out", lock)
    assert rendered == tuple(sorted(lock))
    text = (tmp_path / "out" / "app.yaml").read_text(encoding="utf-8")
    assert "fl-platform/api:latest" not in text
    assert "postgres:16" not in text
    assert all(reference in text for reference in lock.values())


def test_renderer_rejects_unused_lock_entry(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.mkdir()
    (source / "app.yaml").write_text(
        "containers:\n  - image: fl-platform/api:latest\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unused"):
        render_immutable_kubernetes_images(
            source,
            tmp_path / "out",
            {
                "fl-platform/api:latest": _digest("api"),
                "redis:7": _digest("redis"),
            },
        )


def test_release_artifact_manifest_hashes_and_sorts_files(tmp_path: Path) -> None:
    first = tmp_path / "b.whl"
    second = tmp_path / "a.tar.gz"
    first.write_bytes(b"wheel-bytes")
    second.write_bytes(b"source-bytes")

    manifest = build_release_artifact_manifest(
        tmp_path,
        (first, second),
        version="3.0.0.dev0",
        commit_sha="a" * 40,
    )
    payload = manifest.to_dict()
    assert [item["path"] for item in payload["artifacts"]] == [
        "a.tar.gz",
        "b.whl",
    ]
    assert payload["release_ready_version"] is False
    assert all(len(item["sha256"]) == 64 for item in payload["artifacts"])

    json.dumps(payload, sort_keys=True)


def test_release_version_and_commit_provenance_validation(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact.bin"
    artifact.write_bytes(b"payload")
    with pytest.raises(ValueError, match="40-character"):
        build_release_artifact_manifest(
            tmp_path,
            (artifact,),
            version="3.0.0",
            commit_sha="short",
        )

    manifest = build_release_artifact_manifest(
        tmp_path,
        (artifact,),
        version="3.0.0",
        commit_sha="b" * 40,
    )
    assert manifest.release_ready_version is True
