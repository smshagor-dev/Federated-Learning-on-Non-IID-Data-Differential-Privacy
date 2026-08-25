"""Worker startup configuration: CLI args, environment variables, or a
TOML file, in that precedence order (CLI overrides env overrides file
overrides defaults).
"""

from __future__ import annotations

import argparse
import os
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


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
    # Security Runtime Completion and Release Evidence slice
    # (docs/security-event-centralization.md): these three were
    # previously silently unused -- WorkerConfig declared tls_enabled/
    # tls_ca_cert_path but __main__.py never read them when constructing
    # GrpcCoordinatorClient, so a deployed worker container always
    # connected insecure regardless of this config. Now wired for real
    # in __main__.py's main(). client_cert/client_key must both be set
    # together for mTLS (a client certificate presented to the
    # coordinator), or both left empty for TLS-only (server-authenticated).
    tls_client_cert_path: str = ""
    tls_client_key_path: str = ""
    # The coordinator's certificate carries a spiffe:// URI SAN, which
    # Python's ssl/grpc hostname verification does not check the way it
    # checks DNS SANs -- this must be a DNS name actually present on the
    # coordinator's certificate (e.g. its Compose service-discovery
    # hostname), matching the same real-world constraint already
    # documented for the Go side in infra/compose/docker-compose.security.yml.
    tls_server_name: str = ""
    # Directory holding this worker's persistent Ed25519 signing
    # identity (<worker_id>.signing-key.pem/.pub -- see
    # signing_identity.py). Empty disables signing entirely (every
    # signed-message code path in GrpcCoordinatorClient is already
    # optional-by-default). If the directory exists but no key file for
    # this worker_id is found yet, one is generated and persisted on
    # first boot -- trust-on-first-use, the same model
    # RegisterWorker's signed-capability path already assumes.
    signing_key_dir: str = ""
    # Sequence-number state (nonce/sequence tracking across process
    # restarts) -- see sequence_state.py. Empty uses
    # GrpcCoordinatorClient's own default
    # (".fl_worker_sequence_state.<worker_id>.json").
    sequence_state_path: str = ""
    # Coordinator-Signed Tasks slice (docs/signed-coordinator-tasks.md):
    # path to the trusted coordinator public-key bundle file. Like
    # signing_key_dir/security_event_journal_path above, this was never
    # actually wired into __main__.py before this slice -- a deployed
    # worker container never verified coordinator-signed tasks at all,
    # regardless of whether the coordinator was signing them. Empty
    # disables coordinator-task verification (acquire_task accepts
    # unsigned tasks, the pre-existing default-compatible behavior).
    trusted_coordinator_keys_path: str = ""
    # Security Runtime Completion and Release Evidence slice, Work
    # Package B: the worker-local durable security-event journal this
    # worker's WorkerSecurityEventQueue selects pending events from and
    # periodically flushes to the coordinator via
    # GrpcCoordinatorClient.submit_security_events. Empty disables
    # worker-side security-event journaling/centralization entirely
    # (matches _emit_security_event's existing "no-op unless
    # configured" convention).
    security_event_journal_path: str = ""
    # How often the background flush thread (see __main__.py's
    # _run_security_event_flush_loop) attempts to submit any
    # locally-queued security events to the coordinator. Only consulted
    # when security_event_journal_path is set.
    security_event_flush_interval_seconds: float = 15.0
    # Privacy Engineering phase: which run this worker polls against, over
    # the real gRPC path (GrpcCoordinatorClient) — see
    # docs/create-run-wire-mapping.md. Empty means "no run configured yet";
    # __main__.py falls back to Health()-only polling in that case rather
    # than acquiring tasks for an undefined run.
    run_id: str = ""
    # Privacy Engineering phase: this worker's Prometheus HTTP endpoint
    # (see fl_platform.privacy.metrics) — 0 disables it entirely (no
    # port bound), since a worker running non-private training has
    # nothing privacy-specific to export and shouldn't claim a port by
    # default in every deployment.
    metrics_port: int = 0


_ENV_PREFIX = "FL_WORKER_"


def _load_toml_defaults(path: Path | None) -> dict[str, Any]:
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
    parser.add_argument("--run-id", type=str, default=None)
    parser.add_argument("--metrics-port", type=int, default=None)
    parser.add_argument("--tls-enabled", action="store_true", default=None)
    parser.add_argument("--tls-ca-cert-path", type=str, default=None)
    parser.add_argument("--tls-client-cert-path", type=str, default=None)
    parser.add_argument("--tls-client-key-path", type=str, default=None)
    parser.add_argument("--tls-server-name", type=str, default=None)
    parser.add_argument("--signing-key-dir", type=str, default=None)
    parser.add_argument("--sequence-state-path", type=str, default=None)
    parser.add_argument("--trusted-coordinator-keys-path", type=str, default=None)
    parser.add_argument("--security-event-journal-path", type=str, default=None)
    parser.add_argument(
        "--security-event-flush-interval-seconds", type=float, default=None
    )
    args = parser.parse_args(argv)

    file_defaults = _load_toml_defaults(args.config_file)
    config = WorkerConfig(**{**{}, **file_defaults})
    _apply_env(config)

    for f in fields(config):
        cli_value = getattr(args, f.name, None)
        if cli_value is not None:
            setattr(config, f.name, cli_value)

    return config
