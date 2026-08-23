"""Worker process entrypoint (``python -m fl_platform.worker``).

Privacy Engineering phase: GrpcCoordinatorClient now implements every
CoordinatorClient method for real (register_worker, acquire_task,
submit_result, ...) — see coordinator_client.py's module docstring and
docs/create-run-wire-mapping.md. When ``run_id`` is configured (see
configuration.py's WorkerConfig.run_id), this entrypoint runs the real
WorkerService loop (register -> acquire task -> train -> submit ->
repeat) against the live gRPC coordinator, the same loop already
validated against the CLI-bridge transport in
tests/baseline/test_coordinator_worker_integration.py. Falls back to a
Health()-only poll loop when no run_id is configured (nothing to work
on yet) — genuine end-to-end connectivity proof between the Python
worker container and the C++ coordinator container in docker-compose.
See docs/python-worker.md and docs/docker-runtime.md.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from fl_platform.privacy import ensure_metrics_server_started
from fl_platform.security.signing_identity import (
    SigningIdentityError,
    WorkerSigningIdentity,
    generate_signing_identity,
    load_signing_identity,
    save_signing_identity,
)
from fl_platform.security.transport import WorkerTLSConfig
from fl_platform.worker.configuration import WorkerConfig, load_worker_config
from fl_platform.worker.coordinator_client import GrpcCoordinatorClient, RunSpec
from fl_platform.worker.partition_aware_client import PartitionAwareGrpcCoordinatorClient
from fl_platform.worker.service import WorkerLoopOptions, WorkerService

logger = logging.getLogger("fl_platform.worker")


def _build_tls_config(config: WorkerConfig) -> WorkerTLSConfig | None:
    """Security Runtime Completion and Release Evidence slice, Work
    Package B: config.tls_enabled/tls_ca_cert_path existed as declared
    fields before this slice but were never actually read here -- a
    deployed worker container always connected insecure regardless of
    this config (a real documentation-vs-live-behavior discrepancy,
    found by direct inspection before writing this function). Returns
    None (insecure) unless tls_enabled is set, matching every other
    optional-by-default security feature in this codebase."""
    if not config.tls_enabled:
        return None
    return WorkerTLSConfig(
        trusted_ca_path=config.tls_ca_cert_path,
        client_cert_path=config.tls_client_cert_path,
        client_key_path=config.tls_client_key_path,
        expected_server_name=config.tls_server_name,
    )


def _load_or_generate_signing_identity(
    config: WorkerConfig,
) -> WorkerSigningIdentity | None:
    """Returns None (signing disabled) unless signing_key_dir is set --
    matching every signed-message code path's existing
    optional-by-default convention. When set, loads this worker's
    persistent key if one already exists on the mounted volume;
    otherwise generates and persists a fresh one on first boot
    (trust-on-first-use, the same model RegisterWorker's signed-
    capability path already assumes for a never-before-seen worker_id)."""
    if not config.signing_key_dir:
        return None
    try:
        return load_signing_identity(config.worker_id, config.signing_key_dir)
    except SigningIdentityError:
        identity = generate_signing_identity(config.worker_id)
        save_signing_identity(identity, config.signing_key_dir)
        logger.info(
            "generated a new persistent signing identity: "
            "worker_id=%s key_id=%s dir=%s",
            config.worker_id,
            identity.key_id,
            config.signing_key_dir,
        )
        return identity


def _run_security_event_flush_loop(
    config: WorkerConfig, client: GrpcCoordinatorClient, stop_event: threading.Event
) -> None:
    """Security Runtime Completion and Release Evidence slice, Work
    Package B: periodically flushes this worker's locally-queued
    security events (see security_event_queue.py) to the coordinator,
    independent of whichever loop (_run_health_poll_loop or
    _run_training_loop) is currently driving the worker's main
    lifecycle -- a separate daemon thread rather than a hook threaded
    through WorkerService.run() itself, so this additive capability
    never touches that already-tested control flow. No-op per call
    when submit_security_events itself no-ops (journal/signing identity
    not configured) -- see GrpcCoordinatorClient.submit_security_events'
    own docstring. Never lets a flush failure reach the caller: an
    unexpected exception here must not take down the worker process
    over what is, at worst, an observability gap.
    """
    while not stop_event.wait(config.security_event_flush_interval_seconds):
        try:
            client.submit_security_events(config.worker_id)
        except Exception:  # noqa: BLE001 - observability flush must never crash the worker
            logger.exception(
                "security-event flush failed: worker_id=%s (will retry next interval)",
                config.worker_id,
            )


def _start_security_event_flush_thread(
    config: WorkerConfig, client: GrpcCoordinatorClient
) -> tuple[threading.Thread, threading.Event] | None:
    if not config.security_event_journal_path:
        return None
    stop_event = threading.Event()
    thread = threading.Thread(
        target=_run_security_event_flush_loop,
        args=(config, client, stop_event),
        name="security-event-flush",
        daemon=True,
    )
    thread.start()
    return thread, stop_event


def _run_health_poll_loop(config: WorkerConfig, client: GrpcCoordinatorClient) -> int:
    # RegisterWorker is run-agnostic (GrpcCoordinatorClient.register_worker
    # ignores `spec`/`now` entirely -- see its own comment), so a worker
    # with no run_id configured yet should still register its identity
    # with the coordinator on startup rather than being invisible to it
    # until a training run happens to start. Before this fix, only
    # WorkerService.run()'s training-loop path ever called
    # register_worker(), which meant a container run in this (the
    # default docker-compose.dev.yml) health-poll-only mode never
    # registered at all -- caught by this slice's live Docker Compose
    # validation, where worker-1 never appeared in the coordinator's
    # worker identity registry despite the container running and its
    # health checks succeeding. One-shot, not retried in a loop of its
    # own: a real deployment's coordinator is expected to already be up
    # (docker-compose.dev.yml's `depends_on: condition: service_healthy`
    # for python-worker enforces exactly that) -- if it is not, this is
    # logged and the process still starts its health-poll loop rather
    # than crashing, since Health() below will itself keep reporting the
    # outage.
    try:
        client.register_worker(RunSpec(run_id="", algorithm=""), config.worker_id, 0.0)
        logger.info(
            "worker registered with the coordinator: worker_id=%s", config.worker_id
        )
    except Exception as error:  # noqa: BLE001 - see comment above: never fatal to startup
        logger.warning(
            "worker registration failed at startup: worker_id=%s error=%s",
            config.worker_id,
            error,
        )

    attempt = 0
    try:
        while True:
            attempt += 1
            try:
                status = client.health()
                logger.info(
                    "coordinator health check ok: worker_id=%s status=%s attempt=%d",
                    config.worker_id,
                    status,
                    attempt,
                )
            except Exception as error:  # noqa: BLE001 - any transport failure just gets logged and retried
                logger.warning(
                    "coordinator health check failed: worker_id=%s attempt=%d error=%s",
                    config.worker_id,
                    attempt,
                    error,
                )
            time.sleep(config.heartbeat_interval_seconds)
    except KeyboardInterrupt:
        logger.info("worker shutting down: worker_id=%s", config.worker_id)
        return 0


def _run_training_loop(config: WorkerConfig, client: GrpcCoordinatorClient) -> int:
    logger.info(
        "worker running real training loop: worker_id=%s run_id=%s",
        config.worker_id,
        config.run_id,
    )
    spec = RunSpec(run_id=config.run_id, algorithm="")
    options = WorkerLoopOptions(
        worker_id=config.worker_id,
        # Effectively unbounded: a deployed worker container polls for the
        # lifetime of the process, not a fixed test iteration count (see
        # WorkerService.run()'s "max_iterations is None -> stop on first
        # no-task" behavior, which is the *test* shortcut, not what a
        # long-running container wants).
        max_iterations=10**9,
        poll_interval_seconds=config.task_poll_interval_seconds,
    )
    service = WorkerService(client, spec, options)
    service.install_signal_handlers()
    result = service.run()
    logger.info(
        "worker training loop stopped: worker_id=%s reason=%s completed=%d failed=%d",
        config.worker_id,
        result.stopped_reason,
        result.tasks_completed,
        result.tasks_failed,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    config = load_worker_config(argv)
    tls_config = _build_tls_config(config)
    signing_identity = _load_or_generate_signing_identity(config)
    logger.info(
        "worker starting: worker_id=%s coordinator_address=%s run_id=%s "
        "tls=%s signed=%s security_events=%s",
        config.worker_id,
        config.coordinator_address,
        config.run_id or "(none)",
        tls_config is not None,
        signing_identity is not None,
        bool(config.security_event_journal_path),
    )
    client = PartitionAwareGrpcCoordinatorClient(
        config.coordinator_address,
        insecure=tls_config is None,
        tls_config=tls_config,
        signing_identity=signing_identity,
        sequence_state_path=config.sequence_state_path or None,
        trusted_coordinator_keys_path=config.trusted_coordinator_keys_path or None,
        security_event_journal_path=config.security_event_journal_path or None,
    )
    ensure_metrics_server_started(config.metrics_port)

    flush_thread_state = _start_security_event_flush_thread(config, client)
    try:
        if config.run_id:
            return _run_training_loop(config, client)
        return _run_health_poll_loop(config, client)
    finally:
        if flush_thread_state is not None:
            thread, stop_event = flush_thread_state
            stop_event.set()
            thread.join(timeout=5.0)


if __name__ == "__main__":
    sys.exit(main())
