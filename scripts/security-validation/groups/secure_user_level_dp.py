"""Secure User-Level DP Operations, Observability, and Release Evidence
slice, Work Area T: a bounded, real scenario group for the new
/api/v1/secure-aggregation/privacy/* API surface.

Scope, disclosed: this harness (framework.py's Context) only speaks raw
HTTP against an already-running Compose stack -- it has no worker-
orchestration capability, so it cannot itself drive a real masked
update/clipping/attestation/noise-application round the way
scripts/validate_secure_user_level_dp.py's real 3-worker Docker
validation does. This group therefore covers exactly what a pure-HTTP
harness can meaningfully assert: route shape, access control (Work
Areas I/J), and error handling -- not the mechanism itself. The
mechanism-level assertions (clipping engaged, attestation accepted,
noise applied, round completed) remain the live-validation script's
responsibility (Work Area U), not duplicated here.
"""

from __future__ import annotations

from framework import Context, Scenario, Status


def _status_real(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/secure-aggregation/privacy/status", token=admin)
    ctx.assert_true(status == 200, f"GET .../status returns 200, got {status}")
    ctx.assert_true(
        bool(body) and body.get("provider") == "SECAGG_NO_DROPOUT_EXPERIMENTAL",
        "status.provider is the real, unchanged provider name",
    )
    ctx.assert_true(
        bool(body) and body.get("adjacency_model") == "ADD_REMOVE_ONE",
        "status.adjacency_model matches the approved mechanism (never silently changed)",
    )
    ctx.assert_true(
        bool(body) and isinstance(body.get("trust_limitations"), list) and len(body["trust_limitations"]) > 0,
        "status carries a non-empty trust_limitations list",
    )


def _health_real(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/secure-aggregation/privacy/health", token=admin)
    ctx.assert_true(status == 200, f"GET .../health returns 200, got {status}")
    ctx.assert_true(
        bool(body) and "capability" in body and "provider_status" in body,
        "health response embeds both capability and live component-status fields",
    )
    ctx.assert_true(
        bool(body) and isinstance(body.get("active_runs_with_user_level_dp"), int),
        "health reports an aggregate active-run count (never a per-run breakdown)",
    )


def _service_role_denied(ctx: Context) -> None:
    service = ctx.login("service@fl-platform.dev", "service-demo")
    for path in (
        "/api/v1/secure-aggregation/privacy/status",
        "/api/v1/secure-aggregation/privacy/health",
        "/api/v1/secure-aggregation/privacy/budget?run_id=x",
        "/api/v1/secure-aggregation/privacy/rounds?run_id=x",
    ):
        status, _, _ = ctx.http("GET", path, token=service)
        ctx.assert_true(status == 403, f"SERVICE role gets 403 on {path} (no implicit access), got {status}")


def _viewer_denied_rounds_and_budget(ctx: Context) -> None:
    viewer = ctx.login("viewer@fl-platform.dev", "viewer-demo")
    for path in (
        "/api/v1/secure-aggregation/privacy/budget?run_id=x",
        "/api/v1/secure-aggregation/privacy/rounds?run_id=x",
    ):
        status, _, _ = ctx.http("GET", path, token=viewer)
        ctx.assert_true(
            status == 403, f"VIEWER is denied per-run privacy accounting detail on {path}, got {status}"
        )
    status, _, _ = ctx.http("GET", "/api/v1/secure-aggregation/privacy/status", token=viewer)
    ctx.assert_true(status == 200, f"VIEWER can still read aggregate capability status, got {status}")


def _budget_requires_run_id(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, _, _ = ctx.http("GET", "/api/v1/secure-aggregation/privacy/budget", token=admin)
    ctx.assert_true(status == 400, f"budget without run_id returns 400, got {status}")


def _round_not_found(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, _, _ = ctx.http(
        "GET", "/api/v1/secure-aggregation/privacy/rounds/999999?run_id=no-such-run-harness", token=admin
    )
    ctx.assert_true(status == 404, f"a round that was never committed returns 404, got {status}")


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="secagg.user-dp.status",
        name="Secure user-level DP status endpoint reflects the real, unchanged mechanism",
        category="secure-aggregation-user-level-dp",
        description="GET .../status returns the real provider/adjacency-model/trust-limitations.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="provider == SECAGG_NO_DROPOUT_EXPERIMENTAL, adjacency_model == ADD_REMOVE_ONE",
        expected_result="matches the approved mechanism",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_status_real,
    ),
    Scenario(
        scenario_id="secagg.user-dp.health",
        name="Secure user-level DP health endpoint returns real component status",
        category="secure-aggregation-user-level-dp",
        description="GET .../health embeds capability + live provider/accountant/ledger status.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.user-dp.status",
        assertion="response has capability and provider_status fields",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_health_real,
    ),
    Scenario(
        scenario_id="secagg.user-dp.service-denied",
        name="SERVICE role has no implicit access to any secure user-level DP route",
        category="secure-aggregation-user-level-dp",
        description="A real 403 for SERVICE on status/health/budget/rounds.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.user-dp.status",
        assertion="403 for every route",
        expected_result="all denied",
        timeout_seconds=20.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_service_role_denied,
    ),
    Scenario(
        scenario_id="secagg.user-dp.viewer-denied-detail",
        name="VIEWER can read aggregate status but not per-run budget/round detail",
        category="secure-aggregation-user-level-dp",
        description="A real 403 for VIEWER on budget/rounds, 200 on status.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.user-dp.status",
        assertion="403 for budget/rounds, 200 for status",
        expected_result="split access",
        timeout_seconds=20.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_viewer_denied_rounds_and_budget,
    ),
    Scenario(
        scenario_id="secagg.user-dp.budget-requires-run-id",
        name="Budget endpoint requires a run_id query parameter",
        category="secure-aggregation-user-level-dp",
        description="A missing run_id is rejected with 400, not a silent empty/default response.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.user-dp.status",
        assertion="400 without run_id",
        expected_result="400",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_budget_requires_run_id,
    ),
    Scenario(
        scenario_id="secagg.user-dp.round-not-found",
        name="A round that was never committed returns 404, not an empty 200",
        category="secure-aggregation-user-level-dp",
        description="GET .../rounds/{roundId} for a nonexistent round_id/run_id returns 404.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.user-dp.status",
        assertion="404 for a nonexistent round",
        expected_result="404",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_round_not_found,
    ),
]
