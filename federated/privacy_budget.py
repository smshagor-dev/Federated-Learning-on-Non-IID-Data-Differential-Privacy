"""Privacy-budget calibration and same-adjacency RDP composition helpers.

The module sits above :mod:`federated.dp_accountant` and provides:

* target-epsilon calibration for the Gaussian noise multiplier;
* effective configuration resolution after CLI/UI overrides;
* RDP composition only when mechanisms share the same neighboring relation.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Iterable, Sequence

from federated.dp_accountant import (
    DEFAULT_ORDERS,
    MomentsAccountant,
    compose_rdp_curves,
    compute_rdp,
    rdp_to_epsilon,
)

CLIENT_ADD_REMOVE_ADJACENCY = "client_add_remove"


@dataclass(frozen=True, slots=True)
class NoiseCalibrationResult:
    target_epsilon: float
    achieved_epsilon: float
    noise_multiplier: float
    sample_rate: float
    steps: int
    delta: float


@dataclass(frozen=True, slots=True)
class RDPMechanism:
    name: str
    adjacency: str
    sample_rate: float
    noise_multiplier: float
    steps: int


@dataclass(frozen=True, slots=True)
class PrivacyCompositionResult:
    epsilon: float
    delta: float
    optimal_order: int
    orders: tuple[int, ...]
    total_rdp: tuple[float, ...]
    mechanism_names: tuple[str, ...]
    adjacency: str


def _normalize_orders(orders: Iterable[int] | None) -> tuple[int, ...]:
    source = DEFAULT_ORDERS if orders is None else orders
    normalized = tuple(sorted(set(int(order) for order in source)))
    if not normalized:
        raise ValueError("At least one RDP order is required.")
    if normalized[0] < 2:
        raise ValueError("All RDP orders must be integers >= 2.")
    return normalized


def epsilon_for_client_level_gaussian(
    *,
    noise_multiplier: float,
    sample_rate: float,
    steps: int,
    delta: float,
    orders: Iterable[int] | None = None,
) -> float:
    accountant = MomentsAccountant(
        noise_multiplier=noise_multiplier,
        sample_rate=sample_rate,
        target_delta=delta,
        orders=_normalize_orders(orders),
    )
    accountant.step(steps)
    return accountant.get_epsilon()


def calibrate_noise_multiplier(
    *,
    target_epsilon: float,
    sample_rate: float,
    steps: int,
    delta: float,
    orders: Iterable[int] | None = None,
    epsilon_tolerance: float = 1e-4,
    min_noise_multiplier: float = 1e-6,
    max_noise_multiplier: float = 1e3,
    max_iterations: int = 200,
) -> NoiseCalibrationResult:
    """Return the smallest practical sigma on the privacy-safe side."""
    if target_epsilon <= 0.0:
        raise ValueError("target_epsilon must be > 0.")
    if not 0.0 <= sample_rate <= 1.0:
        raise ValueError("sample_rate must lie in [0, 1].")
    if steps < 0:
        raise ValueError("steps must be >= 0.")
    if not 0.0 < delta < 1.0:
        raise ValueError("delta must lie in (0, 1).")
    if epsilon_tolerance <= 0.0:
        raise ValueError("epsilon_tolerance must be > 0.")
    if min_noise_multiplier <= 0.0:
        raise ValueError("min_noise_multiplier must be > 0.")
    if max_noise_multiplier <= min_noise_multiplier:
        raise ValueError("max_noise_multiplier must exceed min_noise_multiplier.")
    if max_iterations <= 0:
        raise ValueError("max_iterations must be > 0.")

    normalized_orders: Sequence[int] = _normalize_orders(orders)
    if steps == 0 or sample_rate == 0.0:
        return NoiseCalibrationResult(
            target_epsilon=target_epsilon,
            achieved_epsilon=0.0,
            noise_multiplier=0.0,
            sample_rate=sample_rate,
            steps=steps,
            delta=delta,
        )

    def epsilon_at(sigma: float) -> float:
        return epsilon_for_client_level_gaussian(
            noise_multiplier=sigma,
            sample_rate=sample_rate,
            steps=steps,
            delta=delta,
            orders=normalized_orders,
        )

    low = min_noise_multiplier
    high = min(max_noise_multiplier, max(1.0, low * 2.0))
    while epsilon_at(high) > target_epsilon and high < max_noise_multiplier:
        high = min(max_noise_multiplier, high * 2.0)

    high_epsilon = epsilon_at(high)
    if high_epsilon > target_epsilon:
        raise ValueError(
            "target_epsilon is not reachable within max_noise_multiplier; "
            f"epsilon({max_noise_multiplier})={high_epsilon:.8g}."
        )

    for _ in range(max_iterations):
        mid = (low + high) / 2.0
        mid_epsilon = epsilon_at(mid)
        if mid_epsilon <= target_epsilon:
            high = mid
            high_epsilon = mid_epsilon
        else:
            low = mid
        if abs(target_epsilon - high_epsilon) <= epsilon_tolerance:
            break
        if high - low <= 1e-12 * max(1.0, high):
            break

    achieved = epsilon_at(high)
    return NoiseCalibrationResult(
        target_epsilon=target_epsilon,
        achieved_epsilon=achieved,
        noise_multiplier=high,
        sample_rate=sample_rate,
        steps=steps,
        delta=delta,
    )


def resolve_target_epsilon_config(
    config: dict,
    *,
    manual_noise_override: bool = False,
) -> tuple[dict, NoiseCalibrationResult | None]:
    """Resolve the effective sigma after all runtime overrides."""
    resolved = copy.deepcopy(config)
    dp_cfg = resolved.setdefault("dp", {})
    fed_cfg = resolved.setdefault("federated", {})

    if not bool(dp_cfg.get("enabled", False)):
        return resolved, None

    if manual_noise_override:
        if float(dp_cfg.get("noise_multiplier", 0.0)) < 0.0:
            raise ValueError("dp.noise_multiplier must be >= 0 for a manual override.")
        dp_cfg["target_epsilon"] = None
        dp_cfg["privacy_parameter_source"] = "manual_noise_multiplier"
        return resolved, None

    target = dp_cfg.get("target_epsilon")
    if target is None:
        dp_cfg["privacy_parameter_source"] = "configured_noise_multiplier"
        return resolved, None

    result = calibrate_noise_multiplier(
        target_epsilon=float(target),
        sample_rate=float(fed_cfg["sample_rate"]),
        steps=int(fed_cfg["rounds"]),
        delta=float(dp_cfg["target_delta"]),
        epsilon_tolerance=float(dp_cfg.get("epsilon_tolerance", 1e-4)),
    )
    dp_cfg["noise_multiplier"] = result.noise_multiplier
    dp_cfg["calibrated_epsilon"] = result.achieved_epsilon
    dp_cfg["privacy_parameter_source"] = "target_epsilon_calibration"
    return resolved, result


def compose_same_adjacency_rdp(
    *,
    mechanisms: Sequence[RDPMechanism],
    delta: float,
    orders: Iterable[int] | None = None,
) -> PrivacyCompositionResult:
    """Compose RDP only when every mechanism uses identical adjacency."""
    if not mechanisms:
        raise ValueError("At least one mechanism is required.")
    if not 0.0 < delta < 1.0:
        raise ValueError("delta must lie in (0, 1).")

    adjacency = mechanisms[0].adjacency
    if not adjacency:
        raise ValueError("Mechanism adjacency must be non-empty.")
    for mechanism in mechanisms:
        if mechanism.adjacency != adjacency:
            raise ValueError(
                "Cannot compose mechanisms with different neighboring relations: "
                f"{adjacency!r} vs {mechanism.adjacency!r}."
            )
        if not mechanism.name.strip():
            raise ValueError("Mechanism name must be non-empty.")
        if mechanism.steps < 0:
            raise ValueError(f"{mechanism.name}: steps must be >= 0.")
        if not 0.0 <= mechanism.sample_rate <= 1.0:
            raise ValueError(f"{mechanism.name}: sample_rate must lie in [0, 1].")
        if mechanism.noise_multiplier < 0.0:
            raise ValueError(f"{mechanism.name}: noise_multiplier must be >= 0.")

    normalized_orders = _normalize_orders(orders)
    curves = [
        compute_rdp(
            q=mechanism.sample_rate,
            noise_multiplier=mechanism.noise_multiplier,
            steps=mechanism.steps,
            orders=normalized_orders,
        )
        for mechanism in mechanisms
    ]
    total_rdp = compose_rdp_curves(curves)
    epsilon, optimal_order = rdp_to_epsilon(
        orders=normalized_orders,
        total_rdp=total_rdp,
        delta=delta,
    )
    return PrivacyCompositionResult(
        epsilon=float(epsilon),
        delta=delta,
        optimal_order=int(optimal_order),
        orders=normalized_orders,
        total_rdp=tuple(float(value) for value in total_rdp),
        mechanism_names=tuple(mechanism.name for mechanism in mechanisms),
        adjacency=adjacency,
    )
