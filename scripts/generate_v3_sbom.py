#!/usr/bin/env python3
"""Generate a deterministic CycloneDX-compatible Python dependency inventory."""

from __future__ import annotations

import argparse
import json
from importlib import metadata
from pathlib import Path


def build_sbom() -> dict[str, object]:
    components = [
        {
            "type": "library",
            "name": distribution.metadata["Name"],
            "version": distribution.version,
            "purl": (
                "pkg:pypi/"
                f"{distribution.metadata['Name'].lower().replace('_', '-')}@{distribution.version}"
            ),
        }
        for distribution in metadata.distributions()
        if distribution.metadata["Name"]
    ]
    components.sort(key=lambda component: (str(component["name"]).lower(), str(component["version"])))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": "fl-platform", "version": "3.0.0.dev0"}},
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("artifacts/v3-python-sbom.cdx.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(build_sbom(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
