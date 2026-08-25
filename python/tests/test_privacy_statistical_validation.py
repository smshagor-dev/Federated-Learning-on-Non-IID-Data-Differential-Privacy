"""Statistical validation of the privacy accounting math across a range
of parameters — not just the single golden-parity point already checked
in test_privacy_accounting.py. This is the Privacy Engineering phase's
explicit "statistical validation" deliverable: confirms the *shape* of
the epsilon/noise relationships the mathematics predicts actually holds
in the real (Opacus-backed and legacy-accountant-backed) implementations,
across many parameter combinations — not just that one hand-picked case
happens to match.
"""

from __future__ import annotations

import unittest

import numpy as np
from opacus.accountants import create_accountant

from fl_platform.privacy.accounting import UserLevelAccountant
from fl_platform.privacy.adaptive_clipping import (
    AdaptiveClipConfig,
    AdaptiveClipController,
)


class NoiseMultiplierEpsilonRelationshipTests(unittest.TestCase):
    """More noise must always mean less privacy loss (lower epsilon) for
    a fixed number of steps and sample rate — this is the entire point
    of a noise_multiplier parameter, and both accountants must respect
    it monotonically, not just "on average."
    """

    def test_sample_level_epsilon_decreases_with_noise_multiplier(self) -> None:
        sample_rate = 0.1
        steps = 50
        noise_multipliers = [0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
        epsilons = []
        for sigma in noise_multipliers:
            accountant = create_accountant(mechanism="rdp")
            for _ in range(steps):
                accountant.step(noise_multiplier=sigma, sample_rate=sample_rate)
            epsilons.append(accountant.get_epsilon(delta=1e-5))
        for earlier, later in zip(epsilons, epsilons[1:], strict=False):
            self.assertGreater(
                earlier, later, f"epsilon must decrease as sigma grows: {epsilons}"
            )

    def test_user_level_epsilon_strictly_decreases_with_noise_multiplier(self) -> None:
        sample_rate = 0.2
        steps = 20
        noise_multipliers = [0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
        epsilons = []
        for sigma in noise_multipliers:
            accountant = UserLevelAccountant(
                noise_multiplier=sigma, sample_rate=sample_rate, target_delta=1e-5
            )
            accountant.step(steps)
            epsilons.append(accountant.get_epsilon())
        for earlier, later in zip(epsilons, epsilons[1:], strict=False):
            self.assertGreater(
                earlier, later, f"epsilon must decrease as sigma grows: {epsilons}"
            )


class SampleRateEpsilonRelationshipTests(unittest.TestCase):
    """A higher sample rate (weaker subsampling amplification) must
    always mean higher epsilon for a fixed noise_multiplier and step
    count — subsampling is what buys privacy amplification, so less of
    it must cost more.
    """

    def test_user_level_epsilon_strictly_increases_with_sample_rate(self) -> None:
        noise_multiplier = 1.0
        steps = 20
        sample_rates = [0.01, 0.05, 0.1, 0.3, 0.5, 1.0]
        epsilons = []
        for rate in sample_rates:
            accountant = UserLevelAccountant(
                noise_multiplier=noise_multiplier, sample_rate=rate, target_delta=1e-5
            )
            accountant.step(steps)
            epsilons.append(accountant.get_epsilon())
        for earlier, later in zip(epsilons, epsilons[1:], strict=False):
            self.assertLess(
                earlier, later, f"epsilon must increase with sample_rate: {epsilons}"
            )


class StepCountEpsilonRelationshipTests(unittest.TestCase):
    """More composed steps must always cost more epsilon (composition
    never decreases privacy loss) — checked across several
    (noise_multiplier, sample_rate) combinations, not just one.
    """

    def test_epsilon_is_nondecreasing_in_steps_across_many_configs(self) -> None:
        configs = [
            (0.6, 0.05),
            (1.0, 0.1),
            (1.5, 0.3),
            (2.0, 0.5),
        ]
        for noise_multiplier, sample_rate in configs:
            accountant = UserLevelAccountant(
                noise_multiplier=noise_multiplier,
                sample_rate=sample_rate,
                target_delta=1e-5,
            )
            previous = accountant.get_epsilon()
            for _ in range(30):
                accountant.step(1)
                current = accountant.get_epsilon()
                self.assertGreaterEqual(
                    current,
                    previous,
                    f"epsilon decreased for config (sigma={noise_multiplier}, "
                    f"q={sample_rate}) — composition must never reduce privacy loss",
                )
                previous = current


class AdaptiveClipConvergenceTests(unittest.TestCase):
    """The adaptive clip bound must actually converge toward a value
    that makes the (noised) over-threshold fraction track
    target_quantile — not just move in the right direction for one
    step (already checked in test_privacy_foundations.py), but settle
    into a stable regime over many rounds under a realistic client-norm
    distribution.
    """

    def test_clip_bound_converges_near_the_target_quantile_norm(self) -> None:
        rng = np.random.default_rng(42)
        # Client norms drawn from a fixed, known distribution: the true
        # target_quantile=0.5 (median) norm is exactly 1.0 by
        # construction (a standard normal's median magnitude is not
        # exactly 1.0, so use a distribution engineered to have a known
        # median instead: norms are |N(0,1)| + 0.5, whose median is
        # ~1.174 — computed directly below rather than asserted as a
        # magic number).
        cohort_size = 200
        true_norms = np.abs(rng.normal(0.0, 1.0, size=cohort_size)) + 0.5
        target_median = float(np.median(true_norms))

        config = AdaptiveClipConfig(
            initial_clip=0.1,  # deliberately far from the true median
            target_quantile=0.5,
            learning_rate=0.2,
            min_clip=1e-3,
            max_clip=1e3,
            count_noise_multiplier=0.05,  # small noise: converges cleanly here
            target_delta=1e-5,
        )
        controller = AdaptiveClipController(config, rng=rng)

        clip_history = []
        for _ in range(150):
            # Fresh draw each round (same distribution, i.i.d. across
            # rounds) — this is what makes "converges to the
            # distribution's median" a meaningful, checkable claim.
            round_norms = np.abs(rng.normal(0.0, 1.0, size=cohort_size)) + 0.5
            over_threshold = int(np.sum(round_norms > controller.clip_value))
            controller.step(
                over_threshold_count=over_threshold, cohort_size=cohort_size
            )
            clip_history.append(controller.clip_value)

        # Average over the last 30 rounds (post-convergence) should be
        # close to the true median norm — not exact (noise + a
        # discrete client-count query keeps it a moving target), but
        # within a generous, pre-registered tolerance.
        converged_clip = float(np.mean(clip_history[-30:]))
        self.assertLess(
            abs(converged_clip - target_median) / target_median,
            0.25,
            f"converged clip {converged_clip:.3f} is not within 25% of the true "
            f"median norm {target_median:.3f} — adaptive clipping did not converge",
        )


if __name__ == "__main__":
    unittest.main()
