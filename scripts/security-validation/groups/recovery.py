"""Recovery scenarios distinct from the restart-persistence checks
already covered in event_journal.py/event_centralization.py: whether
the Go API's own coordinator-health status accurately reflects a real
outage-and-recovery cycle, and a clean full-stack teardown.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _coordinator_health_reflects_real_outage(ctx: Context) -> None:
    # GET /api/v1/system/coordinator-health's real response shape
    # (go/internal/transport/httpapi/coordinator_handlers.go's
    # handleCoordinatorHealth) is {"status": "<coordinator's own Health
    # RPC status string>"} on success (200) -- there is no "healthy"
    # boolean field. On a real coordinator outage, Health() returns
    # coordinator.ErrUnavailable, which writeCoordinatorError maps to
    # HTTP 503, not a 200 with a false flag. This scenario originally
    # assumed a {"healthy": bool} shape that does not exist -- caught by
    # this slice's own live Docker Compose run (see
    # docs/security-runtime-validation.md), not by any unit test, since
    # nothing else in this harness had exercised this specific endpoint
    # before.
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/system/coordinator-health", token=admin)
    ctx.assert_true(
        status == 200 and bool(body) and bool(body.get("status")),
        f"coordinator-health reports a real status before the outage, got status={status} body={body}",
    )

    ctx.compose("stop", "coordinator")
    try:
        # The Go API's own request timeout for the underlying coordinator
        # RPC bounds how long a single poll can take; a couple of retries
        # is enough to observe the transition without an arbitrary sleep.
        deadline = time.monotonic() + 20.0
        saw_unhealthy = False
        while time.monotonic() < deadline:
            status, _, _ = ctx.http("GET", "/api/v1/system/coordinator-health", token=admin)
            if status != 200:
                saw_unhealthy = True
                break
            time.sleep(2.0)
        ctx.assert_true(
            saw_unhealthy,
            "coordinator-health stops returning 200 within 20s of the coordinator "
            "actually being stopped -- the status is live-checked, not cached forever",
        )
    finally:
        ctx.compose("start", "coordinator")

    deadline = time.monotonic() + 90.0
    recovered = False
    while time.monotonic() < deadline:
        status, body, _ = ctx.http("GET", "/api/v1/system/coordinator-health", token=admin)
        if status == 200 and bool((body or {}).get("status")):
            recovered = True
            break
        time.sleep(3.0)
    ctx.assert_true(
        recovered, "coordinator-health reports a real status again within 90s of restart"
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="recovery.coordinator-health.reflects-real-outage",
        name="Go API's coordinator-health status reflects a real stop/start cycle",
        category="recovery",
        description="healthy transitions true -> false -> true across a real coordinator outage.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="healthy is true, then false during the outage, then true again after recovery",
        expected_result="full transition observed",
        # Real-world budget: baseline check + up to 20s outage-detection
        # polling + docker compose stop/start overhead (observed to take
        # longer than the C++ coordinator's in-process restart alone --
        # container teardown/startup has its own real cost) + up to 90s
        # recovery polling. 120s was too tight in practice -- caught by
        # this slice's own live run, which hit the harness's own
        # ScenarioTimeout watchdog even though the scenario's real
        # assertions were still succeeding right up to that point.
        timeout_seconds=220.0,
        cleanup="coordinator is left running",
        required=True,
        support_status=Status.SKIPPED,
        run=_coordinator_health_reflects_real_outage,
    ),
    Scenario(
        scenario_id="recovery.teardown.clean-not-exercised-per-scenario",
        name="Full stack teardown leaves no orphaned containers/volumes",
        category="recovery",
        description="`docker compose down -v` after the full harness run.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=(
            "teardown is performed once, by the harness's own run() orchestration "
            "after every selected group finishes, not as an individually-scored "
            "scenario -- see run.py's finally-block teardown, which this registry "
            "entry documents rather than duplicates"
        ),
    ),
]
