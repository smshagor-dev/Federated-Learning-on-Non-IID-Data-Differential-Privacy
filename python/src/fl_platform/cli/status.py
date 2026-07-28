from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(slots=True)
class RuntimeState:
    schema_version: int
    launcher_pid: int
    started_at_unix_s: int
    compose_project_name: str
    profile: str
    compose_files: list[str]
    backend_services: list[str]
    web_pid: int | None
    web_port: int
    keep_backend: bool


def load_runtime_state(path: Path) -> RuntimeState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return RuntimeState(**payload)
    except TypeError:
        return None


def save_runtime_state(path: Path, state: RuntimeState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent
    ) as handle:
        json.dump(asdict(state), handle, indent=2)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def delete_runtime_state(path: Path) -> None:
    if path.exists():
        path.unlink()


def process_is_running(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def new_runtime_state(
    compose_project_name: str,
    profile: str,
    compose_files: list[str],
    backend_services: list[str],
    web_pid: int | None,
    web_port: int,
    keep_backend: bool,
) -> RuntimeState:
    return RuntimeState(
        schema_version=1,
        launcher_pid=os.getpid(),
        started_at_unix_s=int(time.time()),
        compose_project_name=compose_project_name,
        profile=profile,
        compose_files=compose_files,
        backend_services=backend_services,
        web_pid=web_pid,
        web_port=web_port,
        keep_backend=keep_backend,
    )
