"""Tests for fl_platform.secure_aggregation.user_level_clipping --
Secure User-Level Differential Privacy Runtime slice, Work Areas F/G/H.
See docs/secure-user-level-dp-semantics.md.
"""

from __future__ import annotations

import math
import unittest

import torch
from fl_platform.secure_aggregation.fixed_point_encoding import (
    FixedPointEncodingProfile,
)
from fl_platform.secure_aggregation.user_level_clipping import (
    UserLevelClippingError,
    clip_delta_to_l2_norm,
    compute_effective_sensitivity,
    compute_global_l2_norm,
    compute_quantization_margin,
    compute_total_element_count,
)


class GlobalL2NormTests(unittest.TestCase):
    def test_single_tensor_norm(self) -> None:
        delta = {"w": torch.tensor([3.0, 4.0])}
        self.assertAlmostEqual(compute_global_l2_norm(delta), 5.0, places=9)

    def test_multi_tensor_norm_combines_across_tensors(self) -> None:
        # 3-4-0 right triangle extended into a second tensor contributing
        # the remaining component of a 5-12-13 triple: sqrt(9+16+144)=13.
        delta = {"a": torch.tensor([3.0, 4.0]), "b": torch.tensor([12.0])}
        self.assertAlmostEqual(compute_global_l2_norm(delta), 13.0, places=9)

    def test_canonical_tensor_order_does_not_affect_norm(self) -> None:
        delta_1 = {"a": torch.tensor([3.0]), "z": torch.tensor([4.0])}
        delta_2 = {"z": torch.tensor([4.0]), "a": torch.tensor([3.0])}
        self.assertEqual(
            compute_global_l2_norm(delta_1), compute_global_l2_norm(delta_2)
        )

    def test_zero_delta_has_zero_norm(self) -> None:
        delta = {"w": torch.zeros(4)}
        self.assertEqual(compute_global_l2_norm(delta), 0.0)

    def test_empty_delta_rejected(self) -> None:
        with self.assertRaises(UserLevelClippingError):
            compute_global_l2_norm({})

    def test_nan_rejected(self) -> None:
        delta = {"w": torch.tensor([1.0, float("nan")])}
        with self.assertRaises(UserLevelClippingError):
            compute_global_l2_norm(delta)

    def test_infinity_rejected(self) -> None:
        delta = {"w": torch.tensor([1.0, float("inf")])}
        with self.assertRaises(UserLevelClippingError):
            compute_global_l2_norm(delta)

    def test_negative_infinity_rejected(self) -> None:
        delta = {"w": torch.tensor([float("-inf")])}
        with self.assertRaises(UserLevelClippingError):
            compute_global_l2_norm(delta)


class ClipDeltaToL2NormTests(unittest.TestCase):
    def test_oversized_update_is_scaled_down_to_exactly_the_bound(self) -> None:
        delta = {"w": torch.tensor([3.0, 4.0])}  # norm 5.0
        outcome = clip_delta_to_l2_norm(delta, clip_norm=2.5)
        self.assertAlmostEqual(outcome.clipping_factor, 0.5, places=9)
        self.assertAlmostEqual(
            compute_global_l2_norm(outcome.clipped_delta), 2.5, places=6
        )

    def test_exact_bound_update_is_unchanged(self) -> None:
        delta = {"w": torch.tensor([3.0, 4.0])}  # norm 5.0
        outcome = clip_delta_to_l2_norm(delta, clip_norm=5.0)
        self.assertAlmostEqual(outcome.clipping_factor, 1.0, places=9)
        self.assertTrue(torch.allclose(outcome.clipped_delta["w"], delta["w"]))

    def test_undersized_update_is_unchanged(self) -> None:
        delta = {"w": torch.tensor([1.0, 0.0])}  # norm 1.0
        outcome = clip_delta_to_l2_norm(delta, clip_norm=10.0)
        self.assertEqual(outcome.clipping_factor, 1.0)
        self.assertTrue(torch.equal(outcome.clipped_delta["w"], delta["w"]))

    def test_zero_norm_update_is_a_safe_no_op(self) -> None:
        delta = {"w": torch.zeros(3)}
        outcome = clip_delta_to_l2_norm(delta, clip_norm=1.0)
        self.assertEqual(outcome.clipping_factor, 1.0)
        self.assertTrue(torch.equal(outcome.clipped_delta["w"], delta["w"]))

    def test_very_small_norm_does_not_divide_by_zero(self) -> None:
        delta = {"w": torch.tensor([1e-20])}
        outcome = clip_delta_to_l2_norm(delta, clip_norm=1.0, numerical_floor=1e-12)
        self.assertTrue(math.isfinite(outcome.clipping_factor))

    def test_non_finite_delta_rejected_before_clipping(self) -> None:
        delta = {"w": torch.tensor([float("nan")])}
        with self.assertRaises(UserLevelClippingError):
            clip_delta_to_l2_norm(delta, clip_norm=1.0)

    def test_non_positive_clip_norm_rejected(self) -> None:
        delta = {"w": torch.tensor([1.0])}
        with self.assertRaises(UserLevelClippingError):
            clip_delta_to_l2_norm(delta, clip_norm=0.0)
        with self.assertRaises(UserLevelClippingError):
            clip_delta_to_l2_norm(delta, clip_norm=-1.0)

    def test_non_finite_clip_norm_rejected(self) -> None:
        delta = {"w": torch.tensor([1.0])}
        with self.assertRaises(UserLevelClippingError):
            clip_delta_to_l2_norm(delta, clip_norm=float("nan"))

    def test_clipping_is_deterministic(self) -> None:
        delta = {"w": torch.tensor([3.0, 4.0])}
        outcome_a = clip_delta_to_l2_norm(delta, clip_norm=2.5)
        outcome_b = clip_delta_to_l2_norm(delta, clip_norm=2.5)
        self.assertEqual(outcome_a.clipping_factor, outcome_b.clipping_factor)
        self.assertTrue(
            torch.equal(outcome_a.clipped_delta["w"], outcome_b.clipped_delta["w"])
        )

    def test_clipping_applies_uniformly_across_tensors(self) -> None:
        delta = {"a": torch.tensor([3.0]), "b": torch.tensor([4.0])}  # norm 5.0
        outcome = clip_delta_to_l2_norm(delta, clip_norm=2.5)
        self.assertAlmostEqual(outcome.clipped_delta["a"].item(), 1.5, places=6)
        self.assertAlmostEqual(outcome.clipped_delta["b"].item(), 2.0, places=6)


class QuantizationMarginTests(unittest.TestCase):
    def test_matches_hand_derived_value(self) -> None:
        profile = FixedPointEncodingProfile()
        margin = compute_quantization_margin(1000, profile)
        # sqrt(1000) * (0.5 / 1048576.0) -- must byte-for-byte match the
        # C++ mirror's own golden value (cross-checked in
        # fixed_point_encoding_test.cpp).
        self.assertAlmostEqual(margin, 1.5078914929239174e-05, places=15)

    def test_matches_cpp_golden_value_exactly(self) -> None:
        # Cross-language fixture (Work Area AC): this exact value is
        # independently hand-derived in
        # cpp/coordinator/tests/fixed_point_encoding_test.cpp -- both
        # sides must agree without either reading the other's source.
        profile = FixedPointEncodingProfile()
        margin = compute_quantization_margin(1000, profile)
        expected = math.sqrt(1000) * (0.5 / 1048576.0)
        self.assertEqual(margin, expected)

    def test_zero_elements_yields_zero_margin(self) -> None:
        profile = FixedPointEncodingProfile()
        self.assertEqual(compute_quantization_margin(0, profile), 0.0)

    def test_more_elements_yields_larger_margin(self) -> None:
        profile = FixedPointEncodingProfile()
        small = compute_quantization_margin(1000, profile)
        large = compute_quantization_margin(1_000_000, profile)
        self.assertGreater(large, small)

    def test_non_positive_scale_factor_yields_infinity(self) -> None:
        profile = FixedPointEncodingProfile(scale_factor=0.0)
        self.assertTrue(math.isinf(compute_quantization_margin(1000, profile)))

    def test_effective_sensitivity_is_the_sum(self) -> None:
        self.assertEqual(compute_effective_sensitivity(2.5, 0.001), 2.501)

    def test_effective_sensitivity_exceeds_clip_norm_alone(self) -> None:
        profile = FixedPointEncodingProfile()
        margin = compute_quantization_margin(500, profile)
        sensitivity = compute_effective_sensitivity(2.5, margin)
        self.assertGreater(sensitivity, 2.5)


class TotalElementCountTests(unittest.TestCase):
    def test_sums_across_tensors(self) -> None:
        delta = {"a": torch.zeros(3), "b": torch.zeros(2, 2)}
        self.assertEqual(compute_total_element_count(delta), 3 + 4)

    def test_empty_delta_has_zero_elements(self) -> None:
        self.assertEqual(compute_total_element_count({}), 0)


if __name__ == "__main__":
    unittest.main()
