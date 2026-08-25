#!/usr/bin/env python3
"""Render Kubernetes release manifests from a complete immutable image lock."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fl_platform.v3.release_security import render_immutable_kubernetes_images


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("infra/kubernetes"),
    )
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    payload = json.loads(args.lock.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise SystemExit("image lock must be a JSON object of string -> string")
    rendered = render_immutable_kubernetes_images(
        args.input_dir,
        args.output_dir,
        payload,
    )
    print(json.dumps({"rendered_images": list(rendered)}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
