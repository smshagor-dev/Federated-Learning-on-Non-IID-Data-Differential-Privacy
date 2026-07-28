#include "fl_coordinator/coordinator_task_signing.hpp"

#include "fl_coordinator/coordinator_signing_identity.hpp"

#include "coordinator/coordinator.pb.h"
#include "worker/worker.pb.h"

#include <openssl/evp.h>

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <sstream>
#include <vector>

namespace fl::coordinator {

namespace {

// Distinct domain-separation prefix per hashed structure
// (docs/task-configuration-hashes.md): each hash's canonical JSON is
// prefixed before hashing so a hash collision between two
// structurally-different hash *types* (e.g. a training-configuration
// hash byte-string vs. a privacy-configuration hash byte-string) can
// never be confused with each other, even though neither is
// independently signed on its own -- only embedded as a field inside
// the one signature the whole SignedCoordinatorTask carries.
constexpr char kDomainSeparationPrefix[] = "FL_PLATFORM_COORDINATOR_TASK_V1\x00";
constexpr std::size_t kDomainSeparationPrefixLength = sizeof(kDomainSeparationPrefix) - 1;
constexpr char kTrainingConfigPrefix[] = "FL_PLATFORM_TRAINING_CONFIG_V1\x00";
constexpr std::size_t kTrainingConfigPrefixLength = sizeof(kTrainingConfigPrefix) - 1;
constexpr char kModelConfigPrefix[] = "FL_PLATFORM_MODEL_CONFIG_V1\x00";
constexpr std::size_t kModelConfigPrefixLength = sizeof(kModelConfigPrefix) - 1;
constexpr char kDatasetPartitionPrefix[] = "FL_PLATFORM_DATASET_PARTITION_V1\x00";
constexpr std::size_t kDatasetPartitionPrefixLength = sizeof(kDatasetPartitionPrefix) - 1;
constexpr char kPrivacyConfigPrefix[] = "FL_PLATFORM_PRIVACY_CONFIG_V1\x00";
constexpr std::size_t kPrivacyConfigPrefixLength = sizeof(kPrivacyConfigPrefix) - 1;
constexpr char kPersonalizationConfigPrefix[] = "FL_PLATFORM_PERSONALIZATION_CONFIG_V1\x00";
constexpr std::size_t kPersonalizationConfigPrefixLength = sizeof(kPersonalizationConfigPrefix) - 1;
constexpr char kTaskPayloadPrefix[] = "FL_PLATFORM_COORDINATOR_TASK_PAYLOAD_V1\x00";
constexpr std::size_t kTaskPayloadPrefixLength = sizeof(kTaskPayloadPrefix) - 1;
constexpr char kSecureAggregationConfigPrefix[] =
    "FL_PLATFORM_SECURE_AGGREGATION_TASK_BINDING_V1\x00";
constexpr std::size_t kSecureAggregationConfigPrefixLength =
    sizeof(kSecureAggregationConfigPrefix) - 1;
constexpr char kSecureUserLevelDpConfigPrefix[] = "FL_PLATFORM_SECURE_USER_LEVEL_DP_CONFIG_V1\x00";
constexpr std::size_t kSecureUserLevelDpConfigPrefixLength =
    sizeof(kSecureUserLevelDpConfigPrefix) - 1;
constexpr char kSecureAdaptiveClippingConfigPrefix[] =
    "FL_PLATFORM_SECURE_ADAPTIVE_CLIPPING_CONFIG_V1\x00";
constexpr std::size_t kSecureAdaptiveClippingConfigPrefixLength =
    sizeof(kSecureAdaptiveClippingConfigPrefix) - 1;

// -- JSON/hash helpers -- deliberately a local copy, matching this
// codebase's established convention (see signed_envelope_verifier.cpp's
// identical header comment) of each file keeping its own small
// serialization helpers rather than a shared utility header.

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

// Unlike signed_envelope_verifier.cpp's identical-looking helper (only
// ever used for Unix-timestamp-magnitude fields, where std::to_chars's
// default format already happens to agree with Python's), this file's
// hash inputs include genuine small-magnitude ML hyperparameters
// (learning rates, noise multipliers, epsilon/delta values as low as
// ~1e-5..1e-10) where std::to_chars's *default* format picks scientific
// notation at a different threshold than Python's float repr does --
// e.g. to_chars(0.0001) with no explicit format produces "1e-04", but
// Python's json.dumps(0.0001) produces "0.0001". Confirmed by direct
// comparison against CPython's repr() (see
// docs/task-configuration-hashes.md): Python uses fixed notation for
// 1e-4 <= |value| < 1e16 (value == 0 counts as fixed) and scientific
// notation otherwise -- this function reproduces that same threshold
// explicitly, rather than trusting to_chars's own (different) choice
// of when to switch formats.
std::string json_double(double value) {
    std::array<char, 64> buffer{};
    const double abs_value = std::fabs(value);
    const bool use_scientific = abs_value != 0.0 && (abs_value < 1e-4 || abs_value >= 1e16);
    const auto format = use_scientific ? std::chars_format::scientific : std::chars_format::fixed;
    const auto result = std::to_chars(buffer.data(), buffer.data() + buffer.size(), value, format);
    std::string text(buffer.data(), result.ptr);
    const bool looks_integral = text.find_first_of(".eEnN") == std::string::npos;
    if (looks_integral) {
        text += ".0";
    }
    return text;
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

std::string json_string_array(const google::protobuf::RepeatedPtrField<std::string>& values) {
    std::string out = "[";
    for (int i = 0; i < values.size(); ++i) {
        if (i > 0)
            out += ",";
        out += json_escape_string(values.Get(i));
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

TaskHashResult make_result(std::string hash_input) {
    TaskHashResult result;
    result.ok = true;
    result.hash_hex = sha256_hex(hash_input);
    result.hash_input = std::move(hash_input);
    return result;
}

TaskHashResult make_error(std::string reason) {
    TaskHashResult result;
    result.ok = false;
    result.reason = std::move(reason);
    return result;
}

}  // namespace

TaskHashResult training_configuration_hash(const fl::coordinator::v1::ClientTrainingTask& task) {
    if (!all_finite(
            {task.learning_rate(), task.momentum(), task.weight_decay(), task.fedprox_mu()})) {
        return make_error("a NaN or infinite value cannot be hashed (training configuration)");
    }
    std::string prefix(kTrainingConfigPrefix, kTrainingConfigPrefixLength);
    std::ostringstream out;
    out << "{";
    out << "\"algorithm\":" << json_escape_string(task.task().algorithm()) << ",";
    out << "\"batch_size\":" << task.batch_size() << ",";
    out << "\"fedprox_mu\":" << json_double(task.fedprox_mu()) << ",";
    out << "\"learning_rate\":" << json_double(task.learning_rate()) << ",";
    out << "\"local_epochs\":" << task.local_epochs() << ",";
    out << "\"momentum\":" << json_double(task.momentum()) << ",";
    out << "\"weight_decay\":" << json_double(task.weight_decay());
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult model_configuration_hash(const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kModelConfigPrefix, kModelConfigPrefixLength);
    std::ostringstream tensors;
    tensors << "[";
    for (int i = 0; i < task.task().model_manifest_size(); ++i) {
        if (i > 0)
            tensors << ",";
        tensors << json_tensor_descriptor(task.task().model_manifest(i));
    }
    tensors << "]";
    const auto& manifest = task.aggregation_manifest();
    std::ostringstream out;
    out << "{";
    out << "\"aggregation_manifest\":{";
    out << "\"frozen_parameter_names\":" << json_string_array(manifest.frozen_parameter_names())
        << ",";
    out << "\"personalized_parameter_names\":"
        << json_string_array(manifest.personalized_parameter_names()) << ",";
    out << "\"schema_hash\":" << json_escape_string(manifest.schema_hash()) << ",";
    out << "\"shared_parameter_names\":" << json_string_array(manifest.shared_parameter_names());
    out << "},";
    out << "\"model_manifest\":" << tensors.str() << ",";
    out << "\"model_version\":" << json_escape_string(task.task().model_version());
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult dataset_partition_hash(const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kDatasetPartitionPrefix, kDatasetPartitionPrefixLength);
    std::ostringstream out;
    out << "{";
    out << "\"client_id\":" << json_escape_string(task.task().client_id()) << ",";
    out << "\"dataset_reference\":" << json_escape_string(task.task().dataset_reference()) << ",";
    out << "\"run_id\":" << json_escape_string(task.task().run_id());
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult privacy_configuration_hash(const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kPrivacyConfigPrefix, kPrivacyConfigPrefixLength);
    // Alphabetical key order (matching every other canonical JSON object
    // in this codebase): accountant, epsilon_budget, max_grad_norm,
    // noise_multiplier, poisson_sampling, sample_budget_policy,
    // sample_level_dp_active, target_delta -- sample_level_dp_active is
    // NOT first alphabetically, unlike its position in the proto/struct
    // field lists.
    if (!task.sample_level_dp_active()) {
        return make_result(prefix + "{\"sample_level_dp_active\":false}");
    }
    const auto& privacy = task.sample_level_privacy();
    if (!all_finite({privacy.noise_multiplier(),
                     privacy.max_grad_norm(),
                     privacy.target_delta(),
                     privacy.epsilon_budget()})) {
        return make_error("a NaN or infinite value cannot be hashed (privacy configuration)");
    }
    std::ostringstream out;
    out << "{";
    out << "\"accountant\":" << static_cast<int>(privacy.accountant()) << ",";
    out << "\"epsilon_budget\":" << json_double(privacy.epsilon_budget()) << ",";
    out << "\"max_grad_norm\":" << json_double(privacy.max_grad_norm()) << ",";
    out << "\"noise_multiplier\":" << json_double(privacy.noise_multiplier()) << ",";
    out << "\"poisson_sampling\":" << (privacy.poisson_sampling() ? "true" : "false") << ",";
    out << "\"sample_budget_policy\":" << static_cast<int>(privacy.sample_budget_policy()) << ",";
    out << "\"sample_level_dp_active\":true,";
    out << "\"target_delta\":" << json_double(privacy.target_delta());
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult personalization_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kPersonalizationConfigPrefix, kPersonalizationConfigPrefixLength);
    const auto& manifest = task.aggregation_manifest();
    std::ostringstream out;
    out << "{";
    out << "\"frozen_parameter_names\":" << json_string_array(manifest.frozen_parameter_names())
        << ",";
    out << "\"personalized_parameter_names\":"
        << json_string_array(manifest.personalized_parameter_names());
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult secure_aggregation_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kSecureAggregationConfigPrefix, kSecureAggregationConfigPrefixLength);
    // Alphabetical key order, matching privacy_configuration_hash's
    // identical convention -- secure_aggregation_active is not first
    // alphabetically either, same reasoning as that function's own
    // comment.
    if (!task.has_secure_aggregation() || !task.secure_aggregation().secure_aggregation_active()) {
        return make_result(prefix + "{\"secure_aggregation_active\":false}");
    }
    const auto& binding = task.secure_aggregation();
    if (!all_finite({binding.key_advertisement_deadline_unix_s(),
                     binding.masked_update_deadline_unix_s(),
                     binding.session_expiry_unix_s(),
                     binding.max_absolute_update_bound()})) {
        return make_error(
            "a NaN or infinite value cannot be hashed (secure aggregation task binding)");
    }
    std::ostringstream out;
    out << "{";
    out << "\"key_advertisement_deadline_unix_s\":"
        << json_double(binding.key_advertisement_deadline_unix_s()) << ",";
    out << "\"masked_update_deadline_unix_s\":"
        << json_double(binding.masked_update_deadline_unix_s()) << ",";
    out << "\"max_absolute_update_bound\":" << json_double(binding.max_absolute_update_bound())
        << ",";
    out << "\"max_aggregate_bound\":" << binding.max_aggregate_bound() << ",";
    out << "\"max_client_weight\":" << binding.max_client_weight() << ",";
    out << "\"minimum_cohort_size\":" << binding.minimum_cohort_size() << ",";
    out << "\"privacy_incompatibility_reason\":"
        << json_escape_string(binding.privacy_incompatibility_reason()) << ",";
    out << "\"privacy_mode_compatible\":" << (binding.privacy_mode_compatible() ? "true" : "false")
        << ",";
    out << "\"protocol_version\":" << binding.protocol_version() << ",";
    out << "\"provider\":" << static_cast<int>(binding.provider()) << ",";
    out << "\"secure_aggregation_active\":true,";
    out << "\"session_configuration_hash\":"
        << json_escape_string(binding.session_configuration_hash()) << ",";
    out << "\"session_expiry_unix_s\":" << json_double(binding.session_expiry_unix_s()) << ",";
    out << "\"session_id\":" << json_escape_string(binding.session_id()) << ",";
    out << "\"tensor_chunk_size\":" << binding.tensor_chunk_size();
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult secure_user_level_dp_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kSecureUserLevelDpConfigPrefix, kSecureUserLevelDpConfigPrefixLength);
    if (!task.has_secure_aggregation() ||
        !task.secure_aggregation().secure_user_level_dp_active()) {
        return make_result(prefix + "{\"secure_user_level_dp_active\":false}");
    }
    const auto& binding = task.secure_aggregation();
    if (!all_finite({binding.secure_user_level_clip_norm(),
                     binding.secure_user_level_quantization_margin(),
                     binding.secure_user_level_effective_sensitivity(),
                     binding.secure_user_level_noise_multiplier(),
                     binding.secure_user_level_target_delta(),
                     binding.secure_user_level_max_epsilon()})) {
        return make_error(
            "a NaN or infinite value cannot be hashed (secure user-level DP configuration)");
    }
    std::ostringstream out;
    out << "{";
    out << "\"secure_user_level_adjacency_model\":"
        << static_cast<int>(binding.secure_user_level_adjacency_model()) << ",";
    out << "\"secure_user_level_clip_norm\":" << json_double(binding.secure_user_level_clip_norm())
        << ",";
    out << "\"secure_user_level_dp_active\":true,";
    out << "\"secure_user_level_effective_sensitivity\":"
        << json_double(binding.secure_user_level_effective_sensitivity()) << ",";
    out << "\"secure_user_level_fixed_weight\":" << binding.secure_user_level_fixed_weight() << ",";
    out << "\"secure_user_level_max_epsilon\":"
        << json_double(binding.secure_user_level_max_epsilon()) << ",";
    out << "\"secure_user_level_noise_multiplier\":"
        << json_double(binding.secure_user_level_noise_multiplier()) << ",";
    out << "\"secure_user_level_quantization_margin\":"
        << json_double(binding.secure_user_level_quantization_margin()) << ",";
    out << "\"secure_user_level_sampling_assumption\":"
        << static_cast<int>(binding.secure_user_level_sampling_assumption()) << ",";
    out << "\"secure_user_level_target_delta\":"
        << json_double(binding.secure_user_level_target_delta());
    out << "}";
    return make_result(prefix + out.str());
}

// Secure Adaptive Clipping with Private Indicator Aggregation slice.
// A third sibling hash, identical structure/discipline to
// secure_user_level_dp_configuration_hash above -- see
// docs/secure-adaptive-clipping-semantics.md section 12.
TaskHashResult secure_adaptive_clipping_configuration_hash(
    const fl::coordinator::v1::ClientTrainingTask& task) {
    std::string prefix(kSecureAdaptiveClippingConfigPrefix,
                       kSecureAdaptiveClippingConfigPrefixLength);
    if (!task.has_secure_aggregation() ||
        !task.secure_aggregation().secure_adaptive_clipping_active()) {
        return make_result(prefix + "{\"secure_adaptive_clipping_active\":false}");
    }
    const auto& binding = task.secure_aggregation();
    if (!all_finite({binding.secure_adaptive_clipping_current_bound(),
                     binding.secure_adaptive_clipping_min_bound(),
                     binding.secure_adaptive_clipping_max_bound(),
                     binding.secure_adaptive_clipping_target_quantile(),
                     binding.secure_adaptive_clipping_learning_rate(),
                     binding.secure_adaptive_clipping_indicator_noise_multiplier()})) {
        return make_error(
            "a NaN or infinite value cannot be hashed (secure adaptive clipping configuration)");
    }
    std::ostringstream out;
    out << "{";
    out << "\"secure_adaptive_clipping_active\":true,";
    out << "\"secure_adaptive_clipping_clip_state_step_count\":"
        << binding.secure_adaptive_clipping_clip_state_step_count() << ",";
    out << "\"secure_adaptive_clipping_current_bound\":"
        << json_double(binding.secure_adaptive_clipping_current_bound()) << ",";
    out << "\"secure_adaptive_clipping_indicator_definition\":"
        << static_cast<int>(binding.secure_adaptive_clipping_indicator_definition()) << ",";
    out << "\"secure_adaptive_clipping_indicator_noise_multiplier\":"
        << json_double(binding.secure_adaptive_clipping_indicator_noise_multiplier()) << ",";
    out << "\"secure_adaptive_clipping_learning_rate\":"
        << json_double(binding.secure_adaptive_clipping_learning_rate()) << ",";
    out << "\"secure_adaptive_clipping_max_bound\":"
        << json_double(binding.secure_adaptive_clipping_max_bound()) << ",";
    out << "\"secure_adaptive_clipping_min_bound\":"
        << json_double(binding.secure_adaptive_clipping_min_bound()) << ",";
    out << "\"secure_adaptive_clipping_target_quantile\":"
        << json_double(binding.secure_adaptive_clipping_target_quantile());
    out << "}";
    return make_result(prefix + out.str());
}

TaskHashResult task_payload_hash(const fl::coordinator::v1::ClientTrainingTask& task) {
    if (!all_finite(
            {task.learning_rate(), task.momentum(), task.weight_decay(), task.fedprox_mu()})) {
        return make_error("a NaN or infinite value cannot be hashed (task payload)");
    }
    std::string prefix(kTaskPayloadPrefix, kTaskPayloadPrefixLength);
    const auto training = training_configuration_hash(task);
    const auto model = model_configuration_hash(task);
    const auto dataset = dataset_partition_hash(task);
    const auto privacy = privacy_configuration_hash(task);
    if (!training.ok || !model.ok || !dataset.ok || !privacy.ok) {
        return make_error("a NaN or infinite value cannot be hashed (task payload)");
    }
    std::ostringstream out;
    out << "{";
    out << "\"attempt\":" << task.attempt() << ",";
    out << "\"dataset_partition_hash\":" << json_escape_string(dataset.hash_hex) << ",";
    out << "\"lease_expires_at\":" << json_escape_string(task.lease_expires_at()) << ",";
    out << "\"lease_id\":" << json_escape_string(task.lease_id()) << ",";
    out << "\"model_configuration_hash\":" << json_escape_string(model.hash_hex) << ",";
    out << "\"privacy_configuration_hash\":" << json_escape_string(privacy.hash_hex) << ",";
    out << "\"round_id\":" << task.task().round_id() << ",";
    out << "\"task_available\":" << (task.task_available() ? "true" : "false") << ",";
    out << "\"task_id\":" << json_escape_string(task.task_id()) << ",";
    out << "\"training_configuration_hash\":" << json_escape_string(training.hash_hex);
    out << "}";
    return make_result(prefix + out.str());
}

std::string coordinator_task_signing_bytes(
    const fl::coordinator::v1::SignedCoordinatorTask& signed_task) {
    std::string bytes(kDomainSeparationPrefix, kDomainSeparationPrefixLength);
    std::ostringstream out;
    out << "{";
    out << "\"attempt\":" << signed_task.attempt() << ",";
    out << "\"coordinator_signing_key_id\":"
        << json_escape_string(signed_task.coordinator_signing_key_id()) << ",";
    out << "\"dataset_partition_hash\":" << json_escape_string(signed_task.dataset_partition_hash())
        << ",";
    out << "\"expires_at\":" << json_double(signed_task.expires_at()) << ",";
    out << "\"issued_at\":" << json_double(signed_task.issued_at()) << ",";
    out << "\"lease_id\":" << json_escape_string(signed_task.lease_id()) << ",";
    out << "\"model_configuration_hash\":"
        << json_escape_string(signed_task.model_configuration_hash()) << ",";
    out << "\"nonce\":" << json_escape_string(signed_task.nonce()) << ",";
    out << "\"personalization_configuration_hash\":"
        << json_escape_string(signed_task.personalization_configuration_hash()) << ",";
    out << "\"privacy_configuration_hash\":"
        << json_escape_string(signed_task.privacy_configuration_hash()) << ",";
    out << "\"schema_version\":" << signed_task.schema_version() << ",";
    out << "\"secure_adaptive_clipping_configuration_hash\":"
        << json_escape_string(signed_task.secure_adaptive_clipping_configuration_hash()) << ",";
    out << "\"secure_aggregation_configuration_hash\":"
        << json_escape_string(signed_task.secure_aggregation_configuration_hash()) << ",";
    out << "\"secure_user_level_dp_configuration_hash\":"
        << json_escape_string(signed_task.secure_user_level_dp_configuration_hash()) << ",";
    out << "\"sequence_number\":" << signed_task.sequence_number() << ",";
    out << "\"task_id\":" << json_escape_string(signed_task.task_id()) << ",";
    out << "\"task_payload_hash\":" << json_escape_string(signed_task.task_payload_hash()) << ",";
    out << "\"training_configuration_hash\":"
        << json_escape_string(signed_task.training_configuration_hash()) << ",";
    out << "\"worker_id\":" << json_escape_string(signed_task.worker_id());
    out << "}";
    bytes += out.str();
    return bytes;
}

SignCoordinatorTaskResult sign_coordinator_task(const fl::coordinator::v1::ClientTrainingTask& task,
                                                const SignCoordinatorTaskParams& params,
                                                const CoordinatorSigningIdentity& identity,
                                                fl::coordinator::v1::SignedCoordinatorTask& out) {
    if (!all_finite({params.issued_at, params.expires_at})) {
        return {false, "a NaN or infinite value cannot be signed (task issuance timestamps)"};
    }
    const auto training = training_configuration_hash(task);
    const auto model = model_configuration_hash(task);
    const auto dataset = dataset_partition_hash(task);
    const auto privacy = privacy_configuration_hash(task);
    const auto personalization = personalization_configuration_hash(task);
    const auto secure_aggregation = secure_aggregation_configuration_hash(task);
    const auto secure_user_level_dp = secure_user_level_dp_configuration_hash(task);
    const auto secure_adaptive_clipping = secure_adaptive_clipping_configuration_hash(task);
    const auto payload = task_payload_hash(task);
    if (!training.ok)
        return {false, training.reason};
    if (!model.ok)
        return {false, model.reason};
    if (!dataset.ok)
        return {false, dataset.reason};
    if (!privacy.ok)
        return {false, privacy.reason};
    if (!personalization.ok)
        return {false, personalization.reason};
    if (!secure_aggregation.ok)
        return {false, secure_aggregation.reason};
    if (!secure_user_level_dp.ok)
        return {false, secure_user_level_dp.reason};
    if (!secure_adaptive_clipping.ok)
        return {false, secure_adaptive_clipping.reason};
    if (!payload.ok)
        return {false, payload.reason};

    out.set_schema_version(kSignedCoordinatorTaskSchemaVersion);
    out.set_coordinator_signing_key_id(identity.key_id);
    out.set_worker_id(params.worker_id);
    out.set_task_id(params.task_id);
    out.set_lease_id(params.lease_id);
    out.set_attempt(params.attempt);
    out.set_issued_at(params.issued_at);
    out.set_expires_at(params.expires_at);
    out.set_nonce(params.nonce);
    out.set_sequence_number(params.sequence_number);
    out.set_training_configuration_hash(training.hash_hex);
    out.set_model_configuration_hash(model.hash_hex);
    out.set_dataset_partition_hash(dataset.hash_hex);
    out.set_privacy_configuration_hash(privacy.hash_hex);
    out.set_personalization_configuration_hash(personalization.hash_hex);
    out.set_secure_aggregation_configuration_hash(secure_aggregation.hash_hex);
    out.set_secure_user_level_dp_configuration_hash(secure_user_level_dp.hash_hex);
    out.set_secure_adaptive_clipping_configuration_hash(secure_adaptive_clipping.hash_hex);
    out.set_task_payload_hash(payload.hash_hex);

    const auto signing_bytes = coordinator_task_signing_bytes(out);
    out.set_signature(sign_with_coordinator_identity(identity, signing_bytes));
    return {true, "ok"};
}

bool verify_coordinator_task_signature(
    const fl::coordinator::v1::SignedCoordinatorTask& signed_task,
    const std::string& signing_public_key_hex) {
    bool key_ok = false;
    const auto public_key_bytes = hex_decode(signing_public_key_hex, key_ok);
    bool signature_ok = false;
    const auto signature_bytes = hex_decode(signed_task.signature(), signature_ok);
    if (!key_ok || !signature_ok) {
        return false;
    }
    return ed25519_verify(
        public_key_bytes, signature_bytes, coordinator_task_signing_bytes(signed_task));
}

}  // namespace fl::coordinator
