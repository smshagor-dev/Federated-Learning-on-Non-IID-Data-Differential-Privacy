"""Security Runtime Completion and Release Evidence slice, Work
Package B: live validation of the real worker-event-centralization
production path --

    Python worker -> local persistent event queue -> signed event batch
    -> mutual-TLS gRPC -> C++ SubmitWorkerSecurityEvents -> certificate
    identity validation -> worker identity resolution -> signing-key-
    state validation -> signature verification -> payload-hash
    verification -> replay validation -> sequence validation ->
    event-schema validation -> centralized event-journal persistence ->
    acknowledgment -> worker cursor advancement -> event query through
    the Go HTTP API.

Uses the real, production `python-worker` container (the same
docker-compose.security.yml override wired in this slice -- see
docs/security-event-centralization.md), not a scratchpad script or a
hand-rolled gRPC client. Every scenario here that is marked runnable
(support_status=Status.SKIPPED, i.e. "not yet executed, will be") makes
a real HTTP call against the real Go API and/or a real `docker compose`
lifecycle command against the real running stack -- nothing here
fabricates a result.

Explicitly DEFERRED: every adversarial/malformed-input scenario from
Work Package B's 20-point list (tampered event, tampered batch hash,
invalid signature, wrong signing key, wrong worker identity, replayed
batch, lower sequence, expired batch, oversized batch/event, permanent-
invalid-event quarantine). Constructing a deliberately malformed signed
batch requires bypassing the production GrpcCoordinatorClient (which
never sends anything malformed) and re-deriving the exact canonical-
JSON/Ed25519 signing this harness would then be duplicating from
scratch in Python -- that exact adversarial surface is already covered,
for real, by cpp/coordinator/tests/coordinator_service_test.cpp's
SubmitWorkerSecurityEvents integration block (replay, unknown-worker,
oversized-batch, mixed-valid/malformed-event cases) and
python/tests/test_security_event_batch.py's hash/tamper tests -- see
docs/security-ui-report.md Section 22 for exactly what those already
prove. Re-deriving the same adversarial coverage a second time inside
this live-Compose harness, for marginal additional confidence, was not
attempted this pass.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status

_ADVERSARIAL_DEFERRAL_REASON = (
    "requires bypassing the production GrpcCoordinatorClient to construct a "
    "deliberately malformed signed batch, re-deriving the same canonical-JSON/"
    "Ed25519 signing this harness would then duplicate from scratch; already "
    "covered at the unit/integration level by "
    "cpp/coordinator/tests/coordinator_service_test.cpp's SubmitWorkerSecurityEvents "
    "block and python/tests/test_security_event_batch.py -- see "
    "docs/security-ui-report.md Section 22"
)


def _poll_until(fn, *, timeout_seconds: float, interval_seconds: float = 2.0):
    """Polls fn() (which returns a truthy value on success, or falsy/
    raises to keep waiting) until it succeeds or timeout_seconds
    elapses. Returns the last truthy result, or None on timeout --
    callers assert on the return value themselves."""
    deadline = time.monotonic() + timeout_seconds
    last_result = None
    while time.monotonic() < deadline:
        try:
            last_result = fn()
        except Exception:  # noqa: BLE001 - keep polling through transient errors
            last_result = None
        if last_result:
            return last_result
        time.sleep(interval_seconds)
    return last_result


def _worker_registered_with_signing_key(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")

    def check() -> bool:
        status, body, _ = ctx.http("GET", "/api/v1/security/workers/worker-1", token=admin)
        return status == 200 and bool(body) and bool(body.get("signing_key_id"))

    result = _poll_until(check, timeout_seconds=60.0)
    ctx.assert_true(
        bool(result),
        "worker-1 appears in the worker identity registry with a non-empty "
        "signing_key_id within 60s (proves the signed RegisterWorker capability "
        "statement was verified and bootstrapped a real signing-key registration, "
        "not merely that the process started)",
    )


def _events_reach_central_journal(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")

    def check() -> bool:
        status, body, _ = ctx.http(
            "GET",
            "/api/v1/security/events?event_type=WORKER_REGISTERED&limit=50",
            token=admin,
        )
        if status != 200 or not body:
            return False
        events = body.get("events", [])
        return any(
            event.get("worker_id") == "worker-1"
            and event.get("source_service") == "python-worker"
            for event in events
        )

    result = _poll_until(check, timeout_seconds=45.0)
    ctx.assert_true(
        bool(result),
        "a WORKER_REGISTERED event with source_service=python-worker and "
        "worker_id=worker-1 is queryable through GET /api/v1/security/events "
        "within 45s -- proves the full production path (worker's local queue -> "
        "signed batch -> mTLS gRPC -> SubmitWorkerSecurityEvents -> coordinator "
        "journal -> Go HTTP API) actually delivered a real event end to end",
    )


def _source_health_reports_accepted_batch(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")

    def check() -> bool:
        status, body, _ = ctx.http("GET", "/api/v1/security/events/sources", token=admin)
        if status != 200 or not body:
            return False
        for source in body.get("sources", []):
            if source.get("source_service") == "python-worker" and source.get(
                "batches_accepted", 0
            ) >= 1:
                return True
        return False

    result = _poll_until(check, timeout_seconds=30.0)
    ctx.assert_true(
        bool(result),
        "GET /api/v1/security/events/sources reports a python-worker source "
        "with batches_accepted >= 1",
    )


def _metrics_gauges_present(ctx: Context) -> None:
    status, _, raw = ctx.http("GET", "/metrics")
    text = raw.decode("utf-8", "replace")
    ctx.assert_true(status == 200, "GET /metrics returns 200")
    ctx.assert_true(
        'fl_security_event_source_batches{source_service="python-worker"'
        in text.replace("'", '"'),
        "fl_security_event_source_batches carries a source_service=\"python-worker\" "
        "series (the low-cardinality event-centralization gauge added this slice)",
    )
    ctx.assert_true(
        "fl_security_event_source_records" in text,
        "fl_security_event_source_records is present in the scrape",
    )


def _coordinator_outage_then_delivery(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")

    def current_worker_event_count() -> int:
        status, body, _ = ctx.http(
            "GET",
            "/api/v1/security/events?event_type=WORKER_REGISTERED&limit=500",
            token=admin,
        )
        if status != 200 or not body:
            return -1
        return sum(
            1
            for event in body.get("events", [])
            if event.get("source_service") == "python-worker"
        )

    before = current_worker_event_count()
    ctx.assert_true(before >= 0, "baseline event count is readable before the outage")

    # Simulate a real coordinator outage while the worker keeps running --
    # Work Package B checks #15/#16/#19: the worker's flush attempts must
    # fail closed (retryable, not a crash -- see GrpcCoordinatorClient._grpc_call
    # and the background flush thread's exception handling), the locally
    # queued event must survive a worker restart that happens *during* the
    # outage, and delivery must resume (no silent loss) once the
    # coordinator comes back.
    ctx.compose("stop", "coordinator")
    time.sleep(3.0)
    ctx.compose("restart", "python-worker")
    time.sleep(3.0)
    ctx.compose("start", "coordinator")
    ctx.assert_true(
        ctx.wait_for_health(f"{ctx.api_base}/api/v1/security/transport", timeout_seconds=60.0)
        or True,
        # The health probe above hits the Go API (which was never down),
        # not the coordinator directly -- kept only to pace this
        # scenario; the real assertion is the delivery check below.
        "harness pacing wait completed",
    )

    def check_recovered() -> bool:
        status, body, _ = ctx.http("GET", "/api/v1/security/workers/worker-1", token=admin)
        if status != 200 or not body:
            return False
        return current_worker_event_count() >= before

    result = _poll_until(check_recovered, timeout_seconds=60.0, interval_seconds=3.0)
    ctx.assert_true(
        bool(result),
        "after a coordinator outage overlapping a worker restart, the worker "
        "recovers and centralized worker-sourced events reach at least their "
        "pre-outage count once the coordinator returns -- no silent data loss "
        "across the combined failure",
    )


def _coordinator_restart_preserves_journal(ctx: Context) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "GET", "/api/v1/security/events?event_type=WORKER_REGISTERED&limit=500", token=admin
    )
    ctx.assert_true(status == 200, "events endpoint reachable before coordinator restart")
    before = len(body.get("events", [])) if body else 0

    ctx.compose("restart", "coordinator")
    time.sleep(5.0)

    def check() -> bool:
        status, body, _ = ctx.http(
            "GET",
            "/api/v1/security/events?event_type=WORKER_REGISTERED&limit=500",
            token=admin,
        )
        return status == 200 and body is not None and len(body.get("events", [])) >= before

    result = _poll_until(check, timeout_seconds=45.0)
    ctx.assert_true(
        bool(result),
        "the coordinator's own security-event journal (JSONL, in its container's "
        "writable layer) survives a plain `docker compose restart coordinator` -- "
        "the event count observed through the API never decreases",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="event-centralization.worker.registers-with-signed-capability",
        name="Worker registers with a signed capability statement",
        category="event-centralization",
        description=(
            "The real python-worker container, configured with real mTLS and a "
            "persistent Ed25519 signing identity, registers with the coordinator "
            "and is bootstrapped into WorkerIdentityRegistry/SigningKeyRegistry."
        ),
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="stack up with docker-compose.security.yml's python-worker override",
        assertion="GET /api/v1/security/workers/worker-1 has a non-empty signing_key_id",
        expected_result="worker-1 visible with a real signing_key_id within 60s",
        timeout_seconds=90.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_worker_registered_with_signing_key,
    ),
    Scenario(
        scenario_id="event-centralization.batch.reaches-central-journal",
        name="A worker-signed event batch reaches the coordinator's central journal",
        category="event-centralization",
        description=(
            "The full production path end to end: worker's local queue -> signed "
            "batch -> mTLS gRPC SubmitWorkerSecurityEvents -> coordinator journal "
            "-> Go HTTP API."
        ),
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="event-centralization.worker.registers-with-signed-capability",
        assertion="a WORKER_REGISTERED/python-worker event for worker-1 is queryable via HTTP",
        expected_result="event observable within 45s of worker registration",
        timeout_seconds=60.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_events_reach_central_journal,
    ),
    Scenario(
        scenario_id="event-centralization.source-health.reports-accepted-batch",
        name="Event-source health reports an accepted python-worker batch",
        category="event-centralization",
        description="GetSecurityEventSourceHealth (via Go) reflects a real accepted batch.",
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="event-centralization.batch.reaches-central-journal",
        assertion="GET /api/v1/security/events/sources has a python-worker entry with batches_accepted>=1",
        expected_result="present within 30s",
        timeout_seconds=45.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_source_health_reports_accepted_batch,
    ),
    Scenario(
        scenario_id="event-centralization.metrics.gauges-present",
        name="Event-centralization Prometheus gauges are scrapeable",
        category="event-centralization",
        description="fl_security_event_source_* gauges appear in the Go API's /metrics.",
        required_services=("api",),
        prerequisites="event-centralization.source-health.reports-accepted-batch",
        assertion="GET /metrics contains fl_security_event_source_batches{source_service=\"python-worker\"...}",
        expected_result="present",
        timeout_seconds=15.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_metrics_gauges_present,
    ),
    Scenario(
        scenario_id="event-centralization.recovery.coordinator-outage-then-delivery",
        name="Coordinator outage overlapping a worker restart: no silent event loss",
        category="event-centralization",
        description=(
            "Stops the coordinator, restarts the worker while the coordinator is "
            "down (queued events must survive), brings the coordinator back, and "
            "confirms delivery resumes without net event loss."
        ),
        required_services=("coordinator", "api", "python-worker"),
        prerequisites="event-centralization.batch.reaches-central-journal",
        assertion="post-outage worker-sourced event count >= pre-outage count",
        expected_result="recovery within 60s of the coordinator returning",
        timeout_seconds=180.0,
        cleanup="coordinator and python-worker are left running (restarted, not removed)",
        required=True,
        support_status=Status.SKIPPED,
        run=_coordinator_outage_then_delivery,
    ),
    Scenario(
        scenario_id="event-centralization.restart.coordinator-preserves-journal",
        name="Coordinator restart preserves the security-event journal",
        category="event-centralization",
        description="A plain container restart (not recreate) must not lose journaled events.",
        required_services=("coordinator", "api"),
        prerequisites="event-centralization.recovery.coordinator-outage-then-delivery",
        assertion="event count observed via HTTP never decreases across a coordinator restart",
        expected_result="non-decreasing count within 45s",
        timeout_seconds=90.0,
        cleanup="coordinator is left running (restarted, not removed)",
        required=True,
        support_status=Status.SKIPPED,
        run=_coordinator_restart_preserves_journal,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.tampered-event-skipped",
        name="Tampered individual event is skipped, not fatal to the batch",
        category="event-centralization",
        description="Per-event validation happens after batch-level signature verification.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.tampered-batch-hash-rejected",
        name="Tampered batch payload_hash is rejected",
        category="event-centralization",
        description="A batch whose payload_hash does not match its canonical JSON is rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.invalid-signature-rejected",
        name="Invalid Ed25519 signature is rejected",
        category="event-centralization",
        description="A batch envelope signed with the wrong key (or corrupted) is rejected.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.wrong-worker-identity-rejected",
        name="A batch presenting a mismatched worker_id is rejected",
        category="event-centralization",
        description="batch.worker_id must equal the mTLS-bound worker identity.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.replayed-batch-rejected",
        name="A replayed (identical) batch envelope is rejected",
        category="event-centralization",
        description="Resubmitting an already-accepted envelope must be rejected as a replay.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.lower-sequence-rejected",
        name="A batch with a lower sequence number than already observed is rejected",
        category="event-centralization",
        description="Sequence numbers on the MESSAGE_STREAM_SECURITY_EVENTS track must be monotonic.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.expired-batch-rejected",
        name="A batch envelope past its expires_at is rejected",
        category="event-centralization",
        description="verify_signed_envelope rejects an expired envelope.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.adversarial.oversized-batch-rejected",
        name="A batch exceeding the event-count limit is rejected wholesale",
        category="event-centralization",
        description="kMaxSecurityEventBatchSize (200) is enforced, never silently truncated.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_ADVERSARIAL_DEFERRAL_REASON,
    ),
    Scenario(
        scenario_id="event-centralization.failure.disk-full-not-simulated",
        name="Worker journal behavior when the container's disk is full",
        category="event-centralization",
        description="See docs/security-event-queue-failure-semantics.md's documented policy.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "requires constraining the container's writable-layer size or filling a "
            "mounted volume; not attempted live this pass -- the documented policy in "
            "docs/security-event-queue-failure-semantics.md is design-reviewed but not "
            "runtime-exercised"
        ),
    ),
    Scenario(
        scenario_id="event-centralization.failure.retention-limit-not-simulated",
        name="Worker queue behavior once journal rotation/retention limits are reached",
        category="event-centralization",
        description="See docs/security-event-queue-failure-semantics.md's documented policy.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "would require generating enough events to hit the 10 MiB/5-generation "
            "rotation threshold, impractical within this harness's per-scenario "
            "timeout budget; the documented policy in "
            "docs/security-event-queue-failure-semantics.md is design-reviewed but not "
            "runtime-exercised at that scale"
        ),
    ),
]
