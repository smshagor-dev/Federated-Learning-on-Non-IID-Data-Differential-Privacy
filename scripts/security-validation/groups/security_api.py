"""Security HTTP API scenarios not already covered by a more specific
group: the aggregate overview endpoint, idempotent mutation replay, and
role-based permission denial. Adapted from
scripts/validate_security_observability.py's coordinator-signing-key
idempotent-replay check, generalized to worker mutations too.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _security_overview_real(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/overview", token=admin)
    ctx.assert_true(status == 200, f"GET /api/v1/security/overview returns 200, got {status}")
    ctx.assert_true(
        bool(body) and "feature_availability" in body,
        "response includes a feature_availability block",
    )
    ctx.assert_true(
        bool(body)
        and body.get("feature_availability", {}).get("secure_aggregation_available") is False,
        "feature_availability.secure_aggregation_available is false -- this platform "
        "never claims secure aggregation is implemented, live-checked, not just "
        "documented",
    )
    ctx.assert_true(
        bool(body) and body.get("transport", {}).get("mutual_tls_enforced") is True,
        "the overview's own transport section agrees mTLS is enforced",
    )


def _idempotent_mutation_replay(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    key = f"harness-idempotent-{time.time()}"
    body = {"reason": "harness: idempotent replay check"}

    status1, first, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=admin,
        body=body,
        headers={"Idempotency-Key": key},
    )
    ctx.assert_true(status1 == 200, f"first suspend returns 200, got {status1}")

    # Re-seed a divergent live state behind the scenes (activate), then
    # retry with the SAME Idempotency-Key -- a correct implementation
    # serves the cached first response verbatim rather than re-executing
    # (which would now see "active" and report changed=true a second
    # time, byte-differently from the cached response).
    ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/activate",
        token=admin,
        body={"reason": "harness: force a divergent live state"},
        headers={"Idempotency-Key": f"harness-idempotent-reseed-{time.time()}"},
    )
    status2, second, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=admin,
        body=body,
        headers={"Idempotency-Key": key},
    )
    ctx.assert_true(status2 == 200, f"retried suspend returns 200, got {status2}")
    ctx.assert_true(
        first == second,
        "the retried request (same Idempotency-Key) returns the cached first "
        "response verbatim, not a freshly re-executed one",
    )

    # Restore worker-1 to active for any later scenario in this run.
    ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/activate",
        token=admin,
        body={"reason": "harness: restore worker-1 to active after idempotency check"},
        headers={"Idempotency-Key": f"harness-idempotent-restore-{time.time()}"},
    )


def _permission_denial_matrix(ctx: Context) -> None:
    viewer = ctx.login("viewer@fl-platform.dev", "viewer-demo")
    researcher = ctx.login("researcher@fl-platform.dev", "research-demo")

    status, _, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=viewer,
        body={"reason": "should be denied"},
    )
    ctx.assert_true(status == 403, f"VIEWER cannot suspend a worker, got {status}")

    status, _, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=researcher,
        body={"reason": "should be denied"},
    )
    ctx.assert_true(status == 403, f"RESEARCHER cannot suspend a worker (read-only), got {status}")

    status, _, _ = ctx.http("GET", "/api/v1/security/audit", token=viewer)
    ctx.assert_true(status == 403, f"VIEWER cannot read audit records, got {status}")

    status, _, _ = ctx.http("GET", "/api/v1/security/overview", token="not-a-real-token")
    ctx.assert_true(status == 401, f"an invalid bearer token is unauthorized, got {status}")


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="security-api.overview.real",
        name="Security overview endpoint is real and discloses no secure aggregation",
        category="security-api",
        description="GET /api/v1/security/overview aggregates real state and never claims secure aggregation.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="feature_availability.secure_aggregation_available == false",
        expected_result="false",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_security_overview_real,
    ),
    Scenario(
        scenario_id="security-api.mutation.idempotent-replay",
        name="A retried mutation with the same Idempotency-Key returns the cached response",
        category="security-api",
        description="Worker suspend, retried with the same key after a divergent live-state change, is idempotent.",
        required_services=("coordinator", "api"),
        prerequisites="security-api.overview.real",
        assertion="first and retried response bodies are byte-identical",
        expected_result="identical",
        timeout_seconds=20.0,
        cleanup="worker-1 is restored to active by the scenario itself",
        required=True,
        support_status=Status.SKIPPED,
        run=_idempotent_mutation_replay,
    ),
    Scenario(
        scenario_id="security-api.permission-denial.matrix",
        name="Permission-denial matrix: VIEWER/RESEARCHER mutations and unauthenticated reads are all rejected",
        category="security-api",
        description="A real 403/401 for each case, not merely documented policy.",
        required_services=("coordinator", "api"),
        prerequisites="security-api.overview.real",
        assertion="403 for VIEWER/RESEARCHER mutation attempts; 401 for an invalid token",
        expected_result="all rejected",
        timeout_seconds=20.0,
        cleanup="none (every attempt is rejected before any mutation happens)",
        required=True,
        support_status=Status.SKIPPED,
        run=_permission_denial_matrix,
    ),
    Scenario(
        scenario_id="security-api.idempotency-conflict.not-exercised-live",
        name="An idempotency-key conflict (same key, different body) is detected",
        category="security-api",
        description="Reusing an Idempotency-Key with a materially different request body.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "the current idempotencyCache implementation (go/internal/transport/httpapi/"
            "security_handlers.go) keys strictly on the Idempotency-Key string and does "
            "not itself compare request bodies for a conflict -- there is no live "
            "conflict-detection behavior to exercise yet; see "
            "docs/security-runtime-validation.md's disclosed scope for this gap"
        ),
    ),
]
