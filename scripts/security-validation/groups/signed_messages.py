"""Signed-message scenarios (capability/heartbeat/client-result/
privacy-record). Only the capability-statement path is exercised live
by this harness: it is the one signed message the deployed python-worker
container sends automatically, at registration, with no run configured.
Heartbeat/client-result/privacy-record all require a live
CreateRun->StartRun->task-assignment flow this harness invocation does
not configure -- DEFERRED, with real unit/integration coverage already
existing (signed_envelope_verifier_test.cpp, test_signed_envelope.py,
coordinator_service_test.cpp's hybrid-DP block).
"""

from __future__ import annotations

from framework import Context, Scenario, Status

_RUN_FLOW_REASON = (
    "requires a live CreateRun->StartRun->task-assignment flow, not configured by "
    "this harness invocation (python-worker runs in health-poll mode, no run_id set). "
    "Already covered by signed_envelope_verifier_test.cpp, test_signed_envelope.py, and "
    "coordinator_service_test.cpp's SubmitClientResult/hybrid-DP integration blocks"
)


def _capability_signature_accepted(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/security/workers/worker-1", token=admin)
    ctx.assert_true(status == 200, "worker-1 identity is readable")
    ctx.assert_true(
        bool(body) and bool(body.get("signing_key_id")),
        "worker-1 has a signing_key_id populated -- reachable only if RegisterWorker's "
        "SignedCapabilityStatement passed verify_capability_statement (schema, "
        "payload_hash, Ed25519 signature, and non-expired issued_at/expires_at)",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="signed-messages.capability.signature-accepted",
        name="Signed capability statement is verified and accepted",
        category="signed-messages",
        description="RegisterWorker's SignedCapabilityStatement passes full verification.",
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="event-centralization.worker.registers-with-signed-capability",
        assertion="worker-1's identity carries a real signing_key_id",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_capability_signature_accepted,
    ),
    Scenario(
        scenario_id="signed-messages.capability.tampered-rejected-not-exercised-live",
        name="A tampered capability statement is rejected",
        category="signed-messages",
        description="A capability statement whose payload_hash doesn't match its fields must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "requires bypassing the production GrpcCoordinatorClient to construct a "
            "deliberately malformed capability statement; already covered by "
            "capability_statement_verifier_test.cpp"
        ),
    ),
    Scenario(
        scenario_id="signed-messages.capability.expired-rejected-not-exercised-live",
        name="An expired capability statement is rejected",
        category="signed-messages",
        description="issued_at/expires_at bounds are enforced.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "requires a deliberately backdated envelope; already covered by "
            "capability_statement_verifier_test.cpp"
        ),
    ),
    Scenario(
        scenario_id="signed-messages.heartbeat.accepted-not-exercised-live",
        name="Signed heartbeat is accepted",
        category="signed-messages",
        description="MESSAGE_TYPE_WORKER_HEARTBEAT verification succeeds for a real heartbeat.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_RUN_FLOW_REASON,
    ),
    Scenario(
        scenario_id="signed-messages.heartbeat.replay-rejected-not-exercised-live",
        name="Heartbeat replay is rejected",
        category="signed-messages",
        description="A resubmitted heartbeat envelope (same nonce/sequence) must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_RUN_FLOW_REASON,
    ),
    Scenario(
        scenario_id="signed-messages.heartbeat.sequence-violation-rejected-not-exercised-live",
        name="Heartbeat sequence violation is rejected",
        category="signed-messages",
        description="A lower sequence number than already observed must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_RUN_FLOW_REASON,
    ),
    Scenario(
        scenario_id="signed-messages.client-result.accepted-not-exercised-live",
        name="Signed client result is accepted",
        category="signed-messages",
        description="MESSAGE_TYPE_CLIENT_RESULT verification succeeds for a real submitted result.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_RUN_FLOW_REASON,
    ),
    Scenario(
        scenario_id="signed-messages.client-result.tensor-tampering-rejected-not-exercised-live",
        name="Tensor tampering in a client result is rejected",
        category="signed-messages",
        description="tensor_checksum_matches recomputes and rejects a mismatched checksum.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_RUN_FLOW_REASON,
    ),
    Scenario(
        scenario_id="signed-messages.client-result.replay-rejected-not-exercised-live",
        name="Client result replay is rejected",
        category="signed-messages",
        description="A resubmitted client-result envelope must be rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_RUN_FLOW_REASON,
    ),
]
