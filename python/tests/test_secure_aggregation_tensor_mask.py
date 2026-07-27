"""Tests for fl_platform.secure_aggregation.tensor_mask -- mirrors
cpp/coordinator/tests/secure_aggregation_tensor_mask_test.cpp case-for-
case, including the capstone full-cohort-cancellation /
dropout-breaks-cancellation integration proof, run here with real
PyNaCl X25519 + `cryptography` HKDF/ChaCha20 + the Python fixed-point
encoder -- an independent second implementation of the identical
mathematical claim the C++ capstone test proves.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fl_platform.secure_aggregation.crypto import (
    HKDF_PURPOSE_TENSOR_MASK_STREAM,
    HKDF_PURPOSE_WEIGHT_MASK_STREAM,
    derive_x25519_shared_secret,
    generate_x25519_keypair,
)
from fl_platform.secure_aggregation.fixed_point_encoding import FixedPointEncodingProfile, decode_value, encode_value
from fl_platform.secure_aggregation.pairwise_mask import (
    SignedMask,
    mask_encoded_value,
    participant_sorts_before,
    resolve_pairwise_mask_sign,
    sum_masked_values,
)
from fl_platform.secure_aggregation.tensor_mask import (
    PeerMaskStream,
    derive_tensor_mask_stream,
    derive_weight_mask,
    mask_tensor,
    sum_masked_tensors,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "secure_aggregation"


class MaskStreamDerivationTests(unittest.TestCase):
    def test_uniqueness_and_key_sensitivity(self) -> None:
        secret_a = b"shared-secret-a-32-bytes-long!!"
        secret_b = b"shared-secret-b-32-bytes-long!!"
        stream_a = derive_tensor_mask_stream(secret_a, HKDF_PURPOSE_TENSOR_MASK_STREAM, "ctx", 8)
        stream_b = derive_tensor_mask_stream(secret_b, HKDF_PURPOSE_TENSOR_MASK_STREAM, "ctx", 8)
        self.assertEqual(len(stream_a), 8)
        self.assertNotEqual(stream_a, stream_b)

        stream_a_again = derive_tensor_mask_stream(secret_a, HKDF_PURPOSE_TENSOR_MASK_STREAM, "ctx", 8)
        self.assertEqual(stream_a, stream_a_again)

        stream_a_different_context = derive_tensor_mask_stream(
            secret_a, HKDF_PURPOSE_TENSOR_MASK_STREAM, "different-ctx", 8
        )
        self.assertNotEqual(stream_a, stream_a_different_context)

        self.assertEqual(len(set(stream_a)), len(stream_a))

    def test_matches_the_frozen_golden_fixture(self) -> None:
        with (FIXTURES_DIR / "tensor_mask_stream_golden.json").open(encoding="utf-8") as handle:
            fixture = json.load(handle)
        i = fixture["input"]
        actual = derive_tensor_mask_stream(
            bytes.fromhex(i["shared_secret_hex"]), i["purpose_label"], i["canonical_context"], i["element_count"]
        )
        self.assertEqual(
            actual,
            fixture["expected_mask_values"],
            "Python derive_tensor_mask_stream must match the same frozen reference value the C++ "
            "implementation is checked against",
        )


class MaskTensorTests(unittest.TestCase):
    def test_combining(self) -> None:
        encoded_tensor = [100, -50, 0]
        peers = [
            PeerMaskStream("peer-a", "add", [10, 20, 30]),
            PeerMaskStream("peer-b", "subtract", [1, 2, 3]),
        ]
        masked = mask_tensor(encoded_tensor, peers)
        self.assertEqual(len(masked), 3)
        self.assertEqual(masked[0], (100 + 10 - 1) & ((1 << 64) - 1))
        self.assertEqual(masked[1], (-50 + 20 - 2) & ((1 << 64) - 1))
        self.assertEqual(masked[2], (0 + 30 - 3) & ((1 << 64) - 1))

        with self.assertRaises(ValueError):
            mask_tensor(encoded_tensor, [PeerMaskStream("peer-a", "add", [10, 20])])


class SumMaskedTensorsTests(unittest.TestCase):
    def test_sums_element_wise(self) -> None:
        tensors = [[1, 2, 3], [10, 20, 30], [100, 200, 300]]
        self.assertEqual(sum_masked_tensors(tensors), [111, 222, 333])

        with self.assertRaises(ValueError):
            sum_masked_tensors([])
        with self.assertRaises(ValueError):
            sum_masked_tensors([[1, 2], [1, 2, 3]])


class WeightMaskTests(unittest.TestCase):
    def test_matches_element_count_one_tensor_mask(self) -> None:
        secret = b"shared-secret-for-weight-32byte"
        weight_mask1 = derive_weight_mask(secret, HKDF_PURPOSE_WEIGHT_MASK_STREAM, "ctx")
        weight_mask2 = derive_weight_mask(secret, HKDF_PURPOSE_WEIGHT_MASK_STREAM, "ctx")
        self.assertEqual(weight_mask1, weight_mask2)

        tensor_mask_same_inputs = derive_tensor_mask_stream(secret, HKDF_PURPOSE_WEIGHT_MASK_STREAM, "ctx", 1)
        self.assertEqual(weight_mask1, tensor_mask_same_inputs[0])


class CapstoneCancellationTests(unittest.TestCase):
    """The single most important test in this module -- mirrors the C++
    capstone test in secure_aggregation_tensor_mask_test.cpp exactly,
    as an independent second-language proof of the same claim: a
    complete cohort's real masked-sum decodes to the exact true
    aggregate, and dropping one participant's contribution breaks that
    cancellation.
    """

    def test_complete_cohort_recovers_exact_aggregate_and_dropout_breaks_it(self) -> None:
        participants = ["worker-1", "worker-2", "worker-3", "worker-4"]
        true_values = [1.5, -2.25, 3.75, -1.0]
        profile = FixedPointEncodingProfile()

        keypairs = {p: generate_x25519_keypair() for p in participants}

        shared_secrets: dict[tuple[str, str], bytes] = {}
        for i in range(len(participants)):
            for j in range(i + 1, len(participants)):
                a, b = participants[i], participants[j]
                priv_a, pub_a = keypairs[a]
                priv_b, pub_b = keypairs[b]
                secret_ab = derive_x25519_shared_secret(priv_a, pub_b)
                secret_ba = derive_x25519_shared_secret(priv_b, pub_a)
                self.assertEqual(secret_ab, secret_ba)
                shared_secrets[(a, b)] = secret_ab

        def lookup_shared_secret(a: str, b: str) -> bytes:
            return shared_secrets[(a, b)] if participant_sorts_before(a, b) else shared_secrets[(b, a)]

        session_id = "capstone-session-1"
        round_id = 7
        masked_contributions = []
        for self_id in participants:
            value = true_values[participants.index(self_id)]
            encoded = encode_value(value, profile)
            self.assertTrue(encoded.ok)

            pairwise_masks = []
            for peer_id in participants:
                if peer_id == self_id:
                    continue
                sign = resolve_pairwise_mask_sign(self_id, peer_id)
                secret = lookup_shared_secret(self_id, peer_id)
                ordered_pair = (
                    f"{self_id}|{peer_id}" if participant_sorts_before(self_id, peer_id) else f"{peer_id}|{self_id}"
                )
                context = f"{session_id}|{round_id}|scalar|{ordered_pair}"
                mask_value = derive_weight_mask(secret, HKDF_PURPOSE_WEIGHT_MASK_STREAM, context)
                pairwise_masks.append(SignedMask(mask_value, sign))
            masked_contributions.append(mask_encoded_value(encoded.encoded, pairwise_masks))

        complete_sum = sum_masked_values(masked_contributions)
        # Reinterpret the ring value as signed 64-bit before decoding,
        # matching the C++ side's static_cast<int64_t>(uint64_t).
        signed_sum = complete_sum if complete_sum < (1 << 63) else complete_sum - (1 << 64)
        decoded_complete = decode_value(signed_sum, profile)
        self.assertAlmostEqual(
            decoded_complete,
            sum(true_values),
            places=5,
            msg="a complete, honest 4-participant cohort's real masked-sum must decode to the exact true aggregate",
        )

        partial = masked_contributions[:3]
        partial_sum = sum_masked_values(partial)
        signed_partial_sum = partial_sum if partial_sum < (1 << 63) else partial_sum - (1 << 64)
        decoded_partial = decode_value(signed_partial_sum, profile)
        true_partial_sum = sum(true_values[:3])
        self.assertGreater(
            abs(decoded_partial - true_partial_sum),
            1e-3,
            "an incomplete cohort's masked-sum must NOT recover the true partial aggregate -- this is the "
            "concrete reason a post-freeze dropout must abort the session",
        )


if __name__ == "__main__":
    unittest.main()
