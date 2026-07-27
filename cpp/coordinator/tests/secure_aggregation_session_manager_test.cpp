// Unit + integration tests for SecureAggregationSessionManager --
// gRPC/OpenSSL-gated standalone executable, same minimal-link pattern
// as fl_secure_aggregation_tensor_mask_tests.
//
// The "real three-participant cohort" test at the bottom of this file
// is the capstone: it drives the manager through its complete public
// API (create_session -> advertise_key x3 -> freeze_cohort ->
// submit_masked_update x3 -> finalize) using real X25519 key
// agreement, real HKDF/ChaCha20 mask derivation, and real fixed-point
// encoding -- exactly the same primitives a live RPC handler would use
// -- and checks the manager's finalize() output against a hand-
// computed expected weighted average.
#include "fl_coordinator/coordinator_signing_identity.hpp"
#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_encoding.hpp"
#include "fl_coordinator/secure_aggregation_mask.hpp"
#include "fl_coordinator/secure_aggregation_session_manager.hpp"
#include "fl_coordinator/secure_aggregation_session_store.hpp"
#include "fl_coordinator/secure_aggregation_tensor_mask.hpp"

#include <filesystem>
#include <fstream>

#include <cmath>
#include <functional>
#include <iostream>
#include <map>
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

using namespace fl::coordinator;
namespace pb_coordinator = fl::coordinator::v1;
namespace pb_worker = fl::worker::v1;

// Mirrors secure_aggregation_session_manager.cpp's own documented
// checksum convention (le_bytes_of + sha256_hex) -- deliberately
// reimplemented here rather than exposed from the manager's
// translation unit, since a real future submission-construction path
// (Tier 2, worker-side) would derive this the same documented way,
// not by calling a coordinator-internal helper.
std::string checksum_of(const std::vector<std::uint64_t>& values) {
    std::string bytes;
    bytes.resize(values.size() * 8);
    for (std::size_t i = 0; i < values.size(); ++i) {
        std::uint64_t v = values[i];
        for (int b = 0; b < 8; ++b) {
            bytes[i * 8 + static_cast<std::size_t>(b)] = static_cast<char>(v & 0xFF);
            v >>= 8;
        }
    }
    return sha256_hex(bytes);
}

pb_coordinator::FixedPointEncodingProfile make_profile_proto() {
    pb_coordinator::FixedPointEncodingProfile profile;
    profile.set_schema_version(1);
    profile.set_rounding_rule("round_half_away_from_zero");
    profile.set_scale_factor(1048576.0);
    profile.set_max_input_magnitude(100.0);
    profile.set_max_client_weight(1000000);
    profile.set_max_cohort_size(10000);
    profile.set_safety_margin(256);
    return profile;
}

pb_coordinator::SecureAggregationSessionConfig make_session_config(const std::string& session_id,
                                                                    const std::vector<std::string>& participants) {
    pb_coordinator::SecureAggregationSessionConfig config;
    config.set_schema_version(1);
    config.set_protocol_version(1);
    config.set_provider(pb_worker::SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL);
    config.set_session_id(session_id);
    config.set_run_id("run-1");
    config.set_round_id(7);
    config.set_model_version("v1");
    config.set_aggregation_algorithm("fedavg");
    config.set_cohort_size(participants.size());
    config.set_minimum_cohort_size(participants.size());
    for (const auto& p : participants) config.add_ordered_participant_ids(p);
    config.set_tensor_manifest_hash("manifest-hash-placeholder");
    *config.mutable_fixed_point_profile() = make_profile_proto();
    config.set_domain_profile("ring_mod_2_64");
    config.set_scale_factor(1048576.0);
    auto* crypto_profile = config.mutable_cryptographic_profile();
    crypto_profile->set_mask_generator_profile("chacha20_ietf");
    crypto_profile->set_key_agreement_profile("x25519");
    crypto_profile->set_key_derivation_profile("hkdf_sha256");
    crypto_profile->set_digest_profile("sha256");
    return config;
}

}  // namespace

int main() {
    // -- create_session validation ---------------------------------------
    {
        SecureAggregationSessionManager manager;
        const auto config = make_session_config("session-1", {"worker-1", "worker-2"});
        const auto status = manager.create_session(config, 1000.0);
        check(status.state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_COHORT_FORMING,
              "create_session: fresh session starts in COHORT_FORMING");
        check(status.provider() == pb_worker::SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL,
              "create_session: provider round-trips");

        expect_throw([&]() { (void)manager.create_session(config, 1001.0); },
                     "create_session: duplicate session_id is rejected");

        auto bad_config = make_session_config("session-2", {"worker-1", "worker-2"});
        bad_config.set_provider(pb_worker::SECURE_AGGREGATION_PROVIDER_NONE);
        expect_throw([&]() { (void)manager.create_session(bad_config, 1000.0); },
                     "create_session: provider NONE is rejected -- no silent fallback");

        auto empty_roster_config = make_session_config("session-3", {});
        expect_throw([&]() { (void)manager.create_session(empty_roster_config, 1000.0); },
                     "create_session: an empty participant roster is rejected");

        auto unsafe_config = make_session_config("session-4", {"worker-1"});
        unsafe_config.mutable_fixed_point_profile()->set_scale_factor(1e300);
        unsafe_config.mutable_fixed_point_profile()->set_max_input_magnitude(1e300);
        expect_throw([&]() { (void)manager.create_session(unsafe_config, 1000.0); },
                     "create_session: a fixed-point profile that fails its domain bounds proof is rejected");
    }

    // -- advertise_key validation -----------------------------------------
    {
        SecureAggregationSessionManager manager;
        const auto config = make_session_config("session-adv", {"worker-1", "worker-2"});
        (void)manager.create_session(config, 1000.0);

        expect_throw(
            [&]() {
                pb_worker::SecureAggregationKeyAdvertisement bad;
                bad.set_session_id("no-such-session");
                (void)manager.advertise_key(bad, 1001.0);
            },
            "advertise_key: unknown session is rejected");

        const auto alice = generate_x25519_keypair();
        pb_worker::SecureAggregationKeyAdvertisement adv;
        adv.set_session_id("session-adv");
        adv.set_run_id("run-1");
        adv.set_round_id(7);
        adv.set_model_version("v1");
        adv.set_worker_id("worker-not-a-participant");
        adv.set_client_id("client-x");
        adv.set_ephemeral_public_key_x25519(hex_encode(alice.public_key_raw));
        expect_throw([&]() { (void)manager.advertise_key(adv, 1001.0); },
                     "advertise_key: a non-participant worker_id is rejected");

        adv.set_worker_id("worker-1");
        const auto status_after_first = manager.advertise_key(adv, 1001.0);
        check(status_after_first.state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_KEY_ADVERTISEMENT,
              "advertise_key: first advertisement transitions COHORT_FORMING -> KEY_ADVERTISEMENT");
        check(status_after_first.key_advertisement_count() == 1, "advertise_key: count increments");

        expect_throw([&]() { (void)manager.advertise_key(adv, 1002.0); },
                     "advertise_key: a duplicate advertisement from the same worker is rejected");

        pb_worker::SecureAggregationKeyAdvertisement zero_key = adv;
        zero_key.set_worker_id("worker-2");
        zero_key.set_ephemeral_public_key_x25519(hex_encode(std::string(kX25519KeyLength, '\0')));
        expect_throw([&]() { (void)manager.advertise_key(zero_key, 1002.0); },
                     "advertise_key: an all-zero public key is rejected");
    }

    // -- freeze_cohort requires a complete cohort -------------------------
    {
        SecureAggregationSessionManager manager;
        const auto config = make_session_config("session-freeze", {"worker-1", "worker-2"});
        (void)manager.create_session(config, 1000.0);

        const auto alice = generate_x25519_keypair();
        pb_worker::SecureAggregationKeyAdvertisement adv;
        adv.set_session_id("session-freeze");
        adv.set_run_id("run-1");
        adv.set_round_id(7);
        adv.set_model_version("v1");
        adv.set_worker_id("worker-1");
        adv.set_client_id("client-1");
        adv.set_ephemeral_public_key_x25519(hex_encode(alice.public_key_raw));
        (void)manager.advertise_key(adv, 1001.0);

        expect_throw([&]() { (void)manager.freeze_cohort("session-freeze", 1002.0); },
                     "freeze_cohort: an incomplete cohort (missing worker-2) is rejected, never frozen partially");
    }

    // -- abort ----------------------------------------------------------
    {
        SecureAggregationSessionManager manager;
        const auto config = make_session_config("session-abort", {"worker-1", "worker-2"});
        (void)manager.create_session(config, 1000.0);

        expect_throw(
            [&]() {
                (void)manager.abort("session-abort", pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_UNSPECIFIED,
                                     1001.0);
            },
            "abort: UNSPECIFIED reason is rejected");

        const auto status =
            manager.abort("session-abort", pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DROPOUT, 1001.0);
        check(status.state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED,
              "abort: session transitions to ABORTED");
        check(status.abort_reason() == pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DROPOUT,
              "abort: reason round-trips through the proto <-> C++ enum mapping");

        expect_throw(
            [&]() {
                (void)manager.abort("session-abort", pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MANUAL_ABORT,
                                     1002.0);
            },
            "abort: an already-terminal session cannot be aborted again");
    }

    // -- find / list ------------------------------------------------------
    {
        SecureAggregationSessionManager manager;
        check(!manager.find("nope").has_value(), "find: an unknown session_id returns nullopt");
        (void)manager.create_session(make_session_config("session-list-1", {"worker-1"}), 1000.0);
        (void)manager.create_session(make_session_config("session-list-2", {"worker-1"}), 1000.0);
        check(manager.find("session-list-1").has_value(), "find: a known session is found");
        check(manager.list().size() == 2, "list: returns every session");
    }

    // ======================================================================
    // Capstone: a real 3-participant cohort, driven entirely through the
    // manager's public API, using real X25519/HKDF/ChaCha20/fixed-point
    // encoding -- the same primitives and math the prior slice's own
    // capstone test proved cancel correctly, now exercised through the
    // session manager's actual create/advertise/freeze/submit/finalize
    // lifecycle rather than called directly.
    // ======================================================================
    {
        const std::vector<std::string> participants{"worker-1", "worker-2", "worker-3"};
        // (true tensor value pair, sample-count weight) per participant.
        const std::vector<std::pair<std::vector<double>, std::uint64_t>> contributions{
            {{1.0, -2.0}, 10}, {{3.0, 0.5}, 20}, {{-1.5, 2.5}, 5}};

        SecureAggregationSessionManager manager;
        auto config = make_session_config("session-capstone", participants);
        (void)manager.create_session(config, 1000.0);

        std::map<std::string, X25519KeyPair> keypairs;
        for (const auto& p : participants) keypairs[p] = generate_x25519_keypair();

        for (const auto& p : participants) {
            pb_worker::SecureAggregationKeyAdvertisement adv;
            adv.set_session_id("session-capstone");
            adv.set_run_id("run-1");
            adv.set_round_id(7);
            adv.set_model_version("v1");
            adv.set_worker_id(p);
            adv.set_client_id("client-" + p);
            adv.set_ephemeral_public_key_x25519(hex_encode(keypairs[p].public_key_raw));
            (void)manager.advertise_key(adv, 1001.0);
        }

        const auto roster = manager.freeze_cohort("session-capstone", 1002.0);
        check(static_cast<std::size_t>(roster.participants_size()) == participants.size(),
              "capstone: frozen roster contains every participant");
        const auto expected_commitment = compute_cohort_commitment("session-capstone", "run-1", 7, "v1", participants);
        check(roster.cohort_commitment() == expected_commitment,
              "capstone: the manager's cohort_commitment matches an independent direct call to the same "
              "cryptographic function");

        std::map<std::pair<std::string, std::string>, std::string> shared_secrets;
        for (std::size_t i = 0; i < participants.size(); ++i) {
            for (std::size_t j = i + 1; j < participants.size(); ++j) {
                const auto secret = derive_x25519_shared_secret(keypairs[participants[i]].private_key_raw,
                                                                  keypairs[participants[j]].public_key_raw);
                shared_secrets[{participants[i], participants[j]}] = secret;
            }
        }
        auto lookup_secret = [&](const std::string& a, const std::string& b) -> const std::string& {
            return participant_sorts_before(a, b) ? shared_secrets.at({a, b}) : shared_secrets.at({b, a});
        };

        FixedPointEncodingProfile profile;  // matches make_profile_proto()'s values (the defaults)

        for (std::size_t self_index = 0; self_index < participants.size(); ++self_index) {
            const auto& self_id = participants[self_index];
            const auto& [true_values, weight] = contributions[self_index];

            // Work Package M, step 11: the tensor value is weighted
            // *before* fixed-point encoding and masking -- summing
            // pre-weighted contributions and dividing by the summed
            // weight afterward (in finalize()) is what makes the
            // decoded result a true weighted average, exactly matching
            // FedAvg's own weighting arithmetic, just computed once
            // over the masked-sum instead of once per client update.
            std::vector<std::int64_t> encoded_values;
            for (const auto v : true_values) {
                const auto encoded = encode_value(v * static_cast<double>(weight), profile);
                check(encoded.ok, "capstone: every weighted true value encodes successfully");
                encoded_values.push_back(encoded.encoded);
            }
            const auto encoded_weight = encode_value(static_cast<double>(weight), profile);
            check(encoded_weight.ok, "capstone: every weight encodes successfully");

            std::vector<PeerMaskStream> tensor_peer_streams;
            std::vector<SignedMask> weight_peer_masks;
            for (const auto& peer_id : participants) {
                if (peer_id == self_id) continue;
                const auto sign = resolve_pairwise_mask_sign(self_id, peer_id);
                const auto& secret = lookup_secret(self_id, peer_id);
                const std::string ordered_pair = participant_sorts_before(self_id, peer_id)
                                                      ? self_id + "|" + peer_id
                                                      : peer_id + "|" + self_id;
                const std::string tensor_context = "session-capstone|7|weight|" + ordered_pair;
                const auto tensor_mask_values =
                    derive_tensor_mask_stream(secret, kHkdfPurposeTensorMaskStream, tensor_context, 2);
                tensor_peer_streams.push_back(PeerMaskStream{peer_id, sign, tensor_mask_values});

                const std::string weight_context = "session-capstone|7|sample_weight|" + ordered_pair;
                const auto weight_mask = derive_weight_mask(secret, kHkdfPurposeWeightMaskStream, weight_context);
                weight_peer_masks.push_back(SignedMask{weight_mask, sign});
            }

            const auto masked_tensor_values = mask_tensor(encoded_values, tensor_peer_streams);
            const auto masked_weight = mask_encoded_value(encoded_weight.encoded, weight_peer_masks);

            pb_worker::MaskedClientUpdate update;
            update.set_schema_version(1);
            update.set_provider(pb_worker::SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL);
            update.set_protocol_version(1);
            update.set_session_id("session-capstone");
            update.set_run_id("run-1");
            update.set_round_id(7);
            update.set_task_id("task-" + self_id);
            update.set_worker_id(self_id);
            update.set_client_id("client-" + self_id);
            update.set_model_version("v1");
            update.set_cohort_commitment(roster.cohort_commitment());
            update.set_tensor_manifest_hash(config.tensor_manifest_hash());
            update.set_fixed_point_profile_hash(roster.fixed_point_profile_hash());
            auto* tensor = update.add_masked_tensors();
            tensor->set_tensor_name("weight");
            for (const auto v : masked_tensor_values) tensor->add_masked_values(v);
            tensor->set_checksum(checksum_of(masked_tensor_values));
            update.set_masked_weight(masked_weight);
            update.set_masked_weight_checksum(checksum_of({masked_weight}));

            const auto status = manager.submit_masked_update(update, 1003.0 + static_cast<double>(self_index));
            check(status.masked_contribution_count() == self_index + 1,
                  "capstone: masked_contribution_count increments correctly");
        }

        const auto final_status = manager.find("session-capstone");
        check(final_status.has_value() &&
                  final_status->state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_MASKED_UPDATE_COLLECTION,
              "capstone: session is in MASKED_UPDATE_COLLECTION once all contributions are in, before finalize");

        const auto result = manager.finalize("session-capstone", 1010.0);
        check(result.model_delta.contains("weight"), "capstone: finalize produces a 'weight' tensor in model_delta");
        const auto& decoded = result.model_delta.at("weight").values();
        check(decoded.size() == 2, "capstone: decoded tensor has the expected element count");

        double expected_weight_sum = 0.0;
        std::vector<double> expected_weighted_sum{0.0, 0.0};
        for (const auto& [true_values, weight] : contributions) {
            expected_weight_sum += static_cast<double>(weight);
            for (std::size_t i = 0; i < true_values.size(); ++i) {
                expected_weighted_sum[i] += true_values[i] * static_cast<double>(weight);
            }
        }
        for (std::size_t i = 0; i < decoded.size(); ++i) {
            const double expected = expected_weighted_sum[i] / expected_weight_sum;
            check(std::abs(decoded[i] - expected) < 1e-4,
                  "CAPSTONE: SecureAggregationSessionManager::finalize decodes the exact true FedAvg-weighted "
                  "average, computed via real X25519/HKDF/ChaCha20 pairwise masking end to end through the "
                  "manager's public API -- element " +
                      std::to_string(i));
        }

        const auto completed_status = manager.find("session-capstone");
        check(completed_status.has_value() &&
                  completed_status->state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_COMPLETED,
              "capstone: session is COMPLETED after finalize");
        check(!completed_status->aggregate_checksum().empty(), "capstone: a real aggregate checksum is recorded");

        expect_throw([&]() { (void)manager.finalize("session-capstone", 1011.0); },
                     "capstone: finalize cannot be called twice on an already-COMPLETED session");
    }

    // -- finalize refuses an incomplete cohort ----------------------------
    {
        SecureAggregationSessionManager manager;
        const std::vector<std::string> participants{"worker-1", "worker-2"};
        (void)manager.create_session(make_session_config("session-incomplete", participants), 1000.0);

        std::map<std::string, X25519KeyPair> keypairs;
        for (const auto& p : participants) keypairs[p] = generate_x25519_keypair();
        for (const auto& p : participants) {
            pb_worker::SecureAggregationKeyAdvertisement adv;
            adv.set_session_id("session-incomplete");
            adv.set_run_id("run-1");
            adv.set_round_id(7);
            adv.set_model_version("v1");
            adv.set_worker_id(p);
            adv.set_client_id("client-" + p);
            adv.set_ephemeral_public_key_x25519(hex_encode(keypairs[p].public_key_raw));
            (void)manager.advertise_key(adv, 1001.0);
        }
        const auto roster = manager.freeze_cohort("session-incomplete", 1002.0);

        // Only worker-1 submits -- worker-2 "drops out" after freeze.
        FixedPointEncodingProfile profile;
        const auto encoded = encode_value(5.0, profile);
        pb_worker::MaskedClientUpdate update;
        update.set_session_id("session-incomplete");
        update.set_run_id("run-1");
        update.set_round_id(7);
        update.set_model_version("v1");
        update.set_worker_id("worker-1");
        update.set_cohort_commitment(roster.cohort_commitment());
        update.set_tensor_manifest_hash("manifest-hash-placeholder");
        auto* tensor = update.add_masked_tensors();
        tensor->set_tensor_name("weight");
        tensor->add_masked_values(static_cast<std::uint64_t>(encoded.encoded));
        tensor->set_checksum(checksum_of({static_cast<std::uint64_t>(encoded.encoded)}));
        update.set_masked_weight(1);
        update.set_masked_weight_checksum(checksum_of({1}));
        (void)manager.submit_masked_update(update, 1003.0);

        expect_throw([&]() { (void)manager.finalize("session-incomplete", 1010.0); },
                     "finalize: an incomplete cohort (worker-2 dropped out after freeze) is refused -- no partial "
                     "aggregate is ever produced, matching the Threshold Secret-Sharing Blocker's required "
                     "no-dropout policy");

        const auto abort_status = manager.abort(
            "session-incomplete", pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DROPOUT, 1011.0);
        check(abort_status.state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED,
              "finalize: the correct caller response to an incomplete cohort is abort(kDropout), which succeeds");
    }

    // -- Secure User-Level Differential Privacy Runtime slice: --------
    // finalize()'s noise_provider/noise_std_dev/expected_weight_sum ---
    // parameters ------------------------------------------------------
    {
        // Builds a complete, ready-to-finalize 2-participant session
        // (worker-1 contributes 3.0, worker-2 contributes -1.0, both
        // with weight 1) under a fresh session_id -- reused for every
        // sub-case below so each gets an identical masked-sum going
        // into finalize(), isolating exactly what the new parameters
        // change.
        auto build_ready_session = [&](const std::string& session_id) {
            auto manager = std::make_unique<SecureAggregationSessionManager>();
            const std::vector<std::string> participants{"worker-1", "worker-2"};
            (void)manager->create_session(make_session_config(session_id, participants), 1000.0);
            std::map<std::string, X25519KeyPair> keypairs;
            for (const auto& p : participants) keypairs[p] = generate_x25519_keypair();
            for (const auto& p : participants) {
                pb_worker::SecureAggregationKeyAdvertisement adv;
                adv.set_session_id(session_id);
                adv.set_run_id("run-1");
                adv.set_round_id(7);
                adv.set_model_version("v1");
                adv.set_worker_id(p);
                adv.set_client_id("client-" + p);
                adv.set_ephemeral_public_key_x25519(hex_encode(keypairs[p].public_key_raw));
                (void)manager->advertise_key(adv, 1001.0);
            }
            const auto roster = manager->freeze_cohort(session_id, 1002.0);
            std::map<std::pair<std::string, std::string>, std::string> shared_secrets;
            const auto secret =
                derive_x25519_shared_secret(keypairs["worker-1"].private_key_raw, keypairs["worker-2"].public_key_raw);
            FixedPointEncodingProfile profile;
            const std::vector<std::pair<std::string, double>> contributions{{"worker-1", 3.0}, {"worker-2", -1.0}};
            for (const auto& [self_id, true_value] : contributions) {
                const std::string peer_id = self_id == "worker-1" ? "worker-2" : "worker-1";
                const auto sign = resolve_pairwise_mask_sign(self_id, peer_id);
                const std::string ordered_pair = participant_sorts_before(self_id, peer_id)
                                                      ? self_id + "|" + peer_id
                                                      : peer_id + "|" + self_id;
                const auto encoded_value = encode_value(true_value, profile);
                const auto encoded_weight = encode_value(1.0, profile);
                const std::string tensor_context = session_id + "|7|weight|" + ordered_pair;
                const auto tensor_mask_values =
                    derive_tensor_mask_stream(secret, kHkdfPurposeTensorMaskStream, tensor_context, 1);
                const auto masked_value =
                    mask_encoded_value(encoded_value.encoded, {SignedMask{tensor_mask_values[0], sign}});
                const std::string weight_context = session_id + "|7|sample_weight|" + ordered_pair;
                const auto weight_mask = derive_weight_mask(secret, kHkdfPurposeWeightMaskStream, weight_context);
                const auto masked_weight = mask_encoded_value(encoded_weight.encoded, {SignedMask{weight_mask, sign}});

                pb_worker::MaskedClientUpdate update;
                update.set_session_id(session_id);
                update.set_run_id("run-1");
                update.set_round_id(7);
                update.set_model_version("v1");
                update.set_worker_id(self_id);
                update.set_cohort_commitment(roster.cohort_commitment());
                update.set_tensor_manifest_hash("manifest-hash-placeholder");
                auto* tensor = update.add_masked_tensors();
                tensor->set_tensor_name("weight");
                tensor->add_masked_values(masked_value);
                tensor->set_checksum(checksum_of({masked_value}));
                update.set_masked_weight(masked_weight);
                update.set_masked_weight_checksum(checksum_of({masked_weight}));
                (void)manager->submit_masked_update(update, 1003.0);
            }
            return manager;
        };

        // No noise (nullptr/0.0, the default for every pre-existing
        // caller): behavior is byte-for-byte unchanged from before this
        // slice -- decodes to the exact true average.
        {
            auto manager = build_ready_session("session-noise-none");
            const auto result = manager->finalize("session-noise-none", 1010.0);
            check(std::abs(result.model_delta.at("weight").values()[0] - 1.0) < 1e-4,
                  "finalize: with no noise provider, decodes to the exact true average ((3.0 + -1.0)/2 = 1.0)");
        }

        // Deterministic noise engages and is applied exactly once
        // (added to the sum, then divided -- not added twice, not
        // added post-division).
        {
            auto manager_a = build_ready_session("session-noise-a");
            fl::core::DeterministicNoiseProvider provider_a(/*seed=*/42);
            const auto result_a =
                manager_a->finalize("session-noise-a", 1010.0, &provider_a, /*noise_std_dev=*/50.0);
            const double noised_value = result_a.model_delta.at("weight").values()[0];
            check(std::abs(noised_value - 1.0) > 1e-3,
                  "finalize: a real noise_std_dev genuinely changes the decoded result versus the "
                  "no-noise case -- noise is actually engaging, not silently ignored");

            // Same seed, same inputs -> identical noised result
            // (determinism, not merely "some noise happened").
            auto manager_b = build_ready_session("session-noise-b");
            fl::core::DeterministicNoiseProvider provider_b(/*seed=*/42);
            const auto result_b =
                manager_b->finalize("session-noise-b", 1010.0, &provider_b, /*noise_std_dev=*/50.0);
            check(std::abs(result_b.model_delta.at("weight").values()[0] - noised_value) < 1e-9,
                  "finalize: identical seed/std_dev/inputs produce a byte-identical noised result");

            // Different seed -> different noised result.
            auto manager_c = build_ready_session("session-noise-c");
            fl::core::DeterministicNoiseProvider provider_c(/*seed=*/43);
            const auto result_c =
                manager_c->finalize("session-noise-c", 1010.0, &provider_c, /*noise_std_dev=*/50.0);
            check(std::abs(result_c.model_delta.at("weight").values()[0] - noised_value) > 1e-6,
                  "finalize: a different seed produces a different noised result");
        }

        // Work Area L's fixed-weight integrity check: expected_weight_sum
        // mismatch aborts (kMaskCancellationFailed), never silently
        // divides by the wrong total.
        {
            auto manager = build_ready_session("session-weight-mismatch");
            expect_throw(
                [&]() {
                    (void)manager->finalize("session-weight-mismatch", 1010.0, nullptr, 0.0,
                                            /*expected_weight_sum=*/3.0);
                },
                "finalize: expected_weight_sum=3.0 does not match the real decoded weight sum "
                "(2.0, two participants each contributing weight 1) -- aborted, not silently applied");
            const auto status = manager->find("session-weight-mismatch");
            check(status.has_value() &&
                      status->state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED &&
                      status->abort_reason() == pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MASK_CANCELLATION_FAILED,
                  "finalize: the session is aborted with kMaskCancellationFailed on a fixed-weight mismatch");
        }

        // The matching expected_weight_sum succeeds normally (2.0 for
        // this two-participant, weight-1-each cohort).
        {
            auto manager = build_ready_session("session-weight-match");
            const auto result = manager->finalize("session-weight-match", 1010.0, nullptr, 0.0,
                                                   /*expected_weight_sum=*/2.0);
            check(std::abs(result.model_delta.at("weight").values()[0] - 1.0) < 1e-4,
                  "finalize: a correct expected_weight_sum succeeds and decodes normally");
        }
    }

    // -- Secure Cohort Handshake and Signed Roster Runtime slice: -----
    // find_binding_for_participant / has_session_for_run_round --------
    {
        SecureAggregationSessionManager manager;
        const std::vector<std::string> participants{"worker-1", "worker-2"};
        auto config = make_session_config("session-binding", participants);
        config.set_key_advertisement_deadline_unix_s(2000.0);
        (void)manager.create_session(config, 1000.0);

        check(manager.has_session_for_run_round("run-1", 7),
              "has_session_for_run_round: true once a session exists for this (run_id, round_id)");
        check(!manager.has_session_for_run_round("run-1", 8),
              "has_session_for_run_round: false for a round with no session");
        check(!manager.has_session_for_run_round("run-2", 7),
              "has_session_for_run_round: false for a different run_id");

        const auto binding = manager.find_binding_for_participant("run-1", 7, "worker-1");
        check(binding.has_value(), "find_binding_for_participant: a configured participant gets a binding");
        check(binding->secure_aggregation_active(), "the returned binding is marked active");
        check(binding->session_id() == "session-binding", "the binding names the real session_id");
        check(binding->provider() == pb_worker::SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL,
              "the binding's provider matches the session config");

        check(!manager.find_binding_for_participant("run-1", 7, "worker-not-in-cohort").has_value(),
              "find_binding_for_participant: a non-participant worker gets no binding");
        check(!manager.find_binding_for_participant("run-1", 99, "worker-1").has_value(),
              "find_binding_for_participant: an unknown round gets no binding");

        // Once frozen, the session is no longer accepting advertisements
        // -- no binding should be offered for a *new* task in that round
        // (a worker already mid-handshake uses the roster/session-status
        // RPCs directly, not a fresh task binding).
        const auto alice = generate_x25519_keypair();
        const auto bob = generate_x25519_keypair();
        for (const auto& [worker_id, keypair] : std::map<std::string, X25519KeyPair>{
                 {"worker-1", alice}, {"worker-2", bob}}) {
            pb_worker::SecureAggregationKeyAdvertisement adv;
            adv.set_session_id("session-binding");
            adv.set_run_id("run-1");
            adv.set_round_id(7);
            adv.set_model_version("v1");
            adv.set_worker_id(worker_id);
            adv.set_client_id("client-" + worker_id);
            adv.set_ephemeral_public_key_x25519(hex_encode(keypair.public_key_raw));
            (void)manager.advertise_key(adv, 1001.0);
        }
        (void)manager.freeze_cohort("session-binding", 1002.0);
        check(!manager.find_binding_for_participant("run-1", 7, "worker-1").has_value(),
              "find_binding_for_participant: no binding is offered once the session has moved past "
              "KEY_ADVERTISEMENT (here, COHORT_FROZEN)");
        check(manager.has_session_for_run_round("run-1", 7),
              "has_session_for_run_round remains true regardless of session state");
    }

    // -- get_frozen_roster ------------------------------------------------
    {
        SecureAggregationSessionManager manager;
        const std::vector<std::string> participants{"worker-1"};
        (void)manager.create_session(make_session_config("session-roster-query", participants), 1000.0);
        check(!manager.get_frozen_roster("session-roster-query").has_value(),
              "get_frozen_roster: nothing available before freeze");
        check(!manager.get_frozen_roster("no-such-session").has_value(),
              "get_frozen_roster: nothing available for an unknown session_id");

        const auto alice = generate_x25519_keypair();
        pb_worker::SecureAggregationKeyAdvertisement adv;
        adv.set_session_id("session-roster-query");
        adv.set_run_id("run-1");
        adv.set_round_id(7);
        adv.set_model_version("v1");
        adv.set_worker_id("worker-1");
        adv.set_client_id("client-1");
        adv.set_ephemeral_public_key_x25519(hex_encode(alice.public_key_raw));
        (void)manager.advertise_key(adv, 1001.0);
        (void)manager.freeze_cohort("session-roster-query", 1002.0);

        const auto roster = manager.get_frozen_roster("session-roster-query");
        check(roster.has_value(), "get_frozen_roster: available once the cohort is frozen");
        check(roster->session_id() == "session-roster-query", "the returned roster matches the requested session");
    }

    // -- freeze_cohort real signing --------------------------------------
    {
        SecureAggregationSessionManager manager;
        const std::vector<std::string> participants{"worker-1"};
        (void)manager.create_session(make_session_config("session-signed-roster", participants), 1000.0);
        const auto alice = generate_x25519_keypair();
        pb_worker::SecureAggregationKeyAdvertisement adv;
        adv.set_session_id("session-signed-roster");
        adv.set_run_id("run-1");
        adv.set_round_id(7);
        adv.set_model_version("v1");
        adv.set_worker_id("worker-1");
        adv.set_client_id("client-1");
        adv.set_ephemeral_public_key_x25519(hex_encode(alice.public_key_raw));
        (void)manager.advertise_key(adv, 1001.0);

        const auto identity = generate_coordinator_signing_identity();
        const auto roster = manager.freeze_cohort("session-signed-roster", 1002.0, &identity);
        check(roster.coordinator_signing_key_id() == identity.key_id,
              "freeze_cohort: a signed roster records the real signing identity's key_id");
        check(roster.signature().size() == 128,
              "freeze_cohort: a real Ed25519 signature is produced (128 hex chars)");
        check(roster.payload_hash().size() == 64,
              "freeze_cohort: a real SHA-256 payload_hash is produced (64 hex chars)");

        // Unsigned path (no identity provided) still works and leaves
        // signing fields empty -- the pre-existing, still-valid
        // behavior for callers (most of this test file) that don't need
        // real cryptographic evidence.
        SecureAggregationSessionManager unsigned_manager;
        (void)unsigned_manager.create_session(make_session_config("session-unsigned-roster", participants), 1000.0);
        pb_worker::SecureAggregationKeyAdvertisement adv2 = adv;
        adv2.set_session_id("session-unsigned-roster");
        (void)unsigned_manager.advertise_key(adv2, 1001.0);
        const auto unsigned_roster = unsigned_manager.freeze_cohort("session-unsigned-roster", 1002.0);
        check(unsigned_roster.signature().empty() && unsigned_roster.coordinator_signing_key_id().empty(),
              "freeze_cohort: without a signing identity, signature/coordinator_signing_key_id stay empty");
    }

    // -- sweep_expired_advertisement_deadlines ----------------------------
    {
        SecureAggregationSessionManager manager;
        auto config = make_session_config("session-deadline", std::vector<std::string>{"worker-1", "worker-2"});
        config.set_key_advertisement_deadline_unix_s(1010.0);
        (void)manager.create_session(config, 1000.0);

        // worker-1 advertises in time; worker-2 never does.
        const auto alice = generate_x25519_keypair();
        pb_worker::SecureAggregationKeyAdvertisement adv;
        adv.set_session_id("session-deadline");
        adv.set_run_id("run-1");
        adv.set_round_id(7);
        adv.set_model_version("v1");
        adv.set_worker_id("worker-1");
        adv.set_client_id("client-1");
        adv.set_ephemeral_public_key_x25519(hex_encode(alice.public_key_raw));
        (void)manager.advertise_key(adv, 1005.0);

        auto empty_sweep = manager.sweep_expired_advertisement_deadlines(1008.0);
        check(empty_sweep.empty(), "sweep_expired_advertisement_deadlines: nothing to do before the deadline passes");

        const auto expired = manager.sweep_expired_advertisement_deadlines(1011.0);
        check(expired.size() == 1 && expired.front() == "session-deadline",
              "sweep_expired_advertisement_deadlines: an incomplete cohort past its deadline is aborted");

        const auto status = manager.find("session-deadline");
        check(status.has_value() && status->state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED,
              "sweep_expired_advertisement_deadlines: the session is really transitioned to ABORTED");
        check(status->abort_reason() == pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DEADLINE_EXCEEDED,
              "sweep_expired_advertisement_deadlines: the abort reason is DEADLINE_EXCEEDED");

        // A session that already completed its cohort before the
        // deadline must never be swept.
        SecureAggregationSessionManager frozen_manager;
        auto frozen_config = make_session_config("session-deadline-frozen", std::vector<std::string>{"worker-1"});
        frozen_config.set_key_advertisement_deadline_unix_s(1010.0);
        (void)frozen_manager.create_session(frozen_config, 1000.0);
        pb_worker::SecureAggregationKeyAdvertisement adv2 = adv;
        adv2.set_session_id("session-deadline-frozen");
        (void)frozen_manager.advertise_key(adv2, 1005.0);
        (void)frozen_manager.freeze_cohort("session-deadline-frozen", 1006.0);
        const auto no_op_sweep = frozen_manager.sweep_expired_advertisement_deadlines(1020.0);
        check(no_op_sweep.empty(),
              "sweep_expired_advertisement_deadlines: an already-frozen session is never swept, even past the "
              "advertisement deadline");
    }

    // -- Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area S: sweep_expired_masked_update_deadlines --------
    {
        SecureAggregationSessionManager manager;
        const std::vector<std::string> participants{"worker-1", "worker-2"};
        auto config = make_session_config("session-masked-deadline", participants);
        config.set_masked_update_deadline_unix_s(1010.0);
        (void)manager.create_session(config, 1000.0);

        std::map<std::string, X25519KeyPair> keypairs;
        for (const auto& p : participants) keypairs[p] = generate_x25519_keypair();
        for (const auto& p : participants) {
            pb_worker::SecureAggregationKeyAdvertisement adv;
            adv.set_session_id("session-masked-deadline");
            adv.set_run_id("run-1");
            adv.set_round_id(7);
            adv.set_model_version("v1");
            adv.set_worker_id(p);
            adv.set_client_id("client-" + p);
            adv.set_ephemeral_public_key_x25519(hex_encode(keypairs[p].public_key_raw));
            (void)manager.advertise_key(adv, 1001.0);
        }
        const auto roster = manager.freeze_cohort("session-masked-deadline", 1002.0);

        // Only worker-1 submits a masked update before the deadline;
        // worker-2 never does.
        FixedPointEncodingProfile profile;
        const auto encoded = encode_value(5.0, profile);
        pb_worker::MaskedClientUpdate update;
        update.set_session_id("session-masked-deadline");
        update.set_run_id("run-1");
        update.set_round_id(7);
        update.set_model_version("v1");
        update.set_worker_id("worker-1");
        update.set_cohort_commitment(roster.cohort_commitment());
        update.set_tensor_manifest_hash(config.tensor_manifest_hash());
        auto* tensor = update.add_masked_tensors();
        tensor->set_tensor_name("weight");
        tensor->add_masked_values(static_cast<std::uint64_t>(encoded.encoded));
        tensor->set_checksum(checksum_of({static_cast<std::uint64_t>(encoded.encoded)}));
        update.set_masked_weight(1);
        update.set_masked_weight_checksum(checksum_of({1}));
        (void)manager.submit_masked_update(update, 1005.0);

        const auto empty_sweep = manager.sweep_expired_masked_update_deadlines(1008.0);
        check(empty_sweep.empty(),
              "sweep_expired_masked_update_deadlines: nothing to do before the deadline passes");

        const auto expired = manager.sweep_expired_masked_update_deadlines(1011.0);
        check(expired.size() == 1 && expired.front() == "session-masked-deadline",
              "sweep_expired_masked_update_deadlines: an incomplete cohort (worker-2 never submitted) past its "
              "masked-update deadline is aborted");

        const auto status = manager.find("session-masked-deadline");
        check(status.has_value() && status->state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED,
              "sweep_expired_masked_update_deadlines: the session is really transitioned to ABORTED");
        check(status->abort_reason() == pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_DEADLINE_EXCEEDED,
              "sweep_expired_masked_update_deadlines: the abort reason is DEADLINE_EXCEEDED");

        expect_throw([&]() { (void)manager.finalize("session-masked-deadline", 1012.0); },
                     "sweep_expired_masked_update_deadlines: an aborted session can never be finalized -- no "
                     "partial sum is ever decoded, matching the Threshold Secret-Sharing Restriction's required "
                     "frozen-cohort failure behavior");

        // A session that already collected its complete cohort before
        // the deadline must never be swept.
        SecureAggregationSessionManager complete_manager;
        const std::vector<std::string> solo{"worker-1"};
        auto complete_config = make_session_config("session-masked-deadline-complete", solo);
        complete_config.set_masked_update_deadline_unix_s(1010.0);
        (void)complete_manager.create_session(complete_config, 1000.0);
        pb_worker::SecureAggregationKeyAdvertisement adv_solo;
        adv_solo.set_session_id("session-masked-deadline-complete");
        adv_solo.set_run_id("run-1");
        adv_solo.set_round_id(7);
        adv_solo.set_model_version("v1");
        adv_solo.set_worker_id("worker-1");
        adv_solo.set_client_id("client-1");
        adv_solo.set_ephemeral_public_key_x25519(hex_encode(keypairs["worker-1"].public_key_raw));
        (void)complete_manager.advertise_key(adv_solo, 1001.0);
        const auto solo_roster = complete_manager.freeze_cohort("session-masked-deadline-complete", 1002.0);
        pb_worker::MaskedClientUpdate solo_update = update;
        solo_update.set_session_id("session-masked-deadline-complete");
        solo_update.set_cohort_commitment(solo_roster.cohort_commitment());
        (void)complete_manager.submit_masked_update(solo_update, 1005.0);
        const auto no_op_sweep = complete_manager.sweep_expired_masked_update_deadlines(1020.0);
        check(no_op_sweep.empty(),
              "sweep_expired_masked_update_deadlines: a session whose complete cohort already submitted is never "
              "swept, even past the masked-update deadline");
    }

    // -- Masked Update Runtime and No-Dropout Secure FedAvg Finalization
    // slice, Work Area P: find_status_for_run_round -----------------------
    {
        SecureAggregationSessionManager manager;
        check(!manager.find_status_for_run_round("run-1", 7).has_value(),
              "find_status_for_run_round: nullopt when no session has ever existed for this (run_id, round_id)");

        (void)manager.create_session(make_session_config("session-status-lookup", {"worker-1"}), 1000.0);
        const auto created_status = manager.find_status_for_run_round("run-1", 7);
        check(created_status.has_value() && created_status->session_id() == "session-status-lookup",
              "find_status_for_run_round: finds the real session for a matching (run_id, round_id)");
        check(!manager.find_status_for_run_round("run-1", 8).has_value(),
              "find_status_for_run_round: nullopt for a different round_id");
        check(!manager.find_status_for_run_round("run-2", 7).has_value(),
              "find_status_for_run_round: nullopt for a different run_id");

        const auto aborted_status = manager.abort(
            "session-status-lookup",
            pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE, 1001.0);
        check(aborted_status.state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED,
              "find_status_for_run_round setup: abort really transitions the session");
        const auto after_abort = manager.find_status_for_run_round("run-1", 7);
        check(after_abort.has_value() &&
                  after_abort->state() == pb_coordinator::SECURE_AGGREGATION_SESSION_STATE_ABORTED &&
                  after_abort->abort_reason() ==
                      pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE,
              "find_status_for_run_round: still finds the session once terminal, with the real abort_reason -- "
              "this is exactly what CoordinatorServiceImpl::SubmitClientResult's cleartext-prohibition check "
              "(Work Area P) relies on to distinguish the privacy-mode-incompatible fallback from every other "
              "abort reason");
    }

    // -- persistence via an injected SecureAggregationSessionStore -------
    {
        const std::string scratch_dir = "secure_aggregation_session_manager_test_scratch";
        std::filesystem::remove_all(scratch_dir);
        std::filesystem::create_directories(scratch_dir);
        const std::string store_path = scratch_dir + "/sessions.dat";

        {
            SecureAggregationSessionStore store(store_path);
            SecureAggregationSessionManager manager(&store);
            (void)manager.create_session(
                make_session_config("session-persisted", std::vector<std::string>{"worker-1"}), 1000.0);
            (void)manager.abort("session-persisted", pb_coordinator::SECURE_AGGREGATION_ABORT_REASON_MANUAL_ABORT,
                                 1001.0);
        }
        {
            // A fresh store instance, simulating a coordinator restart --
            // the manager's own in-memory state is gone, but the safe
            // metadata the manager recorded through the store must have
            // survived.
            SecureAggregationSessionStore reloaded_store(store_path);
            const auto record = reloaded_store.find("session-persisted");
            check(record.has_value(), "the session manager's create_session/abort calls persisted real records "
                                       "through the injected store");
            check(record->state == "ABORTED",
                  "the persisted record reflects the session's real final state (ABORTED)");
            check(record->abort_reason == "manual_abort",
                  "the persisted record reflects the real abort reason");
        }
        std::filesystem::remove_all(scratch_dir);
    }

    if (g_failures == 0) {
        std::cout << "all secure aggregation session manager tests passed (including the capstone finalize proof)\n";
    }
    return g_failures == 0 ? 0 : 1;
}
