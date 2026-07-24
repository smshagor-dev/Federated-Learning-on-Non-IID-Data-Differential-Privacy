"""Worker startup configuration: CLI args, environment variables, or a
TOML file, in that precedence order (CLI overrides env overrides file
overrides defaults).
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, fields
from pathlib import Path

import tomllib


@dataclass(slots=True)
class WorkerConfig:
    coordinator_address: str = "127.0.0.1:50051"
    worker_id: str = "worker-1"
    device: str = "cpu"
    data_root: str = "./data_raw"
    cache_dir: str = "./.cache/fl_worker"
    heartbeat_interval_seconds: float = 10.0
    task_poll_interval_seconds: float = 2.0
    max_concurrent_tasks: int = 1
    deterministic: bool = True
    tls_enabled: bool = False
    tls_ca_cert_path: str = ""


_ENV_PREFIX = "FL_WORKER_"


def _load_toml_defaults(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _apply_env(config: WorkerConfig) -> None:
    for f in fields(config):
        env_name = _ENV_PREFIX + f.name.upper()
        if env_name in os.environ:
            raw = os.environ[env_name]
            current = getattr(config, f.name)
            if isinstance(current, bool):
                setattr(config, f.name, raw.lower() in ("1", "true", "yes", "on"))
            elif isinstance(current, int):
                setattr(config, f.name, int(raw))
            elif isinstance(current, float):
                setattr(config, f.name, float(raw))
            else:
                setattr(config, f.name, raw)


def load_worker_config(argv: list[str] | None = None) -> WorkerConfig:
    parser = argparse.ArgumentParser(description="Federated learning PyTorch worker")
    parser.add_argument("--config-file", type=Path, default=None)
    parser.add_argument("--coordinator-address", type=str, default=None)
    parser.add_argument("--worker-id", type=str, default=None)
    parser.add_argument("--device", type=str, default=None)
    parser.add_argument("--data-root", type=str, default=None)
    parser.add_argument("--cache-dir", type=str, default=None)
    parser.add_argument("--heartbeat-interval-seconds", type=float, default=None)
    parser.add_argument("--task-poll-interval-seconds", type=float, default=None)
    parser.add_argument("--max-concurrent-tasks", type=int, default=None)
    parser.add_argument("--deterministic", action="store_true", default=None)
    args = parser.parse_args(argv)

    file_defaults = _load_toml_defaults(args.config_file)
    config = WorkerConfig(**{**{}, **file_defaults})
    _apply_env(config)

    for f in fields(config):
        cli_value = getattr(args, f.name, None)
        if cli_value is not None:
            setattr(config, f.name, cli_value)

    return config
