// Unit tests for secure_aggregation_crypto.hpp -- gRPC/OpenSSL-gated,
// so this is a standalone executable (fl_secure_aggregation_crypto_tests)
// built only alongside fl_coordinator_grpc_server, mirroring
// fl_peer_identity_tests' identical minimal-link pattern (see that
// target's CMakeLists.txt comment). Real OpenSSL, not a fake/mock --
// every primitive below runs the actual EVP code path this protocol
// will use live.
#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_session.hpp"

#include <functional>
#include <iostream>
#include <string>
#include <vector>

namespace {

int g_failures = 0;

void check(bool condition, const std::string& label) {
    if (!condition) {
        std::cerr << "FAILED: " << label << "\n";
        ++g_failures;
    }
}

void expect_throw(const std::function<void()>& action, const std::string& label) {
    bool threw = false;
    try {
        action();
    } catch (const std::exception&) {
        threw = true;
    }
    check(threw, label + " (expected an exception, none was thrown)");
}

}  // namespace

int main() {
    using namespace fl::coordinator;

    // -- hex encode/decode ---------------------------------------------
    {
        // Explicit-length constructor: a leading \x00 byte in an
        // implicit const char* conversion would be misread as the
        // string terminator, silently truncating this to an empty
        // string -- exactly the kind of embedded-NUL bug this raw byte
        // buffer must NOT trigger, since key/nonce/mask material
        // routinely contains zero bytes.
        const std::string raw("\x00\x01\xab\xff", 4);
        const std::string hex = hex_encode(raw);
        check(hex == "0001abff", "hex_encode produces lowercase hex");
        check(hex_decode(hex) == raw, "hex_decode inverts hex_encode");
        expect_throw([]() { (void)hex_decode("abc"); }, "hex_decode rejects odd-length input");
        expect_throw([]() { (void)hex_decode("zz"); }, "hex_decode rejects non-hex characters");
    }

    // -- X25519 -----------------------------------------------------------
    {
        const auto alice = generate_x25519_keypair();
        const auto bob = generate_x25519_keypair();
        check(alice.private_key_raw.size() == kX25519KeyLength, "generated private key is 32 bytes");
        check(alice.public_key_raw.size() == kX25519KeyLength, "generated public key is 32 bytes");
        check(alice.private_key_raw != bob.private_key_raw, "two independently generated keypairs differ");

        const auto secret_from_alice = derive_x25519_shared_secret(alice.private_key_raw, bob.public_key_raw);
        const auto secret_from_bob = derive_x25519_shared_secret(bob.private_key_raw, alice.public_key_raw);
        check(secret_from_alice.size() == kX25519KeyLength, "derived shared secret is 32 bytes");
        check(secret_from_alice == secret_from_bob,
              "both sides of an X25519 exchange derive the identical shared secret");

        const auto carol = generate_x25519_keypair();
        const auto secret_with_carol = derive_x25519_shared_secret(alice.private_key_raw, carol.public_key_raw);
        check(secret_with_carol != secret_from_alice,
              "the same private key against a different peer public key derives a different shared secret");

        expect_throw([&]() { (void)derive_x25519_shared_secret("too-short", bob.public_key_raw); },
                     "derive_x25519_shared_secret rejects a self private key of the wrong length");
        expect_throw([&]() { (void)derive_x25519_shared_secret(alice.private_key_raw, "too-short"); },
                     "derive_x25519_shared_secret rejects a peer public key of the wrong length");
    }

    // -- HKDF-SHA-256 -------------------------------------------------
    {
        const std::string salt = "salt-value";
        const std::string ikm = "input-keying-material-32-bytes!";
        const auto out1 = hkdf_sha256(salt, ikm, "info-a", 32);
        const auto out2 = hkdf_sha256(salt, ikm, "info-a", 32);
        check(out1.size() == 32, "hkdf_sha256 respects the requested output length");
        check(out1 == out2, "hkdf_sha256 is deterministic for identical inputs");

        const auto out_different_info = hkdf_sha256(salt, ikm, "info-b", 32);
        check(out1 != out_different_info, "a different HKDF info string derives a different key");

        const auto out_different_salt = hkdf_sha256("other-salt", ikm, "info-a", 32);
        check(out1 != out_different_salt, "a different HKDF salt derives a different key");

        const auto out_longer = hkdf_sha256(salt, ikm, "info-a", 64);
        check(out_longer.size() == 64, "hkdf_sha256 can expand beyond one SHA-256 block");
    }

    // -- purpose-specific key derivation (Work Package Q) -------------
    {
        const std::string shared_secret = "a-pretend-32-byte-shared-secret";
        const auto tensor_key =
            derive_purpose_key(shared_secret, kHkdfPurposeTensorMaskStream, "session-1|round-1|worker-a|worker-b");
        const auto weight_key =
            derive_purpose_key(shared_secret, kHkdfPurposeWeightMaskStream, "session-1|round-1|worker-a|worker-b");
        check(tensor_key != weight_key,
              "distinct purpose labels derive distinct keys from the identical shared secret and context (Work "
              "Package Q)");

        const auto same_purpose_different_context =
            derive_purpose_key(shared_secret, kHkdfPurposeTensorMaskStream, "session-1|round-2|worker-a|worker-b");
        check(tensor_key != same_purpose_different_context,
              "the same purpose under a different context (e.g. a different round) derives a different key");
    }

    // -- ChaCha20 IETF keystream ---------------------------------------
    {
        const std::string key(kChaCha20KeyLength, '\x01');
        const std::string nonce(kChaCha20NonceLength, '\x02');

        const auto stream1 = chacha20_keystream(key, nonce, 0, 64);
        const auto stream2 = chacha20_keystream(key, nonce, 0, 64);
        check(stream1.size() == 64, "chacha20_keystream produces the requested length");
        check(stream1 == stream2, "chacha20_keystream is deterministic for identical key/nonce/counter");

        const auto stream_with_different_key = chacha20_keystream(std::string(kChaCha20KeyLength, '\x03'), nonce, 0, 64);
        check(stream1 != stream_with_different_key, "a different key produces a different keystream");

        const auto stream_with_different_nonce =
            chacha20_keystream(key, std::string(kChaCha20NonceLength, '\x04'), 0, 64);
        check(stream1 != stream_with_different_nonce, "a different nonce produces a different keystream");

        const auto stream_with_different_counter = chacha20_keystream(key, nonce, 1, 64);
        check(stream1 != stream_with_different_counter, "a different initial counter produces a different keystream");

        expect_throw([&]() { (void)chacha20_keystream("too-short", nonce, 0, 32); },
                     "chacha20_keystream rejects a key of the wrong length");
        expect_throw([&]() { (void)chacha20_keystream(key, "too-short", 0, 32); },
                     "chacha20_keystream rejects a nonce of the wrong length");
    }

    // -- SHA-256 against known test vectors -----------------------------
    {
        check(sha256_hex("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
              "SHA-256('') matches the well-known empty-string digest");
        check(sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
              "SHA-256('abc') matches the well-known NIST test vector");
        check(sha256_digest("abc").size() == kSha256DigestLength, "sha256_digest returns exactly 32 raw bytes");
    }

    // -- cohort commitment (Work Package O) -----------------------------
    {
        const std::vector<std::string> roster{"worker-1", "worker-2", "worker-3"};
        const auto commitment1 = compute_cohort_commitment("session-a", "run-1", 3, "v1", roster);
        const auto commitment2 = compute_cohort_commitment("session-a", "run-1", 3, "v1", roster);
        check(commitment1 == commitment2, "compute_cohort_commitment is deterministic for identical inputs");
        // Golden fixture: fixtures/secure_aggregation/cohort_commitment_golden.json
        // -- frozen from a single reviewed run of this exact implementation
        // against these exact inputs (there is no way to hand-derive a
        // SHA-256 digest independently of running the algorithm, unlike
        // the fixed-point encoding fixtures -- see that fixture file's
        // header comment and this one's for the same honest caveat).
        // Both this C++ assertion and the Python mirror
        // (test_secure_aggregation_crypto.py) check against this same
        // frozen value, not against each other's live output.
        check(commitment1 == "aa2eb188daa1c75e42960d4c76d5b4422c8651c1023f70e5073b73b191ef3195",
              "golden fixture: compute_cohort_commitment matches the frozen reference value");

        const std::vector<std::string> reordered_roster{"worker-2", "worker-1", "worker-3"};
        const auto commitment_reordered = compute_cohort_commitment("session-a", "run-1", 3, "v1", reordered_roster);
        check(commitment1 != commitment_reordered,
              "changing participant order changes the commitment -- order is part of what is committed to");

        const auto commitment_different_round = compute_cohort_commitment("session-a", "run-1", 4, "v1", roster);
        check(commitment1 != commitment_different_round, "changing round_id changes the commitment");

        const auto commitment_different_session = compute_cohort_commitment("session-b", "run-1", 3, "v1", roster);
        check(commitment1 != commitment_different_session, "changing session_id changes the commitment");
    }

    // -- session configuration hash --------------------------------------
    {
        SecureAggregationSessionConfig config;
        config.session_id = "session-1";
        config.run_id = "run-1";
        config.round_id = 5;
        config.provider = SecureAggregationProvider::kSecureAggregationNoDropoutExperimental;

        const auto hash1 = compute_session_configuration_hash(config);
        const auto hash2 = compute_session_configuration_hash(config);
        check(hash1 == hash2, "compute_session_configuration_hash is deterministic for identical config");
        // Golden fixture: fixtures/secure_aggregation/session_configuration_hash_golden.json
        // -- same frozen-reference discipline as the cohort commitment
        // fixture above.
        check(hash1 == "76e4954d92f097321ee7fd340b88dd0294b29a3181e9416922635bd1b00c4ea2",
              "golden fixture: compute_session_configuration_hash matches the frozen reference value");

        SecureAggregationSessionConfig config_changed = config;
        config_changed.round_id = 6;
        const auto hash_changed = compute_session_configuration_hash(config_changed);
        check(hash1 != hash_changed, "changing round_id changes the session configuration hash");

        SecureAggregationSessionConfig config_none_provider = config;
        config_none_provider.provider = SecureAggregationProvider::kNone;
        const auto hash_none_provider = compute_session_configuration_hash(config_none_provider);
        check(hash1 != hash_none_provider, "changing the provider changes the session configuration hash");
    }

    if (g_failures == 0) {
        std::cout << "all secure aggregation crypto tests passed\n";
    }
    return g_failures == 0 ? 0 : 1;
}
