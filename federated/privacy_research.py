"""Research-grade privacy calibration and composition helpers.

This module sits above :mod:`federated.dp_accountant` and provides two
operations that are needed for defensible research experiments:

* calibrate the Gaussian noise multiplier from a target epsilon instead of
  guessing ``sigma``;
* compose RDP curves only when the mechanisms protect the same neighboring
  relation.

It deliberately does not combine privacy guarantees that refer to different
adjacency definitions.  Callers must state the adjacency explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

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
    """One mechanism to be composed at a shared neighboring relation."""

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


def epsilon_for_client_level_gaussian(
    *,
    noise_multiplier: float,
    sample_rate: float,
    steps: int,
    delta: float,
    orders: Iterable[int] | None = None,
) -> float:
    """Return epsilon for the root runtime's client-level Gaussian model."""
    accountant = MomentsAccountant(
        noise_multiplier=noise_multiplier,
        sample_rate=sample_rate,
        target_delta=delta,
        orders=orders,
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
    """Find the smallest practical ``sigma`` whose epsilon is at target.

    Binary search is safe here because, for fixed sampling rate, number of
    steps and delta, the accountant's epsilon is monotone non-increasing in
    the Gaussian noise multiplier.  The returned point is always on the
    privacy-safe side of the target (``achieved_epsilon <= target_epsilon``)
    up to floating point precision.
    """
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

    if steps == 0 or sample_rate == 0.0:
        return NoiseCalibrationResult(
            target_epsilon=target_epsilon,
            achieved_epsilon=0.0,
            noise_multiplier=0.0,
            sample_rate=sample_rate,
            steps=steps,
            delta=delta,
        )

    normalized_orders: Sequence[int] = tuple(orders or DEFAULT_ORDERS)

    def epsilon_at(sigma: float) -> float:
        return epsilon_for_client_level_gaussian(
            noise_multiplier=sigma,
            sample_rate=sample_rate,
            steps=steps,
            delta=delta,
            orders=normalized_orders,
        )

    low = min_noise_multiplier
    high = max(1.0, low * 2.0)
    high = min(high, max_noise_multiplier)

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

    # Re-evaluate the returned safe endpoint so the result is internally
    # consistent even if the loop exited on the sigma-width criterion.
    achieved = epsilon_at(high)
    return NoiseCalibrationResult(
        target_epsilon=target_epsilon,
        achieved_epsilon=achieved,
        noise_multiplier=high,
        sample_rate=sample_rate,
        steps=steps,
        delta=delta,
    )


def compose_same_adjacency_rdp(
    *,
    mechanisms: Sequence[RDPMechanism],
    delta: float,
    orders: Iterable[int] | None = None,
) -> PrivacyCompositionResult:
    """Compose RDP mechanisms only when their adjacency is identical.

    This is the correct primitive for, for example, multiple released
    client-level Gaussian mechanisms over the same add/remove-client
    neighboring relation.  It intentionally refuses to combine sample-level
    DP and client-level DP (or any other mismatched adjacency) into one
    epsilon.
    """
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
        if mechanism.steps < 0:
            raise ValueError(f"{mechanism.name}: steps must be >= 0.")
        if not 0.0 <= mechanism.sample_rate <= 1.0:
            raise ValueError(f"{mechanism.name}: sample_rate must lie in [0, 1].")
        if mechanism.noise_multiplier < 0.0:
            raise ValueError(f"{mechanism.name}: noise_multiplier must be >= 0.")

    normalized_orders = tuple(int(order) for order in (orders or DEFAULT_ORDERS))
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
        total_rdp=tuple(float(value) for value in np.asarray(total_rdp)),
        mechanism_names=tuple(mechanism.name for mechanism in mechanisms),
        adjacency=adjacency,
    )
