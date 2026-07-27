import unittest

import numpy as np
from fl_platform.privacy import (
    AdaptiveClipConfig,
    AdaptiveClipController,
    AdaptiveClippingConfig,
    PrivacyBudgetPolicy,
    PrivacyLedger,
    PrivacyMode,
    SampleLevelDPConfig,
    SampleLevelLedgerEntry,
    UserLevelDPConfig,
    build_privacy_config,
    validate_privacy_config,
)


class PrivacyConfigBuildTests(unittest.TestCase):
    def test_build_sample_level_config(self) -> None:
        config = build_privacy_config(
            {
                "mode": "sample_level_dp",
                "sample_level": {
                    "noise_multiplier": 1.2,
                    "max_grad_norm": 0.8,
                    "target_delta": 1e-5,
                },
            }
        )
        self.assertEqual(config.mode, PrivacyMode.SAMPLE_LEVEL_DP)
        assert config.sample_level is not None
        self.assertIsInstance(config.sample_level, SampleLevelDPConfig)
        self.assertEqual(config.sample_level.noise_multiplier, 1.2)
        self.assertIsNone(config.user_level)

    def test_build_user_level_config(self) -> None:
        config = build_privacy_config(
            {
                "mode": "user_level_dp",
                "user_level": {
                    "noise_multiplier": 0.9,
                    "initial_clipping_bound": 1.5,
                    "weighting_strategy": "uniform",
                },
            }
        )
        self.assertEqual(config.mode, PrivacyMode.USER_LEVEL_DP)
        assert config.user_level is not None
        self.assertIsInstance(config.user_level, UserLevelDPConfig)
        self.assertEqual(config.user_level.initial_clipping_bound, 1.5)

    def test_build_hybrid_config_has_both(self) -> None:
        config = build_privacy_config(
            {
                "mode": "hybrid_dp",
                "sample_level": {"noise_multiplier": 1.1, "max_grad_norm": 1.0},
                "user_level": {"noise_multiplier": 0.9, "initial_clipping_bound": 1.2},
            }
        )
        result = validate_privacy_config(config)
        self.assertTrue(result.valid, result.errors)
        self.assertIsNotNone(config.sample_level)
        self.assertIsNotNone(config.user_level)

    def test_none_mode_requires_no_sub_config(self) -> None:
        config = build_privacy_config({"mode": "none"})
        result = validate_privacy_config(config)
        self.assertTrue(result.valid)
        self.assertIn("privacy disabled", result.warnings)


class PrivacyConfigValidationTests(unittest.TestCase):
    def test_invalid_user_level_config_is_rejected(self) -> None:
        config = build_privacy_config(
            {
                "mode": "user_level_dp",
                "user_level": {"noise_multiplier": 0.0, "initial_clipping_bound": 1.0},
            }
        )
        result = validate_privacy_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(any("noise_multiplier" in e for e in result.errors))

    def test_unrestricted_sample_count_weighting_rejected_for_user_level(self) -> None:
        config = build_privacy_config(
            {
                "mode": "user_level_dp",
                "user_level": {
                    "noise_multiplier": 1.0,
                    "initial_clipping_bound": 1.0,
                    "weighting_strategy": "sample_count",
                },
            }
        )
        result = validate_privacy_config(config)
        self.assertFalse(result.valid)
        self.assertTrue(any("privacy-safe" in e for e in result.errors))

    def test_privacy_safe_weighting_strategies_accepted(self) -> None:
        for strategy in ("uniform", "capped_sample_count", "normalized_bounded"):
            config = build_privacy_config(
                {
                    "mode": "user_level_dp",
                    "user_level": {
                        "noise_multiplier": 1.0,
                        "initial_clipping_bound": 1.0,
                        "weighting_strategy": strategy,
                    },
                }
            )
            result = validate_privacy_config(config)
            self.assertTrue(result.valid, f"{strategy}: {result.errors}")

    def test_unsupported_accountant_rejected(self) -> None:
        config = build_privacy_config(
            {
                "mode": "sample_level_dp",
                "sample_level": {
                    "noise_multiplier": 1.0,
                    "max_grad_norm": 1.0,
                    "accountant": "not-a-real-accountant",
                },
            }
        )
        result = validate_privacy_config(config)
        self.assertFalse(result.valid)

    def test_invalid_adaptive_clipping_bounds_rejected(self) -> None:
        config = build_privacy_config({"mode": "none"})
        config.adaptive_clipping = AdaptiveClippingConfig(
            enabled=True, min_clip=10.0, initial_clip=1.0, max_clip=100.0
        )
        result = validate_privacy_config(config)
        self.assertFalse(result.valid)


class AdaptiveClipControllerTests(unittest.TestCase):
    def test_clip_moves_toward_target_quantile(self) -> None:
        controller = AdaptiveClipController(
            AdaptiveClipConfig(
                initial_clip=1.0,
                target_quantile=0.5,
                learning_rate=0.2,
                count_noise_multiplier=0.0,  # deterministic for this test
            ),
            rng=np.random.default_rng(0),
        )
        # 80% over threshold (> target 50%) -> raise the bound.
        higher = controller.step(over_threshold_count=8, cohort_size=10)
        # 20% over threshold (< target 50%) -> lower the bound.
        lower = controller.step(over_threshold_count=2, cohort_size=10)
        self.assertGreater(higher.clip_value, 1.0)
        self.assertLess(lower.clip_value, higher.clip_value)

    def test_clip_stays_within_bounds(self) -> None:
        controller = AdaptiveClipController(
            AdaptiveClipConfig(
                initial_clip=1.0,
                target_quantile=0.5,
                learning_rate=5.0,
                min_clip=0.5,
                max_clip=2.0,
                count_noise_multiplier=0.0,
            ),
            rng=np.random.default_rng(0),
        )
        for _ in range(50):
            result = controller.step(over_threshold_count=10, cohort_size=10)
            self.assertLessEqual(result.clip_value, 2.0)
            self.assertGreaterEqual(result.clip_value, 0.5)

    def test_never_exposes_raw_count_only_noisy_fraction(self) -> None:
        controller = AdaptiveClipController(
            AdaptiveClipConfig(count_noise_multiplier=1.0), rng=np.random.default_rng(0)
        )
        result = controller.step(over_threshold_count=5, cohort_size=10)
        # The only fraction-like field on the result is explicitly named
        # "noisy_..." — this test documents that contract.
        self.assertTrue(hasattr(result, "noisy_over_threshold_fraction"))
        self.assertFalse(hasattr(result, "over_threshold_count"))
        self.assertFalse(hasattr(result, "raw_fraction"))

    def test_epsilon_accrues_and_is_separate_from_clip_value(self) -> None:
        controller = AdaptiveClipController(
            AdaptiveClipConfig(count_noise_multiplier=2.0), rng=np.random.default_rng(0)
        )
        self.assertEqual(controller.epsilon, 0.0)
        result = controller.step(over_threshold_count=3, cohort_size=10)
        self.assertGreater(result.epsilon, 0.0)
        self.assertEqual(controller.steps, 1)

    def test_rejects_invalid_bounds_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            AdaptiveClipController(AdaptiveClipConfig(initial_clip=0.0))
        with self.assertRaises(ValueError):
            AdaptiveClipController(
                AdaptiveClipConfig(initial_clip=1.0, min_clip=2.0, max_clip=3.0)
            )

    def test_rejects_invalid_step_arguments(self) -> None:
        controller = AdaptiveClipController(AdaptiveClipConfig())
        with self.assertRaises(ValueError):
            controller.step(over_threshold_count=5, cohort_size=0)
        with self.assertRaises(ValueError):
            controller.step(over_threshold_count=11, cohort_size=10)


class PrivacyLedgerTests(unittest.TestCase):
    def test_append_and_project_sample_level(self) -> None:
        ledger = PrivacyLedger(run_id="run-7")
        ledger.append_sample_level(
            SampleLevelLedgerEntry(
                run_id="run-7",
                round_id=1,
                client_id="client-a",
                epsilon=0.5,
                delta=1e-5,
                noise_multiplier=0.9,
                sample_rate=0.1,
                steps=10,
                accountant="rdp",
            )
        )
        ledger.append_sample_level(
            SampleLevelLedgerEntry(
                run_id="run-7",
                round_id=2,
                client_id="client-a",
                epsilon=0.8,
                delta=1e-5,
                noise_multiplier=0.9,
                sample_rate=0.1,
                steps=20,
                accountant="rdp",
            )
        )
        projection = ledger.project(sample_epsilon_budget=2.0)
        assert projection.sample_level is not None
        self.assertAlmostEqual(projection.sample_level.current_epsilon, 0.8)
        self.assertAlmostEqual(projection.sample_level.projected_next_epsilon, 1.1)
        self.assertAlmostEqual(projection.sample_level.budget_remaining, 1.2)
        # No such thing as a combined/user-level projection on a
        # worker-side (sample-level-only) ledger.
        self.assertIsNone(projection.user_level)
        self.assertIsNone(projection.clipping)

    def test_rejects_entry_for_a_different_run(self) -> None:
        ledger = PrivacyLedger(run_id="run-7")
        with self.assertRaises(ValueError):
            ledger.append_sample_level(
                SampleLevelLedgerEntry(
                    run_id="run-other",
                    round_id=1,
                    client_id="client-a",
                    epsilon=0.1,
                    delta=1e-5,
                    noise_multiplier=1.0,
                    sample_rate=0.1,
                    steps=1,
                    accountant="rdp",
                )
            )

    def test_empty_ledger_projects_nothing(self) -> None:
        ledger = PrivacyLedger(run_id="run-7")
        projection = ledger.project()
        self.assertIsNone(projection.sample_level)


class PrivacyBudgetPolicyTests(unittest.TestCase):
    def test_all_four_policies_are_distinct_values(self) -> None:
        values = {policy.value for policy in PrivacyBudgetPolicy}
        self.assertEqual(
            values,
            {
                "warn_only",
                "stop_before_exceeding",
                "stop_after_current_round",
                "fail_run",
            },
        )


if __name__ == "__main__":
    unittest.main()
