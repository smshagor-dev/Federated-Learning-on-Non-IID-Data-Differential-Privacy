"""Privacy-record authenticity scenarios. All DEFERRED for this
harness invocation: every check here (signed privacy record accepted/
tampered/monotonicity-violated/budget-contradiction) requires a live
CreateRun with a sample-level-DP privacy_config, a compatible worker,
and at least one completed private training round -- substantially
more orchestration than this harness's shared, run-agnostic stack
configures. Real, passing coverage already exists at the unit and
integration level.
"""

from __future__ import annotations

from framework import Scenario, Status

_REASON = (
    "requires a live CreateRun with privacy_config set, a compatible worker "
    "advertising supports_sample_level_dp, and at least one completed private "
    "training round -- not configured by this harness invocation. Already covered by "
    "signed_envelope_verifier_test.cpp's sample_privacy_record_payload_hash_input "
    "tests, accountant_monotonicity_store_test.cpp, python/tests/test_private_training.py, "
    "and coordinator_service_test.cpp's hybrid-DP end-to-end block"
)

SCENARIOS: list[Scenario] = [
    Scenario(
        scenario_id="privacy-authenticity.record.accepted-not-exercised-live",
        name="Signed privacy record is accepted",
        category="privacy-authenticity",
        description="MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD verification succeeds for a real record.",
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
        scenario_id="privacy-authenticity.record.tampering-rejected-not-exercised-live",
        name="Privacy-record tampering is rejected",
        category="privacy-authenticity",
        description="A tampered privacy record's payload_hash mismatch is caught.",
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
        scenario_id="privacy-authenticity.monotonicity.violation-rejected-not-exercised-live",
        name="Accountant-step/epsilon monotonicity violation is rejected",
        category="privacy-authenticity",
        description="AccountantMonotonicityStore rejects a non-monotonic epsilon/step sequence.",
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
        scenario_id="privacy-authenticity.budget.contradiction-rejected-not-exercised-live",
        name="Budget-decision contradiction is rejected",
        category="privacy-authenticity",
        description="A normal accepted update accompanying a 'stopped_before_step' decision is rejected.",
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
