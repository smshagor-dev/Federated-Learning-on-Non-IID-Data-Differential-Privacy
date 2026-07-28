"""Tests for fl_platform.secure_aggregation.pairwise_mask -- mirrors
cpp/coordinator/tests/pairwise_mask_test.cpp case-for-case, including
the core cancellation-property proof.
"""

from __future__ import annotations

import unittest

from fl_platform.secure_aggregation.fixed_point_encoding import UINT64_MASK
from fl_platform.secure_aggregation.pairwise_mask import (
    MASK_SIGN_ADD,
    MASK_SIGN_SUBTRACT,
    SignedMask,
    apply_pairwise_mask,
    mask_encoded_value,
    participant_sorts_before,
    resolve_pairwise_mask_sign,
    sum_masked_values,
)


class PairwiseMaskTests(unittest.TestCase):
    def test_canonical_ordering_is_ordinal(self) -> None:
        self.assertTrue(participant_sorts_before("worker-1", "worker-2"))
        self.assertFalse(participant_sorts_before("worker-2", "worker-1"))
        self.assertFalse(participant_sorts_before("worker-1", "worker-1"))

    def test_sign_resolution(self) -> None:
        self.assertEqual(
            resolve_pairwise_mask_sign("worker-1", "worker-2"), MASK_SIGN_ADD
        )
        self.assertEqual(
            resolve_pairwise_mask_sign("worker-2", "worker-1"), MASK_SIGN_SUBTRACT
        )
        with self.assertRaises(ValueError):
            resolve_pairwise_mask_sign("worker-1", "worker-1")

    def test_ring_arithmetic_wraparound(self) -> None:
        self.assertEqual(apply_pairwise_mask(5, 3, MASK_SIGN_ADD), 8)
        self.assertEqual(apply_pairwise_mask(5, 3, MASK_SIGN_SUBTRACT), 2)
        self.assertEqual(apply_pairwise_mask(0, 1, MASK_SIGN_SUBTRACT), UINT64_MASK)
        self.assertEqual(apply_pairwise_mask(UINT64_MASK, 1, MASK_SIGN_ADD), 0)

    def test_mask_encoded_value(self) -> None:
        masks = [SignedMask(10, MASK_SIGN_ADD), SignedMask(4, MASK_SIGN_SUBTRACT)]
        self.assertEqual(mask_encoded_value(100, masks), 106)

        masked_negative_base = mask_encoded_value(-100, masks)
        self.assertEqual(masked_negative_base, (-94) & UINT64_MASK)

    def test_pairwise_masks_cancel_to_zero_across_a_complete_cohort(self) -> None:
        # Same fixed, arbitrary pairwise mask values per unordered pair
        # as the C++ test, so both languages prove the identical
        # cancellation property over the identical inputs.
        mask_ab = 0x1111111111111111
        mask_ac = 0x2222222222222222
        mask_ad = 0x3333333333333333
        mask_bc = 0x4444444444444444
        mask_bd = 0x5555555555555555
        mask_cd = 0x6666666666666666

        worker1 = apply_pairwise_mask(
            apply_pairwise_mask(mask_ab, mask_ac, MASK_SIGN_ADD), mask_ad, MASK_SIGN_ADD
        )

        worker2 = 0
        worker2 = apply_pairwise_mask(worker2, mask_ab, MASK_SIGN_SUBTRACT)
        worker2 = apply_pairwise_mask(worker2, mask_bc, MASK_SIGN_ADD)
        worker2 = apply_pairwise_mask(worker2, mask_bd, MASK_SIGN_ADD)

        worker3 = 0
        worker3 = apply_pairwise_mask(worker3, mask_ac, MASK_SIGN_SUBTRACT)
        worker3 = apply_pairwise_mask(worker3, mask_bc, MASK_SIGN_SUBTRACT)
        worker3 = apply_pairwise_mask(worker3, mask_cd, MASK_SIGN_ADD)

        worker4 = 0
        worker4 = apply_pairwise_mask(worker4, mask_ad, MASK_SIGN_SUBTRACT)
        worker4 = apply_pairwise_mask(worker4, mask_bd, MASK_SIGN_SUBTRACT)
        worker4 = apply_pairwise_mask(worker4, mask_cd, MASK_SIGN_SUBTRACT)

        total = sum_masked_values([worker1, worker2, worker3, worker4])
        self.assertEqual(total, 0)

    def test_sum_masked_values_edge_cases(self) -> None:
        self.assertEqual(sum_masked_values([]), 0)
        self.assertEqual(sum_masked_values([42]), 42)


if __name__ == "__main__":
    unittest.main()
