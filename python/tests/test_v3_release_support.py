from __future__ import annotations

import pytest

from fl_platform.v3.capabilities import CapabilityRequest, validate_capability_request
from fl_platform.v3.release_gates import REQUIRED_V3_GATES, ReleaseGateReport
from fl_platform.v3.release_support import (
    GATE_QUALIFICATIONS,
    QualificationMode,
    release_support_payload,
    validate_release_support_contract,
)


def test_release_support_contract_covers_every_gate() -> None:
    validate_release_support_contract()
    assert tuple(GATE_QUALIFICATIONS) == REQUIRED_V3_GATES


def test_release_support_payload_is_versioned_and_complete() -> None:
    payload = release_support_payload()
    assert payload["schema_version"] == 1
    assert payload["release"] == "3.0.0"
    gates = payload["gates"]
    assert isinstance(gates, list)
    assert len(gates) == len(REQUIRED_V3_GATES)


def test_unqualified_high_risk_surfaces_remain_experimental() -> None:
    async_gate = GATE_QUALIFICATIONS["async-runtime"]
    secure_gate = GATE_QUALIFICATIONS["secure-aggregation"]
    workloads_gate = GATE_QUALIFICATIONS["federated-workloads"]
    benchmark_gate = GATE_QUALIFICATIONS["benchmark-matrix"]
    edge_gate = GATE_QUALIFICATIONS["edge-runtime"]

    assert async_gate.mode is QualificationMode.FAIL_CLOSED_EXPERIMENTAL
    assert secure_gate.mode is QualificationMode.FAIL_CLOSED_EXPERIMENTAL
    assert any("distributed asynchronous" in item for item in async_gate.experimental_exclusions)
    assert any("threshold dropout" in item for item in secure_gate.experimental_exclusions)
    assert any("FEMNIST" in item for item in workloads_gate.experimental_exclusions)
    assert any("full attack/privacy" in item for item in benchmark_gate.experimental_exclusions)
    assert any("physical edge-device" in item for item in edge_gate.experimental_exclusions)


@pytest.mark.parametrize(
    "request",
    [
        CapabilityRequest(algorithm="fedavg", asynchronous=True),
        CapabilityRequest(
            algorithm="fedavg",
            secure_aggregation=True,
            threshold_recovery=True,
        ),
        CapabilityRequest(
            algorithm="fedavg",
            differential_privacy=True,
            robust_aggregation=True,
        ),
        CapabilityRequest(
            algorithm="fedavg",
            secure_aggregation=True,
            robust_aggregation=True,
        ),
    ],
)
def test_experimental_combinations_fail_closed(request: CapabilityRequest) -> None:
    with pytest.raises(ValueError):
        validate_capability_request(request)


def test_release_gate_report_requires_every_gate() -> None:
    all_green = {gate: True for gate in REQUIRED_V3_GATES}
    assert ReleaseGateReport(all_green).release_ready()

    all_green["benchmark-matrix"] = False
    report = ReleaseGateReport(all_green)
    assert not report.release_ready()
    assert report.missing() == ("benchmark-matrix",)
    with pytest.raises(RuntimeError, match="benchmark-matrix"):
        report.require_release_ready()
