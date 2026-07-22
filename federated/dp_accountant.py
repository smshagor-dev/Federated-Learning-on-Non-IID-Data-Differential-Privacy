"""Privacy accounting via Renyi Differential Privacy (the "moments
accountant" of Abadi et al., 2016, in its RDP formulation, Mironov 2017).

Model of computation
--------------------
Each communication round, a fraction q = sample_rate of the N clients is
sampled uniformly at random. Every sampled client clips its model update to
L2 norm C and adds Gaussian noise N(0, (sigma*C)^2 I). One round is therefore
one step of the subsampled Gaussian mechanism at *client level*
(record = one client's entire dataset).

For integer orders alpha >= 2 the RDP of one subsampled-Gaussian step is
(Mironov et al., 2019, upper bound):

    eps_RDP(alpha) = 1/(alpha-1) * log( sum_{k=0}^{alpha}
        C(alpha, k) (1-q)^(alpha-k) q^k * exp(k(k-1) / (2 sigma^2)) )

RDP composes additively over rounds, and converts to approximate DP via

    eps(delta) = min_alpha [ T * eps_RDP(alpha) + log(1/delta)/(alpha-1) ]
"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional

import numpy as np
from scipy.special import gammaln, logsumexp

DEFAULT_ORDERS: List[int] = list(range(2, 65)) + [80, 96, 128, 256, 512]


class MomentsAccountant:
    """Tracks cumulative (epsilon, delta) over federated rounds."""

    def __init__(
        self,
        noise_multiplier: float,
        sample_rate: float,
        target_delta: float = 1e-5,
        orders: Optional[Iterable[int]] = None,
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
        self.orders = sorted(set(int(a) for a in (orders or DEFAULT_ORDERS)))
        if min(self.orders) < 2:
            raise ValueError("All RDP orders must be integers >= 2.")

        self.steps = 0
        # Per-order RDP of a single step; precomputed once.
        self._rdp_per_step = np.array(
            [self._compute_rdp_single_step(a) for a in self.orders]
        )

    # ------------------------------------------------------------------ #
    def _compute_rdp_single_step(self, alpha: int) -> float:
        """RDP of one subsampled Gaussian step at integer order alpha."""
        q = self.sample_rate
        sigma = self.noise_multiplier

        if q == 0.0:
            return 0.0
        if sigma == 0.0:
            return float("inf")
        if q == 1.0:
            # Plain (non-subsampled) Gaussian mechanism.
            return alpha / (2.0 * sigma * sigma)

        # log of sum_{k=0}^{alpha} C(alpha,k) (1-q)^{alpha-k} q^k
        #                         * exp(k(k-1)/(2 sigma^2))
        log_terms = []
        for k in range(alpha + 1):
            log_binom = (
                gammaln(alpha + 1) - gammaln(k + 1) - gammaln(alpha - k + 1)
            )
            log_term = (
                log_binom
                + (alpha - k) * math.log1p(-q)
                + (k * math.log(q) if k > 0 else 0.0)
                + (k * (k - 1)) / (2.0 * sigma * sigma)
            )
            log_terms.append(log_term)

        return float(logsumexp(log_terms) / (alpha - 1))

    # ------------------------------------------------------------------ #
    def step(self, num_steps: int = 1) -> None:
        """Register ``num_steps`` completed communication rounds."""
        if num_steps < 0:
            raise ValueError("num_steps must be >= 0.")
        self.steps += int(num_steps)

    def get_epsilon(self, delta: Optional[float] = None) -> float:
        """Best epsilon over all tracked orders for the given delta."""
        delta = self.target_delta if delta is None else float(delta)
        if self.steps == 0:
            return 0.0
        if np.isinf(self._rdp_per_step).all():
            return float("inf")

        total_rdp = self.steps * self._rdp_per_step
        eps_candidates = [
            rdp + math.log(1.0 / delta) / (alpha - 1)
            for alpha, rdp in zip(self.orders, total_rdp)
            if np.isfinite(rdp)
        ]
        return float(min(eps_candidates)) if eps_candidates else float("inf")

    def get_optimal_order(self, delta: Optional[float] = None) -> int:
        """The Renyi order achieving the reported epsilon (diagnostic)."""
        delta = self.target_delta if delta is None else float(delta)
        steps = max(1, self.steps)
        best_alpha, best_eps = self.orders[0], float("inf")
        for alpha, rdp in zip(self.orders, steps * self._rdp_per_step):
            if not np.isfinite(rdp):
                continue
            eps = rdp + math.log(1.0 / delta) / (alpha - 1)
            if eps < best_eps:
                best_eps, best_alpha = eps, alpha
        return best_alpha

    def summary(self) -> dict:
        """Snapshot of the current privacy expenditure."""
        return {
            "steps": self.steps,
            "noise_multiplier": self.noise_multiplier,
            "sample_rate": self.sample_rate,
            "target_delta": self.target_delta,
            "epsilon": self.get_epsilon(),
            "optimal_order": self.get_optimal_order(),
        }
