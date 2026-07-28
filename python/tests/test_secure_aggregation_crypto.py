"""Tests for fl_platform.secure_aggregation.crypto -- mirrors
cpp/coordinator/tests/secure_aggregation_crypto_test.cpp case-for-case,
plus golden-fixture checks against
fixtures/secure_aggregation/cohort_commitment_golden.json and
session_configuration_hash_golden.json (the same frozen reference
values the C++ test asserts against).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from fl_platform.secure_aggregation.cohort_state_machine import (
    PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL,
    SecureAggregationSessionConfig,
)
from fl_platform.secure_aggregation.crypto import (
    CHACHA20_KEY_LENGTH,
    CHACHA20_NONCE_LENGTH,
    HKDF_PURPOSE_TENSOR_MASK_STREAM,
    HKDF_PURPOSE_WEIGHT_MASK_STREAM,
    SHA256_DIGEST_LENGTH,
    X25519_KEY_LENGTH,
    SecureAggregationCryptoError,
    chacha20_keystream,
    compute_cohort_commitment,
    compute_session_configuration_hash,
    derive_purpose_key,
    derive_x25519_shared_secret,
    generate_x25519_keypair,
    hkdf_sha256,
    sha256_digest,
    sha256_hex,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO_ROOT / "fixtures" / "secure_aggregation"


class X25519Tests(unittest.TestCase):
    def test_keygen_and_shared_secret_agreement(self) -> None:
        alice_priv, alice_pub = generate_x25519_keypair()
        bob_priv, bob_pub = generate_x25519_keypair()
        self.assertEqual(len(alice_priv), X25519_KEY_LENGTH)
        self.assertEqual(len(alice_pub), X25519_KEY_LENGTH)
        self.assertNotEqual(alice_priv, bob_priv)

        secret_from_alice = derive_x25519_shared_secret(alice_priv, bob_pub)
        secret_from_bob = derive_x25519_shared_secret(bob_priv, alice_pub)
        self.assertEqual(len(secret_from_alice), X25519_KEY_LENGTH)
        self.assertEqual(secret_from_alice, secret_from_bob)

        carol_priv, carol_pub = generate_x25519_keypair()
        secret_with_carol = derive_x25519_shared_secret(alice_priv, carol_pub)
        self.assertNotEqual(secret_with_carol, secret_from_alice)

    def test_rejects_wrong_length_keys(self) -> None:
        _, bob_pub = generate_x25519_keypair()
        with self.assertRaises(SecureAggregationCryptoError):
            derive_x25519_shared_secret(b"too-short", bob_pub)
        alice_priv, _ = generate_x25519_keypair()
        with self.assertRaises(SecureAggregationCryptoError):
            derive_x25519_shared_secret(alice_priv, b"too-short")


class HkdfTests(unittest.TestCase):
    def test_deterministic_and_length_respecting(self) -> None:
        salt = b"salt-value"
        ikm = b"input-keying-material-32-bytes!"
        out1 = hkdf_sha256(salt, ikm, b"info-a", 32)
        out2 = hkdf_sha256(salt, ikm, b"info-a", 32)
        self.assertEqual(len(out1), 32)
        self.assertEqual(out1, out2)

        out_different_info = hkdf_sha256(salt, ikm, b"info-b", 32)
        self.assertNotEqual(out1, out_different_info)

        out_different_salt = hkdf_sha256(b"other-salt", ikm, b"info-a", 32)
        self.assertNotEqual(out1, out_different_salt)

        out_longer = hkdf_sha256(salt, ikm, b"info-a", 64)
        self.assertEqual(len(out_longer), 64)

    def test_purpose_key_derivation_is_domain_separated(self) -> None:
        shared_secret = b"a-pretend-32-byte-shared-secret"
        tensor_key = derive_purpose_key(
            shared_secret,
            HKDF_PURPOSE_TENSOR_MASK_STREAM,
            "session-1|round-1|worker-a|worker-b",
        )
        weight_key = derive_purpose_key(
            shared_secret,
            HKDF_PURPOSE_WEIGHT_MASK_STREAM,
            "session-1|round-1|worker-a|worker-b",
        )
        self.assertNotEqual(tensor_key, weight_key)

        different_context_key = derive_purpose_key(
            shared_secret,
            HKDF_PURPOSE_TENSOR_MASK_STREAM,
            "session-1|round-2|worker-a|worker-b",
        )
        self.assertNotEqual(tensor_key, different_context_key)


class ChaCha20Tests(unittest.TestCase):
    def test_keystream_determinism_and_sensitivity(self) -> None:
        key = b"\x01" * CHACHA20_KEY_LENGTH
        nonce = b"\x02" * CHACHA20_NONCE_LENGTH

        stream1 = chacha20_keystream(key, nonce, 0, 64)
        stream2 = chacha20_keystream(key, nonce, 0, 64)
        self.assertEqual(len(stream1), 64)
        self.assertEqual(stream1, stream2)

        stream_different_key = chacha20_keystream(
            b"\x03" * CHACHA20_KEY_LENGTH, nonce, 0, 64
        )
        self.assertNotEqual(stream1, stream_different_key)

        stream_different_nonce = chacha20_keystream(
            key, b"\x04" * CHACHA20_NONCE_LENGTH, 0, 64
        )
        self.assertNotEqual(stream1, stream_different_nonce)

        stream_different_counter = chacha20_keystream(key, nonce, 1, 64)
        self.assertNotEqual(stream1, stream_different_counter)

    def test_rejects_wrong_length_key_or_nonce(self) -> None:
        key = b"\x01" * CHACHA20_KEY_LENGTH
        nonce = b"\x02" * CHACHA20_NONCE_LENGTH
        with self.assertRaises(SecureAggregationCryptoError):
            chacha20_keystream(b"too-short", nonce, 0, 32)
        with self.assertRaises(SecureAggregationCryptoError):
            chacha20_keystream(key, b"too-short", 0, 32)


class Sha256Tests(unittest.TestCase):
    def test_known_vectors(self) -> None:
        self.assertEqual(
            sha256_hex(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        )
        self.assertEqual(len(sha256_digest(b"abc")), SHA256_DIGEST_LENGTH)


class CohortCommitmentTests(unittest.TestCase):
    def test_deterministic_and_order_sensitive(self) -> None:
        roster = ["worker-1", "worker-2", "worker-3"]
        commitment1 = compute_cohort_commitment("session-a", "run-1", 3, "v1", roster)
        commitment2 = compute_cohort_commitment("session-a", "run-1", 3, "v1", roster)
        self.assertEqual(commitment1, commitment2)

        reordered = compute_cohort_commitment(
            "session-a", "run-1", 3, "v1", ["worker-2", "worker-1", "worker-3"]
        )
        self.assertNotEqual(commitment1, reordered)

        different_round = compute_cohort_commitment(
            "session-a", "run-1", 4, "v1", roster
        )
        self.assertNotEqual(commitment1, different_round)

        different_session = compute_cohort_commitment(
            "session-b", "run-1", 3, "v1", roster
        )
        self.assertNotEqual(commitment1, different_session)

    def test_matches_the_frozen_golden_fixture(self) -> None:
        with (FIXTURES_DIR / "cohort_commitment_golden.json").open(
            encoding="utf-8"
        ) as handle:
            fixture = json.load(handle)
        i = fixture["input"]
        actual = compute_cohort_commitment(
            i["session_id"],
            i["run_id"],
            i["round_id"],
            i["model_version"],
            i["ordered_participant_ids"],
        )
        self.assertEqual(
            actual,
            fixture["expected_commitment_hex"],
            "Python compute_cohort_commitment must match the same frozen "
            "reference value the C++ "
            "implementation is checked against",
        )


class SessionConfigurationHashTests(unittest.TestCase):
    def test_deterministic_and_field_sensitive(self) -> None:
        config = SecureAggregationSessionConfig(
            session_id="session-1",
            run_id="run-1",
            round_id=5,
            provider=PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL,
        )
        hash1 = compute_session_configuration_hash(config)
        hash2 = compute_session_configuration_hash(config)
        self.assertEqual(hash1, hash2)

        config_changed = SecureAggregationSessionConfig(
            session_id="session-1",
            run_id="run-1",
            round_id=6,
            provider=PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL,
        )
        self.assertNotEqual(hash1, compute_session_configuration_hash(config_changed))

    def test_matches_the_frozen_golden_fixture(self) -> None:
        with (FIXTURES_DIR / "session_configuration_hash_golden.json").open(
            encoding="utf-8"
        ) as handle:
            fixture = json.load(handle)
        i = fixture["input"]
        config = SecureAggregationSessionConfig(
            session_id=i["session_id"],
            run_id=i["run_id"],
            round_id=i["round_id"],
            provider=i["provider"],
        )
        actual = compute_session_configuration_hash(config)
        self.assertEqual(
            actual,
            fixture["expected_hash_hex"],
            "Python compute_session_configuration_hash must match the same "
            "frozen reference value the "
            "C++ implementation is checked against",
        )


if __name__ == "__main__":
    unittest.main()
