// Unit + capstone integration tests for secure_aggregation_tensor_mask.hpp
// -- gRPC/OpenSSL-gated standalone executable, same minimal-link
// pattern as fl_secure_aggregation_crypto_tests.
//
// The capstone tests at the bottom of this file are the single most
// important test in this entire slice: they prove, with real X25519
// key agreement, real HKDF-SHA-256 key derivation, real ChaCha20
// keystream generation, and real fixed-point encoding, that (a) a
// complete, honest cohort's masked-sum decodes to the exact true
// aggregate, and (b) removing even one participant's masked
// contribution after the fact breaks that cancellation completely --
// this is the concrete, end-to-end evidence for why this protocol
// aborts on any post-freeze dropout rather than attempting a partial
// aggregate (see docs/secure-aggregation-threat-model.md and this
// slice's Mandatory Security Boundary).
#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_encoding.hpp"
#include "fl_coordinator/secure_aggregation_mask.hpp"
#include "fl_coordinator/secure_aggregation_tensor_mask.hpp"

#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <map>
#include <set>
#include <string>
#include <utility>
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

    // -- golden fixture: frozen reference mask stream ---------------------
    // fixtures/secure_aggregation/tensor_mask_stream_golden.json --
    // frozen from this exact reviewed run (same discipline as
    // cohort_commitment_golden.json). Both this assertion and the
    // Python mirror (test_secure_aggregation_tensor_mask.py) check
    // against the same frozen values.
    {
        const std::string fixed_secret = hex_decode("000102030405060708090a0b0c0d0e0f"
                                                      "101112131415161718191a1b1c1d1e1f");
        const auto golden_stream =
            derive_tensor_mask_stream(fixed_secret, kHkdfPurposeTensorMaskStream, "golden-fixture-context", 4);
        check(golden_stream.size() == 4, "golden fixture: derive_tensor_mask_stream produces 4 elements");
        const std::vector<std::uint64_t> expected{9086732726997875728ULL, 16617846648716935654ULL,
                                                    15486973003086668688ULL, 10999587187094550710ULL};
        check(golden_stream == expected,
              "golden fixture: derive_tensor_mask_stream matches the frozen reference values");
    }

    // -- mask stream derivation: uniqueness and key sensitivity -----------
    {
        const std::string secret_a = "shared-secret-a-32-bytes-long!!";
        const std::string secret_b = "shared-secret-b-32-bytes-long!!";
        const auto stream_a = derive_tensor_mask_stream(secret_a, kHkdfPurposeTensorMaskStream, "ctx", 8);
        const auto stream_b = derive_tensor_mask_stream(secret_b, kHkdfPurposeTensorMaskStream, "ctx", 8);
        check(stream_a.size() == 8, "derive_tensor_mask_stream produces the requested element count");
        check(stream_a != stream_b, "a different shared secret derives a completely different mask stream");

        const auto stream_a_again = derive_tensor_mask_stream(secret_a, kHkdfPurposeTensorMaskStream, "ctx", 8);
        check(stream_a == stream_a_again, "derive_tensor_mask_stream is deterministic for identical inputs");

        const auto stream_a_different_context =
            derive_tensor_mask_stream(secret_a, kHkdfPurposeTensorMaskStream, "different-ctx", 8);
        check(stream_a != stream_a_different_context, "a different canonical context derives a different mask stream");

        // Mask uniqueness within one stream: 8 independently-derived
        // 64-bit values colliding by chance is astronomically unlikely,
        // so any collision here would indicate a real generation bug
        // (e.g. accidentally reusing keystream bytes).
        check(std::set<std::uint64_t>(stream_a.begin(), stream_a.end()).size() == stream_a.size(),
              "every element of one mask stream is distinct");
    }

    // -- mask_tensor combining --------------------------------------------
    {
        const std::vector<std::int64_t> encoded_tensor{100, -50, 0};
        std::vector<PeerMaskStream> peers;
        peers.push_back(PeerMaskStream{"peer-a", PairwiseMaskSign::kAdd, {10, 20, 30}});
        peers.push_back(PeerMaskStream{"peer-b", PairwiseMaskSign::kSubtract, {1, 2, 3}});
        const auto masked = mask_tensor(encoded_tensor, peers);
        check(masked.size() == 3, "mask_tensor preserves element count");
        check(masked[0] == static_cast<std::uint64_t>(100 + 10 - 1), "mask_tensor combines masks element-wise (element 0)");
        check(masked[1] == static_cast<std::uint64_t>(-50 + 20 - 2), "mask_tensor combines masks element-wise (element 1)");
        check(masked[2] == static_cast<std::uint64_t>(0 + 30 - 3), "mask_tensor combines masks element-wise (element 2)");

        std::vector<PeerMaskStream> mismatched_peers;
        mismatched_peers.push_back(PeerMaskStream{"peer-a", PairwiseMaskSign::kAdd, {10, 20}});  // wrong length
        expect_throw([&]() { (void)mask_tensor(encoded_tensor, mismatched_peers); },
                     "mask_tensor rejects a peer mask stream whose length does not match the tensor");
    }

    // -- sum_masked_tensors --------------------------------------------
    {
        const std::vector<std::vector<std::uint64_t>> tensors{{1, 2, 3}, {10, 20, 30}, {100, 200, 300}};
        const auto sum = sum_masked_tensors(tensors);
        check(sum == std::vector<std::uint64_t>{111, 222, 333}, "sum_masked_tensors sums element-wise across participants");

        expect_throw([]() { (void)sum_masked_tensors({}); }, "sum_masked_tensors rejects an empty participant list");
        expect_throw(
            [&]() {
                (void)sum_masked_tensors({{1, 2}, {1, 2, 3}});
            },
            "sum_masked_tensors rejects mismatched element counts across participants");
    }

    // -- weight masking (Work Package U) -----------------------------
    {
        const std::string secret = "shared-secret-for-weight-32byte";
        const auto weight_mask1 = derive_weight_mask(secret, kHkdfPurposeWeightMaskStream, "ctx");
        const auto weight_mask2 = derive_weight_mask(secret, kHkdfPurposeWeightMaskStream, "ctx");
        check(weight_mask1 == weight_mask2, "derive_weight_mask is deterministic");

        const auto tensor_mask_same_inputs =
            derive_tensor_mask_stream(secret, kHkdfPurposeWeightMaskStream, "ctx", 1);
        check(weight_mask1 == tensor_mask_same_inputs.front(),
              "derive_weight_mask is exactly the element_count==1 case of derive_tensor_mask_stream");
    }

    // ======================================================================
    // Capstone: a complete, honest 4-participant cohort's masked-sum
    // decodes to the exact true aggregate; removing one participant's
    // contribution breaks the cancellation. Real X25519, real HKDF, real
    // ChaCha20, real fixed-point encoding -- no shortcuts, no fakes.
    // ======================================================================
    {
        const std::vector<std::string> participants{"worker-1", "worker-2", "worker-3", "worker-4"};
        const std::vector<double> true_values{1.5, -2.25, 3.75, -1.0};  // one scalar contribution per participant
        FixedPointEncodingProfile profile;

        // Real X25519 keypairs, one per participant.
        std::map<std::string, X25519KeyPair> keypairs;
        for (const auto& id : participants) {
            keypairs[id] = generate_x25519_keypair();
        }

        // Real pairwise shared secrets for every unordered pair.
        std::map<std::pair<std::string, std::string>, std::string> shared_secrets;
        for (std::size_t i = 0; i < participants.size(); ++i) {
            for (std::size_t j = i + 1; j < participants.size(); ++j) {
                const auto secret = derive_x25519_shared_secret(keypairs[participants[i]].private_key_raw,
                                                                  keypairs[participants[j]].public_key_raw);
                const auto secret_reverse = derive_x25519_shared_secret(keypairs[participants[j]].private_key_raw,
                                                                          keypairs[participants[i]].public_key_raw);
                check(secret == secret_reverse, "both sides of every pairwise X25519 exchange in the cohort agree");
                shared_secrets[{participants[i], participants[j]}] = secret;
            }
        }
        auto lookup_shared_secret = [&](const std::string& a, const std::string& b) -> const std::string& {
            return participant_sorts_before(a, b) ? shared_secrets.at({a, b}) : shared_secrets.at({b, a});
        };

        // Each participant encodes its own scalar contribution, then
        // masks it against every other participant using a real,
        // independently-derived pairwise mask key (canonical context
        // binds session/round/tensor-id/pair so every derived key is
        // unique).
        const std::string session_id = "capstone-session-1";
        const std::uint64_t round_id = 7;
        std::vector<std::uint64_t> masked_contributions;
        for (const auto& self_id : participants) {
            const auto self_index =
                static_cast<std::size_t>(std::find(participants.begin(), participants.end(), self_id) -
                                          participants.begin());
            const auto encoded = encode_value(true_values[self_index], profile);
            check(encoded.ok, "capstone: each participant's true value encodes successfully");

            std::vector<SignedMask> pairwise_masks;
            for (const auto& peer_id : participants) {
                if (peer_id == self_id) continue;
                const auto sign = resolve_pairwise_mask_sign(self_id, peer_id);
                const auto& secret = lookup_shared_secret(self_id, peer_id);
                const std::string context = session_id + "|" + std::to_string(round_id) + "|scalar|" +
                                             (participant_sorts_before(self_id, peer_id) ? self_id + "|" + peer_id
                                                                                          : peer_id + "|" + self_id);
                const auto mask_value = derive_weight_mask(secret, kHkdfPurposeWeightMaskStream, context);
                pairwise_masks.push_back(SignedMask{mask_value, sign});
            }
            masked_contributions.push_back(mask_encoded_value(encoded.encoded, pairwise_masks));
        }

        // Complete cohort: masked-sum must decode to the exact true sum.
        const auto complete_sum = sum_masked_values(masked_contributions);
        const double decoded_complete = decode_value(static_cast<std::int64_t>(complete_sum), profile);
        double true_sum = 0.0;
        for (const double v : true_values) true_sum += v;
        check(std::abs(decoded_complete - true_sum) < 1e-6,
              "CAPSTONE: a complete, honest 4-participant cohort's real masked-sum decodes to the exact true "
              "aggregate (pairwise masks cancel perfectly)");

        // Incomplete cohort (simulated post-freeze dropout of worker-4):
        // summing only 3 of the 4 masked contributions must NOT recover
        // the true partial sum of those 3 participants' values --
        // because each of the 3 remaining participants' masked value
        // still contains its (now-uncancelled) pairwise mask term
        // against the missing worker-4. This is the concrete
        // demonstration of why this protocol aborts rather than
        // continues after a dropout.
        const std::vector<std::uint64_t> partial{masked_contributions[0], masked_contributions[1],
                                                   masked_contributions[2]};
        const auto partial_sum = sum_masked_values(partial);
        const double decoded_partial = decode_value(static_cast<std::int64_t>(partial_sum), profile);
        const double true_partial_sum = true_values[0] + true_values[1] + true_values[2];
        check(std::abs(decoded_partial - true_partial_sum) > 1e-3,
              "CAPSTONE: an incomplete cohort's masked-sum does NOT recover the true partial aggregate -- "
              "pairwise masks against the missing participant do not cancel, concretely demonstrating why a "
              "post-freeze dropout must abort the session rather than silently degrade to a partial aggregate");
    }

    if (g_failures == 0) {
        std::cout << "all secure aggregation tensor mask tests passed (including the capstone cancellation proof)\n";
    }
    return g_failures == 0 ? 0 : 1;
}
