from __future__ import annotations

import math
import unittest

from fl_platform.research.statistics import (
    compare_paired_metrics,
    holm_adjust,
    summarize_metric,
)


class MetricSummaryTests(unittest.TestCase):
    def test_summary_reports_sample_variation_and_deterministic_ci(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        first = summarize_metric(values, bootstrap_samples=1000, seed=17)
        second = summarize_metric(values, bootstrap_samples=1000, seed=17)
        self.assertEqual(first, second)
        self.assertEqual(first.n, 5)
        self.assertAlmostEqual(first.mean, 3.0)
        self.assertAlmostEqual(first.sample_std, math.sqrt(2.5))
        self.assertLess(first.ci_low, first.mean)
        self.assertGreater(first.ci_high, first.mean)

    def test_publication_default_rejects_too_few_replicates(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 5"):
            summarize_metric([1.0, 2.0, 3.0, 4.0])


class PairedComparisonTests(unittest.TestCase):
    def test_matched_seed_comparison_reports_effect_and_exact_randomization(self) -> None:
        baseline = {1: 0.70, 2: 0.71, 3: 0.69, 4: 0.72, 5: 0.70}
        candidate = {1: 0.71, 2: 0.73, 3: 0.72, 4: 0.76, 5: 0.75}
        result = compare_paired_metrics(
            baseline,
            candidate,
            baseline_name="fedavg",
            candidate_name="candidate",
            bootstrap_samples=1000,
            seed=23,
        )
        self.assertEqual(result.n, 5)
        self.assertGreater(result.mean_difference, 0.0)
        self.assertGreater(result.cohen_dz, 0.0)
        self.assertEqual(result.win_rate, 1.0)
        self.assertEqual(result.p_value_method, "exact_paired_sign_flip")
        self.assertGreaterEqual(result.p_value, 0.0)
        self.assertLessEqual(result.p_value, 1.0)

    def test_identical_constant_results_have_zero_effect(self) -> None:
        baseline = {seed: 0.5 for seed in range(5)}
        result = compare_paired_metrics(
            baseline,
            dict(baseline),
            baseline_name="a",
            candidate_name="b",
            bootstrap_samples=500,
        )
        self.assertEqual(result.mean_difference, 0.0)
        self.assertEqual(result.cohen_dz, 0.0)
        self.assertEqual(result.p_value, 1.0)

    def test_mismatched_seed_sets_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identical seed sets"):
            compare_paired_metrics(
                {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0},
                {1: 1.1, 2: 1.1, 3: 1.1, 4: 1.1, 6: 1.1},
                baseline_name="a",
                candidate_name="b",
                bootstrap_samples=500,
            )


class MultipleComparisonTests(unittest.TestCase):
    def test_holm_adjustment_is_monotone_in_ordered_hypotheses(self) -> None:
        adjusted = holm_adjust({"a": 0.01, "b": 0.04, "c": 0.03})
        self.assertAlmostEqual(adjusted["a"], 0.03)
        self.assertAlmostEqual(adjusted["b"], 0.06)
        self.assertAlmostEqual(adjusted["c"], 0.06)


if __name__ == "__main__":
    unittest.main()
