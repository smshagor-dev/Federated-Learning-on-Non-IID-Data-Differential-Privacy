import unittest

from fl_platform.privacy.compatibility import (
    ALGORITHMS,
    SAMPLE_LEVEL_DP_COMPATIBILITY,
    USER_LEVEL_DP_COMPATIBILITY,
    CompatibilityStatus,
    hybrid_status,
    is_usable,
    sample_level_status,
    user_level_status,
)


class CompatibilityMatrixCoverageTests(unittest.TestCase):
    def test_every_algorithm_has_a_sample_level_entry(self) -> None:
        for algorithm in ALGORITHMS:
            self.assertIn(algorithm, SAMPLE_LEVEL_DP_COMPATIBILITY)

    def test_every_algorithm_has_a_user_level_entry(self) -> None:
        for algorithm in ALGORITHMS:
            self.assertIn(algorithm, USER_LEVEL_DP_COMPATIBILITY)

    def test_every_entry_has_a_non_empty_reason(self) -> None:
        for entry in {
            **SAMPLE_LEVEL_DP_COMPATIBILITY,
            **USER_LEVEL_DP_COMPATIBILITY,
        }.values():
            self.assertTrue(entry.reason.strip())

    def test_unknown_algorithm_is_unsupported(self) -> None:
        status = sample_level_status("not-a-real-algorithm")
        self.assertEqual(status.status, CompatibilityStatus.UNSUPPORTED)


class SampleLevelCompatibilityTests(unittest.TestCase):
    def test_fedavg_and_fedprox_are_supported(self) -> None:
        self.assertEqual(
            sample_level_status("fedavg").status, CompatibilityStatus.SUPPORTED
        )
        self.assertEqual(
            sample_level_status("fedprox").status, CompatibilityStatus.SUPPORTED
        )

    def test_scaffold_and_fedsam_are_unsupported(self) -> None:
        self.assertEqual(
            sample_level_status("scaffold").status, CompatibilityStatus.UNSUPPORTED
        )
        self.assertEqual(
            sample_level_status("fedsam").status, CompatibilityStatus.UNSUPPORTED
        )

    def test_ditto_and_per_fedavg_are_deferred(self) -> None:
        self.assertEqual(
            sample_level_status("ditto").status, CompatibilityStatus.DEFERRED
        )
        self.assertEqual(
            sample_level_status("per_fedavg").status, CompatibilityStatus.DEFERRED
        )


class UserLevelCompatibilityTests(unittest.TestCase):
    def test_fedavg_is_supported(self) -> None:
        self.assertEqual(
            user_level_status("fedavg").status, CompatibilityStatus.SUPPORTED
        )

    def test_scaffold_is_unsupported_until_control_variate_privacy_is_proven(
        self,
    ) -> None:
        entry = user_level_status("scaffold")
        self.assertEqual(entry.status, CompatibilityStatus.UNSUPPORTED)
        self.assertIn("control-variate", entry.reason)

    def test_personalization_algorithms_are_experimental_not_unsupported(self) -> None:
        # Global-update path works; the personalization boundary is
        # untested — EXPERIMENTAL (usable), not UNSUPPORTED (blocked).
        self.assertEqual(
            user_level_status("ditto").status, CompatibilityStatus.EXPERIMENTAL
        )
        self.assertEqual(
            user_level_status("per_fedavg").status, CompatibilityStatus.EXPERIMENTAL
        )


class HybridCompatibilityTests(unittest.TestCase):
    def test_hybrid_takes_the_worse_of_the_two_statuses(self) -> None:
        # fedavg: sample=SUPPORTED, user=SUPPORTED -> SUPPORTED
        self.assertEqual(hybrid_status("fedavg").status, CompatibilityStatus.SUPPORTED)
        # scaffold: both sample-level and user-level are unsupported.
        self.assertEqual(
            hybrid_status("scaffold").status, CompatibilityStatus.UNSUPPORTED
        )
        # ditto: sample=DEFERRED, user=EXPERIMENTAL -> DEFERRED
        self.assertEqual(hybrid_status("ditto").status, CompatibilityStatus.DEFERRED)

    def test_unsupported_and_deferred_are_not_usable(self) -> None:
        self.assertFalse(is_usable(CompatibilityStatus.UNSUPPORTED))
        self.assertFalse(is_usable(CompatibilityStatus.DEFERRED))

    def test_supported_and_experimental_are_usable(self) -> None:
        self.assertTrue(is_usable(CompatibilityStatus.SUPPORTED))
        self.assertTrue(is_usable(CompatibilityStatus.EXPERIMENTAL))


if __name__ == "__main__":
    unittest.main()
