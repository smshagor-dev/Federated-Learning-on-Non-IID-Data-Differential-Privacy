"""Prometheus metrics for sample-level DP training on this worker (see
docs/privacy-mathematics.md's Critical Privacy Rule). This is the only
mechanism the Python worker itself applies — user-level DP and adaptive
clipping run centrally in the C++ coordinator and are exported there
(the Go control plane's ``fl_privacy_epsilon`` gauge, labeled
``mechanism="user_level"``/``"clipping"``; see
go/internal/observability/telemetry.go). This module's
``fl_worker_sample_level_epsilon`` gauge is always labeled by
``mechanism="sample_level"`` too, even though this module only ever
reports that one mechanism, so a PromQL query that groups by mechanism
across both sources never has to special-case which side of the split
it's looking at.

The worker's Prometheus HTTP endpoint is opt-in (see
WorkerConfig.metrics_port, default 0/disabled) — a worker running
non-private training has nothing privacy-specific to export and
shouldn't bind a port by default in every deployment.
"""

from __future__ import annotations

import threading

from prometheus_client import Counter, Gauge, start_http_server

SAMPLE_LEVEL_TRAINING_ROUNDS_TOTAL = Counter(
    "fl_worker_sample_level_training_rounds_total",
    "Sample-level-DP local training rounds run by this worker, by outcome.",
    ["outcome"],  # "success" | "rejected"
)

SAMPLE_LEVEL_EPSILON = Gauge(
    "fl_worker_sample_level_epsilon",
    "Last-observed sample-level epsilon for a (run_id, client_id) pair on this worker.",
    ["mechanism", "run_id", "client_id"],
)


def record_sample_level_training_success(
    run_id: str, client_id: str, epsilon: float
) -> None:
    """Called after a client's sample-level-DP local training round
    completes and Opacus reports a real epsilon — never called with a
    fabricated or estimated value.
    """
    SAMPLE_LEVEL_TRAINING_ROUNDS_TOTAL.labels(outcome="success").inc()
    SAMPLE_LEVEL_EPSILON.labels(
        mechanism="sample_level", run_id=run_id, client_id=client_id
    ).set(epsilon)


def record_sample_level_training_rejected() -> None:
    """Called when UnsupportedPrivacyCombinationError stops a task
    before any training happens — see service.py's except handler. No
    epsilon to report (none was computed), only the rejection count.
    """
    SAMPLE_LEVEL_TRAINING_ROUNDS_TOTAL.labels(outcome="rejected").inc()


_metrics_server_lock = threading.Lock()
_metrics_server_started = False


def ensure_metrics_server_started(port: int) -> None:
    """Idempotently starts this worker's Prometheus HTTP endpoint on
    ``port`` (a no-op if already started, or if port is 0 — see
    WorkerConfig.metrics_port's "0 disables it" convention). Safe to
    call from __main__.py on every worker startup without needing a
    "did I already do this" check at the call site.
    """
    global _metrics_server_started
    if port == 0:
        return
    with _metrics_server_lock:
        if _metrics_server_started:
            return
        start_http_server(port)
        _metrics_server_started = True
