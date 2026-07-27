#include "fl_coordinator/trusted_key_bundle.hpp"

#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace fl::coordinator {

namespace {

constexpr std::uint32_t kBundleSchemaVersion = 1;

std::uint64_t fnv1a_hash(const std::string& data) {
    std::uint64_t hash = 1469598103934665603ULL;
    for (const unsigned char byte : data) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string hash_to_hex(std::uint64_t hash) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

std::string json_escape(const std::string& value) {
    std::string out;
    out.reserve(value.size() + 2);
    out += '"';
    for (const char c : value) {
        if (c == '"' || c == '\\') {
            out += '\\';
        }
        out += c;
    }
    out += '"';
    return out;
}

std::string json_double(double value) {
    std::ostringstream out;
    out << std::setprecision(17) << value;
    std::string text = out.str();
    if (text.find_first_of(".eE") == std::string::npos) {
        text += ".0";
    }
    return text;
}

}  // namespace

TrustedKeyBundleError::TrustedKeyBundleError(const std::string& what) : std::runtime_error(what) {}

std::uint64_t read_bundle_version(const std::string& path) {
    if (!std::filesystem::exists(path)) {
        return 0;
    }
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        return 0;
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string content = buffer.str();
    const std::string marker = "\"bundle_version\":";
    const auto pos = content.find(marker);
    if (pos == std::string::npos) {
        return 0;
    }
    std::size_t start = pos + marker.size();
    std::size_t end = start;
    while (end < content.size() && (std::isdigit(static_cast<unsigned char>(content[end])) != 0)) {
        ++end;
    }
    if (end == start) {
        return 0;
    }
    try {
        return std::stoull(content.substr(start, end - start));
    } catch (const std::exception&) {
        return 0;
    }
}

TrustedKeyBundleWriteResult write_trusted_key_bundle(const CoordinatorSigningKeyRegistry& registry,
                                                     const std::string& path,
                                                     const std::string& coordinator_identity_label,
                                                     double now_unix_s) {
    const auto previous_version = read_bundle_version(path);
    const auto new_version = previous_version + 1;
    const auto trusted_keys = registry.trusted_public_keys(now_unix_s);

    std::string active_signing_key_id;
    for (const auto& key : trusted_keys) {
        if (key.status == CoordinatorSigningKeyStatus::kActive) {
            active_signing_key_id = key.signing_key_id;
            break;
        }
    }

    std::ostringstream body;
    body << "{";
    body << "\"schema_version\":" << kBundleSchemaVersion << ",";
    body << "\"coordinator_identity\":" << json_escape(coordinator_identity_label) << ",";
    body << "\"bundle_version\":" << new_version << ",";
    body << "\"generated_at_unix_s\":" << json_double(now_unix_s) << ",";
    body << "\"active_signing_key_id\":" << json_escape(active_signing_key_id) << ",";
    body << "\"keys\":[";
    for (std::size_t i = 0; i < trusted_keys.size(); ++i) {
        if (i > 0) body << ",";
        const auto& key = trusted_keys[i];
        body << "{";
        body << "\"signing_key_id\":" << json_escape(key.signing_key_id) << ",";
        body << "\"public_key_hex\":" << json_escape(key.public_key_hex) << ",";
        body << "\"public_key_fingerprint\":" << json_escape(key.public_key_fingerprint) << ",";
        body << "\"status\":" << json_escape(to_string(key.status)) << ",";
        body << "\"created_at_unix_s\":" << json_double(key.created_at_unix_s) << ",";
        body << "\"expires_at_unix_s\":" << json_double(key.expires_at_unix_s) << ",";
        body << "\"grace_period_end_unix_s\":" << json_double(key.grace_period_end_unix_s) << ",";
        body << "\"revoked_at_unix_s\":" << json_double(key.revoked_at_unix_s);
        body << "}";
    }
    body << "]";
    body << "}";

    const auto body_str = body.str();
    const auto checksum = hash_to_hex(fnv1a_hash(body_str));
    // The checksum field is appended as a sibling key outside the
    // hashed body -- readers must re-derive it the same way (hash
    // everything up to, but not including, the checksum field itself).
    std::string full = body_str.substr(0, body_str.size() - 1);  // drop trailing '}'
    full += ",\"checksum\":" + json_escape(checksum) + "}";

    const std::filesystem::path target(path);
    if (target.has_parent_path()) {
        std::filesystem::create_directories(target.parent_path());
    }
    const auto temp_path = path + ".tmp";
    {
        std::ofstream file(temp_path, std::ios::binary | std::ios::trunc);
        if (!file) {
            return {false, 0, "failed to open trusted-key bundle temp file: " + temp_path};
        }
        file << full;
        file.flush();
        if (!file) {
            return {false, 0, "failed to write trusted-key bundle temp file: " + temp_path};
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            return {false, 0,
                   "failed to atomically move trusted-key bundle into place: " + error_code.message()};
        }
    }
    return {true, new_version, "ok"};
}

}  // namespace fl::coordinator
