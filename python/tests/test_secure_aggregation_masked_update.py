"""Tests for fl_platform.secure_aggregation.masked_update -- Masked
Update Runtime and No-Dropout Secure FedAvg Finalization slice
(docs/secure-aggregation-masked-runtime-audit.md,
docs/secure-aggregation-masked-update.md).
"""

from __future__ import annotations

import unittest

import torch

from fl_platform.secure_aggregation.crypto import generate_x25519_keypair
from fl_platform.secure_aggregation.fixed_point_encoding import (
    FixedPointEncodingProfile,
    decode_value,
    to_signed_ring_value,
)
from fl_platform.secure_aggregation.masked_update import (
    RosterContext,
    SecureCohortHandshakeMaskingError,
    SecureTaskState,
    build_signed_masked_update,
    canonical_mask_context,
    encode_weighted_delta,
    mask_weighted_delta,
    validate_client_weight,
    validate_local_delta,
    validate_transition,
)
from fl_platform.security.signed_envelope import (
    MESSAGE_STREAM_SECURE_AGGREGATION,
    MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE,
    envelope_signing_bytes,
    masked_client_update_payload_hash_input,
    sha256_hex,
)
from fl_platform.security.signing_identity import generate_signing_identity


class SecureTaskStateTransitionTests(unittest.TestCase):
    def test_full_chain_is_valid_in_order(self) -> None:
        chain = [
            SecureTaskState.SECURE_TASK_VERIFIED,
            SecureTaskState.EPHEMERAL_KEY_CREATED,
            SecureTaskState.KEY_ADVERTISEMENT_ACCEPTED,
            SecureTaskState.FROZEN_ROSTER_VERIFIED,
            SecureTaskState.READY_FOR_MASKED_TRAINING,
            SecureTaskState.LOCAL_TRAINING,
            SecureTaskState.LOCAL_UPDATE_VALIDATED,
            SecureTaskState.FIXED_POINT_ENCODED,
            SecureTaskState.MASKED_UPDATE_CREATED,
            SecureTaskState.MASKED_UPDATE_SUBMITTED,
            SecureTaskState.SECURE_TASK_COMPLETED,
        ]
        current = None
        for target in chain:
            validate_transition(current, target)  # must not raise
            current = target

    def test_first_transition_must_be_secure_task_verified(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_transition(None, SecureTaskState.LOCAL_TRAINING)

    def test_skipping_a_state_is_rejected(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_transition(
                SecureTaskState.SECURE_TASK_VERIFIED,
                SecureTaskState.FROZEN_ROSTER_VERIFIED,
            )

    def test_cannot_transition_out_of_a_terminal_state(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_transition(
                SecureTaskState.SECURE_TASK_COMPLETED, SecureTaskState.LOCAL_TRAINING
            )

    def test_abort_reachable_from_any_non_terminal_state(self) -> None:
        for state in (
            SecureTaskState.SECURE_TASK_VERIFIED,
            SecureTaskState.LOCAL_TRAINING,
            SecureTaskState.MASKED_UPDATE_CREATED,
        ):
            # must not raise
            validate_transition(state, SecureTaskState.SECURE_TASK_ABORTED)


class ClientWeightValidationTests(unittest.TestCase):
    def test_valid_weight_returned_as_int(self) -> None:
        self.assertEqual(validate_client_weight(64, 1_000_000), 64)

    def test_zero_rejected(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_client_weight(0, 1_000_000)

    def test_negative_rejected(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_client_weight(-5, 1_000_000)

    def test_above_max_rejected(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_client_weight(200, 100)


class LocalDeltaValidationTests(unittest.TestCase):
    def test_finite_within_bound_accepted(self) -> None:
        validate_local_delta(
            {"weight": torch.tensor([1.0, -2.0])}, max_absolute_update_bound=100.0
        )  # must not raise

    def test_non_finite_rejected(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_local_delta(
                {"weight": torch.tensor([float("nan"), 1.0])},
                max_absolute_update_bound=100.0,
            )

    def test_exceeding_bound_rejected(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            validate_local_delta(
                {"weight": torch.tensor([500.0])}, max_absolute_update_bound=100.0
            )

    def test_zero_bound_disables_the_check(self) -> None:
        validate_local_delta(
            {"weight": torch.tensor([1e9])}, max_absolute_update_bound=0.0
        )  # must not raise


class CanonicalMaskContextTests(unittest.TestCase):
    def test_symmetric_regardless_of_which_side_computes_it(self) -> None:
        a = canonical_mask_context(
            provider=2,
            protocol_version=1,
            session_id="s1",
            run_id="r1",
            round_id=1,
            model_version="v1",
            cohort_commitment="c1",
            self_participant_id="worker-1",
            peer_participant_id="worker-2",
            tensor_name="weight",
        )
        b = canonical_mask_context(
            provider=2,
            protocol_version=1,
            session_id="s1",
            run_id="r1",
            round_id=1,
            model_version="v1",
            cohort_commitment="c1",
            self_participant_id="worker-2",
            peer_participant_id="worker-1",
            tensor_name="weight",
        )
        self.assertEqual(a, b)

    def test_different_tensor_name_changes_context(self) -> None:
        base = {
            "provider": 2,
            "protocol_version": 1,
            "session_id": "s1",
            "run_id": "r1",
            "round_id": 1,
            "model_version": "v1",
            "cohort_commitment": "c1",
            "self_participant_id": "worker-1",
            "peer_participant_id": "worker-2",
        }
        a = canonical_mask_context(**base, tensor_name="weight")
        b = canonical_mask_context(**base, tensor_name="bias")
        self.assertNotEqual(a, b)

    def test_different_session_round_model_changes_context(self) -> None:
        base = {
            "provider": 2,
            "protocol_version": 1,
            "session_id": "s1",
            "run_id": "r1",
            "round_id": 1,
            "model_version": "v1",
            "cohort_commitment": "c1",
            "self_participant_id": "worker-1",
            "peer_participant_id": "worker-2",
            "tensor_name": "weight",
        }
        reference = canonical_mask_context(**base)
        for override in (
            {"session_id": "s2"},
            {"round_id": 2},
            {"model_version": "v2"},
            {"cohort_commitment": "c2"},
        ):
            varied = dict(base)
            varied.update(override)
            self.assertNotEqual(canonical_mask_context(**varied), reference)


def _profile() -> FixedPointEncodingProfile:
    return FixedPointEncodingProfile()


class WeightedEncodingTests(unittest.TestCase):
    def test_encodes_every_tensor_in_sorted_order(self) -> None:
        result = encode_weighted_delta(
            {"b": torch.tensor([1.0]), "a": torch.tensor([2.0])}, 10, _profile()
        )
        self.assertEqual(result.tensor_names, ("a", "b"))
        self.assertEqual(result.total_elements, 2)

    def test_weighting_does_not_overflow_max_input_magnitude_for_realistic_values(
        self,
    ) -> None:
        # The bug this slice's own audit doc discloses: encoding
        # value*weight directly (the initially-assumed order) overflows
        # max_input_magnitude for realistic per-element deltas combined
        # with a realistic sample-count weight. The corrected order
        # (encode raw, then multiply by weight in ring space) must not.
        profile = _profile()
        result = encode_weighted_delta(
            {"weight": torch.tensor([2.5, -1.8])}, 64, profile
        )
        self.assertEqual(result.total_elements, 2)

    def test_rejects_non_finite(self) -> None:
        with self.assertRaises(SecureCohortHandshakeMaskingError):
            encode_weighted_delta(
                {"weight": torch.tensor([float("inf")])}, 10, _profile()
            )


class MaskedRoundTripTests(unittest.TestCase):
    """The real security property this whole slice exists to prove:
    Work Area AL's "complete frozen cohort decodes correctly" /
    "removing one contribution prevents valid cancellation"."""

    def setUp(self) -> None:
        self.participants = ["worker-1", "worker-2", "worker-3"]
        self.keys = {p: generate_x25519_keypair() for p in self.participants}
        self.public_keys = {p: self.keys[p][1] for p in self.participants}
        self.deltas: dict[str, tuple[dict[str, torch.Tensor], int]] = {
            "worker-1": ({"weight": torch.tensor([1.0, -2.0])}, 10),
            "worker-2": ({"weight": torch.tensor([3.0, 0.5])}, 20),
            "worker-3": ({"weight": torch.tensor([-1.5, 2.5])}, 64),
        }

    def _roster_for(self, worker_id: str) -> RosterContext:
        return RosterContext(
            provider=2,
            protocol_version=1,
            session_id="s1",
            run_id="r1",
            round_id=1,
            model_version="v1",
            cohort_commitment="c1",
            tensor_manifest_hash="t1",
            fixed_point_profile_hash="f1",
            cryptographic_profile_hash="cp1",
            payload_hash="p1",
            peer_public_keys={
                peer: self.public_keys[peer]
                for peer in self.participants
                if peer != worker_id
            },
        )

    def _mask_all(self) -> tuple[dict[str, list[int]], dict[str, int]]:
        profile = _profile()
        masked_tensor: dict[str, list[int]] = {}
        masked_weight: dict[str, int] = {}
        for p in self.participants:
            delta, weight = self.deltas[p]
            enc = encode_weighted_delta(delta, weight, profile)
            tensors, weight_ring = mask_weighted_delta(
                enc,
                self_worker_id=p,
                self_private_key_raw=self.keys[p][0],
                roster=self._roster_for(p),
            )
            masked_tensor[p] = tensors["weight"]
            masked_weight[p] = weight_ring
        return masked_tensor, masked_weight

    def test_complete_cohort_decodes_to_the_true_fedavg_weighted_average(self) -> None:
        from fl_platform.secure_aggregation.pairwise_mask import sum_masked_values
        from fl_platform.secure_aggregation.tensor_mask import sum_masked_tensors

        profile = _profile()
        masked_tensor, masked_weight = self._mask_all()

        summed = sum_masked_tensors([masked_tensor[p] for p in self.participants])
        decoded = [decode_value(to_signed_ring_value(v), profile) for v in summed]
        summed_weight = sum_masked_values([masked_weight[p] for p in self.participants])
        decoded_weight = decode_value(to_signed_ring_value(summed_weight), profile)

        expected_weighted_sum = [0.0, 0.0]
        total_weight = 0
        for p in self.participants:
            delta, weight = self.deltas[p]
            total_weight += weight
            for i, v in enumerate(delta["weight"].tolist()):
                expected_weighted_sum[i] += v * weight

        self.assertAlmostEqual(decoded_weight, float(total_weight), places=3)
        global_delta = [d / decoded_weight for d in decoded]
        expected_delta = [s / total_weight for s in expected_weighted_sum]
        for actual, expected in zip(global_delta, expected_delta, strict=True):
            self.assertAlmostEqual(actual, expected, places=3)

    def test_incomplete_cohort_does_not_decode_to_any_real_partial_sum(self) -> None:
        from fl_platform.secure_aggregation.tensor_mask import sum_masked_tensors

        profile = _profile()
        masked_tensor, _masked_weight = self._mask_all()

        summed_two = sum_masked_tensors(
            [masked_tensor[p] for p in self.participants[:2]]
        )
        decoded_two = [
            decode_value(to_signed_ring_value(v), profile) for v in summed_two
        ]

        # The real (unmasked) sum of just the first two participants'
        # contributions, for comparison -- the masked partial sum must
        # NOT be close to this either (pairwise masks from the still-
        # missing third participant never cancel).
        real_partial_sum = [0.0, 0.0]
        for p in self.participants[:2]:
            delta, weight = self.deltas[p]
            for i, v in enumerate(delta["weight"].tolist()):
                real_partial_sum[i] += v * weight

        for actual, real in zip(decoded_two, real_partial_sum, strict=True):
            self.assertGreater(
                abs(actual - real),
                1.0,
                "an incomplete cohort's masked partial sum must not resemble any "
                "real partial sum -- if it does, pairwise masking is not doing its job",
            )

    def test_different_session_id_produces_different_masks(self) -> None:
        profile = _profile()
        delta, weight = self.deltas["worker-1"]
        enc = encode_weighted_delta(delta, weight, profile)
        roster_a = self._roster_for("worker-1")
        roster_b = RosterContext(
            provider=roster_a.provider,
            protocol_version=roster_a.protocol_version,
            session_id="different-session",
            run_id=roster_a.run_id,
            round_id=roster_a.round_id,
            model_version=roster_a.model_version,
            cohort_commitment=roster_a.cohort_commitment,
            tensor_manifest_hash=roster_a.tensor_manifest_hash,
            fixed_point_profile_hash=roster_a.fixed_point_profile_hash,
            cryptographic_profile_hash=roster_a.cryptographic_profile_hash,
            payload_hash=roster_a.payload_hash,
            peer_public_keys=roster_a.peer_public_keys,
        )
        self_private_key = self.keys["worker-1"][0]
        masked_a, weight_a = mask_weighted_delta(
            enc,
            self_worker_id="worker-1",
            self_private_key_raw=self_private_key,
            roster=roster_a,
        )
        masked_b, weight_b = mask_weighted_delta(
            enc,
            self_worker_id="worker-1",
            self_private_key_raw=self_private_key,
            roster=roster_b,
        )
        self.assertNotEqual(masked_a["weight"], masked_b["weight"])
        self.assertNotEqual(weight_a, weight_b)


class BuildSignedMaskedUpdateTests(unittest.TestCase):
    def test_builds_a_real_verifiable_signed_envelope_with_no_clear_values(
        self,
    ) -> None:
        identity = generate_signing_identity("worker-1")
        profile = _profile()
        delta = {"weight": torch.tensor([1.0, -2.0])}
        weight = 10
        enc = encode_weighted_delta(delta, weight, profile)
        keys_self = generate_x25519_keypair()
        keys_peer = generate_x25519_keypair()
        roster = RosterContext(
            provider=2,
            protocol_version=1,
            session_id="s1",
            run_id="r1",
            round_id=1,
            model_version="v1",
            cohort_commitment="c1",
            tensor_manifest_hash="t1",
            fixed_point_profile_hash="f1",
            cryptographic_profile_hash="cp1",
            payload_hash="p1",
            peer_public_keys={"worker-2": keys_peer[1]},
        )
        masked_tensors, masked_weight = mask_weighted_delta(
            enc,
            self_worker_id="worker-1",
            self_private_key_raw=keys_self[0],
            roster=roster,
        )
        update_fields, envelope = build_signed_masked_update(
            masked_tensors=masked_tensors,
            masked_weight=masked_weight,
            encoding=enc,
            roster=roster,
            task_id="task-1",
            lease_id="lease-1",
            attempt=1,
            worker_id="worker-1",
            client_id="client-1",
            sample_privacy_record_hash="",
            signing_identity=identity,
            sequence_number=1,
            nonce="nonce-1",
            issued_at=1000.0,
        )

        # Structural cleartext prohibition: nothing on this dataclass
        # (or its repr) can carry a clear delta value -- only ring
        # integers and hashes/checksums.
        self.assertEqual(len(update_fields.masked_tensors), 1)
        self.assertEqual(update_fields.masked_tensors[0].tensor_name, "weight")
        self.assertNotIn("1.0", repr(update_fields))
        self.assertNotIn("-2.0", repr(update_fields))

        self.assertEqual(
            envelope.fields.message_type, MESSAGE_TYPE_SECURE_AGGREGATION_MASKED_UPDATE
        )
        self.assertEqual(
            envelope.fields.message_stream, MESSAGE_STREAM_SECURE_AGGREGATION
        )
        self.assertEqual(envelope.fields.signing_key_id, identity.key_id)

        expected_payload_hash = sha256_hex(
            masked_client_update_payload_hash_input(update_fields)
        )
        self.assertEqual(envelope.fields.payload_hash, expected_payload_hash)

        identity.verify_key.verify(
            envelope_signing_bytes(envelope.fields),
            bytes.fromhex(envelope.signature_hex),
        )

    def test_tampered_masked_value_changes_the_payload_hash(self) -> None:
        identity = generate_signing_identity("worker-1")
        profile = _profile()
        enc = encode_weighted_delta({"weight": torch.tensor([1.0])}, 10, profile)
        generate_x25519_keypair()
        roster = RosterContext(
            provider=2,
            protocol_version=1,
            session_id="s1",
            run_id="r1",
            round_id=1,
            model_version="v1",
            cohort_commitment="c1",
            tensor_manifest_hash="t1",
            fixed_point_profile_hash="f1",
            cryptographic_profile_hash="cp1",
            payload_hash="p1",
            peer_public_keys={},
        )
        masked_tensors = {"weight": enc.encoded_tensors["weight"]}
        _fields_a, envelope_a = build_signed_masked_update(
            masked_tensors=masked_tensors,
            masked_weight=enc.encoded_weight,
            encoding=enc,
            roster=roster,
            task_id="task-1",
            lease_id="lease-1",
            attempt=1,
            worker_id="worker-1",
            client_id="client-1",
            sample_privacy_record_hash="",
            signing_identity=identity,
            sequence_number=1,
            nonce="nonce-1",
            issued_at=1000.0,
        )
        tampered_tensors = {"weight": [v + 1 for v in enc.encoded_tensors["weight"]]}
        _fields_b, envelope_b = build_signed_masked_update(
            masked_tensors=tampered_tensors,
            masked_weight=enc.encoded_weight,
            encoding=enc,
            roster=roster,
            task_id="task-1",
            lease_id="lease-1",
            attempt=1,
            worker_id="worker-1",
            client_id="client-1",
            sample_privacy_record_hash="",
            signing_identity=identity,
            sequence_number=2,
            nonce="nonce-2",
            issued_at=1000.0,
        )
        self.assertNotEqual(
            envelope_a.fields.payload_hash, envelope_b.fields.payload_hash
        )


if __name__ == "__main__":
    unittest.main()
