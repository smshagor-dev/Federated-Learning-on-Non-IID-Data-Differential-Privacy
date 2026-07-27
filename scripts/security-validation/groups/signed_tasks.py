"""Coordinator-signed task scenarios. The one live, real, read-only
check this harness can perform without a full training run is that the
coordinator has a real, active signing identity available to sign tasks
with at all. Task issuance/verification/tampering/replay/reissue all
require a live CreateRun->StartRun->task-assignment flow -- DEFERRED,
with real coverage already existing in
coordinator_task_signing_test.cpp, coordinator_task_verifier tests
(Python), and coordinator_service_test.cpp.
"""

from __future__ import annotations

from framework import Context, Scenario, Status

_REASON = (
    "requires a live CreateRun->StartRun->task-assignment flow, not configured by "
    "this harness invocation. Already covered by coordinator_task_signing_test.cpp, "
    "the Python coordinator_task_verifier test suite, and "
    "coordinator_service_test.cpp's signed-task integration coverage"
)


def _coordinator_has_active_signing_key(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "GET", "/api/v1/security/coordinator/signing-keys", token=admin
    )
    ctx.assert_true(status == 200, "GET .../coordinator/signing-keys returns 200")
    keys = (body or {}).get("signing_keys", [])
    ctx.assert_true(
        any(k.get("status") == "active" for k in keys),
        "the coordinator has a real ACTIVE signing key -- the persistent Ed25519 "
        "identity every SignedCoordinatorTask would be signed with",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="signed-tasks.coordinator-key.active",
        name="The coordinator has an active task-signing identity",
        category="signed-tasks",
        description="GET /api/v1/security/coordinator/signing-keys reports a real ACTIVE key.",
        required_services=("coordinator", "api"),
        prerequisites="stack up with docker-compose.security.yml",
        assertion="at least one signing key with status == active",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_coordinator_has_active_signing_key,
    ),
    Scenario(
        scenario_id="signed-tasks.issuance.verified-not-exercised-live",
        name="A signed task is issued and verified before training",
        category="signed-tasks",
        description="Worker-side verify_coordinator_task succeeds before any model/dataset access.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.tampering.algorithm-rejected-not-exercised-live",
        name="Tampered algorithm configuration hash is rejected",
        category="signed-tasks",
        description="A task whose algorithm doesn't match its signed configuration hash is rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.tampering.model-config-rejected-not-exercised-live",
        name="Tampered model configuration is rejected",
        category="signed-tasks",
        description="Domain-separated configuration-hash mismatch is caught.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.tampering.dataset-partition-rejected-not-exercised-live",
        name="Tampered dataset partition reference is rejected",
        category="signed-tasks",
        description="Domain-separated configuration-hash mismatch is caught.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.tampering.privacy-config-rejected-not-exercised-live",
        name="Tampered privacy configuration is rejected",
        category="signed-tasks",
        description="Domain-separated configuration-hash mismatch is caught.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.wrong-worker.rejected-not-exercised-live",
        name="A task issued to a different worker is rejected",
        category="signed-tasks",
        description="Worker identity binding on the signed task is checked.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.expired.rejected-not-exercised-live",
        name="An expired signed task is rejected",
        category="signed-tasks",
        description="Task envelope expires_at is enforced worker-side.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.replay.rejected-not-exercised-live",
        name="Task replay is rejected",
        category="signed-tasks",
        description="Worker-side CoordinatorTaskReplayStore rejects a resubmitted task.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.recovery.accepted-task-not-exercised-live",
        name="Accepted-task recovery after a simulated worker crash",
        category="signed-tasks",
        description="AcceptedTaskJournal.recover_on_startup marks an in-flight task FAILED, awaiting reissue.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.duplicate-execution.blocked-not-exercised-live",
        name="Duplicate task execution is blocked",
        category="signed-tasks",
        description="The accepted-task journal detects and blocks a duplicate ACCEPTED task_id.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
    Scenario(
        scenario_id="signed-tasks.reissue.new-nonce-and-sequence-not-exercised-live",
        name="Lease-expiry task reissue carries a new nonce, sequence, and signature",
        category="signed-tasks",
        description="A reissued task after lease expiry is a genuinely new signed task, not a resend.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_REASON,
    ),
]
