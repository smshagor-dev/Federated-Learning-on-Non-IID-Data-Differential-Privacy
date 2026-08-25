#!/usr/bin/env python3
"""Generate SHA-256 provenance metadata for v3 release artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tomllib

from fl_platform.v3.release_security import build_release_artifact_manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("artifacts", nargs="+", type=Path)
    args = parser.parse_args()

    pyproject = args.root / "python" / "pyproject.toml"
    version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"][
        "version"
    ]
    manifest = build_release_artifact_manifest(
        args.root,
        tuple(args.artifacts),
        version=str(version),
        commit_sha=args.commit_sha,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
