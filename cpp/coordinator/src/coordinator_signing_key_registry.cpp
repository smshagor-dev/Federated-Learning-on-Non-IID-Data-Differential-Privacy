#include "fl_coordinator/coordinator_signing_key_registry.hpp"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <vector>

namespace fl::coordinator {

namespace {

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

std::vector<std::string> split(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream(value);
    std::string item;
    while (std::getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

bool is_valid_ed25519_public_key_hex(const std::string& value) {
    if (value.size() != CoordinatorSigningKeyRegistry::kExpectedEd25519PublicKeyHexLength) {
        return false;
    }
    return std::all_of(value.begin(), value.end(),
                       [](unsigned char c) { return std::isxdigit(c) != 0; });
}

std::string encode_record(const CoordinatorSigningKeyRecord& record) {
    std::ostringstream out;
    out << record.schema_version << "\t" << record.signing_key_id << "\t" << record.public_key_hex
        << "\t" << record.public_key_fingerprint << "\t" << to_string(record.status) << "\t"
        << std::setprecision(17) << record.created_at_unix_s << "\t" << record.expires_at_unix_s
        << "\t" << record.grace_period_end_unix_s << "\t" << record.rotated_from_key_id << "\t"
        << record.rotated_to_key_id << "\t" << record.revoked_at_unix_s << "\t"
        << record.revocation_reason;
    return out.str();
}

CoordinatorSigningKeyRecord decode_record(const std::string& line) {
    const auto parts = split(line, '\t');
    if (parts.size() != 12 && parts.size() != 11) {
        throw CoordinatorSigningKeyRegistryError("malformed coordinator signing-key record line");
    }
    CoordinatorSigningKeyRecord record;
    try {
        record.schema_version = static_cast<std::uint32_t>(std::stoul(parts[0]));
        record.signing_key_id = parts[1];
        record.public_key_hex = parts[2];
        record.public_key_fingerprint = parts[3];
        record.status = coordinator_signing_key_status_from_string(parts[4]);
        record.created_at_unix_s = std::stod(parts[5]);
        record.expires_at_unix_s = std::stod(parts[6]);
        record.grace_period_end_unix_s = std::stod(parts[7]);
        record.rotated_from_key_id = parts[8];
        record.rotated_to_key_id = parts[9];
        record.revoked_at_unix_s = std::stod(parts[10]);
        record.revocation_reason = parts.size() == 12 ? parts[11] : std::string();
    } catch (const CoordinatorSigningKeyRegistryError&) {
        throw;
    } catch (const std::exception& error) {
        throw CoordinatorSigningKeyRegistryError(
            std::string("coordinator signing-key record field parse failure: ") + error.what());
    }
    if (record.schema_version != CoordinatorSigningKeyRecord::kSchemaVersion) {
        throw CoordinatorSigningKeyRegistryError(
            "unsupported coordinator signing-key record schema version " +
            std::to_string(record.schema_version));
    }
    return record;
}

}  // namespace

std::string to_string(CoordinatorSigningKeyStatus status) {
    switch (status) {
        case CoordinatorSigningKeyStatus::kActive:
            return "active";
        case CoordinatorSigningKeyStatus::kGracePeriod:
            return "grace_period";
        case CoordinatorSigningKeyStatus::kRevoked:
            return "revoked";
        case CoordinatorSigningKeyStatus::kExpired:
            return "expired";
    }
    return "unknown";
}

CoordinatorSigningKeyStatus coordinator_signing_key_status_from_string(const std::string& value) {
    if (value == "active") return CoordinatorSigningKeyStatus::kActive;
    if (value == "grace_period") return CoordinatorSigningKeyStatus::kGracePeriod;
    if (value == "revoked") return CoordinatorSigningKeyStatus::kRevoked;
    if (value == "expired") return CoordinatorSigningKeyStatus::kExpired;
    throw CoordinatorSigningKeyRegistryError("unknown coordinator signing-key status: " + value);
}

std::string to_string(CoordinatorSigningKeyRotationRejectionReason reason) {
    switch (reason) {
        case CoordinatorSigningKeyRotationRejectionReason::kNone:
            return "none";
        case CoordinatorSigningKeyRotationRejectionReason::kUnknownCurrentKey:
            return "unknown_current_key";
        case CoordinatorSigningKeyRotationRejectionReason::kCurrentKeyNotActive:
            return "current_key_not_active";
        case CoordinatorSigningKeyRotationRejectionReason::kDuplicateNewKeyId:
            return "duplicate_new_key_id";
        case CoordinatorSigningKeyRotationRejectionReason::kDuplicatePublicKey:
            return "duplicate_public_key";
        case CoordinatorSigningKeyRotationRejectionReason::kInvalidKeyLength:
            return "invalid_key_length";
        case CoordinatorSigningKeyRotationRejectionReason::kExcessiveGracePeriod:
            return "excessive_grace_period";
        case CoordinatorSigningKeyRotationRejectionReason::kExcessiveKeyLifetime:
            return "excessive_key_lifetime";
        case CoordinatorSigningKeyRotationRejectionReason::kInvalidExpiry:
            return "invalid_expiry";
    }
    return "unknown";
}

CoordinatorSigningKeyRegistryError::CoordinatorSigningKeyRegistryError(const std::string& what)
    : std::runtime_error(what) {}

CoordinatorSigningKeyRegistry::CoordinatorSigningKeyRegistry(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw CoordinatorSigningKeyRegistryError(
            "failed to open coordinator signing-key registry file: " + persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw CoordinatorSigningKeyRegistryError(
            "coordinator signing-key registry file is truncated or missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    std::string checksum_line = payload.substr(marker + 1);
    const auto equals = checksum_line.find('=');
    std::string checksum_value = equals == std::string::npos ? "" : checksum_line.substr(equals + 1);
    while (!checksum_value.empty() &&
           (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum_value) {
        throw CoordinatorSigningKeyRegistryError(
            "coordinator signing-key registry checksum mismatch: file is corrupt or was truncated");
    }

    std::stringstream stream(body);
    std::string line;
    bool has_count = false;
    std::size_t expected_count = 0;
    std::size_t found_count = 0;
    while (std::getline(stream, line)) {
        if (line.empty()) {
            continue;
        }
        if (line.rfind("record_count=", 0) == 0) {
            expected_count = std::stoull(line.substr(std::string("record_count=").size()));
            has_count = true;
            continue;
        }
        if (line.rfind("record=", 0) == 0) {
            const auto record = decode_record(line.substr(std::string("record=").size()));
            records_.emplace(record.signing_key_id, record);
            ++found_count;
        }
    }
    if (!has_count) {
        throw CoordinatorSigningKeyRegistryError(
            "coordinator signing-key registry file missing record_count");
    }
    if (found_count != expected_count) {
        throw CoordinatorSigningKeyRegistryError(
            "coordinator signing-key registry file truncated: expected " +
            std::to_string(expected_count) + " records, found " + std::to_string(found_count));
    }
}

void CoordinatorSigningKeyRegistry::persist() const {
    std::ostringstream body;
    body << "record_count=" << records_.size() << "\n";
    for (const auto& [key, record] : records_) {
        body << "record=" << encode_record(record) << "\n";
    }
    const auto body_str = body.str();
    std::ostringstream out;
    out << body_str;
    out << "checksum=" << hash_to_hex(fnv1a_hash(body_str)) << "\n";

    const std::filesystem::path target(persistence_path_);
    if (target.has_parent_path()) {
        std::filesystem::create_directories(target.parent_path());
    }
    const auto temp_path = persistence_path_ + ".tmp";
    {
        std::ofstream file(temp_path, std::ios::binary | std::ios::trunc);
        if (!file) {
            throw CoordinatorSigningKeyRegistryError(
                "failed to open coordinator signing-key registry temp file: " + temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw CoordinatorSigningKeyRegistryError(
                "failed to write coordinator signing-key registry temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw CoordinatorSigningKeyRegistryError(
                "failed to atomically move coordinator signing-key registry into place: " +
                error_code.message());
        }
    }
}

CoordinatorSigningKeyRecord CoordinatorSigningKeyRegistry::effective_record(
    const CoordinatorSigningKeyRecord& record, double now_unix_s) const {
    if (record.status == CoordinatorSigningKeyStatus::kActive && record.expires_at_unix_s > 0.0 &&
        now_unix_s >= record.expires_at_unix_s) {
        CoordinatorSigningKeyRecord copy = record;
        copy.status = CoordinatorSigningKeyStatus::kExpired;
        return copy;
    }
    if (record.status == CoordinatorSigningKeyStatus::kGracePeriod &&
        record.grace_period_end_unix_s > 0.0 && now_unix_s >= record.grace_period_end_unix_s) {
        CoordinatorSigningKeyRecord copy = record;
        copy.status = CoordinatorSigningKeyStatus::kExpired;
        return copy;
    }
    return record;
}

CoordinatorSigningKeyRecord CoordinatorSigningKeyRegistry::register_initial_key(
    const InitialCoordinatorSigningKeyRegistration& request) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!is_valid_ed25519_public_key_hex(request.public_key_hex)) {
        throw CoordinatorSigningKeyRegistryError(
            "public_key_hex is not a valid Ed25519 public key encoding");
    }

    const auto existing = records_.find(request.signing_key_id);
    if (existing != records_.end()) {
        if (existing->second.public_key_hex == request.public_key_hex) {
            return existing->second;  // idempotent refresh, no-op
        }
        throw CoordinatorSigningKeyRegistryError(
            "signing_key_id '" + request.signing_key_id +
            "' is already registered with a different public key");
    }

    for (const auto& [other_id, other_record] : records_) {
        if (other_record.public_key_fingerprint == request.public_key_fingerprint) {
            throw CoordinatorSigningKeyRegistryError(
                "public_key_fingerprint is already registered under signing_key_id '" + other_id +
                "'");
        }
        if (effective_record(other_record, request.now_unix_s).status ==
            CoordinatorSigningKeyStatus::kActive) {
            throw CoordinatorSigningKeyRegistryError(
                "the coordinator already has an ACTIVE signing key; use rotation instead of "
                "register_initial_key");
        }
    }

    CoordinatorSigningKeyRecord record;
    record.signing_key_id = request.signing_key_id;
    record.public_key_hex = request.public_key_hex;
    record.public_key_fingerprint = request.public_key_fingerprint;
    record.status = CoordinatorSigningKeyStatus::kActive;
    record.created_at_unix_s = request.now_unix_s;
    record.expires_at_unix_s = request.expires_at_unix_s;
    records_.emplace(request.signing_key_id, record);
    persist();
    return record;
}

CoordinatorSigningKeyRotationResult CoordinatorSigningKeyRegistry::validate_rotation(
    const CoordinatorSigningKeyRotationRequest& request) const {
    std::lock_guard<std::mutex> lock(mutex_);

    const auto current_it = records_.find(request.current_signing_key_id);
    if (current_it == records_.end()) {
        return {false, CoordinatorSigningKeyRotationRejectionReason::kUnknownCurrentKey,
               "unknown current coordinator signing key '" + request.current_signing_key_id + "'"};
    }
    const auto effective_current = effective_record(current_it->second, request.now_unix_s);
    if (effective_current.status != CoordinatorSigningKeyStatus::kActive) {
        return {false, CoordinatorSigningKeyRotationRejectionReason::kCurrentKeyNotActive,
               "current coordinator signing key status is '" + to_string(effective_current.status) +
                   "', not ACTIVE"};
    }

    if (!is_valid_ed25519_public_key_hex(request.new_public_key_hex)) {
        return {false, CoordinatorSigningKeyRotationRejectionReason::kInvalidKeyLength,
               "new_public_key_hex is not a valid Ed25519 public key encoding"};
    }

    for (const auto& [other_id, other_record] : records_) {
        if (other_id == request.new_signing_key_id) {
            return {false, CoordinatorSigningKeyRotationRejectionReason::kDuplicateNewKeyId,
                   "new_signing_key_id '" + request.new_signing_key_id + "' is already registered"};
        }
        if (other_record.public_key_fingerprint == request.new_public_key_fingerprint) {
            return {false, CoordinatorSigningKeyRotationRejectionReason::kDuplicatePublicKey,
                   "the new public key is already registered under a different signing_key_id"};
        }
    }

    if (request.grace_period_seconds < 0.0 || request.grace_period_seconds > kMaxGracePeriodSeconds) {
        return {false, CoordinatorSigningKeyRotationRejectionReason::kExcessiveGracePeriod,
               "requested grace period " + std::to_string(request.grace_period_seconds) +
                   "s exceeds the maximum allowed " + std::to_string(kMaxGracePeriodSeconds) + "s"};
    }

    if (request.new_key_expires_at_unix_s != 0.0 &&
        request.new_key_expires_at_unix_s <= request.now_unix_s) {
        return {false, CoordinatorSigningKeyRotationRejectionReason::kInvalidExpiry,
               "new_key_expires_at_unix_s must be a real time strictly after now, or 0 for "
               "never-expires"};
    }
    if (request.new_key_expires_at_unix_s != 0.0 &&
        request.new_key_expires_at_unix_s - request.now_unix_s > kMaxCoordinatorKeyLifetimeSeconds) {
        return {false, CoordinatorSigningKeyRotationRejectionReason::kExcessiveKeyLifetime,
               "requested key lifetime exceeds the maximum allowed " +
                   std::to_string(kMaxCoordinatorKeyLifetimeSeconds) + "s"};
    }

    return {true, CoordinatorSigningKeyRotationRejectionReason::kNone, "ok", {}, {}};
}

CoordinatorSigningKeyRotationResult CoordinatorSigningKeyRegistry::commit_rotation(
    const CoordinatorSigningKeyRotationRequest& request) {
    std::lock_guard<std::mutex> lock(mutex_);

    const auto current_it = records_.find(request.current_signing_key_id);
    if (current_it == records_.end()) {
        throw CoordinatorSigningKeyRegistryError(
            "commit_rotation called for a current signing key that is not on record -- caller "
            "must call validate_rotation first and only commit when accepted");
    }

    CoordinatorSigningKeyRecord& previous = current_it->second;
    if (request.grace_period_seconds > 0.0) {
        previous.status = CoordinatorSigningKeyStatus::kGracePeriod;
        previous.grace_period_end_unix_s = request.now_unix_s + request.grace_period_seconds;
    } else {
        previous.status = CoordinatorSigningKeyStatus::kExpired;
        previous.grace_period_end_unix_s = request.now_unix_s;
    }
    previous.rotated_to_key_id = request.new_signing_key_id;
    const CoordinatorSigningKeyRecord previous_copy = previous;

    CoordinatorSigningKeyRecord new_record;
    new_record.signing_key_id = request.new_signing_key_id;
    new_record.public_key_hex = request.new_public_key_hex;
    new_record.public_key_fingerprint = request.new_public_key_fingerprint;
    new_record.status = CoordinatorSigningKeyStatus::kActive;
    new_record.created_at_unix_s = request.now_unix_s;
    new_record.expires_at_unix_s = request.new_key_expires_at_unix_s;
    new_record.rotated_from_key_id = request.current_signing_key_id;
    records_[request.new_signing_key_id] = new_record;

    persist();
    return {true, CoordinatorSigningKeyRotationRejectionReason::kNone, "ok", new_record,
           previous_copy};
}

CoordinatorSigningKeyRecord CoordinatorSigningKeyRegistry::revoke_key(
    const std::string& signing_key_id, const std::string& reason, double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(signing_key_id);
    if (it == records_.end()) {
        throw CoordinatorSigningKeyRegistryError("unknown coordinator signing key '" +
                                                 signing_key_id + "'");
    }
    CoordinatorSigningKeyRecord& record = it->second;
    if (record.status == CoordinatorSigningKeyStatus::kRevoked) {
        return record;  // idempotent -- first revocation reason wins
    }
    record.status = CoordinatorSigningKeyStatus::kRevoked;
    record.revoked_at_unix_s = now_unix_s;
    record.revocation_reason = reason;
    persist();
    return record;
}

std::optional<CoordinatorSigningKeyRecord> CoordinatorSigningKeyRegistry::find(
    const std::string& signing_key_id, double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(signing_key_id);
    if (it == records_.end()) {
        return std::nullopt;
    }
    return effective_record(it->second, now_unix_s);
}

std::optional<CoordinatorSigningKeyRecord> CoordinatorSigningKeyRegistry::active_key(
    double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [id, record] : records_) {
        const auto effective = effective_record(record, now_unix_s);
        if (effective.status == CoordinatorSigningKeyStatus::kActive) {
            return effective;
        }
    }
    return std::nullopt;
}

std::vector<CoordinatorSigningKeyRecord> CoordinatorSigningKeyRegistry::trusted_public_keys(
    double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<CoordinatorSigningKeyRecord> result;
    for (const auto& [id, record] : records_) {
        const auto effective = effective_record(record, now_unix_s);
        if (effective.status == CoordinatorSigningKeyStatus::kActive ||
            effective.status == CoordinatorSigningKeyStatus::kGracePeriod) {
            result.push_back(effective);
        }
    }
    return result;
}

std::vector<CoordinatorSigningKeyRecord> CoordinatorSigningKeyRegistry::list(
    double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<CoordinatorSigningKeyRecord> result;
    for (const auto& [id, record] : records_) {
        result.push_back(effective_record(record, now_unix_s));
    }
    return result;
}

std::vector<std::string> CoordinatorSigningKeyRegistry::update_expired_keys(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> transitioned;
    for (auto& [id, record] : records_) {
        const auto effective = effective_record(record, now_unix_s);
        if (effective.status == CoordinatorSigningKeyStatus::kExpired &&
            record.status != CoordinatorSigningKeyStatus::kExpired) {
            record.status = CoordinatorSigningKeyStatus::kExpired;
            transitioned.push_back(id);
        }
    }
    if (!transitioned.empty()) {
        persist();
    }
    return transitioned;
}

}  // namespace fl::coordinator
