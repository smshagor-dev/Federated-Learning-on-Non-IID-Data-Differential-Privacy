"""Client-level privacy accounting for the root federated runtime.

The active root runtime models each communication round as a Poisson-
subsampled Gaussian mechanism under *client-level* add/remove adjacency:

* each of the ``K`` clients is sampled independently with probability ``q``
* every sampled client contributes at most one clipped model update
* the trusted server adds one Gaussian noise vector to the aggregate sum

This module tracks Renyi Differential Privacy (RDP) for that mechanism and
converts the composed RDP curve to an ``(epsilon, delta)`` estimate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from scipy.special import gammaln, logsumexp

DEFAULT_ORDERS: list[int] = list(range(2, 65)) + [80, 96, 128, 256, 512]


def _validate_orders(orders: Iterable[int]) -> list[int]:
    normalized = sorted(set(int(order) for order in orders))
    if not normalized:
        raise ValueError("At least one RDP order is required.")
    if min(normalized) < 2:
        raise ValueError("All RDP orders must be integers >= 2.")
    return normalized


def _compute_rdp_single_step(alpha: int, *, q: float, noise_multiplier: float) -> float:
    if q == 0.0:
        return 0.0
    if noise_multiplier == 0.0:
        return float("inf")
    if q == 1.0:
        return alpha / (2.0 * noise_multiplier * noise_multiplier)

    log_terms: list[float] = []
    for k in range(alpha + 1):
        log_binom = (
            gammaln(alpha + 1) - gammaln(k + 1) - gammaln(alpha - k + 1)
        )
        log_term = (
            log_binom
            + (alpha - k) * math.log1p(-q)
            + (k * math.log(q) if k > 0 else 0.0)
            + (k * (k - 1)) / (2.0 * noise_multiplier * noise_multiplier)
        )
        log_terms.append(log_term)
    return float(logsumexp(log_terms) / (alpha - 1))


def compute_rdp(
    *,
    q: float,
    noise_multiplier: float,
    steps: int,
    orders: Sequence[int] | None = None,
) -> np.ndarray:
    """Return the composed RDP curve for ``steps`` Poisson-sampled rounds."""
    if not 0.0 <= q <= 1.0:
        raise ValueError("sample_rate must lie in [0, 1].")
    if noise_multiplier < 0.0:
        raise ValueError("noise_multiplier must be >= 0.")
    if steps < 0:
        raise ValueError("steps must be >= 0.")
    validated_orders = _validate_orders(orders or DEFAULT_ORDERS)
    per_step = np.asarray(
        [
            _compute_rdp_single_step(
                order, q=q, noise_multiplier=noise_multiplier
            )
            for order in validated_orders
        ],
        dtype=np.float64,
    )
    return per_step * float(steps)


def rdp_to_epsilon(
    *,
    orders: Sequence[int],
    total_rdp: Sequence[float],
    delta: float,
) -> tuple[float, int]:
    """Convert a total RDP curve to the best ``epsilon`` at ``delta``."""
    validated_orders = _validate_orders(orders)
    if len(validated_orders) != len(total_rdp):
        raise ValueError("orders and total_rdp must have the same length.")
    if not 0.0 < delta < 1.0:
        raise ValueError("delta must lie in (0, 1).")

    best_epsilon = float("inf")
    best_order = validated_orders[0]
    for order, rdp_value in zip(validated_orders, total_rdp, strict=True):
        if not np.isfinite(rdp_value):
            continue
        epsilon = float(rdp_value) + math.log(1.0 / delta) / (order - 1)
        if epsilon < best_epsilon:
            best_epsilon = epsilon
            best_order = order
    if np.isinf(best_epsilon):
        return float("inf"), best_order
    return best_epsilon, best_order


def compose_rdp_curves(curves: Sequence[Sequence[float]]) -> np.ndarray:
    """Additively compose multiple RDP curves on the same order grid."""
    if not curves:
        return np.zeros(0, dtype=np.float64)
    return np.sum(np.asarray(curves, dtype=np.float64), axis=0)


@dataclass(slots=True)
class PrivacyEstimate:
    epsilon: float
    optimal_order: int
    total_rdp: np.ndarray


class MomentsAccountant:
    """Tracks composed client-level RDP across communication rounds."""

    def __init__(
        self,
        noise_multiplier: float,
        sample_rate: float,
        target_delta: float = 1e-5,
        orders: Iterable[int] | None = None,
    ) -> None:
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError("sample_rate must lie in [0, 1].")
        if noise_multiplier < 0.0:
            raise ValueError("noise_multiplier must be >= 0.")
        if not 0.0 < target_delta < 1.0:
            raise ValueError("target_delta must lie in (0, 1).")

        self.noise_multiplier = float(noise_multiplier)
        self.sample_rate = float(sample_rate)
        self.target_delta = float(target_delta)
        self.orders = _validate_orders(orders or DEFAULT_ORDERS)
        self.steps = 0
        self._rdp_per_step = compute_rdp(
            q=self.sample_rate,
            noise_multiplier=self.noise_multiplier,
            steps=1,
            orders=self.orders,
        )

    def step(self, num_steps: int = 1) -> None:
        if num_steps < 0:
            raise ValueError("num_steps must be >= 0.")
        self.steps += int(num_steps)

    def get_total_rdp(self) -> np.ndarray:
        if self.steps == 0:
            return np.zeros(len(self.orders), dtype=np.float64)
        return self._rdp_per_step * float(self.steps)

    def estimate(self, delta: float | None = None) -> PrivacyEstimate:
        effective_delta = self.target_delta if delta is None else float(delta)
        total_rdp = self.get_total_rdp()
        if self.steps == 0 or self.sample_rate == 0.0:
            return PrivacyEstimate(
                epsilon=0.0,
                optimal_order=self.orders[0],
                total_rdp=total_rdp,
            )
        if self.noise_multiplier == 0.0:
            return PrivacyEstimate(
                epsilon=float("inf"),
                optimal_order=self.orders[0],
                total_rdp=total_rdp,
            )
        epsilon, optimal_order = rdp_to_epsilon(
            orders=self.orders,
            total_rdp=total_rdp,
            delta=effective_delta,
        )
        return PrivacyEstimate(
            epsilon=epsilon,
            optimal_order=optimal_order,
            total_rdp=total_rdp,
        )

    def get_epsilon(self, delta: float | None = None) -> float:
        return float(self.estimate(delta).epsilon)

    def get_optimal_order(self, delta: float | None = None) -> int:
        return int(self.estimate(delta).optimal_order)

    def summary(self) -> dict:
        estimate = self.estimate()
        return {
            "steps": self.steps,
            "noise_multiplier": self.noise_multiplier,
            "sample_rate": self.sample_rate,
            "target_delta": self.target_delta,
            "epsilon": estimate.epsilon,
            "optimal_order": estimate.optimal_order,
            "total_rdp": estimate.total_rdp.tolist(),
        }
