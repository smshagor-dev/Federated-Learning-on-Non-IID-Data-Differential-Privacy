import unittest

from fl_platform.privacy import (
    AdaptiveClipConfig,
    AdaptiveClipController,
    PrivacyLedger,
    PrivacyLedgerEntry,
    PrivacyMode,
    SampleLevelDPConfig,
    UserLevelDPConfig,
    build_privacy_config,
    validate_privacy_config,
)


class PrivacyFoundationTests(unittest.TestCase):
    def test_build_sample_level_config(self) -> None:
        config = build_privacy_config(
            {
                "mode": "sample_level_dp",
                "noise_multiplier": 1.2,
                "max_grad_norm": 0.8,
                "target_delta": 1e-5,
            }
        )
        self.assertIsInstance(config, SampleLevelDPConfig)
        self.assertEqual(config.mode, PrivacyMode.SAMPLE_LEVEL_DP)

    def test_build_user_level_config(self) -> None:
        config = build_privacy_config(
            {
                "mode": "user_level_dp",
                "noise_multiplier": 0.9,
                "max_update_norm": 1.5,
                "client_sampling_rate": 0.25,
            }
        )
        self.assertIsInstance(config, UserLevelDPConfig)
        self.assertEqual(config.mode, PrivacyMode.USER_LEVEL_DP)

    def test_validate_hybrid_config(self) -> None:
        config = build_privacy_config(
            {
                "mode": "hybrid_dp",
                "sample_level": {"noise_multiplier": 1.1, "max_grad_norm": 1.0},
                "user_level": {
                    "noise_multiplier": 0.9,
                    "max_update_norm": 1.2,
                    "client_sampling_rate": 0.2,
                },
            }
        )
        result = validate_privacy_config(config)
        self.assertTrue(result.valid)

    def test_adaptive_clip_controller_updates(self) -> None:
        controller = AdaptiveClipController(
            AdaptiveClipConfig(initial_clip=1.0, target_quantile=0.5, learning_rate=0.2)
        )
        higher = controller.step(0.8)
        lower = controller.step(0.2)
        self.assertLess(higher, 1.0)
        self.assertGreater(lower, higher)

    def test_privacy_ledger_projection(self) -> None:
        ledger = PrivacyLedger(run_id="run-7")
        ledger.append(
            PrivacyLedgerEntry(
                round_id=1,
                mode=PrivacyMode.USER_LEVEL_DP,
                epsilon=0.5,
                delta=1e-5,
                noise_multiplier=0.9,
                clipping_bound=1.0,
            )
        )
        ledger.append(
            PrivacyLedgerEntry(
                round_id=2,
                mode=PrivacyMode.USER_LEVEL_DP,
                epsilon=0.8,
                delta=1e-5,
                noise_multiplier=0.9,
                clipping_bound=1.0,
            )
        )
        projection = ledger.project_next(epsilon_budget=2.0)
        self.assertAlmostEqual(projection.current_epsilon, 0.8)
        self.assertAlmostEqual(projection.projected_next_epsilon, 1.1)
        self.assertAlmostEqual(projection.budget_remaining, 1.2)

    def test_invalid_user_level_config_is_rejected(self) -> None:
        result = validate_privacy_config(
            UserLevelDPConfig(
                noise_multiplier=1.0,
                max_update_norm=1.0,
                client_sampling_rate=0.0,
            )
        )
        self.assertFalse(result.valid)


if __name__ == "__main__":
    unittest.main()
