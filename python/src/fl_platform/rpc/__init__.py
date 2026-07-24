"""Real gRPC client/server code for the Milestone 3 coordinator contract.

Generated protobuf/gRPC Python modules live in
``python/src/fl_platform/generated/`` and are regenerated on demand (see
``make proto`` / ``scripts/generate_protos.*``), never committed — the same
policy as the C++ and Go generated code (docs/protobuf-generation.md).

protoc's Python plugin emits cross-file imports as bare top-level module
names (e.g. ``from worker import worker_pb2``, because the .proto files
import each other as ``worker/worker.proto`` relative to --proto_path).
That only resolves if the generated/ directory itself is on sys.path, so
every module in this package must call :func:`ensure_generated_on_path`
before importing anything from ``fl_platform.generated``.
"""

from __future__ import annotations

import sys
from pathlib import Path

_GENERATED_ROOT = Path(__file__).resolve().parents[1] / "generated"


def ensure_generated_on_path() -> None:
    path_str = str(_GENERATED_ROOT)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def generated_root_exists() -> bool:
    return _GENERATED_ROOT.is_dir()
