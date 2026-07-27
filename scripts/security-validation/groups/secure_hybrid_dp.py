"""Secure Hybrid Differential Privacy Runtime slice: a bounded, real
scenario group covering what a pure-HTTP harness can meaningfully
assert about the secure hybrid DP mechanism without driving real
workers through it.

Scope, disclosed (mirrors secure_user_level_dp.py's own identical
disclosure): this harness only speaks raw HTTP against an already-
running Compose stack -- it cannot itself drive real sample-level
training, worker-side clipping, masking, or central noise. It creates a
real HYBRID_DP run via the same coordinator API the cleartext hybrid
mechanism already uses, and asserts the *existing* secure user-level-DP
observability routes (status/health/budget) now correctly recognize a
hybrid run rather than rejecting it as "not a user-level-DP run" (the
exact fix this slice made to GetSecureUserLevelPrivacyHealth/Budget).
Configuration-rejection (invalid sample config, variable weight,
combined-epsilon) and mechanism-level assertions (clipping engaged,
both records accepted, central noise applied, dual ledger commit)
remain scripts/validate_secure_hybrid_dp.py's real 3-worker Docker
validation responsibility -- not duplicated here.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _create_and_start_hybrid_run(ctx: Context, run_id: str) -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "POST",
        "/api/v1/coordinator/runs",
        token=admin,
        body={
            "run_id": run_id,
            "algorithm": "fedavg",
            "weighting": "uniform",
            "total_clients": 2,
            "target_clients_per_round": 2,
            "max_rounds": 1,
            "minimum_valid_results": 1,
            "client_ids": ["worker-1", "worker-2"],
            "local_epochs": 1,
            "batch_size": 8,
            "learning_rate": 0.01,
            "privacy": {
                "mode": "hybrid_dp",
                "sample_level": {
                    "noise_multiplier": 1.0,
                    "max_grad_norm": 1.0,
                    "target_delta": 1e-5,
                    "accountant": "rdp",
                },
                "user_level": {
                    "noise_multiplier": 1.0,
                    "target_delta": 1e-5,
                    "accountant": "rdp",
                    "initial_clipping_bound": 1.0,
                    "weighting_strategy": "uniform",
                    "secure_random": True,
                },
            },
        },
    )
    ctx.assert_true(status == 201, f"create hybrid-mode run returns 201, got {status}: {body}")
    status, body, _ = ctx.http("POST", f"/api/v1/coordinator/runs/{run_id}/start", token=admin)
    ctx.assert_true(status == 200, f"start hybrid-mode run returns 200, got {status}: {body}")


def _hybrid_run_budget_not_rejected(ctx: Context) -> None:
    run_id = f"harness-hybrid-budget-{int(time.time())}"
    _create_and_start_hybrid_run(ctx, run_id)
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "GET", f"/api/v1/secure-aggregation/privacy/budget?run_id={run_id}", token=admin
    )
    # Real bug this slice fixed: this route used to reject any run whose
    # privacy_mode was not EXACTLY kUserLevelDp with 412
    # FAILED_PRECONDITION -- a hybrid run's user-level budget was
    # unreachable through this already-built observability surface.
    ctx.assert_true(
        status == 200,
        f"GET .../privacy/budget for a HYBRID_DP run returns 200 (not 412 'not a "
        f"user-level-DP run'), got {status}: {body}",
    )
    ctx.assert_true(
        bool(body) and body.get("run_id") == run_id,
        "budget response echoes the real hybrid run_id",
    )


def _hybrid_run_health_reports_active(ctx: Context) -> None:
    run_id = f"harness-hybrid-health-{int(time.time())}"
    _create_and_start_hybrid_run(ctx, run_id)
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http("GET", "/api/v1/secure-aggregation/privacy/health", token=admin)
    ctx.assert_true(status == 200, f"GET .../privacy/health returns 200, got {status}")
    ctx.assert_true(
        bool(body) and isinstance(body.get("active_runs_with_user_level_dp"), int)
        and body["active_runs_with_user_level_dp"] >= 1,
        f"health's active-run count includes at least this real hybrid run, got {body}",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="secagg.hybrid.configuration-accept",
        name="A structurally valid HYBRID_DP run is created and started via the real coordinator API",
        category="secure-aggregation-hybrid-dp",
        description="POST /api/v1/coordinator/runs with privacy.mode=hybrid_dp returns 201, then starts.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="create returns 201, start returns 200",
        expected_result="201, 200",
        timeout_seconds=20.0,
        cleanup="none (run left in RUNNING state with no workers -- harmless, no aggregate is ever produced)",
        required=True,
        support_status=Status.SKIPPED,
        run=lambda ctx: _create_and_start_hybrid_run(ctx, f"harness-hybrid-accept-{int(time.time())}"),
    ),
    Scenario(
        scenario_id="secagg.hybrid.budget-not-rejected",
        name="The existing secure user-level-DP budget route recognizes a HYBRID_DP run",
        category="secure-aggregation-hybrid-dp",
        description="Real bug fixed this slice: this route used to 412-reject any non-exact-kUserLevelDp run.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.hybrid.configuration-accept",
        assertion="GET .../privacy/budget?run_id=<hybrid-run> returns 200, not 412",
        expected_result="200",
        timeout_seconds=20.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_hybrid_run_budget_not_rejected,
    ),
    Scenario(
        scenario_id="secagg.hybrid.health-includes-hybrid-runs",
        name="The existing secure user-level-DP health route counts HYBRID_DP runs as active",
        category="secure-aggregation-hybrid-dp",
        description="Real bug fixed this slice: the active-run loop used to skip kHybridDp runs entirely.",
        required_services=("coordinator", "api"),
        prerequisites="secagg.hybrid.configuration-accept",
        assertion="active_runs_with_user_level_dp >= 1 after creating a real hybrid run",
        expected_result=">= 1",
        timeout_seconds=20.0,
        cleanup="none (read-only)",
        required=True,
        support_status=Status.SKIPPED,
        run=_hybrid_run_health_reports_active,
    ),
]
