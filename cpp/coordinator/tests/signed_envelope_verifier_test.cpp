// Unit coverage for signed_envelope_verifier.cpp: canonical envelope
// metadata JSON, the heartbeat payload hash input, the client-result
// payload hash input, and a full Ed25519 sign/verify round trip (valid,
// tampered, wrong message_type, expired, future-issued, wrong key).
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "coordinator/coordinator.pb.h"
#include "privacy/privacy.pb.h"
#include "worker/worker.pb.h"

#include <openssl/evp.h>

#include <array>
#include <cmath>
#include <cstring>
#include <iostream>
#include <limits>
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

std::string hex_encode(const unsigned char* data, std::size_t length) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(length * 2);
    for (std::size_t i = 0; i < length; ++i) {
        out += kHex[(data[i] >> 4) & 0xF];
        out += kHex[data[i] & 0xF];
    }
    return out;
}

struct GeneratedKeypair {
    std::string public_key_hex;
    EVP_PKEY* pkey = nullptr;
};

GeneratedKeypair generate_ed25519_keypair() {
    GeneratedKeypair result;
    result.pkey = EVP_PKEY_Q_keygen(nullptr, nullptr, "ED25519");
    unsigned char raw_public[32];
    std::size_t raw_public_len = sizeof(raw_public);
    EVP_PKEY_get_raw_public_key(result.pkey, raw_public, &raw_public_len);
    result.public_key_hex = hex_encode(raw_public, raw_public_len);
    return result;
}

std::string sign_hex(EVP_PKEY* pkey, const std::string& message) {
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestSignInit(ctx, nullptr, nullptr, nullptr, pkey);
    unsigned char signature[64];
    std::size_t signature_len = sizeof(signature);
    EVP_DigestSign(ctx, signature, &signature_len,
                   reinterpret_cast<const unsigned char*>(message.data()), message.size());
    EVP_MD_CTX_free(ctx);
    return hex_encode(signature, signature_len);
}

std::string sha256_hex_for_test(const std::string& message) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_len = 0;
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr);
    EVP_DigestUpdate(ctx, message.data(), message.size());
    EVP_DigestFinal_ex(ctx, digest, &digest_len);
    EVP_MD_CTX_free(ctx);
    return hex_encode(digest, digest_len);
}

fl::worker::v1::WorkerHeartbeatRequest make_heartbeat_request() {
    fl::worker::v1::WorkerHeartbeatRequest request;
    request.set_worker_id("worker-1");
    request.set_status(fl::worker::v1::WORKER_STATUS_IDLE);
    request.set_current_task_id("task-42");
    return request;
}

fl::worker::v1::SignedWorkerEnvelope make_envelope(double now) {
    fl::worker::v1::SignedWorkerEnvelope envelope;
    envelope.set_schema_version(1);
    envelope.set_message_type(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT);
    envelope.set_worker_id("worker-1");
    envelope.set_message_stream(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_HEARTBEAT);
    envelope.set_sequence_number(1);
    envelope.set_issued_at(now);
    envelope.set_expires_at(now + 60.0);
    envelope.set_nonce("test-nonce-1");
    envelope.set_signing_key_id("key-1");
    return envelope;
}

}  // namespace

int main() {
    using fl::coordinator::canonical_envelope_metadata_json;
    using fl::coordinator::envelope_signing_bytes;
    using fl::coordinator::heartbeat_payload_hash_input;
    using fl::coordinator::verify_signed_envelope;

    {
        // Canonical metadata JSON: alphabetical key order, empty
        // optional identifiers encoded as their protobuf zero value.
        auto envelope = make_envelope(1000.0);
        const auto json = canonical_envelope_metadata_json(envelope);
        check(json.find("\"worker_id\":\"worker-1\"") != std::string::npos,
              "canonical envelope JSON includes worker_id");
        check(json.find("\"run_id\":\"\"") != std::string::npos,
              "an unset run_id canonicalizes to an empty string, not omitted");
        check(json.find("\"round_id\":0") != std::string::npos,
              "an unset round_id canonicalizes to 0");
        check(json.find("\"client_id\":") < json.find("\"expires_at\":"),
              "client_id sorts before expires_at (alphabetical key order)");
        check(json.find("\"sequence_number\":1") != std::string::npos,
              "canonical envelope JSON includes sequence_number");
    }

    {
        auto keypair = generate_ed25519_keypair();
        const auto heartbeat_request = make_heartbeat_request();
        auto envelope = make_envelope(1000.0);

        const auto hash_input = heartbeat_payload_hash_input(heartbeat_request, envelope);
        envelope.set_payload_hash(sha256_hex_for_test(hash_input));
        envelope.set_signature(sign_hex(keypair.pkey, envelope_signing_bytes(envelope)));

        const auto result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            hash_input, keypair.public_key_hex, /*now_unix_s=*/1000.5,
            /*future_issued_tolerance_seconds=*/5.0);
        check(result.valid, "a correctly signed, non-expired heartbeat envelope verifies");
        check(result.rejection_code.empty(), "a valid result carries no rejection_code");

        // Wrong message_type.
        const auto wrong_type_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_CLIENT_RESULT),
            hash_input, keypair.public_key_hex, 1000.5, 5.0);
        check(!wrong_type_result.valid, "a mismatched expected message_type is rejected");
        check(wrong_type_result.rejection_code == "wrong_message_type",
              "message_type mismatch is reported with the stable code wrong_message_type");

        // Tamper with the domain payload (a different current_task_id
        // than what payload_hash was computed over).
        auto tampered_request = heartbeat_request;
        tampered_request.set_current_task_id("task-99");
        const auto tampered_hash_input = heartbeat_payload_hash_input(tampered_request, envelope);
        const auto tampered_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            tampered_hash_input, keypair.public_key_hex, 1000.5, 5.0);
        check(!tampered_result.valid, "a tampered domain payload is rejected");
        check(tampered_result.rejection_code == "payload_hash_mismatch",
              "a tampered payload is reported as payload_hash_mismatch");

        // Tamper with the envelope metadata itself (sequence_number)
        // after signing -- payload_hash still matches (domain payload
        // unchanged) but the signature no longer covers this altered
        // envelope.
        auto tampered_envelope = envelope;
        tampered_envelope.set_sequence_number(999);
        const auto envelope_tamper_result = verify_signed_envelope(
            tampered_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            hash_input, keypair.public_key_hex, 1000.5, 5.0);
        check(!envelope_tamper_result.valid,
              "tampering with envelope metadata after signing is rejected");
        check(envelope_tamper_result.rejection_code == "invalid_signature",
              "envelope metadata tampering is reported as invalid_signature");

        // Expired.
        const auto expired_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            hash_input, keypair.public_key_hex, /*now_unix_s=*/1100.0, 5.0);
        check(!expired_result.valid, "an expired envelope is rejected");
        check(expired_result.rejection_code == "expired", "expiry is reported with the code expired");

        // Future-issued beyond tolerance.
        const auto future_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            hash_input, keypair.public_key_hex, /*now_unix_s=*/990.0,
            /*future_issued_tolerance_seconds=*/5.0);
        check(!future_result.valid,
              "an envelope issued further in the future than the tolerance is rejected");
        check(future_result.rejection_code == "future_issued",
              "excess future-issued skew is reported with the code future_issued");

        // Within future-issued tolerance: now=999.0, issued_at=1000.0,
        // tolerance=5.0 -- issued_at is only 1s ahead of now, inside
        // tolerance, so this must still verify.
        const auto within_tolerance_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            hash_input, keypair.public_key_hex, /*now_unix_s=*/999.0,
            /*future_issued_tolerance_seconds=*/5.0);
        check(within_tolerance_result.valid,
              "an envelope issued only slightly ahead of now, within tolerance, still verifies");

        // Wrong key.
        auto wrong_keypair = generate_ed25519_keypair();
        const auto wrong_key_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_WORKER_HEARTBEAT),
            hash_input, wrong_keypair.public_key_hex, 1000.5, 5.0);
        check(!wrong_key_result.valid, "verifying against the wrong public key is rejected");
        check(wrong_key_result.rejection_code == "invalid_signature",
              "wrong-key rejection is reported as invalid_signature");

        EVP_PKEY_free(keypair.pkey);
        EVP_PKEY_free(wrong_keypair.pkey);
    }

    // -- client_result_payload_hash_input --
    {
        using fl::coordinator::client_result_payload_hash_input;
        using fl::coordinator::v1::SubmitClientResultRequest;

        // Mirrors fl_platform.worker.coordinator_client._tensor_manifests_from_dict's
        // struct.pack(f"<{n}d", *flat) + sha256 exactly -- see
        // signed_envelope_verifier.cpp's tensor_checksum_matches for why
        // this must be a REAL checksum over REAL values now, not a
        // placeholder string.
        auto checksum_for = [](const std::vector<double>& values) {
            std::string packed;
            packed.resize(values.size() * sizeof(double));
            for (std::size_t i = 0; i < values.size(); ++i) {
                std::memcpy(packed.data() + i * sizeof(double), &values[i], sizeof(double));
            }
            return sha256_hex_for_test(packed);
        };

        auto make_request = [&]() {
            SubmitClientResultRequest request;
            request.set_task_id("task-1");
            auto* result = request.mutable_result();
            result->set_run_id("run-1");
            result->set_round_id(3);
            result->set_client_id("client-a");
            result->set_base_model_version("v2");
            result->set_local_step_count(10);
            result->set_sample_count(64);
            result->set_algorithm("fedavg");
            result->set_update_norm(1.25);
            result->set_completion_timestamp("2026-07-25T00:00:00Z");
            result->set_nonce("nonce-xyz");
            result->set_worker_id("worker-1");
            // Deliberately inserted out of alphabetical order -- the
            // hash function must sort by name regardless.
            const std::vector<double> values_b = {0.1, 0.2, 0.3, 0.4};
            auto* tensor_b = result->add_tensor_manifest();
            tensor_b->set_name("layer2.weight");
            tensor_b->add_shape(4);
            tensor_b->set_dtype("float64");
            tensor_b->set_byte_length(static_cast<std::uint64_t>(values_b.size() * sizeof(double)));
            tensor_b->set_checksum(checksum_for(values_b));
            for (const double value : values_b) tensor_b->add_values(value);

            const std::vector<double> values_a = {1.0, 2.0, 3.0, 4.0};
            auto* tensor_a = result->add_tensor_manifest();
            tensor_a->set_name("layer1.weight");
            tensor_a->add_shape(2);
            tensor_a->add_shape(2);
            tensor_a->set_dtype("float64");
            tensor_a->set_byte_length(static_cast<std::uint64_t>(values_a.size() * sizeof(double)));
            tensor_a->set_checksum(checksum_for(values_a));
            for (const double value : values_a) tensor_a->add_values(value);
            return request;
        };

        const auto request = make_request();
        const auto hash_result = client_result_payload_hash_input(request);
        check(hash_result.ok, "a well-formed client result hashes successfully");
        // Canonical tensor ordering: layer1 (sorted first) must appear
        // before layer2 in the hash input, even though it was added to
        // the wire message second.
        check(hash_result.hash_input.find("layer1.weight") <
                  hash_result.hash_input.find("layer2.weight"),
              "tensor manifest entries are canonically sorted by name regardless of wire order");
        check(hash_result.hash_input.find("\"privacy_record\":{}") != std::string::npos,
              "an absent sample_level_privacy canonicalizes to an empty object, not omitted");
        check(hash_result.hash_input.find("\"personalization_metrics\":{}") != std::string::npos,
              "an absent personalization_metrics canonicalizes to an empty object, not omitted");

        // Determinism: hashing the same logical request twice (freshly
        // constructed both times) must produce byte-identical output.
        const auto second_request = make_request();
        const auto second_hash_result = client_result_payload_hash_input(second_request);
        check(hash_result.hash_input == second_hash_result.hash_input,
              "hashing the same logical result twice produces byte-identical canonical output");

        // Tamper: setting a tensor checksum that no longer matches its
        // declared values is now caught directly (not merely "produces
        // a different hash") -- tensor_checksum_matches recomputes the
        // real checksum from `values` and rejects on mismatch.
        auto tampered = make_request();
        tampered.mutable_result()->mutable_tensor_manifest(0)->set_checksum("tampered");
        const auto tampered_hash = client_result_payload_hash_input(tampered);
        check(!tampered_hash.ok,
              "a tensor checksum that doesn't match its declared values is rejected outright");
        check(tampered_hash.hash_input != hash_result.hash_input,
              "changing a tensor checksum changes the canonical hash input (here: to empty, "
              "since the whole result is now rejected)");

        // Tamper with the raw values themselves while leaving the
        // (now stale) checksum untouched -- this is the exact scenario
        // tensor_checksum_matches exists to catch: without it, a
        // signature would only ever cover the checksum *string*, never
        // verify it against the actual floats.
        auto tampered_values = make_request();
        tampered_values.mutable_result()->mutable_tensor_manifest(0)->set_values(0, 999.0);
        const auto tampered_values_hash = client_result_payload_hash_input(tampered_values);
        check(!tampered_values_hash.ok,
              "tampering with raw tensor values while leaving the old checksum in place is "
              "rejected (the checksum is recomputed and compared, not merely trusted)");

        // Tamper: changing sample_count must change the hash input.
        auto tampered_count = make_request();
        tampered_count.mutable_result()->set_sample_count(999);
        const auto tampered_count_hash = client_result_payload_hash_input(tampered_count);
        check(tampered_count_hash.hash_input != hash_result.hash_input,
              "changing sample_count changes the canonical hash input");

        // NaN rejection.
        auto nan_request = make_request();
        nan_request.mutable_result()->set_update_norm(std::nan(""));
        const auto nan_result = client_result_payload_hash_input(nan_request);
        check(!nan_result.ok, "a NaN update_norm is rejected before hashing");

        // Infinity rejection (via a metric value).
        auto inf_request = make_request();
        auto* metric = inf_request.mutable_result()->add_metrics();
        metric->set_name("loss");
        metric->set_value(std::numeric_limits<double>::infinity());
        const auto inf_result = client_result_payload_hash_input(inf_request);
        check(!inf_result.ok, "an infinite metric value is rejected before hashing");

        // Empty tensor name rejection.
        auto empty_name_request = make_request();
        empty_name_request.mutable_result()->mutable_tensor_manifest(0)->set_name("");
        const auto empty_name_result = client_result_payload_hash_input(empty_name_request);
        check(!empty_name_result.ok, "a tensor descriptor with an empty name is rejected");

        // Full sign/verify round trip using the CLIENT_RESULT message type.
        auto keypair = generate_ed25519_keypair();
        fl::worker::v1::SignedWorkerEnvelope envelope;
        envelope.set_schema_version(1);
        envelope.set_message_type(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_CLIENT_RESULT);
        envelope.set_worker_id("worker-1");
        envelope.set_run_id("run-1");
        envelope.set_round_id(3);
        envelope.set_task_id("task-1");
        envelope.set_client_id("client-a");
        envelope.set_model_version("v2");
        envelope.set_message_stream(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_CLIENT_RESULT);
        envelope.set_sequence_number(1);
        envelope.set_issued_at(2000.0);
        envelope.set_expires_at(2060.0);
        envelope.set_nonce("envelope-nonce-1");
        envelope.set_signing_key_id("key-1");
        envelope.set_payload_hash(sha256_hex_for_test(hash_result.hash_input));
        envelope.set_signature(sign_hex(keypair.pkey, envelope_signing_bytes(envelope)));

        const auto verify_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_CLIENT_RESULT),
            hash_result.hash_input, keypair.public_key_hex, /*now_unix_s=*/2000.5, 5.0);
        check(verify_result.valid, "a correctly signed client-result envelope verifies");

        // A tampered result (different canonical hash input) must fail
        // payload_hash verification against the original signature.
        const auto tampered_verify_result = verify_signed_envelope(
            envelope, static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_CLIENT_RESULT),
            tampered_hash.hash_input, keypair.public_key_hex, 2000.5, 5.0);
        check(!tampered_verify_result.valid,
              "verifying a tampered result's hash input against the original envelope fails");
        check(tampered_verify_result.rejection_code == "payload_hash_mismatch",
              "tampered result rejection is reported as payload_hash_mismatch");

        EVP_PKEY_free(keypair.pkey);
    }

    // -- sample_privacy_record_payload_hash_input --
    {
        using fl::coordinator::sample_privacy_record_payload_hash_input;
        using fl::privacy::v1::SignedSamplePrivacyRecord;

        auto make_record = [&]() {
            SignedSamplePrivacyRecord record;
            record.set_schema_version(1);
            record.set_worker_id("worker-1");
            record.set_run_id("run-1");
            record.set_round_id(3);
            record.set_task_id("task-1");
            record.set_client_id("client-a");
            record.set_model_version("v2");
            record.set_algorithm("fedavg");
            record.set_privacy_mode(fl::privacy::v1::PRIVACY_MODE_SAMPLE_LEVEL_DP);
            record.set_accountant_type(fl::privacy::v1::ACCOUNTANT_TYPE_RDP);
            record.set_accountant_step(42);
            record.set_epsilon(0.8);
            record.set_delta(1e-5);
            record.set_noise_multiplier(1.1);
            record.set_max_grad_norm(1.0);
            record.set_sample_rate(0.01);
            record.set_expected_batch_size(64);
            record.set_local_epochs(1);
            record.set_configuration_hash("cfg-hash-abc");
            record.set_accountant_state_hash("state-hash-def");
            record.set_budget_target_epsilon(8.0);
            record.set_budget_target_delta(1e-5);
            record.set_budget_policy(fl::privacy::v1::SAMPLE_BUDGET_POLICY_STOP_BEFORE_EXCEEDING);
            record.set_budget_decision("allowed");
            record.set_secure_random_required(false);
            record.set_secure_random_available(true);
            record.set_secure_random_provider("os_csprng");
            return record;
        };

        const auto record = make_record();
        const auto privacy_hash_result = sample_privacy_record_payload_hash_input(record);
        check(privacy_hash_result.ok, "a well-formed privacy record hashes successfully");
        check(privacy_hash_result.hash_input.find("\"accountant_state_hash\":") <
                  privacy_hash_result.hash_input.find("\"worker_id\":"),
              "privacy record canonical JSON uses alphabetical key order");

        // Cross-language golden fixture (Work Package T): this exact
        // byte string was independently generated by Python's
        // sample_privacy_record_payload_hash_input (signed_envelope.py)
        // for the identical logical record built by make_record() above
        // -- not derived from this C++ implementation. If the two
        // encoders ever disagree (field order, float formatting, bool/
        // int encoding), this check fails even though every other check
        // in this file (which only exercises the C++ side against
        // itself) would still pass.
        const std::string kGoldenPrivacyRecordJson =
            "{\"accountant_state_hash\":\"state-hash-def\",\"accountant_step\":42,"
            "\"accountant_type\":1,\"algorithm\":\"fedavg\",\"budget_decision\":\"allowed\","
            "\"budget_policy\":2,\"budget_target_delta\":1e-05,\"budget_target_epsilon\":8.0,"
            "\"client_id\":\"client-a\",\"configuration_hash\":\"cfg-hash-abc\","
            "\"delta\":1e-05,\"epsilon\":0.8,\"expected_batch_size\":64,\"local_epochs\":1,"
            "\"max_grad_norm\":1.0,\"model_version\":\"v2\",\"noise_multiplier\":1.1,"
            "\"privacy_mode\":2,\"round_id\":3,\"run_id\":\"run-1\",\"sample_rate\":0.01,"
            "\"schema_version\":1,\"secure_random_available\":true,"
            "\"secure_random_provider\":\"os_csprng\",\"secure_random_required\":false,"
            "\"task_id\":\"task-1\",\"worker_id\":\"worker-1\"}";
        check(privacy_hash_result.hash_input == kGoldenPrivacyRecordJson,
              "C++ canonical privacy-record JSON matches Python's independently-generated "
              "golden fixture byte-for-byte");
        check(privacy_hash_result.hash_input.find("\"epsilon\":0.8") != std::string::npos,
              "privacy record canonical JSON includes epsilon");
        check(privacy_hash_result.hash_input.find("\"budget_decision\":\"allowed\"") !=
                  std::string::npos,
              "privacy record canonical JSON includes budget_decision");

        // Secure Hybrid Differential Privacy Runtime slice: same
        // cross-language golden-fixture discipline as
        // kGoldenPrivacyRecordJson above, but with
        // privacy_mode=PRIVACY_MODE_HYBRID_DP (4) -- the value a
        // hybrid-mode worker's sample record carries
        // (coordinator_client.py's
        // _build_signed_sample_privacy_record_payload, is_hybrid=True).
        // This exact byte string is also embedded in
        // test_signed_envelope.py's
        // test_golden_hash_for_hybrid_privacy_mode_matches_the_cross_language_fixture,
        // independently generated by Python, not derived from this C++
        // implementation.
        {
            auto hybrid_record = make_record();
            hybrid_record.set_privacy_mode(fl::privacy::v1::PRIVACY_MODE_HYBRID_DP);
            const auto hybrid_hash_result =
                sample_privacy_record_payload_hash_input(hybrid_record);
            check(hybrid_hash_result.ok, "a well-formed hybrid-mode privacy record hashes successfully");
            const std::string kGoldenHybridPrivacyRecordJson =
                "{\"accountant_state_hash\":\"state-hash-def\",\"accountant_step\":42,"
                "\"accountant_type\":1,\"algorithm\":\"fedavg\",\"budget_decision\":\"allowed\","
                "\"budget_policy\":2,\"budget_target_delta\":1e-05,\"budget_target_epsilon\":8.0,"
                "\"client_id\":\"client-a\",\"configuration_hash\":\"cfg-hash-abc\","
                "\"delta\":1e-05,\"epsilon\":0.8,\"expected_batch_size\":64,\"local_epochs\":1,"
                "\"max_grad_norm\":1.0,\"model_version\":\"v2\",\"noise_multiplier\":1.1,"
                "\"privacy_mode\":4,\"round_id\":3,\"run_id\":\"run-1\",\"sample_rate\":0.01,"
                "\"schema_version\":1,\"secure_random_available\":true,"
                "\"secure_random_provider\":\"os_csprng\",\"secure_random_required\":false,"
                "\"task_id\":\"task-1\",\"worker_id\":\"worker-1\"}";
            check(hybrid_hash_result.hash_input == kGoldenHybridPrivacyRecordJson,
                  "C++ canonical hybrid-mode privacy-record JSON matches Python's "
                  "independently-generated golden fixture byte-for-byte");
        }

        // Determinism.
        const auto second_record = make_record();
        const auto second_hash_result = sample_privacy_record_payload_hash_input(second_record);
        check(privacy_hash_result.hash_input == second_hash_result.hash_input,
              "hashing the same logical privacy record twice produces byte-identical output");

        // Tamper: changing epsilon must change the hash input.
        auto tampered_epsilon = make_record();
        tampered_epsilon.set_epsilon(0.9);
        const auto tampered_epsilon_hash = sample_privacy_record_payload_hash_input(tampered_epsilon);
        check(tampered_epsilon_hash.hash_input != privacy_hash_result.hash_input,
              "changing epsilon changes the canonical privacy-record hash input");

        // Tamper: changing accountant_step must change the hash input.
        auto tampered_step = make_record();
        tampered_step.set_accountant_step(43);
        const auto tampered_step_hash = sample_privacy_record_payload_hash_input(tampered_step);
        check(tampered_step_hash.hash_input != privacy_hash_result.hash_input,
              "changing accountant_step changes the canonical privacy-record hash input");

        // NaN rejection.
        auto nan_record = make_record();
        nan_record.set_epsilon(std::nan(""));
        check(!sample_privacy_record_payload_hash_input(nan_record).ok,
              "a NaN epsilon is rejected before hashing");

        // Negative-value rejection.
        auto negative_record = make_record();
        negative_record.set_epsilon(-0.1);
        check(!sample_privacy_record_payload_hash_input(negative_record).ok,
              "a negative epsilon is rejected before hashing");

        // Full sign/verify round trip using MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD.
        auto keypair = generate_ed25519_keypair();
        fl::worker::v1::SignedWorkerEnvelope privacy_envelope;
        privacy_envelope.set_schema_version(1);
        privacy_envelope.set_message_type(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD);
        privacy_envelope.set_worker_id("worker-1");
        privacy_envelope.set_run_id("run-1");
        privacy_envelope.set_round_id(3);
        privacy_envelope.set_task_id("task-1");
        privacy_envelope.set_client_id("client-a");
        privacy_envelope.set_model_version("v2");
        privacy_envelope.set_message_stream(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_PRIVACY_RECORD);
        privacy_envelope.set_sequence_number(1);
        privacy_envelope.set_issued_at(2000.0);
        privacy_envelope.set_expires_at(2060.0);
        privacy_envelope.set_nonce("privacy-envelope-nonce-1");
        privacy_envelope.set_signing_key_id("key-1");
        privacy_envelope.set_payload_hash(sha256_hex_for_test(privacy_hash_result.hash_input));
        privacy_envelope.set_signature(sign_hex(keypair.pkey, envelope_signing_bytes(privacy_envelope)));

        const auto privacy_verify_result = verify_signed_envelope(
            privacy_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD),
            privacy_hash_result.hash_input, keypair.public_key_hex, /*now_unix_s=*/2000.5, 5.0);
        check(privacy_verify_result.valid, "a correctly signed privacy record envelope verifies");

        const auto tampered_privacy_verify = verify_signed_envelope(
            privacy_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD),
            tampered_epsilon_hash.hash_input, keypair.public_key_hex, 2000.5, 5.0);
        check(!tampered_privacy_verify.valid,
              "a tampered privacy record's hash input fails verification against the original "
              "envelope");
        check(tampered_privacy_verify.rejection_code == "payload_hash_mismatch",
              "tampered privacy record rejection is reported as payload_hash_mismatch");

        EVP_PKEY_free(keypair.pkey);
    }

    // -- client_result_payload_hash_input binds to privacy_record_envelope --
    {
        using fl::coordinator::client_result_payload_hash_input;
        using fl::coordinator::v1::SubmitClientResultRequest;

        SubmitClientResultRequest request;
        request.set_task_id("task-1");
        auto* result = request.mutable_result();
        result->set_run_id("run-1");
        result->set_round_id(3);
        result->set_client_id("client-a");
        result->set_base_model_version("v2");
        result->set_worker_id("worker-1");
        result->set_nonce("nonce-xyz");

        auto* privacy_entry = request.mutable_sample_level_privacy();
        privacy_entry->set_run_id("run-1");
        privacy_entry->set_round_id(3);
        privacy_entry->set_client_id("client-a");
        privacy_entry->set_epsilon(0.8);

        const auto without_envelope_hash = client_result_payload_hash_input(request);
        check(without_envelope_hash.ok,
              "a client result with sample_level_privacy but no privacy_record_envelope still "
              "hashes successfully");
        check(without_envelope_hash.hash_input.find("\"privacy_record_payload_hash\":\"\"") !=
                  std::string::npos,
              "an absent privacy_record_envelope canonicalizes privacy_record_payload_hash to an "
              "empty string");

        request.mutable_privacy_record_envelope()->set_payload_hash("deadbeef");
        const auto with_envelope_hash = client_result_payload_hash_input(request);
        check(with_envelope_hash.ok, "a client result with a privacy_record_envelope hashes successfully");
        check(with_envelope_hash.hash_input.find("\"privacy_record_payload_hash\":\"deadbeef\"") !=
                  std::string::npos,
              "the outer client-result hash binds to the privacy record envelope's payload_hash");
        check(with_envelope_hash.hash_input != without_envelope_hash.hash_input,
              "attaching a privacy_record_envelope changes the outer client-result canonical hash");
    }

    // -- rotation_payload_hash_input --
    {
        using fl::coordinator::rotation_payload_hash_input;
        using fl::worker::v1::WorkerKeyRotationPayload;

        auto make_payload = [] {
            WorkerKeyRotationPayload payload;
            payload.set_schema_version(1);
            payload.set_worker_id("worker-1");
            payload.set_current_signing_key_id("key-1");
            payload.set_new_signing_key_id("key-2");
            payload.set_new_public_key_hex(std::string(64, 'b'));
            payload.set_new_key_expires_at_unix_s(0.0);
            payload.set_requested_grace_period_seconds(3600.0);
            return payload;
        };

        const auto payload = make_payload();
        const auto hash_result = rotation_payload_hash_input(payload);
        check(hash_result.ok, "a well-formed rotation payload hashes successfully");

        // Cross-language golden fixture (Work Package Y): this exact
        // byte string was independently generated by Python's
        // rotation_payload_hash_input (signed_envelope.py) for the
        // identical logical payload built by make_payload() above.
        const std::string kGoldenRotationJson =
            "{\"current_signing_key_id\":\"key-1\",\"new_key_expires_at_unix_s\":0.0,"
            "\"new_public_key_hex\":\"" +
            std::string(64, 'b') +
            "\",\"new_signing_key_id\":\"key-2\",\"requested_grace_period_seconds\":3600.0,"
            "\"schema_version\":1,\"worker_id\":\"worker-1\"}";
        check(hash_result.hash_input == kGoldenRotationJson,
              "C++ canonical rotation-payload JSON matches Python's independently-generated "
              "golden fixture byte-for-byte");

        // Determinism.
        const auto second_hash_result = rotation_payload_hash_input(make_payload());
        check(hash_result.hash_input == second_hash_result.hash_input,
              "hashing the same logical rotation payload twice produces byte-identical output");

        // Tamper: changing new_public_key_hex must change the hash input.
        auto tampered = make_payload();
        tampered.set_new_public_key_hex(std::string(64, 'c'));
        const auto tampered_hash = rotation_payload_hash_input(tampered);
        check(tampered_hash.hash_input != hash_result.hash_input,
              "changing new_public_key_hex changes the canonical rotation-payload hash input");

        // Negative grace period rejection.
        auto negative_grace = make_payload();
        negative_grace.set_requested_grace_period_seconds(-1.0);
        check(!rotation_payload_hash_input(negative_grace).ok,
              "a negative requested_grace_period_seconds is rejected before hashing");

        // Full sign/verify round trip using MESSAGE_TYPE_KEY_ROTATION_REQUEST.
        auto keypair = generate_ed25519_keypair();
        fl::worker::v1::SignedWorkerEnvelope rotation_envelope;
        rotation_envelope.set_schema_version(1);
        rotation_envelope.set_message_type(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_KEY_ROTATION_REQUEST);
        rotation_envelope.set_worker_id("worker-1");
        rotation_envelope.set_message_stream(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_KEY_MANAGEMENT);
        rotation_envelope.set_sequence_number(1);
        rotation_envelope.set_issued_at(3000.0);
        rotation_envelope.set_expires_at(3060.0);
        rotation_envelope.set_nonce("rotation-envelope-nonce-1");
        rotation_envelope.set_signing_key_id("key-1");
        rotation_envelope.set_payload_hash(sha256_hex_for_test(hash_result.hash_input));
        rotation_envelope.set_signature(
            sign_hex(keypair.pkey, envelope_signing_bytes(rotation_envelope)));

        const auto verify_result = verify_signed_envelope(
            rotation_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_KEY_ROTATION_REQUEST),
            hash_result.hash_input, keypair.public_key_hex, /*now_unix_s=*/3000.5, 5.0);
        check(verify_result.valid, "a correctly signed rotation request envelope verifies");

        const auto tampered_verify = verify_signed_envelope(
            rotation_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_KEY_ROTATION_REQUEST),
            tampered_hash.hash_input, keypair.public_key_hex, 3000.5, 5.0);
        check(!tampered_verify.valid,
              "a tampered rotation request's hash input fails verification against the "
              "original envelope");
        check(tampered_verify.rejection_code == "payload_hash_mismatch",
              "tampered rotation request rejection is reported as payload_hash_mismatch");

        EVP_PKEY_free(keypair.pkey);
    }

    // -- security_event_batch_payload_hash_input --
    {
        using fl::coordinator::security_event_batch_payload_hash_input;
        using fl::worker::v1::SignedWorkerSecurityEventBatch;
        using fl::worker::v1::WorkerSecurityEventPayload;

        auto make_batch = [] {
            SignedWorkerSecurityEventBatch batch;
            batch.set_schema_version(1);
            batch.set_worker_id("worker-1");
            batch.set_queue_depth_hint(2);
            auto* event = batch.add_events();
            event->set_schema_version(1);
            event->set_event_type("WORKER_KEY_ROTATION_ACCEPTED");
            event->set_severity("INFO");
            event->set_timestamp("2026-01-01T00:00:00Z");
            event->set_actor_type("WORKER");
            event->set_safe_actor_id("worker-1");
            event->set_subject_type("WORKER_SIGNING_KEY");
            event->set_safe_subject_id("key-2");
            event->set_outcome("ACCEPTED");
            event->set_source_component("signing_key_rotation");
            event->set_safe_signing_key_id("key-2");
            (*event->mutable_safe_details())["previous_key"] = "key-1";
            return batch;
        };

        const auto batch = make_batch();
        const auto hash_result = security_event_batch_payload_hash_input(batch);
        check(hash_result.ok, "a well-formed security-event batch hashes successfully");

        // Cross-language golden fixture: independently generated by
        // Python's security_event_batch_payload_hash_input
        // (signed_envelope.py) for the identical logical batch built by
        // make_batch() above.
        const std::string kGoldenBatchJson =
            "{\"events\":[{\"actor_type\":\"WORKER\",\"event_type\":\"WORKER_KEY_ROTATION_ACCEPTED\","
            "\"outcome\":\"ACCEPTED\",\"reason_code\":\"\",\"request_id\":\"\",\"round_id\":0,"
            "\"run_id\":\"\",\"safe_actor_id\":\"worker-1\",\"safe_details\":{\"previous_key\":"
            "\"key-1\"},\"safe_signing_key_id\":\"key-2\",\"safe_subject_id\":\"key-2\","
            "\"schema_version\":1,\"severity\":\"INFO\",\"source_component\":"
            "\"signing_key_rotation\",\"subject_type\":\"WORKER_SIGNING_KEY\",\"task_id\":\"\","
            "\"timestamp\":\"2026-01-01T00:00:00Z\",\"trace_id\":\"\"}],\"queue_depth_hint\":2,"
            "\"schema_version\":1,\"worker_id\":\"worker-1\"}";
        check(hash_result.hash_input == kGoldenBatchJson,
              "C++ canonical security-event-batch JSON matches Python's independently-generated "
              "golden fixture byte-for-byte");

        // Determinism.
        const auto second_hash_result = security_event_batch_payload_hash_input(make_batch());
        check(hash_result.hash_input == second_hash_result.hash_input,
              "hashing the same logical batch twice produces byte-identical output");

        // Tamper: an added event must change the hash.
        auto tampered = make_batch();
        auto* extra_event = tampered.add_events();
        extra_event->set_event_type("HEARTBEAT_ACCEPTED");
        extra_event->set_severity("INFO");
        extra_event->set_actor_type("WORKER");
        extra_event->set_subject_type("HEARTBEAT");
        extra_event->set_outcome("ACCEPTED");
        const auto tampered_hash = security_event_batch_payload_hash_input(tampered);
        check(tampered_hash.hash_input != hash_result.hash_input,
              "adding an event to the batch changes the canonical hash input");

        // Event order is preserved, not re-sorted (unlike client-result's
        // tensor/metric lists) -- swapping the two events' order changes
        // the hash.
        SignedWorkerSecurityEventBatch reordered;
        reordered.set_schema_version(1);
        reordered.set_worker_id("worker-1");
        reordered.set_queue_depth_hint(2);
        *reordered.add_events() = tampered.events(1);
        *reordered.add_events() = tampered.events(0);
        const auto reordered_hash = security_event_batch_payload_hash_input(reordered);
        check(reordered_hash.hash_input != tampered_hash.hash_input,
              "security-event batch hashing preserves submission order rather than "
              "canonically re-sorting events");

        // Full sign/verify round trip using MESSAGE_TYPE_SECURITY_EVENT_BATCH.
        auto keypair = generate_ed25519_keypair();
        fl::worker::v1::SignedWorkerEnvelope batch_envelope;
        batch_envelope.set_schema_version(1);
        batch_envelope.set_message_type(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURITY_EVENT_BATCH);
        batch_envelope.set_worker_id("worker-1");
        batch_envelope.set_message_stream(
            fl::worker::v1::SignedWorkerEnvelope::MESSAGE_STREAM_SECURITY_EVENTS);
        batch_envelope.set_sequence_number(1);
        batch_envelope.set_issued_at(4000.0);
        batch_envelope.set_expires_at(4060.0);
        batch_envelope.set_nonce("batch-envelope-nonce-1");
        batch_envelope.set_signing_key_id("key-1");
        batch_envelope.set_payload_hash(sha256_hex_for_test(hash_result.hash_input));
        batch_envelope.set_signature(sign_hex(keypair.pkey, envelope_signing_bytes(batch_envelope)));

        const auto verify_result = verify_signed_envelope(
            batch_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURITY_EVENT_BATCH),
            hash_result.hash_input, keypair.public_key_hex, /*now_unix_s=*/4000.5, 5.0);
        check(verify_result.valid, "a correctly signed security-event batch envelope verifies");

        const auto tampered_verify = verify_signed_envelope(
            batch_envelope,
            static_cast<int>(fl::worker::v1::SignedWorkerEnvelope::MESSAGE_TYPE_SECURITY_EVENT_BATCH),
            tampered_hash.hash_input, keypair.public_key_hex, 4000.5, 5.0);
        check(!tampered_verify.valid,
              "a tampered security-event batch's hash input fails verification against the "
              "original envelope");
        check(tampered_verify.rejection_code == "payload_hash_mismatch",
              "tampered security-event batch rejection is reported as payload_hash_mismatch");

        EVP_PKEY_free(keypair.pkey);
    }

    // -- Secure User-Level Differential Privacy Runtime slice: --------
    // user_level_privacy_attestation_payload_hash_input /
    // verify_user_level_privacy_attestation ----------------------------
    {
        using fl::coordinator::user_level_privacy_attestation_payload_hash_input;
        using fl::coordinator::user_level_privacy_attestation_signing_bytes;
        using fl::coordinator::verify_user_level_privacy_attestation;
        using fl::worker::v1::SignedUserLevelPrivacyAttestation;

        auto build_attestation = [](EVP_PKEY* pkey) {
            SignedUserLevelPrivacyAttestation attestation;
            attestation.set_schema_version(1);
            attestation.set_worker_id("worker-1");
            attestation.set_client_id("client-1");
            attestation.set_run_id("run-1");
            attestation.set_round_id(7);
            attestation.set_task_id("task-1");
            attestation.set_session_id("session-1");
            attestation.set_model_version("v1");
            attestation.set_privacy_mode(fl::privacy::v1::PRIVACY_MODE_USER_LEVEL_DP);
            attestation.set_privacy_configuration_hash("config-hash-abc");
            attestation.set_clip_norm(2.5);
            attestation.set_effective_sensitivity(2.500015);
            attestation.set_clipping_strategy("global_l2");
            attestation.set_fixed_weight(1);
            attestation.set_fixed_point_profile_hash("fp-profile-hash");
            attestation.set_tensor_manifest_hash("tensor-manifest-hash");
            attestation.set_provider(fl::worker::v1::SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL);
            attestation.set_operation_completed(true);
            attestation.set_issued_at(1000.0);
            attestation.set_expires_at(1300.0);
            attestation.set_signing_key_id("worker-key-1");
            const auto hash_result = user_level_privacy_attestation_payload_hash_input(attestation);
            attestation.set_payload_hash(sha256_hex_for_test(hash_result.hash_input));
            attestation.set_signature(sign_hex(pkey, user_level_privacy_attestation_signing_bytes(attestation)));
            return attestation;
        };

        auto keypair = generate_ed25519_keypair();
        const auto valid = build_attestation(keypair.pkey);
        const auto valid_result = verify_user_level_privacy_attestation(valid, keypair.public_key_hex, 1100.0);
        check(valid_result.valid, "a genuinely signed user-level privacy attestation verifies successfully");

        // Work Area AC: a cross-language golden fixture -- these exact
        // hex digests are independently hardcoded a second time in
        // python/tests/test_user_level_attestation.py's
        // CrossLanguageGoldenFixtureTests, computed there from the
        // identical field values, without either side reading the
        // other's source. This is exactly the check that would have
        // caught (before ever reaching live Docker validation) the
        // real client_id/clip_norm alphabetical-ordering bug this
        // slice's own live validation actually found: Python's
        // json.dumps(sort_keys=True) silently self-corrected a
        // dict-literal ordering mistake that this file's hand-written
        // (non-self-sorting) JSON construction did not.
        check(valid.payload_hash() == "dccb624cb56e6743ec4823e5bab7c71da234635b7fe9d3056a6878e59309b4c1",
              "golden fixture: payload_hash matches the independently-computed Python value");
        check(sha256_hex_for_test(user_level_privacy_attestation_signing_bytes(valid)) ==
                  "4fcab078d846cfce70d72a59046c9e0d0f385a8ca7961af2c87de56dbd6d9c1d",
              "golden fixture: sha256(signing_bytes) matches the independently-computed Python value");

        auto tampered_clip_norm = valid;
        tampered_clip_norm.set_clip_norm(999.0);
        const auto tampered_result =
            verify_user_level_privacy_attestation(tampered_clip_norm, keypair.public_key_hex, 1100.0);
        check(!tampered_result.valid && tampered_result.rejection_code == "payload_hash_mismatch",
              "tampering with clip_norm after signing is rejected as payload_hash_mismatch");

        auto wrong_keypair = generate_ed25519_keypair();
        const auto wrong_key_result =
            verify_user_level_privacy_attestation(valid, wrong_keypair.public_key_hex, 1100.0);
        check(!wrong_key_result.valid && wrong_key_result.rejection_code == "invalid_signature",
              "verifying against the wrong public key is rejected as invalid_signature");

        const auto expired_result = verify_user_level_privacy_attestation(valid, keypair.public_key_hex, 1400.0);
        check(!expired_result.valid && expired_result.rejection_code == "expired",
              "an attestation past its expires_at is rejected as expired");

        EVP_PKEY_free(keypair.pkey);
        EVP_PKEY_free(wrong_keypair.pkey);
    }

    // -- public_key_fingerprint_hex --
    {
        using fl::coordinator::public_key_fingerprint_hex;
        const std::string key_hex(64, 'a');
        const auto fingerprint = public_key_fingerprint_hex(key_hex);
        check(fingerprint.size() == 64, "public_key_fingerprint_hex returns a sha256 hex digest");
        check(fingerprint == public_key_fingerprint_hex(key_hex),
              "public_key_fingerprint_hex is deterministic for the same input");
        check(public_key_fingerprint_hex(std::string(64, 'b')) != fingerprint,
              "public_key_fingerprint_hex differs for different public keys");
        check(public_key_fingerprint_hex("not-valid-hex").empty(),
              "public_key_fingerprint_hex returns empty for invalid hex input");
    }

    if (g_failures == 0) {
        std::cout << "all signed envelope verifier tests passed\n";
    }
    return g_failures == 0 ? 0 : 1;
}
