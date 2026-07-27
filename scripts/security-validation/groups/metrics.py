"""Prometheus metrics scenarios -- Work Package Q/P live validation.
Adapted from scripts/validate_security_observability.py's check 5,
extended to cover the event-centralization gauges added this slice and
a direct check that no high-cardinality (per-worker/per-task) label
ever appears.
"""

from __future__ import annotations

import re

from framework import Context, Scenario, Status


def _security_events_counter_present(ctx: Context) -> None:
    status, _, raw = ctx.http("GET", "/metrics")
    ctx.assert_true(status == 200, f"GET /metrics returns 200, got {status}")
    text = raw.decode("utf-8", "replace")
    ctx.assert_true(
        "fl_security_events_total" in text,
        "fl_security_events_total appears in the scrape",
    )
    ctx.assert_true(
        "# TYPE fl_security_events_total counter" in text,
        "fl_security_events_total is correctly typed as a counter",
    )


def _event_source_gauges_correctly_typed(ctx: Context) -> None:
    status, _, raw = ctx.http("GET", "/metrics")
    ctx.assert_true(status == 200, "GET /metrics returns 200")
    text = raw.decode("utf-8", "replace")
    for name in (
        "fl_security_event_source_records",
        "fl_security_event_source_batches",
        "fl_security_event_source_distinct_workers",
    ):
        ctx.assert_true(
            f"# TYPE {name} gauge" in text,
            f"{name} is correctly typed as a gauge (a last-observed snapshot, not "
            "a monotonic counter)",
        )


def _no_high_cardinality_labels(ctx: Context) -> None:
    status, _, raw = ctx.http("GET", "/metrics")
    ctx.assert_true(status == 200, "GET /metrics returns 200")
    text = raw.decode("utf-8", "replace")
    # Every security_event_source_* series must carry only
    # source_service (a 3-value fixed set) as a label -- never a raw
    # worker_id or task_id, which would make the series cardinality
    # scale with fleet size.
    offending = [
        line
        for line in text.splitlines()
        if line.startswith("fl_security_event_source_")
        and ("worker_id=" in line or "task_id=" in line or "run_id=" in line)
    ]
    ctx.assert_true(
        not offending,
        f"no fl_security_event_source_* series carries a worker_id/task_id/run_id "
        f"label (would be high-cardinality); offending lines: {offending[:3]}",
    )
    # Every fl_security_events_total series must use only the coarse
    # category label, never the raw ~55-value event_type.
    event_lines = [line for line in text.splitlines() if line.startswith("fl_security_events_total{")]
    raw_event_type_leak = [
        line for line in event_lines if re.search(r'category="[A-Z_]+"', line)
    ]
    ctx.assert_true(
        not raw_event_type_leak,
        "fl_security_events_total never uses a SCREAMING_SNAKE_CASE raw event_type "
        "as its category label value (category is always the coarsened lowercase set)",
    )


def _go_metrics_endpoint_no_duplicates(ctx: Context) -> None:
    status, _, raw = ctx.http("GET", "/metrics")
    ctx.assert_true(status == 200, "GET /metrics returns 200")
    text = raw.decode("utf-8", "replace")
    help_lines = [line for line in text.splitlines() if line.startswith("# HELP ")]
    metric_names = [line.split()[2] for line in help_lines]
    duplicates = {name for name in metric_names if metric_names.count(name) > 1}
    ctx.assert_true(
        not duplicates,
        f"no metric name has more than one # HELP line (would indicate a duplicate "
        f"registration bug); duplicates: {duplicates}",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="metrics.security-events.counter-present",
        name="fl_security_events_total is scrapeable and correctly typed",
        category="metrics",
        description="The Go API's /metrics exposes the low-cardinality security-event counter.",
        required_services=("api",),
        prerequisites="event-journal.permission-denial.produces-event",
        assertion="present, TYPE counter",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_security_events_counter_present,
    ),
    Scenario(
        scenario_id="metrics.event-source.gauges-typed",
        name="Event-centralization gauges are correctly typed as gauges",
        category="metrics",
        description="fl_security_event_source_{records,batches,distinct_workers} are TYPE gauge.",
        required_services=("api",),
        prerequisites="event-centralization.metrics.gauges-present",
        assertion="all three carry '# TYPE ... gauge'",
        expected_result="all three",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_event_source_gauges_correctly_typed,
    ),
    Scenario(
        scenario_id="metrics.cardinality.no-per-worker-or-per-task-labels",
        name="No metric carries a per-worker or per-task (high-cardinality) label",
        category="metrics",
        description="Work Package P/Q's explicit 'no dynamic worker or task labels' requirement, checked live.",
        required_services=("api",),
        prerequisites="metrics.security-events.counter-present",
        assertion="no fl_security_event_source_* line carries worker_id/task_id/run_id",
        expected_result="none found",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_no_high_cardinality_labels,
    ),
    Scenario(
        scenario_id="metrics.registration.no-duplicates",
        name="No metric name is registered/documented more than once",
        category="metrics",
        description="Every metric has exactly one # HELP line -- a real duplicate-registration test.",
        required_services=("api",),
        prerequisites="metrics.security-events.counter-present",
        assertion="no metric name appears in more than one # HELP line",
        expected_result="no duplicates",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_go_metrics_endpoint_no_duplicates,
    ),
    Scenario(
        scenario_id="metrics.python-worker.scrape-not-exercised-live",
        name="Python worker's own /metrics endpoint is scrapeable",
        category="metrics",
        description="fl_platform.security.metrics exposes worker-local security-event counters.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "the python-worker container's metrics_port defaults to 0 (disabled) and "
            "docker-compose.security.yml does not set FL_WORKER_METRICS_PORT or "
            "publish a port for it; not wired this pass. Unit-tested directly via "
            "fl_platform.security.metrics's own test suite instead"
        ),
    ),
]
