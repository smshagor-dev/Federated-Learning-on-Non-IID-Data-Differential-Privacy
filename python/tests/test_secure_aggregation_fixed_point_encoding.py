"""Tests for fl_platform.secure_aggregation.fixed_point_encoding --
mirrors cpp/coordinator/tests/fixed_point_encoding_test.cpp case-for-
case, plus a dedicated golden-fixture test loading the same stored
vectors as the C++ test
(fixtures/secure_aggregation/fixed_point_encoding_golden.json). See
docs/secure-aggregation-protocol-foundation.md.
"""

from __future__ import annotations

import json
import math
import unittest
from pathlib import Path

from fl_platform.secure_aggregation.fixed_point_encoding import (
    INT64_MAX,
    REJECTION_MAGNITUDE_OVERFLOW,
    REJECTION_NON_FINITE_INPUT,
    ROUNDING_RULE_ROUND_HALF_AWAY_FROM_ZERO,
    FixedPointEncodingProfile,
    decode_value,
    encode_value,
    prove_domain_bounds,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_FIXTURE_PATH = (
    REPO_ROOT / "fixtures" / "secure_aggregation" / "fixed_point_encoding_golden.json"
)


class FixedPointEncodingTests(unittest.TestCase):
    def test_round_trips_a_representative_sample(self) -> None:
        profile = FixedPointEncodingProfile()

        positive = encode_value(3.5, profile)
        self.assertTrue(positive.ok)
        self.assertEqual(decode_value(positive.encoded, profile), 3.5)

        negative = encode_value(-3.5, profile)
        self.assertTrue(negative.ok)
        self.assertEqual(decode_value(negative.encoded, profile), -3.5)

        zero = encode_value(0.0, profile)
        self.assertTrue(zero.ok)
        self.assertEqual(zero.encoded, 0)

        negative_zero = encode_value(-0.0, profile)
        self.assertTrue(negative_zero.ok)
        self.assertEqual(negative_zero.encoded, 0)

        half_step = 0.5 / profile.scale_factor
        halfway = encode_value(half_step, profile)
        self.assertTrue(halfway.ok)
        self.assertEqual(halfway.encoded, 1)
        halfway_negative = encode_value(-half_step, profile)
        self.assertTrue(halfway_negative.ok)
        self.assertEqual(halfway_negative.encoded, -1)

    def test_rejects_non_finite_and_out_of_magnitude_inputs(self) -> None:
        profile = FixedPointEncodingProfile()

        nan_result = encode_value(math.nan, profile)
        self.assertFalse(nan_result.ok)
        self.assertEqual(nan_result.reason, REJECTION_NON_FINITE_INPUT)

        inf_result = encode_value(math.inf, profile)
        self.assertFalse(inf_result.ok)
        self.assertEqual(inf_result.reason, REJECTION_NON_FINITE_INPUT)

        neg_inf_result = encode_value(-math.inf, profile)
        self.assertFalse(neg_inf_result.ok)
        self.assertEqual(neg_inf_result.reason, REJECTION_NON_FINITE_INPUT)

        too_large = encode_value(profile.max_input_magnitude + 1.0, profile)
        self.assertFalse(too_large.ok)
        self.assertEqual(too_large.reason, REJECTION_MAGNITUDE_OVERFLOW)

        at_boundary = encode_value(profile.max_input_magnitude, profile)
        self.assertTrue(
            at_boundary.ok,
            "a value exactly at max_input_magnitude is accepted (inclusive boundary)",
        )

    def test_quantization_error_is_small_and_non_negative(self) -> None:
        profile = FixedPointEncodingProfile()
        result = encode_value(1.0 / 3.0, profile)
        self.assertTrue(result.ok)
        self.assertGreaterEqual(result.quantization_error, 0.0)
        self.assertLess(result.quantization_error, 1.0 / profile.scale_factor)

    def test_default_profile_proves_domain_safe(self) -> None:
        profile = FixedPointEncodingProfile()
        proof = prove_domain_bounds(profile)
        self.assertTrue(proof.safe)
        self.assertFalse(proof.computation_overflowed)
        self.assertLess(proof.worst_case_aggregate_magnitude, INT64_MAX)

    def test_an_unsafe_profile_is_rejected_not_silently_permitted(self) -> None:
        profile = FixedPointEncodingProfile(
            scale_factor=1e12,
            max_input_magnitude=1e6,
            max_client_weight=(1 << 63),
            max_cohort_size=(1 << 63),
        )
        proof = prove_domain_bounds(profile)
        self.assertFalse(proof.safe)
        self.assertTrue(proof.explanation)

    def test_rounding_rule_is_the_one_deterministic_rule(self) -> None:
        profile = FixedPointEncodingProfile()
        self.assertEqual(profile.rounding_rule, ROUNDING_RULE_ROUND_HALF_AWAY_FROM_ZERO)


class FixedPointEncodingGoldenFixtureTests(unittest.TestCase):
    """Loads fixtures/secure_aggregation/fixed_point_encoding_golden.json
    -- fixed, hand-derived vectors, independent of this implementation
    (see that file's header comment) -- and checks this Python
    implementation against them, exactly mirroring the C++ side's golden
    block in fixed_point_encoding_test.cpp. This is the cross-language
    parity check (Work Package AL): both languages are graded against
    the same external, stored fixture, never against each other's live
    output.
    """

    @classmethod
    def setUpClass(cls) -> None:
        if not GOLDEN_FIXTURE_PATH.exists():
            raise AssertionError(
                f"golden fixture file not found: {GOLDEN_FIXTURE_PATH}"
            )
        with GOLDEN_FIXTURE_PATH.open(encoding="utf-8") as handle:
            cls.fixture = json.load(handle)

    def _profile_from_fixture(self) -> FixedPointEncodingProfile:
        p = self.fixture["profile"]
        return FixedPointEncodingProfile(
            schema_version=p["schema_version"],
            rounding_rule=p["rounding_rule"],
            scale_factor=p["scale_factor"],
            max_input_magnitude=p["max_input_magnitude"],
            max_client_weight=p["max_client_weight"],
            max_cohort_size=p["max_cohort_size"],
            safety_margin=p["safety_margin"],
        )

    def test_encode_cases_match_the_stored_fixture(self) -> None:
        profile = self._profile_from_fixture()
        for case in self.fixture["encode_cases"]:
            raw_value = case["value"]
            if raw_value == "NaN":
                value = math.nan
            elif raw_value == "Infinity":
                value = math.inf
            elif raw_value == "-Infinity":
                value = -math.inf
            else:
                value = float(raw_value)

            result = encode_value(value, profile)
            if case["ok"]:
                self.assertTrue(
                    result.ok, f"fixture case {case['id']!r} expected ok=True"
                )
                self.assertEqual(
                    result.encoded,
                    case["expected_encoded"],
                    f"fixture case {case['id']!r} encoded value mismatch",
                )
            else:
                self.assertFalse(
                    result.ok, f"fixture case {case['id']!r} expected ok=False"
                )
                self.assertEqual(
                    result.reason,
                    case["expected_rejection_reason"],
                    f"fixture case {case['id']!r} rejection reason mismatch",
                )

    def test_decode_cases_match_the_stored_fixture(self) -> None:
        profile = self._profile_from_fixture()
        for case in self.fixture["decode_cases"]:
            actual = decode_value(case["ring_value"], profile)
            self.assertEqual(
                actual,
                case["expected_value"],
                f"fixture case {case['id']!r} decode mismatch",
            )

    def test_domain_bounds_case_matches_the_stored_fixture(self) -> None:
        profile = self._profile_from_fixture()
        case = self.fixture["domain_bounds_case"]
        proof = prove_domain_bounds(profile)
        self.assertEqual(proof.safe, case["expected_safe"])
        self.assertFalse(proof.computation_overflowed)
        self.assertEqual(
            proof.worst_case_aggregate_magnitude, case["worst_case_aggregate_magnitude"]
        )


if __name__ == "__main__":
    unittest.main()
