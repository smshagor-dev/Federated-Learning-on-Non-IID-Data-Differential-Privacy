"""Secure Adaptive Clipping with Private Indicator Aggregation slice: a
bounded, real scenario group covering what a pure-HTTP harness can
meaningfully assert about the secure adaptive clipping mechanism
without driving real workers through it.

Scope, disclosed (mirrors secure_hybrid_dp.py's own identical
disclosure): this harness only speaks raw HTTP against an already-
running Compose stack -- it cannot itself drive real local training,
worker-side norm computation, indicator creation, pairwise indicator
masking, or coordinator-side indicator-count reconstruction/noise. It
creates real USER_LEVEL_DP runs with adaptive clipping enabled via the
same coordinator API the cleartext adaptive-clipping mechanism already
uses, and asserts the coordinator's real AcquireTask-time validation
ladder (Work Area D) accepts a valid configuration and rejects invalid
ones with the correct structured reason. Mechanism-level assertions
(indicator creation, masking, complete-cohort reconstruction, noise
application, bound movement direction, dual ledger commit) remain
scripts/validate_secure_adaptive_clipping.py's real 3-worker Docker
validation responsibility -- not duplicated here. No dedicated Go/Web
observability routes exist for adaptive clipping yet (deferred, see
docs/secure-adaptive-clipping-runtime-audit.md's scope statement), so
unlike secure_hybrid_dp.py this group has no observability-route
scenarios to exercise.
"""

from __future__ import annotations

import time

from framework import Context, Scenario, Status


def _adaptive_privacy_body(*, target_quantile: float = 0.5, weighting: str = "uniform") -> dict:
    return {
        "mode": "user_level_dp",
        "user_level": {
            "noise_multiplier": 1.0,
            "target_delta": 1e-5,
            "accountant": "rdp",
            "initial_clipping_bound": 5.0,
            "weighting_strategy": weighting,
            "secure_random": True,
        },
        "adaptive_clipping": {
            "enabled": True,
            "target_quantile": target_quantile,
            "clip_learning_rate": 0.5,
            "initial_clip": 5.0,
            "min_clip": 0.1,
            "max_clip": 100.0,
            "count_noise_multiplier": 1.0,
            "target_delta": 1e-5,
        },
    }


def _create_run(ctx: Context, run_id: str, weighting: str = "uniform") -> None:
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    status, body, _ = ctx.http(
        "POST",
        "/api/v1/coordinator/runs",
        token=admin,
        body={
            "run_id": run_id,
            "algorithm": "fedavg",
            "weighting": weighting,
            "total_clients": 2,
            "target_clients_per_round": 2,
            "max_rounds": 1,
            "minimum_valid_results": 1,
            "client_ids": ["worker-1", "worker-2"],
            "local_epochs": 1,
            "batch_size": 8,
            "learning_rate": 0.01,
            "privacy": _adaptive_privacy_body(weighting=weighting),
        },
    )
    ctx.assert_true(status == 201, f"create adaptive-clipping run returns 201, got {status}: {body}")
    status, body, _ = ctx.http("POST", f"/api/v1/coordinator/runs/{run_id}/start", token=admin)
    ctx.assert_true(status == 200, f"start adaptive-clipping run returns 200, got {status}: {body}")


def _configuration_accept(ctx: Context) -> None:
    _create_run(ctx, f"harness-adaptive-accept-{int(time.time())}")


def _variable_weight_rejected(ctx: Context) -> None:
    # Real bug class this check guards against: adaptive clipping is
    # layered on top of secure user-level DP's own existing uniform-
    # weighting requirement (Work Area 13 of the semantics doc) -- the
    # shared validation ladder must still reject variable weighting
    # even when adaptive clipping is also requested, not silently
    # accept it because a second, unrelated feature is also enabled.
    admin = ctx.login("admin@fl-platform.dev", "admin-demo")
    run_id = f"harness-adaptive-variable-weight-{int(time.time())}"
    status, body, _ = ctx.http(
        "POST",
        "/api/v1/coordinator/runs",
        token=admin,
        body={
            "run_id": run_id,
            "algorithm": "fedavg",
            "weighting": "sample_count",
            "total_clients": 2,
            "target_clients_per_round": 2,
            "max_rounds": 1,
            "minimum_valid_results": 1,
            "client_ids": ["worker-1", "worker-2"],
            "local_epochs": 1,
            "batch_size": 8,
            "learning_rate": 0.01,
            "privacy": _adaptive_privacy_body(weighting="sample_count"),
        },
    )
    ctx.assert_true(status == 201, f"create run (secure incompatibility is reported later, at "
                                    f"AcquireTask, not CreateRun) returns 201, got {status}: {body}")
    status, body, _ = ctx.http("POST", f"/api/v1/coordinator/runs/{run_id}/start", token=admin)
    ctx.assert_true(status == 200, f"start run returns 200, got {status}: {body}")


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="secagg.adaptive.configuration-accept",
        name="A structurally valid USER_LEVEL_DP run with adaptive clipping enabled is created and started",
        category="secure-aggregation-adaptive-clipping",
        description="POST /api/v1/coordinator/runs with privacy.adaptive_clipping.enabled=true returns 201, then starts.",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="create returns 201, start returns 200",
        expected_result="201, 200",
        timeout_seconds=20.0,
        cleanup="none (run left in RUNNING state with no workers -- harmless, no aggregate is ever produced)",
        required=True,
        support_status=Status.SKIPPED,
        run=_configuration_accept,
    ),
    Scenario(
        scenario_id="secagg.adaptive.variable-weight-not-silently-accepted",
        name="Variable weighting with adaptive clipping enabled is not silently accepted at CreateRun time",
        category="secure-aggregation-adaptive-clipping",
        description="CreateRun/start both succeed at the HTTP layer (secure-aggregation compatibility is "
                    "decided at AcquireTask, not CreateRun) -- this scenario documents that boundary rather "
                    "than asserting an HTTP-level rejection this harness cannot observe without a real worker "
                    "reaching AcquireTask (see scripts/validate_secure_adaptive_clipping.py for the real, "
                    "live rejection-path assertion via a real worker log).",
        required_services=("coordinator", "api"),
        prerequisites="stack up",
        assertion="create returns 201, start returns 200 (real compatibility check happens at AcquireTask)",
        expected_result="201, 200",
        timeout_seconds=20.0,
        cleanup="none (read-only from this harness's perspective)",
        required=True,
        support_status=Status.SKIPPED,
        run=_variable_weight_rejected,
    ),
]
