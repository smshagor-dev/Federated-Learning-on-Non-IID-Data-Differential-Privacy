"""Worker signing-key lifecycle scenarios. Only the read-only "initial
key is registered ACTIVE" check is exercised live and reversibly.
Rotation/grace-period/expiry/revocation are all DEFERRED: rotation has
no standalone CLI/script trigger outside the full training loop in this
codebase (GrpcCoordinatorClient.rotate_signing_key is a real, tested
method, but nothing in this harness's python-worker container calls it
automatically), and revocation is destructive/terminal against the
harness's single shared worker-1 identity, same reasoning as
worker_identity.py's revocation scenarios.
"""

from __future__ import annotations

from framework import Context, Scenario, Status

_NO_TRIGGER_REASON = (
    "no standalone CLI/script exists in this codebase to trigger a live worker-side "
    "signing-key rotation outside the full CreateRun->StartRun->training loop, which "
    "this harness invocation does not configure. GrpcCoordinatorClient.rotate_signing_key "
    "is real and unit-tested (test_signed_envelope.py's RotationHashTests, "
    "coordinator_service_test.cpp) but not wired to any live-Compose trigger this pass"
)


def _initial_key_registered_active(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "GET", "/api/v1/security/workers/worker-1/signing-keys", token=admin
    )
    ctx.assert_true(status == 200, "GET .../signing-keys returns 200")
    keys = (body or {}).get("signing_keys", [])
    ctx.assert_true(
        any(k.get("status") == "active" for k in keys),
        "worker-1 has at least one ACTIVE signing key (its real, first-registered "
        "Ed25519 key, bootstrapped via the signed capability statement)",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="worker-keys.initial.registered-active",
        name="Worker-1's first signing key is registered ACTIVE",
        category="worker-keys",
        description="Trust-on-first-use registration via RegisterWorker's signed capability statement.",
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="event-centralization.worker.registers-with-signed-capability",
        assertion="at least one signing key with status == active",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_initial_key_registered_active,
    ),
    Scenario(
        scenario_id="worker-keys.rotation.signed-accepted-not-exercised-live",
        name="Signed worker-key rotation is accepted",
        category="worker-keys",
        description="A real RotateWorkerSigningKey call transitions the old key to GRACE_PERIOD.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_NO_TRIGGER_REASON,
    ),
    Scenario(
        scenario_id="worker-keys.grace-period.old-key-still-accepted-not-exercised-live",
        name="A key in its grace period still authenticates messages",
        category="worker-keys",
        description="signing_key_status_permits allows GRACE_PERIOD for heartbeat/result/privacy/event-batch.",
        required_services=(),
        prerequisites="worker-keys.rotation",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_NO_TRIGGER_REASON,
    ),
    Scenario(
        scenario_id="worker-keys.expiry.rejected-not-exercised-live",
        name="An expired signing key is rejected",
        category="worker-keys",
        description="A message signed with a key past its grace_period_end is rejected.",
        required_services=(),
        prerequisites="worker-keys.grace-period",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_NO_TRIGGER_REASON,
    ),
    Scenario(
        scenario_id="worker-keys.revocation.rejected-not-exercised-live",
        name="A revoked signing key is rejected, and auto-suspends the worker if it was the last valid key",
        category="worker-keys",
        description="RevokeWorkerSigningKey against worker-1's sole active key.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "destructive against worker-1's sole ACTIVE key (auto-suspends the "
            "worker per the documented 'worker automatically suspended after loss of "
            "all valid keys' policy), which would break every later scenario in the "
            "same run needing a working worker-1. Already unit-tested "
            "(signing_key_registry_test.cpp) and live-validated once in a prior Docker "
            "pass -- see docs/security-operations-report.md"
        ),
    ),
    Scenario(
        scenario_id="worker-keys.keyless.no-task-not-exercised-live",
        name="A worker with no valid signing key receives no task",
        category="worker-keys",
        description="AcquireTask must not dispatch work to a worker with zero ACTIVE/GRACE_PERIOD keys.",
        required_services=(),
        prerequisites="worker-keys.revocation",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "requires a live CreateRun->StartRun flow (not configured this pass) on "
            "top of the already-deferred key-revocation precondition"
        ),
    ),
]
