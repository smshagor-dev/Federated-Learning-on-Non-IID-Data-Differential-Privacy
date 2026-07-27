"""Golden parity tests for the privacy accountants.

Validates the Critical Privacy Rule's practical consequence — sample-
level and user-level accounting must never be produced by the same
accountant instance/class — and validates
:class:`fl_platform.privacy.accounting.UserLevelAccountant`'s reused
legacy math against Opacus's own RDP implementation (the trusted
reference) before trusting it for real accounting.
"""

from __future__ import annotations

import unittest

import numpy as np
from fl_platform.privacy.accounting import (
    AdaptiveClippingAccountant,
    SampleLevelAccountant,
    UserLevelAccountant,
    UserLevelAccountantState,
    opacus_capabilities,
)
from opacus.accountants import create_accountant
from opacus.accountants.analysis.rdp import compute_rdp

from federated.dp_accountant import MomentsAccountant


class UserLevelAccountantGoldenParityTests(unittest.TestCase):
    """Validates federated.dp_accountant.MomentsAccountant's assumptions
    (per module docstring's "do not copy without validating" requirement)
    before UserLevelAccountant relies on it."""

    def test_per_order_rdp_matches_opacus_at_shared_integer_orders(self) -> None:
        q, sigma = 0.1, 1.2
        for alpha in (2, 5, 10, 20, 32, 63):
            legacy = MomentsAccountant(
                noise_multiplier=sigma, sample_rate=q, orders=[alpha]
            )
            legacy_rdp = legacy._rdp_per_step[0]
            opacus_rdp = np.asarray(
                compute_rdp(q=q, noise_multiplier=sigma, steps=1, orders=[alpha])
            ).item()
            self.assertAlmostEqual(
                legacy_rdp,
                opacus_rdp,
                places=10,
                msg=f"per-order RDP mismatch at alpha={alpha}",
            )

    def test_epsilon_is_a_valid_conservative_upper_bound_vs_opacus(self) -> None:
        """The legacy accountant's integer-only order search is expected
        to report a *valid but less tight* epsilon than Opacus's
        fractional-order search for the identical mechanism — never a
        *lower* (unsound) epsilon."""
        q, sigma, steps, delta = 0.1, 1.2, 100, 1e-5
        user_accountant = UserLevelAccountant(
            noise_multiplier=sigma, sample_rate=q, target_delta=delta
        )
        user_accountant.step(steps)
        legacy_eps = user_accountant.get_epsilon()

        opacus_acc = create_accountant(mechanism="rdp")
        for _ in range(steps):
            opacus_acc.step(noise_multiplier=sigma, sample_rate=q)
        opacus_eps = opacus_acc.get_epsilon(delta=delta)

        self.assertGreaterEqual(
            legacy_eps,
            opacus_eps,
            "legacy accountant must never report a lower (unsound) epsilon than the "
            "trusted reference",
        )
        # Sanity bound: same mechanism, same parameters — conservatism
        # from a coarser order grid should be a modest gap, not orders
        # of magnitude off (which would indicate a real bug, not just
        # grid coarseness).
        self.assertLess(legacy_eps / opacus_eps, 1.5)

    def test_checkpoint_round_trip_preserves_epsilon(self) -> None:
        """Required recovery property (docs/coordinator-recovery.md):
        restoring from checkpointed state and resuming must match the
        uninterrupted accountant's epsilon exactly."""
        accountant = UserLevelAccountant(
            noise_multiplier=1.0, sample_rate=0.2, target_delta=1e-5
        )
        accountant.step(5)
        state = accountant.to_state()
        self.assertEqual(state, UserLevelAccountantState(1.0, 0.2, 1e-5, 5))

        restored = UserLevelAccountant.from_state(state)
        self.assertEqual(restored.get_epsilon(), accountant.get_epsilon())
        self.assertEqual(restored.steps, accountant.steps)

    def test_epsilon_grows_monotonically_with_steps(self) -> None:
        accountant = UserLevelAccountant(
            noise_multiplier=1.0, sample_rate=0.1, target_delta=1e-5
        )
        previous = accountant.get_epsilon()
        for _ in range(10):
            accountant.step(1)
            current = accountant.get_epsilon()
            self.assertGreaterEqual(current, previous)
            previous = current


class SampleLevelAccountantTests(unittest.TestCase):
    """SampleLevelAccountant must delegate to Opacus, not reimplement —
    this is enforced by construction (it holds an Opacus accountant
    instance), verified here by checking its output matches calling
    Opacus directly with identical steps."""

    def test_matches_direct_opacus_usage(self) -> None:
        noise_multiplier, sample_rate, delta = 1.1, 0.05, 1e-5
        sample_accountant = SampleLevelAccountant(mechanism="rdp")
        direct = create_accountant(mechanism="rdp")
        for _ in range(20):
            sample_accountant.step(
                noise_multiplier=noise_multiplier, sample_rate=sample_rate
            )
            direct.step(noise_multiplier=noise_multiplier, sample_rate=sample_rate)
        self.assertAlmostEqual(
            sample_accountant.get_epsilon(delta), direct.get_epsilon(delta=delta)
        )

    def test_rejects_unsupported_accountant(self) -> None:
        with self.assertRaises(ValueError):
            SampleLevelAccountant(mechanism="not-a-real-accountant")

    def test_supports_rdp_prv_gdp(self) -> None:
        for mechanism in ("rdp", "prv", "gdp"):
            SampleLevelAccountant(mechanism=mechanism)  # must not raise


class AccountantSeparationTests(unittest.TestCase):
    """Direct enforcement of the Critical Privacy Rule at the type level:
    sample-level and user-level accounting must never be produced by the
    same class, and their epsilons must never be summed anywhere in this
    module (no such function exists here — verified by its absence)."""

    def test_sample_and_user_level_accountants_are_distinct_types(self) -> None:
        self.assertIsNot(SampleLevelAccountant, UserLevelAccountant)
        self.assertFalse(issubclass(UserLevelAccountant, SampleLevelAccountant))
        self.assertFalse(issubclass(SampleLevelAccountant, UserLevelAccountant))

    def test_adaptive_clipping_accountant_is_also_distinct(self) -> None:
        self.assertIsNot(AdaptiveClippingAccountant, UserLevelAccountant)
        self.assertIsNot(AdaptiveClippingAccountant, SampleLevelAccountant)

    def test_adaptive_clipping_accounts_as_plain_gaussian_mechanism(self) -> None:
        # sample_rate=1.0 (every cohort member contributes one bit to the
        # count, no further subsampling) means RDP(alpha) = alpha / (2
        # sigma^2) exactly, the textbook Gaussian-mechanism formula.
        sigma = 2.0
        clipping_accountant = AdaptiveClippingAccountant(count_noise_multiplier=sigma)
        clipping_accountant.step(1)
        reference = MomentsAccountant(
            noise_multiplier=sigma, sample_rate=1.0, target_delta=1e-5
        )
        reference.step(1)
        self.assertAlmostEqual(
            clipping_accountant.get_epsilon(), reference.get_epsilon()
        )


class OpacusCapabilitiesTests(unittest.TestCase):
    """Regression coverage for worker privacy capability advertisement
    (docs/worker-privacy-capabilities.md): the coordinator's compatible-
    worker-only task assignment depends on this being a truthful probe,
    not a hardcoded value.
    """

    def test_reports_installed_and_a_real_version_string(self) -> None:
        # Opacus is a real dev dependency in this environment (used
        # throughout this module) — the probe must reflect that, not
        # hardcode True independent of what's actually importable.
        installed, version = opacus_capabilities()
        self.assertTrue(installed)
        self.assertNotEqual(version, "")

    def test_reports_not_installed_when_the_package_is_absent(self) -> None:
        import importlib.metadata
        from unittest import mock

        with mock.patch(
            "importlib.metadata.version",
            side_effect=importlib.metadata.PackageNotFoundError("opacus"),
        ):
            installed, version = opacus_capabilities()
        self.assertFalse(installed)
        self.assertEqual(version, "")


if __name__ == "__main__":
    unittest.main()
