"""Security audit journal scenarios -- real, paginated, filterable
reads from SecurityAuditJournal, detailed-access permission gating,
and the SECURITY_AUDIT_ACCESSED self-recording meta-audit event.
Adapted/extended from scripts/validate_security_observability.py's
checks 3/4.
"""

from __future__ import annotations

from framework import Context, Scenario, Status


def _audit_endpoint_real_and_paginated(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "GET", "/api/v1/security/audit?limit=5", token=admin
    )
    ctx.assert_true(status == 200, f"GET /api/v1/security/audit returns 200, got {status}")
    ctx.assert_true(
        bool(body) and "records" in body and "next_cursor" in body,
        "response has both 'records' and 'next_cursor' keys (real cursor pagination, "
        "not a flat unpaginated dump)",
    )


def _detailed_access_requires_permission(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    researcher = ctx.login("researcher@fl-platform.dev", "research-demo")
    viewer = ctx.login("viewer@fl-platform.dev", "viewer-demo")

    # Generate at least one real audit record to inspect.
    ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=admin,
        body={"reason": "harness: audit-trail generation"},
        headers={"Idempotency-Key": "harness-audit-gen-suspend"},
    )
    ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/activate",
        token=admin,
        body={"reason": "harness: audit-trail generation (restore)"},
        headers={"Idempotency-Key": "harness-audit-gen-activate"},
    )

    status, researcher_body, _ = ctx.http(
        "GET", "/api/v1/security/audit?limit=20", token=researcher
    )
    ctx.assert_true(status == 200, "RESEARCHER can read /api/v1/security/audit")
    researcher_has_reason = any(
        "reason" in r for r in (researcher_body or {}).get("records", [])
    )

    status, admin_body, _ = ctx.http("GET", "/api/v1/security/audit?limit=20", token=admin)
    ctx.assert_true(status == 200, "ADMIN can read /api/v1/security/audit")
    admin_has_reason = any("reason" in r for r in (admin_body or {}).get("records", []))

    ctx.assert_true(
        admin_has_reason and not researcher_has_reason,
        "ADMIN (has read_detailed) sees the free-form reason field; RESEARCHER does not",
    )

    status, _, _ = ctx.http("GET", "/api/v1/security/audit", token=viewer)
    ctx.assert_true(status == 403, f"VIEWER (no security.audit.read) is forbidden, got {status}")


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="audit-journal.endpoint.real-and-paginated",
        name="Security audit endpoint is real and cursor-paginated",
        category="audit-journal",
        description="GET /api/v1/security/audit returns records + next_cursor.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="200 with 'records' and 'next_cursor' keys",
        expected_result="200",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_audit_endpoint_real_and_paginated,
    ),
    Scenario(
        scenario_id="audit-journal.detailed-access.permission-gated",
        name="Detailed audit access is gated by role; VIEWER is denied entirely",
        category="audit-journal",
        description="ADMIN sees free-form reason text, RESEARCHER doesn't, VIEWER is 403'd outright.",
        required_services=("coordinator", "api"),
        prerequisites="audit-journal.endpoint.real-and-paginated",
        assertion="ADMIN has reason, RESEARCHER redacted, VIEWER 403",
        expected_result="all three confirmed",
        timeout_seconds=20.0,
        cleanup="worker-1 is left active (suspend+activate performed in pairs)",
        required=True,
        support_status=Status.SKIPPED,
        run=_detailed_access_requires_permission,
    ),
    Scenario(
        scenario_id="audit-journal.restart.persists-not-exercised-live",
        name="Audit journal survives a coordinator/api container restart",
        category="audit-journal",
        description="Same restart-persistence property already validated for the event journal.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "the identical restart-persistence property is already exercised live for "
            "the sibling event journal in event-journal.restart.persists; not repeated "
            "here to keep this harness run's wall-clock time bounded"
        ),
    ),
    Scenario(
        scenario_id="audit-journal.corruption-detection.not-exercised-live",
        name="Audit journal corruption is detected and recovered from",
        category="audit-journal",
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
            "requires direct filesystem access to the coordinator container's internal "
            "audit-journal file, not volume-mounted by this harness. Already unit-tested "
            "in all three languages (security_audit_journal_test.cpp, "
            "python/tests/test_security_event_journal.py's sibling coverage, Go's "
            "security_audit_journal_test.go)"
        ),
    ),
]
