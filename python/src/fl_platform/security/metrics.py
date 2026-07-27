"""Low-cardinality Prometheus security metrics for this worker --
Security Events, Metrics, and Durable Audit Journal slice. See
docs/security-metrics.md.

Deliberately labeled by a coarse ``category`` (a handful of fixed
values), not the full ~55-value event_type enum -- Work Package
requirement "Avoid high-cardinality labels." The exact event_type is
still available in the local journal (security_event_journal.py); this
metric answers "how many, of what kind, with what outcome" for
dashboards/alerts, not "which specific event."

Reuses privacy/metrics.py's ``ensure_metrics_server_started`` opt-in
HTTP-endpoint mechanism -- one process-wide Prometheus port, not a
second one just for security metrics.
"""

from __future__ import annotations

from prometheus_client import Counter

from fl_platform.security import security_event as _event

WORKER_SECURITY_EVENTS_TOTAL = Counter(
    "fl_worker_security_events_total",
    "Security-relevant events observed by this worker, by category/severity/outcome.",
    ["category", "severity", "outcome"],
)

_CATEGORY_BY_PREFIX = (
    ("TRANSPORT_", "transport"),
    ("PEER_CERTIFICATE_", "transport"),
    ("CERTIFICATE_", "transport"),
    ("WORKER_KEY_", "worker_signing_key"),
    ("MESSAGE_REJECTED_BY_KEY_STATE", "worker_signing_key"),
    ("WORKER_", "worker_identity"),
    ("CAPABILITY_", "signed_message"),
    ("HEARTBEAT_", "signed_message"),
    ("CLIENT_RESULT_", "signed_message"),
    ("PRIVACY_RECORD_", "signed_message"),
    ("SIGNATURE_", "signed_message"),
    ("PAYLOAD_HASH_", "signed_message"),
    ("MESSAGE_", "signed_message"),
    ("COORDINATOR_", "coordinator_task"),
    ("ACCEPTED_TASK_", "coordinator_task"),
    ("DUPLICATE_TASK_", "coordinator_task"),
    ("TASK_REISSUED", "coordinator_task"),
    ("SECURITY_", "administration"),
    ("IDEMPOTENCY_", "administration"),
)


def event_category(event_type: str) -> str:
    for prefix, category in _CATEGORY_BY_PREFIX:
        if event_type.startswith(prefix):
            return category
    return "other"


def record_security_event(event: _event.SecurityEvent) -> None:
    """Called alongside (never instead of) SecurityEventJournal.emit --
    this is a fire-and-forget counter increment, not a substitute for the
    durable per-event record."""
    WORKER_SECURITY_EVENTS_TOTAL.labels(
        category=event_category(event.event_type),
        severity=event.severity,
        outcome=event.outcome,
    ).inc()
