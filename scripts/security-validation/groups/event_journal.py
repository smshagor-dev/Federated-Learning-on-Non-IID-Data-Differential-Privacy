"""Security event journal scenarios -- real endpoint (not 501), a real
permission-denial-triggered event, pagination/filters, role redaction,
and restart persistence. Adapted from
scripts/validate_security_observability.py's checks 1/2/4/6, extended
with filter/pagination assertions the original script did not make.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _events_endpoint_real(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/events", token=admin)
    ctx.assert_true(status == 200, f"GET /api/v1/security/events returns 200, got {status}")
    ctx.assert_true(bool(body) and "events" in body, "response body has an 'events' key")


def _permission_denial_produces_event(ctx: Context) -> None:
    viewer = ctx.login("viewer@fl-platform.dev", "viewer-demo")
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, _, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=viewer,
        body={"reason": "harness: VIEWER must not be able to suspend"},
    )
    ctx.assert_true(status == 403, f"VIEWER worker-suspend attempt is forbidden, got {status}")

    def check() -> bool:
        status, body, _ = ctx.http(
            "GET",
            "/api/v1/security/events?event_type=SECURITY_PERMISSION_DENIED&limit=20",
            token=admin,
        )
        return status == 200 and bool(body) and len(body.get("events", [])) > 0

    deadline = time.monotonic() + 20.0
    found = False
    while time.monotonic() < deadline:
        if check():
            found = True
            break
        time.sleep(2.0)
    ctx.assert_true(found, "a SECURITY_PERMISSION_DENIED event is observable after the denial")


def _pagination_and_filters(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/events?limit=1", token=admin)
    ctx.assert_true(status == 200, "limit=1 request returns 200")
    events = (body or {}).get("events", [])
    ctx.assert_true(len(events) <= 1, "limit=1 is actually honored (bounded response, not full dump)")

    status, body, _ = ctx.http(
        "GET", "/api/v1/security/events?min_severity=CRITICAL&limit=50", token=admin
    )
    ctx.assert_true(status == 200, "min_severity filter is accepted")
    severities = {e.get("severity") for e in (body or {}).get("events", [])}
    ctx.assert_true(
        severities <= {"CRITICAL"},
        f"min_severity=CRITICAL returns only CRITICAL events, got severities={severities}",
    )


def _role_redaction(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    researcher = ctx.login("researcher@fl-platform.dev", "research-demo")

    status, _, _ = ctx.http(
        "GET", "/api/v1/security/events?event_type=SECURITY_PERMISSION_DENIED", token=researcher
    )
    ctx.assert_true(status == 200, "RESEARCHER can read /api/v1/security/events")

    status, admin_body, _ = ctx.http(
        "GET", "/api/v1/security/events?event_type=SECURITY_PERMISSION_DENIED&limit=5", token=admin
    )
    status2, researcher_body, _ = ctx.http(
        "GET",
        "/api/v1/security/events?event_type=SECURITY_PERMISSION_DENIED&limit=5",
        token=researcher,
    )
    ctx.assert_true(status == 200 and status2 == 200, "both reads succeed")
    admin_has_reason = any(
        "reason_code" in e for e in (admin_body or {}).get("events", [])
    )
    researcher_has_reason = any(
        "reason_code" in e for e in (researcher_body or {}).get("events", [])
    )
    ctx.assert_true(
        admin_has_reason and not researcher_has_reason,
        "ADMIN (has read_detailed) sees reason_code; RESEARCHER (no read_detailed) does not",
    )


def _restart_persistence(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "GET", "/api/v1/security/events?event_type=SECURITY_PERMISSION_DENIED&limit=1", token=admin
    )
    ctx.assert_true(
        status == 200 and bool(body) and len(body.get("events", [])) > 0,
        "a SECURITY_PERMISSION_DENIED event exists before the restart",
    )
    ctx.compose("restart", "api")
    time.sleep(3.0)

    def check() -> bool:
        status, body, _ = ctx.http(
            "GET",
            "/api/v1/security/events?event_type=SECURITY_PERMISSION_DENIED&limit=1",
            token=admin,
        )
        return status == 200 and bool(body) and len(body.get("events", [])) > 0

    deadline = time.monotonic() + 30.0
    found = False
    while time.monotonic() < deadline:
        if check():
            found = True
            break
        time.sleep(2.0)
    ctx.assert_true(found, "the earlier event survives an api container restart")


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="event-journal.endpoint.real",
        name="Security events endpoint is real (not a 501 stub)",
        category="event-journal",
        description="GET /api/v1/security/events returns a real 200 JSON body.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="200 with an 'events' key",
        expected_result="200",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_events_endpoint_real,
    ),
    Scenario(
        scenario_id="event-journal.permission-denial.produces-event",
        name="A permission-denied mutation produces a real, observable event",
        category="event-journal",
        description="A VIEWER worker-suspend attempt is 403'd and journaled as SECURITY_PERMISSION_DENIED.",
        required_services=("coordinator", "api"),
        prerequisites="event-journal.endpoint.real",
        assertion="a SECURITY_PERMISSION_DENIED event appears within 20s",
        expected_result="present",
        timeout_seconds=30.0,
        cleanup="none (the denial itself has no side effect)",
        required=True,
        support_status=Status.SKIPPED,
        run=_permission_denial_produces_event,
    ),
    Scenario(
        scenario_id="event-journal.pagination-and-filters.real",
        name="Event pagination and severity filtering are real, not ignored",
        category="event-journal",
        description="limit and min_severity query parameters actually constrain the response.",
        required_services=("coordinator", "api"),
        prerequisites="event-journal.permission-denial.produces-event",
        assertion="limit=1 returns <=1 event; min_severity=CRITICAL returns only CRITICAL",
        expected_result="both honored",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_pagination_and_filters,
    ),
    Scenario(
        scenario_id="event-journal.redaction.role-aware",
        name="Event redaction differs by role (RESEARCHER vs ADMIN)",
        category="event-journal",
        description="RESEARCHER (no read_detailed) never sees reason_code; ADMIN does.",
        required_services=("coordinator", "api"),
        prerequisites="event-journal.permission-denial.produces-event",
        assertion="ADMIN response has reason_code; RESEARCHER response does not",
        expected_result="redaction confirmed",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_role_redaction,
    ),
    Scenario(
        scenario_id="event-journal.restart.persists",
        name="Event journal survives an api container restart",
        category="event-journal",
        description="A plain `docker compose restart api` must not lose journaled events.",
        required_services=("coordinator", "api"),
        prerequisites="event-journal.permission-denial.produces-event",
        assertion="the pre-restart event is still observable after the restart",
        expected_result="present within 30s",
        timeout_seconds=60.0,
        cleanup="api is left running (restarted, not removed)",
        required=True,
        support_status=Status.SKIPPED,
        run=_restart_persistence,
    ),
    Scenario(
        scenario_id="event-journal.corruption-detection.not-exercised-live",
        name="Event journal corruption is detected and recovered from",
        category="event-journal",
        description="A malformed/checksum-failing line is skipped and counted, not fatal.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "requires directly corrupting the coordinator container's internal "
            "journal file, which this harness has no volume-mounted access to (the "
            "journal lives in the container's own writable layer by design -- see "
            "docs/security-event-centralization.md). Already unit-tested in all three "
            "languages (security_event_journal_test.cpp, "
            "python/tests/test_security_event_journal.py, Go's "
            "security_event_journal_test.go)"
        ),
    ),
]
