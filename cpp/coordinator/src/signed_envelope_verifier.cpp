#include "fl_coordinator/signed_envelope_verifier.hpp"

#include "coordinator/coordinator.pb.h"
#include "worker/worker.pb.h"

#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <sstream>
#include <vector>

namespace fl::coordinator {

namespace {

// Domain separation (docs/canonical-security-serialization.md's
// previously-flagged gap): a null byte can never appear in the ASCII
// text this codebase's canonical JSON encoder produces, so prefixing
// with a human-readable label followed by a null byte cannot be
// confused with legitimate signed content, and cannot collide with any
// other structure's signed bytes (e.g. SignedCapabilityStatement's,
// which uses no prefix at all) even if the same Ed25519 signing key is
// reused across structures.
constexpr char kDomainSeparationPrefix[] = "fl.worker.v1.SignedWorkerEnvelope\x00";
constexpr std::size_t kDomainSeparationPrefixLength = sizeof(kDomainSeparationPrefix) - 1;

// -- JSON helpers -- deliberately a local copy, not shared with
// capability_statement_verifier.cpp, matching this codebase's existing
// convention of each file keeping its own small serialization helpers
// (see e.g. fnv1a_hash/hash_to_hex duplicated across run_manager.cpp,
// checkpoint.cpp, worker_identity_registry.cpp) rather than a shared
// utility header.
void append_u_escape(std::string& out, std::uint32_t code_unit) {
    static constexpr char kHex[] = "0123456789abcdef";
    out += "\\u";
    out += kHex[(code_unit >> 12) & 0xF];
    out += kHex[(code_unit >> 8) & 0xF];
    out += kHex[(code_unit >> 4) & 0xF];
    out += kHex[code_unit & 0xF];
}

std::string json_escape_string(const std::string& utf8) {
    std::string out;
    out.reserve(utf8.size() + 2);
    out += '"';
    std::size_t i = 0;
    while (i < utf8.size()) {
        const unsigned char byte0 = static_cast<unsigned char>(utf8[i]);
        if (byte0 == '"') {
            out += "\\\"";
            ++i;
        } else if (byte0 == '\\') {
            out += "\\\\";
            ++i;
        } else if (byte0 == '\n') {
            out += "\\n";
            ++i;
        } else if (byte0 == '\r') {
            out += "\\r";
            ++i;
        } else if (byte0 == '\t') {
            out += "\\t";
            ++i;
        } else if (byte0 == '\b') {
            out += "\\b";
            ++i;
        } else if (byte0 == '\f') {
            out += "\\f";
            ++i;
        } else if (byte0 < 0x20) {
            append_u_escape(out, byte0);
            ++i;
        } else if (byte0 < 0x80) {
            out += static_cast<char>(byte0);
            ++i;
        } else {
            std::uint32_t code_point = 0;
            int extra_bytes = 0;
            if ((byte0 & 0xE0) == 0xC0) {
                code_point = byte0 & 0x1F;
                extra_bytes = 1;
            } else if ((byte0 & 0xF0) == 0xE0) {
                code_point = byte0 & 0x0F;
                extra_bytes = 2;
            } else if ((byte0 & 0xF8) == 0xF0) {
                code_point = byte0 & 0x07;
                extra_bytes = 3;
            } else {
                append_u_escape(out, byte0);
                ++i;
                continue;
            }
            if (i + static_cast<std::size_t>(extra_bytes) >= utf8.size()) {
                append_u_escape(out, byte0);
                ++i;
                continue;
            }
            bool valid_continuation = true;
            for (int k = 1; k <= extra_bytes; ++k) {
                const unsigned char continuation = static_cast<unsigned char>(utf8[i + k]);
                if ((continuation & 0xC0) != 0x80) {
                    valid_continuation = false;
                    break;
                }
                code_point = (code_point << 6) | (continuation & 0x3F);
            }
            if (!valid_continuation) {
                append_u_escape(out, byte0);
                ++i;
                continue;
            }
            if (code_point > 0xFFFF) {
                const std::uint32_t adjusted = code_point - 0x10000;
                append_u_escape(out, 0xD800 + (adjusted >> 10));
                append_u_escape(out, 0xDC00 + (adjusted & 0x3FF));
            } else {
                append_u_escape(out, code_point);
            }
            i += static_cast<std::size_t>(extra_bytes) + 1;
        }
    }
    out += '"';
    return out;
}

std::string json_double(double value) {
    std::array<char, 64> buffer{};
    const auto result = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value);
    std::string text(buffer.data(), result.ptr);
    const bool looks_integral = text.find_first_of(".eEnN") == std::string::npos;
    if (looks_integral) {
        text += ".0";
    }
    return text;
}

std::vector<std::uint8_t> hex_decode(const std::string& hex, bool& ok) {
    ok = true;
    if (hex.size() % 2 != 0) {
        ok = false;
        return {};
    }
    std::vector<std::uint8_t> bytes;
    bytes.reserve(hex.size() / 2);
    auto nibble = [&](char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        const int high = nibble(hex[i]);
        const int low = nibble(hex[i + 1]);
        if (high < 0 || low < 0) {
            ok = false;
            return {};
        }
        bytes.push_back(static_cast<std::uint8_t>((high << 4) | low));
    }
    return bytes;
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

std::string sha256_hex(const std::string& message) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_len = 0;
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr);
    EVP_DigestUpdate(ctx, message.data(), message.size());
    EVP_DigestFinal_ex(ctx, digest, &digest_len);
    EVP_MD_CTX_free(ctx);
    return hex_encode(digest, digest_len);
}

bool ed25519_verify(const std::vector<std::uint8_t>& public_key,
                    const std::vector<std::uint8_t>& signature,
                    const std::string& message) {
    if (public_key.size() != 32 || signature.size() != 64) {
        return false;
    }
    EVP_PKEY* pkey = EVP_PKEY_new_raw_public_key(
        EVP_PKEY_ED25519, nullptr, public_key.data(), public_key.size());
    if (pkey == nullptr) {
        return false;
    }
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    bool ok = false;
    if (EVP_DigestVerifyInit(ctx, nullptr, nullptr, nullptr, pkey) == 1) {
        ok = EVP_DigestVerify(ctx,
                              signature.data(),
                              signature.size(),
                              reinterpret_cast<const unsigned char*>(message.data()),
                              message.size()) == 1;
    }
    EVP_MD_CTX_free(ctx);
    EVP_PKEY_free(pkey);
    return ok;
}

}  // namespace

std::string canonical_envelope_metadata_json(const fl::worker::v1::SignedWorkerEnvelope& envelope) {
    // Alphabetical key order, matching capability_statement_verifier.cpp's
    // canonical_capability_payload_json convention.
    std::ostringstream out;
    out << "{";
    out << "\"client_id\":" << json_escape_string(envelope.client_id()) << ",";
    out << "\"expires_at\":" << json_double(envelope.expires_at()) << ",";
    out << "\"issued_at\":" << json_double(envelope.issued_at()) << ",";
    out << "\"message_stream\":" << static_cast<int>(envelope.message_stream()) << ",";
    out << "\"message_type\":" << static_cast<int>(envelope.message_type()) << ",";
    out << "\"model_version\":" << json_escape_string(envelope.model_version()) << ",";
    out << "\"nonce\":" << json_escape_string(envelope.nonce()) << ",";
    out << "\"payload_hash\":" << json_escape_string(envelope.payload_hash()) << ",";
    out << "\"round_id\":" << envelope.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(envelope.run_id()) << ",";
    out << "\"schema_version\":" << envelope.schema_version() << ",";
    out << "\"sequence_number\":" << envelope.sequence_number() << ",";
    out << "\"signing_key_id\":" << json_escape_string(envelope.signing_key_id()) << ",";
    out << "\"task_id\":" << json_escape_string(envelope.task_id()) << ",";
    out << "\"worker_id\":" << json_escape_string(envelope.worker_id());
    out << "}";
    return out.str();
}

std::string envelope_signing_bytes(const fl::worker::v1::SignedWorkerEnvelope& envelope) {
    std::string bytes(kDomainSeparationPrefix, kDomainSeparationPrefixLength);
    bytes += canonical_envelope_metadata_json(envelope);
    return bytes;
}

std::string heartbeat_payload_hash_input(const fl::worker::v1::WorkerHeartbeatRequest& request,
                                         const fl::worker::v1::SignedWorkerEnvelope& envelope) {
    std::ostringstream out;
    out << "{";
    out << "\"current_task_id\":" << json_escape_string(request.current_task_id()) << ",";
    out << "\"status\":" << static_cast<int>(request.status()) << ",";
    out << "\"timestamp\":" << json_double(envelope.issued_at()) << ",";
    out << "\"worker_id\":" << json_escape_string(request.worker_id());
    out << "}";
    return out.str();
}

namespace {

bool all_finite(std::initializer_list<double> values) {
    return std::all_of(
        values.begin(), values.end(), [](double value) { return std::isfinite(value); });
}

std::string json_shape_array(const google::protobuf::RepeatedField<std::uint64_t>& shape) {
    std::string out = "[";
    for (int i = 0; i < shape.size(); ++i) {
        if (i > 0)
            out += ",";
        out += std::to_string(shape.Get(i));
    }
    out += "]";
    return out;
}

std::string json_tensor_descriptor(const fl::worker::v1::TensorManifest& tensor) {
    std::ostringstream out;
    out << "{";
    out << "\"byte_length\":" << tensor.byte_length() << ",";
    out << "\"checksum\":" << json_escape_string(tensor.checksum()) << ",";
    out << "\"dtype\":" << json_escape_string(tensor.dtype()) << ",";
    out << "\"name\":" << json_escape_string(tensor.name()) << ",";
    out << "\"shape\":" << json_shape_array(tensor.shape());
    out << "}";
    return out.str();
}

std::string json_client_metric(const fl::worker::v1::ClientMetric& metric) {
    std::ostringstream out;
    out << "{\"name\":" << json_escape_string(metric.name())
        << ",\"value\":" << json_double(metric.value()) << "}";
    return out.str();
}

// Canonical empty representation for the two optional sub-messages
// (docs/payload-hashing.md's "Optional fields need canonical empty
// representations" rule): an empty JSON object, not a missing key and
// not a JSON null -- so a genuinely-absent sub-message can never be
// confused with one whose fields happen to canonicalize to the same
// bytes as some other non-empty state.
std::string json_personalization_metrics(
    const fl::coordinator::v1::SubmitClientResultRequest& request, bool& all_finite_out) {
    if (!request.has_personalization_metrics()) {
        return "{}";
    }
    const auto& metrics = request.personalization_metrics();
    all_finite_out = all_finite_out && all_finite({metrics.global_local_accuracy(),
                                                   metrics.personalized_local_accuracy(),
                                                   metrics.global_local_loss(),
                                                   metrics.personalized_local_loss(),
                                                   metrics.personalized_improvement()});
    std::ostringstream out;
    out << "{";
    out << "\"algorithm\":" << json_escape_string(metrics.algorithm()) << ",";
    out << "\"global_local_accuracy\":" << json_double(metrics.global_local_accuracy()) << ",";
    out << "\"global_local_loss\":" << json_double(metrics.global_local_loss()) << ",";
    out << "\"has_personalized_model\":" << (metrics.has_personalized_model() ? "true" : "false")
        << ",";
    out << "\"personalized_improvement\":" << json_double(metrics.personalized_improvement())
        << ",";
    out << "\"personalized_local_accuracy\":" << json_double(metrics.personalized_local_accuracy())
        << ",";
    out << "\"personalized_local_loss\":" << json_double(metrics.personalized_local_loss()) << ",";
    out << "\"personalized_model_version\":" << metrics.personalized_model_version() << ",";
    out << "\"sample_count\":" << metrics.sample_count();
    out << "}";
    return out.str();
}

// Privacy Record Authenticity slice: an additional field,
// privacy_record_payload_hash, binds the outer Client Result Hash to
// whichever independently-signed SignedSamplePrivacyRecord (if any)
// accompanied this submission -- so a tampered or entirely-missing
// signed privacy record is detectable from the outer signature alone,
// as a second, independent layer on top of the privacy record's own
// signature (see docs/signed-privacy-records.md's "Two independent
// bindings" section). Empty string when no privacy_record_envelope is
// present, matching every other optional field's canonical-empty
// convention. This is purely additive: requests with no
// sample_level_privacy at all still canonicalize the whole object to
// "{}"; existing golden vectors, none of which used a non-empty
// privacy_record, are unaffected.
std::string json_privacy_record(const fl::coordinator::v1::SubmitClientResultRequest& request,
                                bool& all_finite_out) {
    if (!request.has_sample_level_privacy()) {
        return "{}";
    }
    const auto& privacy = request.sample_level_privacy();
    all_finite_out = all_finite_out && all_finite({privacy.epsilon(),
                                                   privacy.delta(),
                                                   privacy.noise_multiplier(),
                                                   privacy.sample_rate()});
    const std::string privacy_record_payload_hash =
        request.has_privacy_record_envelope() ? request.privacy_record_envelope().payload_hash()
                                              : "";
    std::ostringstream out;
    out << "{";
    out << "\"accountant\":" << static_cast<int>(privacy.accountant()) << ",";
    out << "\"client_id\":" << json_escape_string(privacy.client_id()) << ",";
    out << "\"delta\":" << json_double(privacy.delta()) << ",";
    out << "\"entry_id\":" << json_escape_string(privacy.entry_id()) << ",";
    out << "\"epsilon\":" << json_double(privacy.epsilon()) << ",";
    out << "\"noise_multiplier\":" << json_double(privacy.noise_multiplier()) << ",";
    out << "\"privacy_record_payload_hash\":" << json_escape_string(privacy_record_payload_hash)
        << ",";
    out << "\"round_id\":" << privacy.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(privacy.run_id()) << ",";
    out << "\"sample_rate\":" << json_double(privacy.sample_rate()) << ",";
    out << "\"steps\":" << privacy.steps();
    out << "}";
    return out.str();
}

// Recomputes the SHA-256 checksum a tensor descriptor *claims* over its
// own `values` field and compares it, so a payload_hash match actually
// means "these exact values, not just this claimed checksum string" --
// without this, a worker (or an attacker with write access to a
// not-yet-signed request) could tamper with `values` while leaving a
// stale/wrong `checksum` string untouched, and neither the signature
// nor the hash would notice since payload_hash otherwise only ever
// reads the checksum field, never the raw values.
//
// Packs each double as little-endian, matching
// fl_platform.worker.coordinator_client._tensor_manifests_from_dict's
// `struct.pack(f"<{n}d", *flat)` exactly. A raw memcpy of a C++
// `double` produces those same bytes on every platform this project
// targets (x86-64 Docker/CI images, Windows x86-64 development
// machine) -- both little-endian -- so no manual byte-swapping is
// implemented; this is a real, if narrow, portability assumption,
// documented here rather than silently relied upon.
bool tensor_checksum_matches(const fl::worker::v1::TensorManifest& tensor) {
    std::string packed;
    packed.resize(static_cast<std::size_t>(tensor.values_size()) * sizeof(double));
    for (int i = 0; i < tensor.values_size(); ++i) {
        const double value = tensor.values(i);
        std::memcpy(
            packed.data() + static_cast<std::size_t>(i) * sizeof(double), &value, sizeof(double));
    }
    return sha256_hex(packed) == tensor.checksum();
}

}  // namespace

ClientResultPayloadHashResult client_result_payload_hash_input(
    const fl::coordinator::v1::SubmitClientResultRequest& request) {
    const auto& result = request.result();

    bool finite = all_finite({result.update_norm()});
    for (const auto& metric : result.metrics()) {
        finite = finite && all_finite({metric.value()});
    }
    for (const auto& tensor : result.tensor_manifest()) {
        if (tensor.name().empty()) {
            return {false, "", "a tensor descriptor with an empty name cannot be hashed"};
        }
        if (!tensor_checksum_matches(tensor)) {
            return {false,
                    "",
                    "tensor '" + tensor.name() + "' checksum does not match its declared values"};
        }
    }
    const std::string personalization_json = json_personalization_metrics(request, finite);
    const std::string privacy_json = json_privacy_record(request, finite);
    if (!finite) {
        return {false, "", "a NaN or infinite value cannot be hashed (metrics/privacy record)"};
    }

    // Canonical tensor ordering: sorted by name, regardless of wire
    // order (docs/payload-hashing.md's "Tensor ordering must be
    // canonical" rule) -- protects against two semantically identical
    // submissions producing different hashes merely because the sender
    // enumerated a dict/map in a different order.
    std::vector<const fl::worker::v1::TensorManifest*> tensors;
    tensors.reserve(static_cast<std::size_t>(result.tensor_manifest_size()));
    for (const auto& tensor : result.tensor_manifest()) {
        tensors.push_back(&tensor);
    }
    std::sort(tensors.begin(), tensors.end(), [](const auto* a, const auto* b) {
        return a->name() < b->name();
    });

    std::vector<const fl::worker::v1::ClientMetric*> metrics;
    metrics.reserve(static_cast<std::size_t>(result.metrics_size()));
    for (const auto& metric : result.metrics()) {
        metrics.push_back(&metric);
    }
    std::sort(metrics.begin(), metrics.end(), [](const auto* a, const auto* b) {
        return a->name() < b->name();
    });

    std::ostringstream out;
    out << "{";
    out << "\"algorithm\":" << json_escape_string(result.algorithm()) << ",";
    out << "\"client_id\":" << json_escape_string(result.client_id()) << ",";
    out << "\"completion_timestamp\":" << json_escape_string(result.completion_timestamp()) << ",";
    out << "\"model_version\":" << json_escape_string(result.base_model_version()) << ",";
    out << "\"nonce\":" << json_escape_string(result.nonce()) << ",";
    out << "\"personalization_metrics\":" << personalization_json << ",";
    out << "\"privacy_record\":" << privacy_json << ",";
    out << "\"round_id\":" << result.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(result.run_id()) << ",";
    out << "\"sample_count\":" << result.sample_count() << ",";
    out << "\"schema_version\":" << kSignedWorkerEnvelopeSchemaVersion << ",";
    out << "\"step_count\":" << result.local_step_count() << ",";
    out << "\"task_id\":" << json_escape_string(request.task_id()) << ",";
    out << "\"tensor_manifest\":[";
    for (std::size_t i = 0; i < tensors.size(); ++i) {
        if (i > 0)
            out << ",";
        out << json_tensor_descriptor(*tensors[i]);
    }
    out << "],";
    out << "\"training_metrics\":[";
    for (std::size_t i = 0; i < metrics.size(); ++i) {
        if (i > 0)
            out << ",";
        out << json_client_metric(*metrics[i]);
    }
    out << "],";
    out << "\"update_norm\":" << json_double(result.update_norm()) << ",";
    out << "\"worker_id\":" << json_escape_string(result.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

PrivacyRecordPayloadHashResult sample_privacy_record_payload_hash_input(
    const fl::privacy::v1::SignedSamplePrivacyRecord& record) {
    const bool finite = all_finite({record.epsilon(),
                                    record.delta(),
                                    record.noise_multiplier(),
                                    record.max_grad_norm(),
                                    record.sample_rate(),
                                    record.budget_target_epsilon(),
                                    record.budget_target_delta()});
    if (!finite) {
        return {false, "", "a NaN or infinite value cannot be hashed (privacy record)"};
    }
    if (record.epsilon() < 0.0 || record.delta() < 0.0 || record.noise_multiplier() < 0.0 ||
        record.max_grad_norm() < 0.0 || record.sample_rate() < 0.0) {
        return {false,
                "",
                "epsilon/delta/noise_multiplier/max_grad_norm/sample_rate cannot be negative"};
    }

    std::ostringstream out;
    out << "{";
    out << "\"accountant_state_hash\":" << json_escape_string(record.accountant_state_hash())
        << ",";
    out << "\"accountant_step\":" << record.accountant_step() << ",";
    out << "\"accountant_type\":" << static_cast<int>(record.accountant_type()) << ",";
    out << "\"algorithm\":" << json_escape_string(record.algorithm()) << ",";
    out << "\"budget_decision\":" << json_escape_string(record.budget_decision()) << ",";
    out << "\"budget_policy\":" << static_cast<int>(record.budget_policy()) << ",";
    out << "\"budget_target_delta\":" << json_double(record.budget_target_delta()) << ",";
    out << "\"budget_target_epsilon\":" << json_double(record.budget_target_epsilon()) << ",";
    out << "\"client_id\":" << json_escape_string(record.client_id()) << ",";
    out << "\"configuration_hash\":" << json_escape_string(record.configuration_hash()) << ",";
    out << "\"delta\":" << json_double(record.delta()) << ",";
    out << "\"epsilon\":" << json_double(record.epsilon()) << ",";
    out << "\"expected_batch_size\":" << record.expected_batch_size() << ",";
    out << "\"local_epochs\":" << record.local_epochs() << ",";
    out << "\"max_grad_norm\":" << json_double(record.max_grad_norm()) << ",";
    out << "\"model_version\":" << json_escape_string(record.model_version()) << ",";
    out << "\"noise_multiplier\":" << json_double(record.noise_multiplier()) << ",";
    out << "\"privacy_mode\":" << static_cast<int>(record.privacy_mode()) << ",";
    out << "\"round_id\":" << record.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(record.run_id()) << ",";
    out << "\"sample_rate\":" << json_double(record.sample_rate()) << ",";
    out << "\"schema_version\":" << record.schema_version() << ",";
    out << "\"secure_random_available\":" << (record.secure_random_available() ? "true" : "false")
        << ",";
    out << "\"secure_random_provider\":" << json_escape_string(record.secure_random_provider())
        << ",";
    out << "\"secure_random_required\":" << (record.secure_random_required() ? "true" : "false")
        << ",";
    out << "\"task_id\":" << json_escape_string(record.task_id()) << ",";
    out << "\"worker_id\":" << json_escape_string(record.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

EnvelopeVerificationResult verify_signed_envelope(
    const fl::worker::v1::SignedWorkerEnvelope& envelope,
    int expected_message_type,
    const std::string& payload_hash_input,
    const std::string& signing_public_key_hex,
    double now_unix_s,
    double future_issued_tolerance_seconds) {
    if (envelope.schema_version() != kSignedWorkerEnvelopeSchemaVersion) {
        return {false, "unsupported envelope schema_version", "unsupported_schema_version"};
    }
    if (static_cast<int>(envelope.message_type()) != expected_message_type) {
        return {false,
                "envelope message_type does not match the RPC it was sent with",
                "wrong_message_type"};
    }

    const auto recomputed_payload_hash = sha256_hex(payload_hash_input);
    if (recomputed_payload_hash != envelope.payload_hash()) {
        return {false, "payload_hash does not match the domain message", "payload_hash_mismatch"};
    }

    bool key_ok = false;
    const auto public_key_bytes = hex_decode(signing_public_key_hex, key_ok);
    bool signature_ok = false;
    const auto signature_bytes = hex_decode(envelope.signature(), signature_ok);
    if (!key_ok || !signature_ok) {
        return {false, "signing key or signature is not valid hex", "invalid_signature"};
    }
    if (!ed25519_verify(public_key_bytes, signature_bytes, envelope_signing_bytes(envelope))) {
        return {false, "invalid signature", "invalid_signature"};
    }

    if (envelope.expires_at() <= 0.0 || now_unix_s >= envelope.expires_at()) {
        return {false, "envelope has expired", "expired"};
    }
    if (envelope.issued_at() > now_unix_s + future_issued_tolerance_seconds) {
        return {false, "envelope issued_at is too far in the future", "future_issued"};
    }

    return {true, "ok", ""};
}

std::string public_key_fingerprint_hex(const std::string& public_key_hex) {
    bool ok = false;
    const auto bytes = hex_decode(public_key_hex, ok);
    if (!ok) {
        return "";
    }
    std::string raw(bytes.begin(), bytes.end());
    return sha256_hex(raw);
}

RotationPayloadHashResult rotation_payload_hash_input(
    const fl::worker::v1::WorkerKeyRotationPayload& payload) {
    if (!std::isfinite(payload.new_key_expires_at_unix_s()) ||
        !std::isfinite(payload.requested_grace_period_seconds())) {
        return {false, "", "a NaN or infinite value cannot be hashed (rotation payload)"};
    }
    if (payload.requested_grace_period_seconds() < 0.0) {
        return {false, "", "requested_grace_period_seconds cannot be negative"};
    }
    std::ostringstream out;
    out << "{";
    out << "\"current_signing_key_id\":" << json_escape_string(payload.current_signing_key_id())
        << ",";
    out << "\"new_key_expires_at_unix_s\":" << json_double(payload.new_key_expires_at_unix_s())
        << ",";
    out << "\"new_public_key_hex\":" << json_escape_string(payload.new_public_key_hex()) << ",";
    out << "\"new_signing_key_id\":" << json_escape_string(payload.new_signing_key_id()) << ",";
    out << "\"requested_grace_period_seconds\":"
        << json_double(payload.requested_grace_period_seconds()) << ",";
    out << "\"schema_version\":" << payload.schema_version() << ",";
    out << "\"worker_id\":" << json_escape_string(payload.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

SecureAggregationKeyAdvertisementPayloadHashResult
secure_aggregation_key_advertisement_payload_hash_input(
    const fl::worker::v1::SecureAggregationKeyAdvertisement& advertisement) {
    if (!std::isfinite(advertisement.issued_at()) || !std::isfinite(advertisement.expires_at())) {
        return {false,
                "",
                "a NaN or infinite value cannot be hashed (secure aggregation key advertisement)"};
    }
    std::ostringstream out;
    out << "{";
    out << "\"client_id\":" << json_escape_string(advertisement.client_id()) << ",";
    out << "\"ephemeral_public_key_x25519\":"
        << json_escape_string(advertisement.ephemeral_public_key_x25519()) << ",";
    out << "\"expires_at\":" << json_double(advertisement.expires_at()) << ",";
    out << "\"issued_at\":" << json_double(advertisement.issued_at()) << ",";
    out << "\"model_version\":" << json_escape_string(advertisement.model_version()) << ",";
    out << "\"public_key_fingerprint\":"
        << json_escape_string(advertisement.public_key_fingerprint()) << ",";
    out << "\"round_id\":" << advertisement.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(advertisement.run_id()) << ",";
    out << "\"schema_version\":" << advertisement.schema_version() << ",";
    out << "\"session_id\":" << json_escape_string(advertisement.session_id()) << ",";
    out << "\"worker_id\":" << json_escape_string(advertisement.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

MaskedClientUpdatePayloadHashResult masked_client_update_payload_hash_input(
    const fl::worker::v1::MaskedClientUpdate& update) {
    const auto& stats = update.encoding_statistics();
    if (!std::isfinite(update.issued_at()) || !std::isfinite(update.expires_at()) ||
        !std::isfinite(stats.max_quantization_error()) ||
        !std::isfinite(stats.mean_quantization_error())) {
        return {false, "", "a NaN or infinite value cannot be hashed (masked client update)"};
    }
    // Canonical tensor ordering: sorted by tensor_name, not the wire's
    // own (worker-controlled, non-canonical) repeated-field order.
    std::vector<const fl::worker::v1::SecureAggregationMaskedTensor*> sorted_tensors;
    sorted_tensors.reserve(static_cast<std::size_t>(update.masked_tensors_size()));
    for (const auto& tensor : update.masked_tensors()) {
        sorted_tensors.push_back(&tensor);
    }
    std::sort(sorted_tensors.begin(), sorted_tensors.end(), [](const auto* a, const auto* b) {
        return a->tensor_name() < b->tensor_name();
    });

    std::ostringstream out;
    out << "{";
    out << "\"attempt\":" << update.attempt() << ",";
    out << "\"client_id\":" << json_escape_string(update.client_id()) << ",";
    out << "\"cohort_commitment\":" << json_escape_string(update.cohort_commitment()) << ",";
    out << "\"cryptographic_profile_hash\":"
        << json_escape_string(update.cryptographic_profile_hash()) << ",";
    out << "\"encoding_statistics\":{";
    out << "\"max_quantization_error\":" << json_double(stats.max_quantization_error()) << ",";
    out << "\"mean_quantization_error\":" << json_double(stats.mean_quantization_error()) << ",";
    out << "\"total_elements\":" << stats.total_elements();
    out << "},";
    out << "\"expires_at\":" << json_double(update.expires_at()) << ",";
    out << "\"fixed_point_profile_hash\":" << json_escape_string(update.fixed_point_profile_hash())
        << ",";
    out << "\"frozen_roster_payload_hash\":"
        << json_escape_string(update.frozen_roster_payload_hash()) << ",";
    out << "\"issued_at\":" << json_double(update.issued_at()) << ",";
    out << "\"lease_id\":" << json_escape_string(update.lease_id()) << ",";
    // Secure Adaptive Clipping with Private Indicator Aggregation
    // slice: masked_clipping_indicator/_checksum are covered by this
    // outer hash exactly like masked_weight/masked_weight_checksum
    // above -- tampering is caught by the outer envelope signature.
    // adaptive_clipping_binding is deliberately NOT included here,
    // exactly like user_level_attestation is not: it is a separate,
    // self-contained signed structure with its own signature.
    out << "\"masked_clipping_indicator\":" << update.masked_clipping_indicator() << ",";
    out << "\"masked_clipping_indicator_checksum\":"
        << json_escape_string(update.masked_clipping_indicator_checksum()) << ",";
    out << "\"masked_tensors\":[";
    for (std::size_t i = 0; i < sorted_tensors.size(); ++i) {
        const auto* tensor = sorted_tensors[i];
        out << "{\"checksum\":" << json_escape_string(tensor->checksum()) << ",\"masked_values\":[";
        for (int j = 0; j < tensor->masked_values_size(); ++j) {
            if (j != 0) {
                out << ",";
            }
            out << tensor->masked_values(j);
        }
        out << "],\"tensor_name\":" << json_escape_string(tensor->tensor_name()) << "}";
        if (i + 1 != sorted_tensors.size()) {
            out << ",";
        }
    }
    out << "],";
    out << "\"masked_weight\":" << update.masked_weight() << ",";
    out << "\"masked_weight_checksum\":" << json_escape_string(update.masked_weight_checksum())
        << ",";
    out << "\"model_version\":" << json_escape_string(update.model_version()) << ",";
    out << "\"protocol_version\":" << update.protocol_version() << ",";
    out << "\"provider\":" << static_cast<int>(update.provider()) << ",";
    out << "\"round_id\":" << update.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(update.run_id()) << ",";
    out << "\"sample_privacy_record_hash\":"
        << json_escape_string(update.sample_privacy_record_hash()) << ",";
    out << "\"schema_version\":" << update.schema_version() << ",";
    out << "\"session_id\":" << json_escape_string(update.session_id()) << ",";
    out << "\"task_id\":" << json_escape_string(update.task_id()) << ",";
    out << "\"tensor_manifest_hash\":" << json_escape_string(update.tensor_manifest_hash()) << ",";
    out << "\"worker_id\":" << json_escape_string(update.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

UserLevelPrivacyAttestationPayloadHashResult user_level_privacy_attestation_payload_hash_input(
    const fl::worker::v1::SignedUserLevelPrivacyAttestation& attestation) {
    if (!std::isfinite(attestation.issued_at()) || !std::isfinite(attestation.expires_at()) ||
        !std::isfinite(attestation.clip_norm()) ||
        !std::isfinite(attestation.effective_sensitivity())) {
        return {
            false, "", "a NaN or infinite value cannot be hashed (user-level privacy attestation)"};
    }
    std::ostringstream out;
    out << "{";
    out << "\"client_id\":" << json_escape_string(attestation.client_id()) << ",";
    out << "\"clip_norm\":" << json_double(attestation.clip_norm()) << ",";
    out << "\"clipping_strategy\":" << json_escape_string(attestation.clipping_strategy()) << ",";
    out << "\"effective_sensitivity\":" << json_double(attestation.effective_sensitivity()) << ",";
    out << "\"expires_at\":" << json_double(attestation.expires_at()) << ",";
    out << "\"fixed_point_profile_hash\":"
        << json_escape_string(attestation.fixed_point_profile_hash()) << ",";
    out << "\"fixed_weight\":" << attestation.fixed_weight() << ",";
    out << "\"issued_at\":" << json_double(attestation.issued_at()) << ",";
    out << "\"model_version\":" << json_escape_string(attestation.model_version()) << ",";
    out << "\"operation_completed\":" << (attestation.operation_completed() ? "true" : "false")
        << ",";
    out << "\"privacy_configuration_hash\":"
        << json_escape_string(attestation.privacy_configuration_hash()) << ",";
    out << "\"privacy_mode\":" << static_cast<int>(attestation.privacy_mode()) << ",";
    out << "\"provider\":" << static_cast<int>(attestation.provider()) << ",";
    out << "\"round_id\":" << attestation.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(attestation.run_id()) << ",";
    out << "\"schema_version\":" << attestation.schema_version() << ",";
    out << "\"session_id\":" << json_escape_string(attestation.session_id()) << ",";
    out << "\"task_id\":" << json_escape_string(attestation.task_id()) << ",";
    out << "\"tensor_manifest_hash\":" << json_escape_string(attestation.tensor_manifest_hash())
        << ",";
    out << "\"worker_id\":" << json_escape_string(attestation.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

namespace {
constexpr char kUserLevelAttestationSigningPrefix[] =
    "FL_PLATFORM_SECURE_USER_LEVEL_DP_ATTESTATION_V1\x00";
constexpr std::size_t kUserLevelAttestationSigningPrefixLength =
    sizeof(kUserLevelAttestationSigningPrefix) - 1;
}  // namespace

std::string user_level_privacy_attestation_signing_bytes(
    const fl::worker::v1::SignedUserLevelPrivacyAttestation& attestation) {
    std::string bytes(kUserLevelAttestationSigningPrefix, kUserLevelAttestationSigningPrefixLength);
    std::ostringstream out;
    out << "{";
    out << "\"client_id\":" << json_escape_string(attestation.client_id()) << ",";
    out << "\"clip_norm\":" << json_double(attestation.clip_norm()) << ",";
    out << "\"clipping_strategy\":" << json_escape_string(attestation.clipping_strategy()) << ",";
    out << "\"effective_sensitivity\":" << json_double(attestation.effective_sensitivity()) << ",";
    out << "\"expires_at\":" << json_double(attestation.expires_at()) << ",";
    out << "\"fixed_point_profile_hash\":"
        << json_escape_string(attestation.fixed_point_profile_hash()) << ",";
    out << "\"fixed_weight\":" << attestation.fixed_weight() << ",";
    out << "\"issued_at\":" << json_double(attestation.issued_at()) << ",";
    out << "\"model_version\":" << json_escape_string(attestation.model_version()) << ",";
    out << "\"operation_completed\":" << (attestation.operation_completed() ? "true" : "false")
        << ",";
    out << "\"payload_hash\":" << json_escape_string(attestation.payload_hash()) << ",";
    out << "\"privacy_configuration_hash\":"
        << json_escape_string(attestation.privacy_configuration_hash()) << ",";
    out << "\"privacy_mode\":" << static_cast<int>(attestation.privacy_mode()) << ",";
    out << "\"provider\":" << static_cast<int>(attestation.provider()) << ",";
    out << "\"round_id\":" << attestation.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(attestation.run_id()) << ",";
    out << "\"schema_version\":" << attestation.schema_version() << ",";
    out << "\"session_id\":" << json_escape_string(attestation.session_id()) << ",";
    out << "\"signing_key_id\":" << json_escape_string(attestation.signing_key_id()) << ",";
    out << "\"task_id\":" << json_escape_string(attestation.task_id()) << ",";
    out << "\"tensor_manifest_hash\":" << json_escape_string(attestation.tensor_manifest_hash())
        << ",";
    out << "\"worker_id\":" << json_escape_string(attestation.worker_id());
    out << "}";
    bytes += out.str();
    return bytes;
}

EnvelopeVerificationResult verify_user_level_privacy_attestation(
    const fl::worker::v1::SignedUserLevelPrivacyAttestation& attestation,
    const std::string& signing_public_key_hex,
    double now_unix_s) {
    const auto hash_result = user_level_privacy_attestation_payload_hash_input(attestation);
    if (!hash_result.ok) {
        return {false, hash_result.reason, "payload_hash_computation_failed"};
    }
    const auto recomputed_payload_hash = sha256_hex(hash_result.hash_input);
    if (recomputed_payload_hash != attestation.payload_hash()) {
        return {false,
                "payload_hash does not match the attestation's own fields",
                "payload_hash_mismatch"};
    }
    bool key_ok = false;
    const auto public_key_bytes = hex_decode(signing_public_key_hex, key_ok);
    bool signature_ok = false;
    const auto signature_bytes = hex_decode(attestation.signature(), signature_ok);
    if (!key_ok || !signature_ok) {
        return {false, "signing key or signature is not valid hex", "invalid_signature"};
    }
    if (!ed25519_verify(public_key_bytes,
                        signature_bytes,
                        user_level_privacy_attestation_signing_bytes(attestation))) {
        return {false, "invalid signature", "invalid_signature"};
    }
    if (attestation.expires_at() <= 0.0 || now_unix_s >= attestation.expires_at()) {
        return {false, "attestation has expired", "expired"};
    }
    return {true, "ok", ""};
}

// Secure Adaptive Clipping with Private Indicator Aggregation slice.
// Identical structure to the three UserLevelPrivacyAttestation
// functions above, for SignedAdaptiveClippingBinding instead -- see
// docs/secure-adaptive-clipping-semantics.md section 15.
AdaptiveClippingBindingPayloadHashResult adaptive_clipping_binding_payload_hash_input(
    const fl::worker::v1::SignedAdaptiveClippingBinding& binding) {
    if (!std::isfinite(binding.issued_at()) || !std::isfinite(binding.expires_at()) ||
        !std::isfinite(binding.current_clip_bound())) {
        return {false, "", "a NaN or infinite value cannot be hashed (adaptive clipping binding)"};
    }
    std::ostringstream out;
    out << "{";
    out << "\"adaptive_configuration_hash\":"
        << json_escape_string(binding.adaptive_configuration_hash()) << ",";
    out << "\"client_id\":" << json_escape_string(binding.client_id()) << ",";
    out << "\"clip_state_step_count\":" << binding.clip_state_step_count() << ",";
    out << "\"current_clip_bound\":" << json_double(binding.current_clip_bound()) << ",";
    out << "\"expires_at\":" << json_double(binding.expires_at()) << ",";
    out << "\"issued_at\":" << json_double(binding.issued_at()) << ",";
    out << "\"model_version\":" << json_escape_string(binding.model_version()) << ",";
    out << "\"operation_completed\":" << (binding.operation_completed() ? "true" : "false") << ",";
    out << "\"provider\":" << static_cast<int>(binding.provider()) << ",";
    out << "\"round_id\":" << binding.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(binding.run_id()) << ",";
    out << "\"schema_version\":" << binding.schema_version() << ",";
    out << "\"session_id\":" << json_escape_string(binding.session_id()) << ",";
    out << "\"task_id\":" << json_escape_string(binding.task_id()) << ",";
    out << "\"worker_id\":" << json_escape_string(binding.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

namespace {
constexpr char kAdaptiveClippingBindingSigningPrefix[] =
    "FL_PLATFORM_SECURE_ADAPTIVE_CLIPPING_ATTESTATION_V1\x00";
constexpr std::size_t kAdaptiveClippingBindingSigningPrefixLength =
    sizeof(kAdaptiveClippingBindingSigningPrefix) - 1;
}  // namespace

std::string adaptive_clipping_binding_signing_bytes(
    const fl::worker::v1::SignedAdaptiveClippingBinding& binding) {
    std::string bytes(kAdaptiveClippingBindingSigningPrefix,
                      kAdaptiveClippingBindingSigningPrefixLength);
    std::ostringstream out;
    out << "{";
    out << "\"adaptive_configuration_hash\":"
        << json_escape_string(binding.adaptive_configuration_hash()) << ",";
    out << "\"client_id\":" << json_escape_string(binding.client_id()) << ",";
    out << "\"clip_state_step_count\":" << binding.clip_state_step_count() << ",";
    out << "\"current_clip_bound\":" << json_double(binding.current_clip_bound()) << ",";
    out << "\"expires_at\":" << json_double(binding.expires_at()) << ",";
    out << "\"issued_at\":" << json_double(binding.issued_at()) << ",";
    out << "\"model_version\":" << json_escape_string(binding.model_version()) << ",";
    out << "\"operation_completed\":" << (binding.operation_completed() ? "true" : "false") << ",";
    out << "\"payload_hash\":" << json_escape_string(binding.payload_hash()) << ",";
    out << "\"provider\":" << static_cast<int>(binding.provider()) << ",";
    out << "\"round_id\":" << binding.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(binding.run_id()) << ",";
    out << "\"schema_version\":" << binding.schema_version() << ",";
    out << "\"session_id\":" << json_escape_string(binding.session_id()) << ",";
    out << "\"signing_key_id\":" << json_escape_string(binding.signing_key_id()) << ",";
    out << "\"task_id\":" << json_escape_string(binding.task_id()) << ",";
    out << "\"worker_id\":" << json_escape_string(binding.worker_id());
    out << "}";
    bytes += out.str();
    return bytes;
}

EnvelopeVerificationResult verify_adaptive_clipping_binding(
    const fl::worker::v1::SignedAdaptiveClippingBinding& binding,
    const std::string& signing_public_key_hex,
    double now_unix_s) {
    const auto hash_result = adaptive_clipping_binding_payload_hash_input(binding);
    if (!hash_result.ok) {
        return {false, hash_result.reason, "payload_hash_computation_failed"};
    }
    const auto recomputed_payload_hash = sha256_hex(hash_result.hash_input);
    if (recomputed_payload_hash != binding.payload_hash()) {
        return {
            false, "payload_hash does not match the binding's own fields", "payload_hash_mismatch"};
    }
    bool key_ok = false;
    const auto public_key_bytes = hex_decode(signing_public_key_hex, key_ok);
    bool signature_ok = false;
    const auto signature_bytes = hex_decode(binding.signature(), signature_ok);
    if (!key_ok || !signature_ok) {
        return {false, "signing key or signature is not valid hex", "invalid_signature"};
    }
    if (!ed25519_verify(
            public_key_bytes, signature_bytes, adaptive_clipping_binding_signing_bytes(binding))) {
        return {false, "invalid signature", "invalid_signature"};
    }
    if (binding.expires_at() <= 0.0 || now_unix_s >= binding.expires_at()) {
        return {false, "binding has expired", "expired"};
    }
    return {true, "ok", ""};
}

namespace {

std::string json_string_map(const google::protobuf::Map<std::string, std::string>& map) {
    // std::map, not the map's own (unspecified) iteration order --
    // matches SecurityEvent.safe_details' std::map<std::string,std::string>
    // canonicalization in security_event.cpp exactly.
    std::map<std::string, std::string> sorted(map.begin(), map.end());
    std::string out = "{";
    bool first = true;
    for (const auto& [key, value] : sorted) {
        if (!first)
            out += ",";
        first = false;
        out += json_escape_string(key);
        out += ":";
        out += json_escape_string(value);
    }
    out += "}";
    return out;
}

std::string json_worker_security_event_payload(
    const fl::worker::v1::WorkerSecurityEventPayload& event) {
    std::ostringstream out;
    out << "{";
    out << "\"actor_type\":" << json_escape_string(event.actor_type()) << ",";
    out << "\"event_type\":" << json_escape_string(event.event_type()) << ",";
    out << "\"outcome\":" << json_escape_string(event.outcome()) << ",";
    out << "\"reason_code\":" << json_escape_string(event.reason_code()) << ",";
    out << "\"request_id\":" << json_escape_string(event.request_id()) << ",";
    out << "\"round_id\":" << event.round_id() << ",";
    out << "\"run_id\":" << json_escape_string(event.run_id()) << ",";
    out << "\"safe_actor_id\":" << json_escape_string(event.safe_actor_id()) << ",";
    out << "\"safe_details\":" << json_string_map(event.safe_details()) << ",";
    out << "\"safe_signing_key_id\":" << json_escape_string(event.safe_signing_key_id()) << ",";
    out << "\"safe_subject_id\":" << json_escape_string(event.safe_subject_id()) << ",";
    out << "\"schema_version\":" << event.schema_version() << ",";
    out << "\"severity\":" << json_escape_string(event.severity()) << ",";
    out << "\"source_component\":" << json_escape_string(event.source_component()) << ",";
    out << "\"subject_type\":" << json_escape_string(event.subject_type()) << ",";
    out << "\"task_id\":" << json_escape_string(event.task_id()) << ",";
    out << "\"timestamp\":" << json_escape_string(event.timestamp()) << ",";
    out << "\"trace_id\":" << json_escape_string(event.trace_id());
    out << "}";
    return out.str();
}

}  // namespace

SecurityEventBatchPayloadHashResult security_event_batch_payload_hash_input(
    const fl::worker::v1::SignedWorkerSecurityEventBatch& batch) {
    std::ostringstream out;
    out << "{";
    out << "\"events\":[";
    for (int i = 0; i < batch.events_size(); ++i) {
        if (i > 0)
            out << ",";
        out << json_worker_security_event_payload(batch.events(i));
    }
    out << "],";
    out << "\"queue_depth_hint\":" << batch.queue_depth_hint() << ",";
    out << "\"schema_version\":" << batch.schema_version() << ",";
    out << "\"worker_id\":" << json_escape_string(batch.worker_id());
    out << "}";
    return {true, out.str(), ""};
}

}  // namespace fl::coordinator
