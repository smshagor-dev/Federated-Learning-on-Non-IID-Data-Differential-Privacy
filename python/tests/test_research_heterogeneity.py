from __future__ import annotations

import unittest

from fl_platform.research.heterogeneity import compute_heterogeneity_vector


class HeterogeneityVectorTests(unittest.TestCase):
    def test_identical_balanced_clients_look_iid_in_label_and_quantity_metrics(self) -> None:
        vector = compute_heterogeneity_vector(
            {
                "client-0": {0: 5, 1: 5},
                "client-1": {0: 5, 1: 5},
            }
        )
        self.assertEqual(vector.client_count, 2)
        self.assertEqual(vector.total_samples, 20)
        self.assertEqual(vector.quantity_coefficient_of_variation, 0.0)
        self.assertAlmostEqual(vector.mean_normalized_label_entropy, 1.0)
        self.assertAlmostEqual(vector.mean_js_divergence_to_global, 0.0)
        self.assertAlmostEqual(vector.mean_class_coverage, 1.0)
        self.assertAlmostEqual(vector.mean_effective_label_count, 2.0)

    def test_label_skew_is_visible_even_when_client_sizes_match(self) -> None:
        vector = compute_heterogeneity_vector(
            {
                "client-0": {0: 10},
                "client-1": {1: 10},
            }
        )
        self.assertEqual(vector.quantity_coefficient_of_variation, 0.0)
        self.assertAlmostEqual(vector.mean_normalized_label_entropy, 0.0)
        self.assertGreater(vector.mean_js_divergence_to_global, 0.0)
        self.assertAlmostEqual(vector.mean_class_coverage, 0.5)
        self.assertAlmostEqual(vector.mean_effective_label_count, 1.0)

    def test_quantity_skew_is_separate_from_label_distribution_skew(self) -> None:
        vector = compute_heterogeneity_vector(
            {
                "client-0": {0: 5, 1: 5},
                "client-1": {0: 15, 1: 15},
            }
        )
        self.assertAlmostEqual(vector.mean_js_divergence_to_global, 0.0)
        self.assertAlmostEqual(vector.mean_normalized_label_entropy, 1.0)
        self.assertAlmostEqual(vector.quantity_coefficient_of_variation, 0.5)

    def test_fingerprint_is_independent_of_mapping_insertion_order(self) -> None:
        first = compute_heterogeneity_vector(
            {"b": {1: 7, 0: 3}, "a": {0: 8, 1: 2}}
        )
        second = compute_heterogeneity_vector(
            {"a": {1: 2, 0: 8}, "b": {0: 3, 1: 7}}
        )
        self.assertEqual(first.fingerprint_sha256, second.fingerprint_sha256)
        self.assertEqual(first, second)

    def test_empty_client_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "every client must have at least one sample"):
            compute_heterogeneity_vector({"client-0": {0: 1}, "client-1": {}})


if __name__ == "__main__":
    unittest.main()
