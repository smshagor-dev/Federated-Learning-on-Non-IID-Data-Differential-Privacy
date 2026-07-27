"""Worker identity lifecycle scenarios. Suspend/activate are exercised
live and reversed within the same scenario (net effect: worker-1 ends
active again) so later groups in the same harness run (event-
centralization, recovery) can keep relying on a healthy worker-1.
Revocation is DEFERRED -- it is terminal (see
cpp/coordinator/include/fl_coordinator/worker_identity_registry.hpp's
"revocation is terminal" contract) and would permanently break every
other scenario in the same run that needs worker-1's signing key to
keep working. Revocation itself is already unit/integration tested
(worker_identity_registry_test.cpp, security_handlers_test.go) and was
live-validated once in the Security Operations and Administration
slice's own Docker pass (docs/security-operations-report.md).
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _suspend_then_activate(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")

    status, body, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/suspend",
        token=admin,
        body={"reason": "security-runtime-validation harness: reversible suspend check"},
        headers={"Idempotency-Key": f"harness-suspend-{time.time()}"},
    )
    ctx.assert_true(status == 200, f"suspend returns 200, got {status}")
    ctx.assert_true(
        bool(body) and body.get("identity", {}).get("registration_status") == "suspended",
        "worker-1's registration_status is 'suspended' immediately after the call",
    )

    status, body, _ = ctx.http("GET", "/api/v1/security/workers/worker-1", token=admin)
    ctx.assert_true(
        status == 200 and bool(body) and body.get("registration_status") == "suspended",
        "a follow-up GET confirms the suspended status persisted",
    )

    status, body, _ = ctx.http(
        "POST",
        "/api/v1/security/workers/worker-1/activate",
        token=admin,
        body={"reason": "security-runtime-validation harness: restoring worker-1 to active"},
        headers={"Idempotency-Key": f"harness-activate-{time.time()}"},
    )
    ctx.assert_true(status == 200, f"activate returns 200, got {status}")
    ctx.assert_true(
        bool(body) and body.get("identity", {}).get("registration_status") == "active",
        "worker-1's registration_status is 'active' again after the reversing call "
        "(net effect of this scenario: worker-1 is left exactly as it started)",
    )


def _viewer_gets_redacted_worker_detail(ctx: Context) -> None:
    viewer = ctx.login("viewer@fl-platform.dev", "viewer-demo")
    status, body, raw = ctx.http("GET", "/api/v1/security/workers/worker-1", token=viewer)
    ctx.assert_true(status == 200, "VIEWER can read worker detail (aggregate-level access)")
    text = raw.decode("utf-8", "replace")
    ctx.assert_true(
        "certificate_fingerprint" not in text and "signing_key_id" not in text,
        "VIEWER's response omits certificate_fingerprint/signing_key_id -- role-aware "
        "redaction is real, not merely documented",
    )
    ctx.assert_true(
        bool(body) and body.get("worker_id") == "worker-1",
        "the redacted view still identifies which worker it is",
    )


def _worker_list_visible(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/workers", token=admin)
    ctx.assert_true(status == 200, "GET /api/v1/security/workers returns 200")
    workers = (body or {}).get("workers", [])
    ctx.assert_true(
        any(w.get("worker_id") == "worker-1" for w in workers),
        "worker-1 appears in the full worker identity listing",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="worker-identity.list.visible",
        name="Worker identity listing includes the real registered worker",
        category="worker-identity",
        description="GET /api/v1/security/workers lists worker-1.",
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="event-centralization.worker.registers-with-signed-capability",
        assertion="worker-1 present in the listing",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_worker_list_visible,
    ),
    Scenario(
        scenario_id="worker-identity.detail.viewer-redacted",
        name="VIEWER role receives a redacted worker-detail projection",
        category="worker-identity",
        description="VIEWER may see worker_id/registration_status but not certificate/key identifiers.",
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="worker-identity.list.visible",
        assertion="VIEWER response omits certificate_fingerprint and signing_key_id",
        expected_result="redacted",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_viewer_gets_redacted_worker_detail,
    ),
    Scenario(
        scenario_id="worker-identity.lifecycle.suspend-then-activate",
        name="Suspension and activation both work live and are reversible",
        category="worker-identity",
        description="A real ADMIN suspend followed by a real activate, restoring worker-1 to active.",
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="worker-identity.list.visible",
        assertion="registration_status transitions active -> suspended -> active",
        expected_result="worker-1 ends active",
        timeout_seconds=30.0,
        cleanup="worker-1 is restored to active by the scenario itself",
        required=True,
        support_status=Status.SKIPPED,
        run=_suspend_then_activate,
    ),
    Scenario(
        scenario_id="worker-identity.revocation.terminal-not-exercised-live",
        name="Worker revocation (terminal)",
        category="worker-identity",
        description="Revoking worker-1 live would permanently break every later scenario needing it.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "revocation is terminal (no un-revoke) against this harness's single "
            "shared worker-1 identity; would break every later scenario in the same "
            "run needing a working worker-1. Already unit-tested "
            "(worker_identity_registry_test.cpp) and live-validated once in a prior "
            "Docker pass -- see docs/security-operations-report.md"
        ),
    ),
    Scenario(
        scenario_id="worker-identity.revocation.late-result-rejected-not-exercised-live",
        name="A revoked worker's late-arriving result is rejected",
        category="worker-identity",
        description="SubmitClientResult from a REVOKED worker_id must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "depends on worker-identity.revocation, itself deferred for the same "
            "shared-stack reason -- see that scenario"
        ),
    ),
    Scenario(
        scenario_id="worker-identity.lease.active-lease-cancellation-not-exercised-live",
        name="Suspending a worker cancels its active task lease",
        category="worker-identity",
        description="WorkerLifecycleResult.leases_canceled reflects a real in-flight lease cancellation.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "requires a live CreateRun->StartRun->task-assignment flow so a real lease "
            "exists to cancel; this harness invocation does not configure a run for "
            "python-worker. Unit-tested directly in worker_registry_test.cpp / "
            "coordinator_service_test.cpp instead"
        ),
    ),
]
