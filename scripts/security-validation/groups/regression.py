"""Federated-learning regression scenarios. Full distributed-run
scenarios (non-private/sample-private/user-private/hybrid-private,
personalization) are DEFERRED here: they are already covered, for
real, by the existing Python/C++/Go test suites (run fresh as part of
this same slice's regression pass -- see docs/security-ui-report.md)
and orchestrating a full multi-round federated run inside this
security-focused harness would substantially duplicate that existing
coverage for no new signal. What IS exercised live here is that the
two static, whole-repository correctness gates (terminology policy,
protobuf contract compatibility) still pass against the exact code
this slice shipped.
"""

from __future__ import annotations

import subprocess

from framework import REPO_ROOT, Context, Scenario, Status

_DISTRIBUTED_RUN_REASON = (
    "requires orchestrating a full multi-round CreateRun->StartRun->AcquireTask-> "
    "SubmitClientResult training loop, substantially duplicating existing coverage "
    "for no new signal specific to this security-focused harness. Already covered, "
    "and re-run fresh as part of this slice's own regression pass, by "
    "python/tests/test_private_training.py, coordinator_service_test.cpp's hybrid-DP "
    "block, and the Algorithm Expansion phase's personalization test suite -- see "
    "docs/security-ui-report.md for fresh pass/fail counts"
)


def _terminology_policy_passes(ctx: Context) -> None:
    result = subprocess.run(
        ["python", "scripts/check_project_terminology.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    ctx.assert_true(
        result.returncode == 0,
        "check_project_terminology.py exits 0: "
        f"{(result.stdout + result.stderr)[-500:]}",
    )


def _proto_contracts_pass(ctx: Context) -> None:
    result = subprocess.run(
        ["python", "scripts/verify_proto_contracts.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    ctx.assert_true(
        result.returncode == 0,
        "verify_proto_contracts.py exits 0: "
        f"{(result.stdout + result.stderr)[-500:]}",
    )


SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="regression.terminology.policy-passes",
        name="Repository terminology policy passes against the shipped code",
        category="regression",
        description="scripts/check_project_terminology.py exits 0.",
        required_services=(),
        prerequisites="none",
        assertion="exit code 0",
        expected_result="0",
        timeout_seconds=60.0,
        cleanup="none",
        required=True,
        support_status=Status.SKIPPED,
        run=_terminology_policy_passes,
    ),
    Scenario(
        scenario_id="regression.protobuf.contracts-pass",
        name="Protobuf contract compatibility check passes",
        category="regression",
        description="scripts/verify_proto_contracts.py exits 0.",
        required_services=(),
        prerequisites="none",
        assertion="exit code 0",
        expected_result="0",
        timeout_seconds=60.0,
        cleanup="none",
        required=True,
        support_status=Status.SKIPPED,
        run=_proto_contracts_pass,
    ),
    Scenario(
        scenario_id="regression.distributed-run.non-private-not-exercised-live",
        name="Non-private distributed run completes",
        category="regression",
        description="A full CreateRun->StartRun->...->completed round trip with no privacy config.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_DISTRIBUTED_RUN_REASON,
    ),
    Scenario(
        scenario_id="regression.distributed-run.sample-private-not-exercised-live",
        name="Sample-private distributed run completes",
        category="regression",
        description="A full round trip with sample-level DP configured.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_DISTRIBUTED_RUN_REASON,
    ),
    Scenario(
        scenario_id="regression.distributed-run.user-private-not-exercised-live",
        name="User-private distributed run completes",
        category="regression",
        description="A full round trip with user-level DP configured.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_DISTRIBUTED_RUN_REASON,
    ),
    Scenario(
        scenario_id="regression.distributed-run.hybrid-private-not-exercised-live",
        name="Hybrid-private distributed run completes",
        category="regression",
        description="A full round trip with both sample- and user-level DP configured.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_DISTRIBUTED_RUN_REASON,
    ),
    Scenario(
        scenario_id="regression.personalization.supported-run-not-exercised-live",
        name="A personalization-enabled run completes where currently supported",
        category="regression",
        description="FedSAM/Ditto/Per-FedAvg personalization round trip.",
        required_services=(),
        prerequisites="n/a",
        assertion="n/a",
        expected_result="n/a",
        timeout_seconds=0.0,
        cleanup="n/a",
        required=False,
        support_status=Status.DEFERRED,
        unsupported_reason=_DISTRIBUTED_RUN_REASON,
    ),
]
