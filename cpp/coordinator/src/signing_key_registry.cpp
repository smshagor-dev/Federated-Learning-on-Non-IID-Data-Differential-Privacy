#include "fl_coordinator/signing_key_registry.hpp"

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
    if (value.size() != SigningKeyRegistry::kExpectedEd25519PublicKeyHexLength) {
        return false;
    }
    return std::all_of(
        value.begin(), value.end(), [](unsigned char c) { return std::isxdigit(c) != 0; });
}

std::string encode_record(const SigningKeyRecord& record) {
    std::ostringstream out;
    out << record.schema_version << "\t" << record.worker_id << "\t" << record.signing_key_id
        << "\t" << record.public_key_hex << "\t" << record.public_key_fingerprint << "\t"
        << to_string(record.status) << "\t" << std::setprecision(17) << record.created_at_unix_s
        << "\t" << record.activated_at_unix_s << "\t" << record.expires_at_unix_s << "\t"
        << record.grace_period_start_unix_s << "\t" << record.grace_period_end_unix_s << "\t"
        << record.rotated_from_key_id << "\t" << record.rotated_to_key_id << "\t"
        << record.revoked_at_unix_s << "\t" << record.revocation_reason << "\t"
        << record.registration_source;
    return out.str();
}

SigningKeyRecord decode_record(const std::string& line) {
    const auto parts = split(line, '\t');
    // registration_source (the final field) is always non-empty in
    // practice, but split() drops a trailing empty field after the
    // final delimiter regardless -- accept both 16 and 15 for the same
    // reason worker_identity_registry.cpp's decode_record does.
    if (parts.size() != 16 && parts.size() != 15) {
        throw SigningKeyRegistryError("malformed signing-key record line");
    }
    SigningKeyRecord record;
    try {
        record.schema_version = static_cast<std::uint32_t>(std::stoul(parts[0]));
        record.worker_id = parts[1];
        record.signing_key_id = parts[2];
        record.public_key_hex = parts[3];
        record.public_key_fingerprint = parts[4];
        record.status = signing_key_status_from_string(parts[5]);
        record.created_at_unix_s = std::stod(parts[6]);
        record.activated_at_unix_s = std::stod(parts[7]);
        record.expires_at_unix_s = std::stod(parts[8]);
        record.grace_period_start_unix_s = std::stod(parts[9]);
        record.grace_period_end_unix_s = std::stod(parts[10]);
        record.rotated_from_key_id = parts[11];
        record.rotated_to_key_id = parts[12];
        record.revoked_at_unix_s = std::stod(parts[13]);
        record.revocation_reason = parts[14];
        record.registration_source = parts.size() == 16 ? parts[15] : std::string();
    } catch (const SigningKeyRegistryError&) {
        throw;
    } catch (const std::exception& error) {
        throw SigningKeyRegistryError(std::string("signing-key record field parse failure: ") +
                                      error.what());
    }
    if (record.schema_version != SigningKeyRecord::kSchemaVersion) {
        throw SigningKeyRegistryError("unsupported signing-key record schema version " +
                                      std::to_string(record.schema_version));
    }
    return record;
}

}  // namespace

std::string to_string(SigningKeyStatus status) {
    switch (status) {
        case SigningKeyStatus::kPending:
            return "pending";
        case SigningKeyStatus::kActive:
            return "active";
        case SigningKeyStatus::kGracePeriod:
            return "grace_period";
        case SigningKeyStatus::kRevoked:
            return "revoked";
        case SigningKeyStatus::kExpired:
            return "expired";
    }
    return "unknown";
}

SigningKeyStatus signing_key_status_from_string(const std::string& value) {
    if (value == "pending")
        return SigningKeyStatus::kPending;
    if (value == "active")
        return SigningKeyStatus::kActive;
    if (value == "grace_period")
        return SigningKeyStatus::kGracePeriod;
    if (value == "revoked")
        return SigningKeyStatus::kRevoked;
    if (value == "expired")
        return SigningKeyStatus::kExpired;
    throw SigningKeyRegistryError("unknown signing-key status: " + value);
}

std::string to_string(SigningKeyRotationRejectionReason reason) {
    switch (reason) {
        case SigningKeyRotationRejectionReason::kNone:
            return "none";
        case SigningKeyRotationRejectionReason::kUnknownCurrentKey:
            return "unknown_current_key";
        case SigningKeyRotationRejectionReason::kCurrentKeyNotActive:
            return "current_key_not_active";
        case SigningKeyRotationRejectionReason::kDuplicateNewKeyId:
            return "duplicate_new_key_id";
        case SigningKeyRotationRejectionReason::kDuplicatePublicKey:
            return "duplicate_public_key";
        case SigningKeyRotationRejectionReason::kInvalidKeyLength:
            return "invalid_key_length";
        case SigningKeyRotationRejectionReason::kExcessiveGracePeriod:
            return "excessive_grace_period";
        case SigningKeyRotationRejectionReason::kInvalidExpiry:
            return "invalid_expiry";
        case SigningKeyRotationRejectionReason::kMaxActiveKeysExceeded:
            return "max_active_keys_exceeded";
    }
    return "unknown";
}

SigningKeyRegistryError::SigningKeyRegistryError(const std::string& what)
    : std::runtime_error(what) {}

SigningKeyRegistry::SigningKeyRegistry(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw SigningKeyRegistryError("failed to open signing-key registry file: " +
                                      persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw SigningKeyRegistryError("signing-key registry file is truncated or missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    std::string checksum_line = payload.substr(marker + 1);
    const auto equals = checksum_line.find('=');
    std::string checksum_value =
        equals == std::string::npos ? "" : checksum_line.substr(equals + 1);
    while (!checksum_value.empty() &&
           (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum_value) {
        throw SigningKeyRegistryError(
            "signing-key registry checksum mismatch: file is corrupt or was truncated");
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
            records_.emplace(Key{record.worker_id, record.signing_key_id}, record);
            ++found_count;
        }
    }
    if (!has_count) {
        throw SigningKeyRegistryError("signing-key registry file missing record_count");
    }
    if (found_count != expected_count) {
        throw SigningKeyRegistryError("signing-key registry file truncated: expected " +
                                      std::to_string(expected_count) + " records, found " +
                                      std::to_string(found_count));
    }
}

void SigningKeyRegistry::persist() const {
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
            throw SigningKeyRegistryError("failed to open signing-key registry temp file: " +
                                          temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw SigningKeyRegistryError("failed to write signing-key registry temp file: " +
                                          temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw SigningKeyRegistryError(
                "failed to atomically move signing-key registry into place: " +
                error_code.message());
        }
    }
}

SigningKeyRecord SigningKeyRegistry::effective_record(const SigningKeyRecord& record,
                                                      double now_unix_s) const {
    if (record.status == SigningKeyStatus::kActive && record.expires_at_unix_s > 0.0 &&
        now_unix_s >= record.expires_at_unix_s) {
        SigningKeyRecord copy = record;
        copy.status = SigningKeyStatus::kExpired;
        return copy;
    }
    if (record.status == SigningKeyStatus::kGracePeriod && record.grace_period_end_unix_s > 0.0 &&
        now_unix_s >= record.grace_period_end_unix_s) {
        SigningKeyRecord copy = record;
        copy.status = SigningKeyStatus::kExpired;
        return copy;
    }
    return record;
}

SigningKeyRecord SigningKeyRegistry::register_initial_key(
    const InitialSigningKeyRegistration& request) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (!is_valid_ed25519_public_key_hex(request.public_key_hex)) {
        throw SigningKeyRegistryError("public_key_hex is not a valid Ed25519 public key encoding");
    }

    const Key key{request.worker_id, request.signing_key_id};
    const auto existing = records_.find(key);
    if (existing != records_.end()) {
        if (existing->second.public_key_hex == request.public_key_hex) {
            return existing->second;  // idempotent refresh, no-op
        }
        throw SigningKeyRegistryError("signing_key_id '" + request.signing_key_id +
                                      "' is already registered for worker_id '" +
                                      request.worker_id + "' with a different public key");
    }

    for (const auto& [other_key, other_record] : records_) {
        if (other_key.signing_key_id == request.signing_key_id &&
            other_key.worker_id != request.worker_id) {
            throw SigningKeyRegistryError("signing_key_id '" + request.signing_key_id +
                                          "' is already registered for a different worker_id ('" +
                                          other_key.worker_id + "')");
        }
        if (other_record.public_key_fingerprint == request.public_key_fingerprint &&
            other_key.worker_id != request.worker_id) {
            throw SigningKeyRegistryError(
                "public_key_fingerprint is already registered for a different worker_id ('" +
                other_key.worker_id + "')");
        }
        if (other_key.worker_id == request.worker_id &&
            effective_record(other_record, request.now_unix_s).status ==
                SigningKeyStatus::kActive) {
            throw SigningKeyRegistryError(
                "worker_id '" + request.worker_id +
                "' already has an ACTIVE signing key; use rotate_key (validate_rotation/"
                "commit_rotation) instead of register_initial_key");
        }
    }

    SigningKeyRecord record;
    record.worker_id = request.worker_id;
    record.signing_key_id = request.signing_key_id;
    record.public_key_hex = request.public_key_hex;
    record.public_key_fingerprint = request.public_key_fingerprint;
    record.status = SigningKeyStatus::kActive;
    record.created_at_unix_s = request.now_unix_s;
    record.activated_at_unix_s = request.now_unix_s;
    record.expires_at_unix_s = request.expires_at_unix_s;
    record.registration_source = request.registration_source;
    records_.emplace(key, record);
    persist();
    return record;
}

SigningKeyRotationResult SigningKeyRegistry::validate_rotation(
    const SigningKeyRotationRequest& request) const {
    std::lock_guard<std::mutex> lock(mutex_);

    const auto current_it = records_.find(Key{request.worker_id, request.current_signing_key_id});
    if (current_it == records_.end()) {
        return {false,
                SigningKeyRotationRejectionReason::kUnknownCurrentKey,
                "unknown current signing key '" + request.current_signing_key_id +
                    "' for worker_id '" + request.worker_id + "'"};
    }
    const auto effective_current = effective_record(current_it->second, request.now_unix_s);
    if (effective_current.status != SigningKeyStatus::kActive) {
        return {false,
                SigningKeyRotationRejectionReason::kCurrentKeyNotActive,
                "current signing key status is '" + to_string(effective_current.status) +
                    "', not ACTIVE; only an ACTIVE key may authorize a rotation"};
    }

    if (!is_valid_ed25519_public_key_hex(request.new_public_key_hex)) {
        return {false,
                SigningKeyRotationRejectionReason::kInvalidKeyLength,
                "new_public_key_hex is not a valid Ed25519 public key encoding"};
    }

    for (const auto& [other_key, other_record] : records_) {
        if (other_key.signing_key_id == request.new_signing_key_id) {
            return {
                false,
                SigningKeyRotationRejectionReason::kDuplicateNewKeyId,
                "new_signing_key_id '" + request.new_signing_key_id + "' is already registered"};
        }
        if (other_record.public_key_fingerprint == request.new_public_key_fingerprint) {
            return {false,
                    SigningKeyRotationRejectionReason::kDuplicatePublicKey,
                    "the new public key is already registered under a different signing_key_id"};
        }
    }

    if (request.grace_period_seconds < 0.0 ||
        request.grace_period_seconds > kMaxGracePeriodSeconds) {
        return {false,
                SigningKeyRotationRejectionReason::kExcessiveGracePeriod,
                "requested grace period " + std::to_string(request.grace_period_seconds) +
                    "s exceeds the maximum allowed " + std::to_string(kMaxGracePeriodSeconds) +
                    "s"};
    }

    if (request.new_key_expires_at_unix_s != 0.0 &&
        request.new_key_expires_at_unix_s <= request.now_unix_s) {
        return {false,
                SigningKeyRotationRejectionReason::kInvalidExpiry,
                "new_key_expires_at_unix_s must be a real time strictly after now, or 0 for "
                "never-expires"};
    }

    for (const auto& [other_key, other_record] : records_) {
        if (other_key.worker_id == request.worker_id &&
            other_key.signing_key_id != request.current_signing_key_id &&
            effective_record(other_record, request.now_unix_s).status ==
                SigningKeyStatus::kActive) {
            return {false,
                    SigningKeyRotationRejectionReason::kMaxActiveKeysExceeded,
                    "worker_id '" + request.worker_id +
                        "' unexpectedly already has a second ACTIVE key on record"};
        }
    }

    return {true, SigningKeyRotationRejectionReason::kNone, "ok", {}, {}};
}

SigningKeyRotationResult SigningKeyRegistry::commit_rotation(
    const SigningKeyRotationRequest& request) {
    std::lock_guard<std::mutex> lock(mutex_);

    const Key current_key{request.worker_id, request.current_signing_key_id};
    const auto current_it = records_.find(current_key);
    if (current_it == records_.end()) {
        throw SigningKeyRegistryError(
            "commit_rotation called for a current signing key that is not on record -- caller "
            "must call validate_rotation first and only commit when accepted");
    }

    SigningKeyRecord& previous = current_it->second;
    if (request.grace_period_seconds > 0.0) {
        previous.status = SigningKeyStatus::kGracePeriod;
        previous.grace_period_start_unix_s = request.now_unix_s;
        previous.grace_period_end_unix_s = request.now_unix_s + request.grace_period_seconds;
    } else {
        previous.status = SigningKeyStatus::kExpired;
        previous.grace_period_start_unix_s = request.now_unix_s;
        previous.grace_period_end_unix_s = request.now_unix_s;
    }
    previous.rotated_to_key_id = request.new_signing_key_id;
    const SigningKeyRecord previous_copy = previous;

    SigningKeyRecord new_record;
    new_record.worker_id = request.worker_id;
    new_record.signing_key_id = request.new_signing_key_id;
    new_record.public_key_hex = request.new_public_key_hex;
    new_record.public_key_fingerprint = request.new_public_key_fingerprint;
    new_record.status = SigningKeyStatus::kActive;
    new_record.created_at_unix_s = request.now_unix_s;
    new_record.activated_at_unix_s = request.now_unix_s;
    new_record.expires_at_unix_s = request.new_key_expires_at_unix_s;
    new_record.rotated_from_key_id = request.current_signing_key_id;
    new_record.registration_source = "rotation";
    records_[Key{request.worker_id, request.new_signing_key_id}] = new_record;

    persist();
    return {true, SigningKeyRotationRejectionReason::kNone, "ok", new_record, previous_copy};
}

SigningKeyRecord SigningKeyRegistry::revoke_key(const std::string& worker_id,
                                                const std::string& signing_key_id,
                                                const std::string& reason,
                                                double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(Key{worker_id, signing_key_id});
    if (it == records_.end()) {
        throw SigningKeyRegistryError("unknown signing key '" + signing_key_id +
                                      "' for worker_id '" + worker_id + "'");
    }
    SigningKeyRecord& record = it->second;
    if (record.status == SigningKeyStatus::kRevoked) {
        return record;  // idempotent -- first revocation reason wins
    }
    record.status = SigningKeyStatus::kRevoked;
    record.revoked_at_unix_s = now_unix_s;
    record.revocation_reason = reason;
    persist();
    return record;
}

std::optional<SigningKeyRecord> SigningKeyRegistry::find(const std::string& worker_id,
                                                         const std::string& signing_key_id,
                                                         double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(Key{worker_id, signing_key_id});
    if (it == records_.end()) {
        return std::nullopt;
    }
    return effective_record(it->second, now_unix_s);
}

std::optional<SigningKeyRecord> SigningKeyRegistry::find_active(const std::string& worker_id,
                                                                double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [key, record] : records_) {
        if (key.worker_id != worker_id)
            continue;
        const auto effective = effective_record(record, now_unix_s);
        if (effective.status == SigningKeyStatus::kActive) {
            return effective;
        }
    }
    return std::nullopt;
}

bool SigningKeyRegistry::has_any_valid_key(const std::string& worker_id, double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [key, record] : records_) {
        if (key.worker_id != worker_id)
            continue;
        const auto effective = effective_record(record, now_unix_s);
        if (effective.status == SigningKeyStatus::kActive ||
            effective.status == SigningKeyStatus::kGracePeriod) {
            return true;
        }
    }
    return false;
}

std::vector<SigningKeyRecord> SigningKeyRegistry::list_for_worker(const std::string& worker_id,
                                                                  double now_unix_s) const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<SigningKeyRecord> result;
    for (const auto& [key, record] : records_) {
        if (key.worker_id != worker_id)
            continue;
        result.push_back(effective_record(record, now_unix_s));
    }
    return result;
}

std::vector<std::pair<std::string, std::string>> SigningKeyRegistry::sweep_expired(
    double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::pair<std::string, std::string>> transitioned;
    for (auto& [key, record] : records_) {
        const auto effective = effective_record(record, now_unix_s);
        if (effective.status == SigningKeyStatus::kExpired &&
            record.status != SigningKeyStatus::kExpired) {
            record.status = SigningKeyStatus::kExpired;
            transitioned.emplace_back(key.worker_id, key.signing_key_id);
        }
    }
    if (!transitioned.empty()) {
        persist();
    }
    return transitioned;
}

}  // namespace fl::coordinator
