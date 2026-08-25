"""Fail-closed v3 algorithm and security/privacy capability matrix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlgorithmCapability:
    algorithm: str
    production_in_v2: bool
    personalization: bool
    async_validated: bool = False
    differential_privacy_validated: bool = False


ALGORITHMS: dict[str, AlgorithmCapability] = {
    "fedavg": AlgorithmCapability("fedavg", True, False, False, True),
    "fedprox": AlgorithmCapability("fedprox", True, False, False, True),
    "scaffold": AlgorithmCapability("scaffold", True, False),
    "fedsam": AlgorithmCapability("fedsam", True, False),
    "ditto": AlgorithmCapability("ditto", True, True),
    "per_fedavg": AlgorithmCapability("per_fedavg", True, True),
    "fedadam": AlgorithmCapability("fedadam", False, False),
    "fedyogi": AlgorithmCapability("fedyogi", False, False),
    "fedadagrad": AlgorithmCapability("fedadagrad", False, False),
    "fednova": AlgorithmCapability("fednova", False, False),
    "fedbn": AlgorithmCapability("fedbn", False, True),
    "fedrep": AlgorithmCapability("fedrep", False, True),
    "moon": AlgorithmCapability("moon", False, False),
    "pfedme": AlgorithmCapability("pfedme", False, True),
}


@dataclass(frozen=True)
class CapabilityRequest:
    algorithm: str
    asynchronous: bool = False
    differential_privacy: bool = False
    secure_aggregation: bool = False
    robust_aggregation: bool = False
    adaptive_clipping: bool = False
    threshold_recovery: bool = False


def validate_capability_request(request: CapabilityRequest) -> None:
    """Reject combinations that v3 has not yet validated end to end."""
    algorithm = request.algorithm.lower()
    capability = ALGORITHMS.get(algorithm)
    if capability is None:
        raise ValueError(f"unknown algorithm: {request.algorithm}")
    if request.asynchronous and not capability.async_validated:
        raise ValueError(f"asynchronous {algorithm} is not release-validated")
    if request.differential_privacy and not capability.differential_privacy_validated:
        raise ValueError(f"DP-enabled {algorithm} is not release-validated")
    if request.asynchronous and request.secure_aggregation:
        raise ValueError("asynchronous secure aggregation is not release-validated")
    if request.robust_aggregation and request.secure_aggregation:
        raise ValueError(
            "robust aggregation cannot inspect individual updates behind the "
            "current secure aggregation path"
        )
    if request.robust_aggregation and request.differential_privacy:
        raise ValueError("robust aggregation + DP composition is not release-validated")
    if request.adaptive_clipping and request.asynchronous:
        raise ValueError(
            "adaptive clipping accounting for asynchronous releases is not validated"
        )
    if request.threshold_recovery and not request.secure_aggregation:
        raise ValueError("threshold recovery requires secure aggregation")
    if request.threshold_recovery:
        raise ValueError("threshold secure-aggregation recovery remains experimental")


__all__ = [
    "ALGORITHMS",
    "AlgorithmCapability",
    "CapabilityRequest",
    "validate_capability_request",
]
