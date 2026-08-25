"""Fail-closed release artifact and immutable-image security primitives."""

from __future__ import annotations

import hashlib
import re
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

_DIGEST_REF = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_IMAGE_LINE = re.compile(r"^(?P<prefix>\s*image:\s*)(?P<image>\S+)(?P<suffix>\s*)$")


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseArtifactManifest:
    schema_version: int
    version: str
    commit_sha: str
    artifacts: tuple[ReleaseArtifact, ...]
    release_ready_version: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "commit_sha": self.commit_sha,
            "release_ready_version": self.release_ready_version,
            "artifacts": [asdict(artifact) for artifact in self.artifacts],
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_release_artifact_manifest(
    root: Path,
    artifacts: Iterable[Path],
    *,
    version: str,
    commit_sha: str,
) -> ReleaseArtifactManifest:
    root = root.resolve()
    if not version.strip():
        raise ValueError("release version must not be empty")
    normalized_commit = commit_sha.lower()
    if _COMMIT_SHA.fullmatch(normalized_commit) is None:
        raise ValueError("commit_sha must be a full 40-character hexadecimal SHA")

    entries: list[ReleaseArtifact] = []
    seen: set[str] = set()
    for artifact_path in artifacts:
        resolved = artifact_path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError("release artifact must be contained by root") from exc
        if relative in seen:
            raise ValueError(f"duplicate release artifact: {relative}")
        seen.add(relative)
        if not resolved.is_file():
            raise ValueError(f"release artifact does not exist: {relative}")
        entries.append(
            ReleaseArtifact(
                path=relative,
                size_bytes=resolved.stat().st_size,
                sha256=sha256_file(resolved),
            )
        )
    if not entries:
        raise ValueError("at least one release artifact is required")
    entries.sort(key=lambda item: item.path)
    release_ready_version = not any(
        marker in version.lower() for marker in ("dev", "rc", "alpha", "beta")
    )
    return ReleaseArtifactManifest(
        schema_version=1,
        version=version,
        commit_sha=normalized_commit,
        artifacts=tuple(entries),
        release_ready_version=release_ready_version,
    )


def validate_immutable_image_reference(image: str) -> None:
    if _DIGEST_REF.fullmatch(image) is None:
        raise ValueError(
            "release image reference must be immutable and use @sha256:<64 lowercase hex>"
        )


def discover_kubernetes_images(input_dir: Path) -> tuple[str, ...]:
    if not input_dir.is_dir():
        raise ValueError("Kubernetes input directory does not exist")
    images: set[str] = set()
    for path in sorted(input_dir.glob("*.yaml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _IMAGE_LINE.fullmatch(line)
            if match is not None:
                images.add(match.group("image"))
    if not images:
        raise ValueError("no Kubernetes image references were discovered")
    return tuple(sorted(images))


def render_immutable_kubernetes_images(
    input_dir: Path,
    output_dir: Path,
    image_lock: Mapping[str, str],
) -> tuple[str, ...]:
    discovered = discover_kubernetes_images(input_dir)
    discovered_set = set(discovered)
    missing = discovered_set - set(image_lock)
    unused = set(image_lock) - discovered_set
    if missing:
        raise ValueError(f"image lock is missing references: {sorted(missing)}")
    if unused:
        raise ValueError(f"image lock contains unused references: {sorted(unused)}")
    for original in discovered:
        validate_immutable_image_reference(image_lock[original])

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    replaced: set[str] = set()
    for source in sorted(input_dir.glob("*.yaml")):
        rendered_lines: list[str] = []
        for line in source.read_text(encoding="utf-8").splitlines():
            match = _IMAGE_LINE.fullmatch(line)
            if match is None:
                rendered_lines.append(line)
                continue
            original = match.group("image")
            replacement = image_lock[original]
            replaced.add(original)
            rendered_lines.append(
                f"{match.group('prefix')}{replacement}{match.group('suffix')}"
            )
        destination = output_dir / source.name
        destination.write_text("\n".join(rendered_lines) + "\n", encoding="utf-8")

    if replaced != discovered_set:
        raise RuntimeError("not every discovered image reference was rendered")
    return discovered


__all__ = [
    "ReleaseArtifact",
    "ReleaseArtifactManifest",
    "build_release_artifact_manifest",
    "discover_kubernetes_images",
    "render_immutable_kubernetes_images",
    "sha256_file",
    "validate_immutable_image_reference",
]
