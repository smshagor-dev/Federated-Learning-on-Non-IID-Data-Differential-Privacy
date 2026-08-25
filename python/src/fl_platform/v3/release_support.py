"""Truthful v3.0.0 stable-support and experimental-exclusion contract."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from fl_platform.v3.release_gates import REQUIRED_V3_GATES


class QualificationMode(StrEnum):
    """How a release gate is qualified for the stable support contract."""

    STABLE = "stable"
    FAIL_CLOSED_EXPERIMENTAL = "fail-closed-experimental"


@dataclass(frozen=True, slots=True)
class GateQualification:
    """Stable claims and explicit exclusions associated with one release gate."""

    gate: str
    mode: QualificationMode
    stable_capabilities: tuple[str, ...]
    experimental_exclusions: tuple[str, ...]
    checks: tuple[str, ...]


GATE_QUALIFICATIONS: dict[str, GateQualification] = {
    "async-runtime": GateQualification(
        gate="async-runtime",
        mode=QualificationMode.FAIL_CLOSED_EXPERIMENTAL,
        stable_capabilities=(
            "durable model/version checkpoints and replay protection primitives",
            "lease-based client membership primitives",
        ),
        experimental_exclusions=(
            "true distributed asynchronous training is not a stable v3.0.0 capability",
            "asynchronous secure aggregation is rejected",
        ),
        checks=(
            "test_v3_async_durability.py",
            "capability matrix rejects unvalidated asynchronous combinations",
        ),
    ),
    "robust-aggregation": GateQualification(
        gate="robust-aggregation",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "median and trimmed-mean robust aggregation for supported non-private synchronous runs",
            "deterministic poisoning transformations and adversarial validation",
        ),
        experimental_exclusions=(
            "robust aggregation combined with differential privacy is rejected",
            "robust aggregation combined with secure aggregation is rejected",
        ),
        checks=(
            "test_v3_adversarial_robustness.py",
            "release-candidate robust aggregation tests",
        ),
    ),
    "privacy-validation": GateQualification(
        gate="privacy-validation",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "validated differential-privacy accounting and budget enforcement",
            "release-validated DP capability combinations for FedAvg and FedProx",
        ),
        experimental_exclusions=(
            "unvalidated DP algorithm combinations are rejected",
            "adaptive clipping with asynchronous releases is rejected",
        ),
        checks=(
            "privacy statistical validation tests",
            "privacy budget enforcement tests",
            "v3 privacy validation tests",
        ),
    ),
    "secure-aggregation": GateQualification(
        gate="secure-aggregation",
        mode=QualificationMode.FAIL_CLOSED_EXPERIMENTAL,
        stable_capabilities=(
            "authenticated secure-aggregation protocol primitives and wire compatibility",
            "encrypted recovery-share relay with integrity and replay protection",
        ),
        experimental_exclusions=(
            "threshold dropout recovery remains experimental in the public capability matrix",
            "in-flight secure rounds are not guaranteed resumable after coordinator restart",
            "asynchronous secure aggregation is rejected",
        ),
        checks=(
            "secure aggregation Python and C++ compatibility tests",
            "live distributed recovery security-validation scenario",
            "capability matrix rejects threshold recovery as stable",
        ),
    ),
    "algorithm-suite": GateQualification(
        gate="algorithm-suite",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "FedAvg, FedProx, SCAFFOLD, FedSAM, Ditto, and Per-FedAvg worker implementations",
            "canonical algorithm registry and fail-closed capability discovery",
        ),
        experimental_exclusions=(
            "newer v3 algorithms without release benchmark qualification are not promoted to stable claims",
        ),
        checks=(
            "test_v3_algorithm_suite.py",
            "algorithm expansion foundation tests",
        ),
    ),
    "system-heterogeneity": GateQualification(
        gate="system-heterogeneity",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "deterministic compute, network, availability, and payload heterogeneity simulation",
            "heterogeneity-aware execution policies",
        ),
        experimental_exclusions=(
            "physical heterogeneous-fleet performance is not claimed by v3.0.0",
        ),
        checks=(
            "test_v3_system_heterogeneity.py",
            "release-candidate heterogeneity tests",
        ),
    ),
    "federated-workloads": GateQualification(
        gate="federated-workloads",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "MNIST, FashionMNIST, CIFAR10, and CIFAR100 workload catalog entries",
            "validated partition and dataset-loading contracts for the stable image workloads",
        ),
        experimental_exclusions=(
            "FEMNIST loader remains experimental",
            "Shakespeare loader remains experimental",
            "Sent140 loader remains experimental",
        ),
        checks=(
            "test_v3_federated_workloads.py",
            "dataset partition and loader tests",
        ),
    ),
    "distributed-infrastructure": GateQualification(
        gate="distributed-infrastructure",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "containerized coordinator, API, and Python worker deployment contract",
            "mTLS identity, signed-message, replay, audit, and restart validation in real containers",
        ),
        experimental_exclusions=(
            "geographically distributed multi-host performance is not claimed by v3.0.0",
        ),
        checks=(
            "v3 distributed runtime evidence workflow",
            "Kubernetes release manifest validation",
        ),
    ),
    "observability": GateQualification(
        gate="observability",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "privacy-safe aggregate runtime metrics and observability export primitives",
            "security and audit event centralization in the distributed validation stack",
        ),
        experimental_exclusions=(
            "a hosted production OpenTelemetry collector/dashboard is not bundled as a service guarantee",
        ),
        checks=(
            "test_v3_observability_runtime.py",
            "distributed metrics and event-centralization scenarios",
        ),
    ),
    "benchmark-matrix": GateQualification(
        gate="benchmark-matrix",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "real five-seed root-runtime MNIST/FedAvg stable-baseline evidence",
            "fail-closed v3 benchmark matrix planning and provenance validation",
        ),
        experimental_exclusions=(
            "the full attack/privacy/heterogeneity research cross-product is not claimed complete for v3.0.0",
        ),
        checks=(
            "v3 final qualification real runtime benchmark",
            "v3 benchmark matrix contract tests",
        ),
    ),
    "edge-runtime": GateQualification(
        gate="edge-runtime",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "ARM64 OCI worker image build and self-test",
            "edge payload/resource policy validation",
        ),
        experimental_exclusions=(
            "physical edge-device throughput, energy, and thermal performance are not claimed",
        ),
        checks=(
            "release-candidate ARM64 edge OCI job",
            "test_v3_edge_runtime.py",
        ),
    ),
    "supply-chain-security": GateQualification(
        gate="supply-chain-security",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "immutable OCI digest lock for first-party and third-party release images",
            "CycloneDX SBOM, artifact hashes, and GitHub build attestations",
        ),
        experimental_exclusions=(),
        checks=(
            "v3 supply-chain workflow",
            "v3 release artifact workflow",
        ),
    ),
    "chaos-reliability": GateQualification(
        gate="chaos-reliability",
        mode=QualificationMode.STABLE,
        stable_capabilities=(
            "deterministic 500-seed chaos soak",
            "containerized worker/coordinator restart and recovery validation",
        ),
        experimental_exclusions=(
            "long-duration physical multi-host soak is not a v3.0.0 stability claim",
        ),
        checks=(
            "run_v3_chaos_soak.py release-candidate execution",
            "v3 distributed runtime restart scenarios",
        ),
    ),
}


def validate_release_support_contract() -> None:
    """Require one explicit, internally consistent qualification per release gate."""
    expected = set(REQUIRED_V3_GATES)
    actual = set(GATE_QUALIFICATIONS)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise ValueError(
            "release support contract does not match required gates: "
            f"missing={missing}, unexpected={unexpected}"
        )

    for gate in REQUIRED_V3_GATES:
        qualification = GATE_QUALIFICATIONS[gate]
        if qualification.gate != gate:
            raise ValueError(f"release support gate key mismatch: {gate}")
        if not qualification.stable_capabilities and not qualification.experimental_exclusions:
            raise ValueError(f"release support gate has no scoped capability: {gate}")
        if not qualification.checks:
            raise ValueError(f"release support gate has no qualification checks: {gate}")
        overlap = set(qualification.stable_capabilities).intersection(
            qualification.experimental_exclusions
        )
        if overlap:
            raise ValueError(f"release support gate has overlapping claims: {gate}")
        if (
            qualification.mode is QualificationMode.FAIL_CLOSED_EXPERIMENTAL
            and not qualification.experimental_exclusions
        ):
            raise ValueError(
                f"fail-closed experimental gate lacks explicit exclusions: {gate}"
            )


def release_support_payload() -> dict[str, object]:
    """Return a deterministic JSON-compatible support contract."""
    validate_release_support_contract()
    return {
        "schema_version": 1,
        "release": "3.0.0",
        "gates": [
            {
                **asdict(GATE_QUALIFICATIONS[gate]),
                "mode": GATE_QUALIFICATIONS[gate].mode.value,
            }
            for gate in REQUIRED_V3_GATES
        ],
    }


__all__ = [
    "GATE_QUALIFICATIONS",
    "GateQualification",
    "QualificationMode",
    "release_support_payload",
    "validate_release_support_contract",
]
