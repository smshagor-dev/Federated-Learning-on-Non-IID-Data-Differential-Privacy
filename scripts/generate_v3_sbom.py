#!/usr/bin/env python3
"""Generate a deterministic CycloneDX-compatible Python dependency inventory."""

from __future__ import annotations

import argparse
import json
from importlib import metadata
from pathlib import Path

_PROJECT_NAME = "fl-platform"


def build_sbom() -> dict[str, object]:
    project_version = metadata.version(_PROJECT_NAME)
    components_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for distribution in metadata.distributions():
        raw_name = distribution.metadata["Name"]
        if not raw_name:
            continue
        normalized_name = raw_name.lower().replace("_", "-")
        if normalized_name == _PROJECT_NAME:
            continue
        key = (normalized_name, distribution.version)
        components_by_key[key] = {
            "type": "library",
            "name": raw_name,
            "version": distribution.version,
            "purl": f"pkg:pypi/{normalized_name}@{distribution.version}",
        }
    components = [
        components_by_key[key]
        for key in sorted(components_by_key, key=lambda item: (item[0], item[1]))
    ]
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": _PROJECT_NAME,
                "version": project_version,
            }
        },
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/v3-python-sbom.cdx.json"),
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
