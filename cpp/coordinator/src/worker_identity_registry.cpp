#include "fl_coordinator/worker_identity_registry.hpp"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace fl::coordinator {

namespace {

// Same FNV-1a checksum convention as AggregatorCheckpointStore
// (cpp/core/src/checkpoint.cpp) and RunInstance's own checkpointing
// (cpp/coordinator/src/run_manager.cpp) -- duplicated locally rather
// than shared, matching how every other coordinator persistence module
// in this codebase keeps its own copy of this small helper rather than
// exposing it as a public utility.
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

// Tab-separated, one record per line -- same convention as
// encode_sample_level_entry/encode_user_level_entry in run_manager.cpp.
// None of these fields are expected to legitimately contain a tab or
// newline (worker ids, hex-encoded keys/fingerprints, short human
// revocation reasons); if one somehow did, decode would simply fail
// with a field-count mismatch rather than corrupt an adjacent record,
// since each record is confined to exactly one line.
std::string encode_record(const WorkerIdentityRecord& record) {
    std::ostringstream out;
    out << record.schema_version << "\t" << record.worker_id << "\t" << record.certificate_identity
        << "\t" << record.certificate_serial << "\t" << record.certificate_fingerprint << "\t"
        << record.signing_public_key << "\t" << record.signing_key_id << "\t"
        << to_string(record.registration_status) << "\t" << record.software_version << "\t"
        << record.build_id << "\t" << std::setprecision(17) << record.created_at_unix_s << "\t"
        << record.updated_at_unix_s << "\t" << record.expires_at_unix_s << "\t"
        << record.suspended_at_unix_s << "\t" << record.revoked_at_unix_s << "\t"
        << record.revocation_reason;
    return out.str();
}

WorkerIdentityRecord decode_record(const std::string& line) {
    const auto parts = split(line, '\t');
    // revocation_reason may legitimately be empty, and split() drops a
    // trailing empty field after the final delimiter -- so a record
    // with an empty reason yields 15 fields, not 16. Accept both.
    if (parts.size() != 16 && parts.size() != 15) {
        throw WorkerIdentityRegistryError("malformed worker identity record line");
    }
    WorkerIdentityRecord record;
    try {
        record.schema_version = static_cast<std::uint32_t>(std::stoul(parts[0]));
        record.worker_id = parts[1];
        record.certificate_identity = parts[2];
        record.certificate_serial = parts[3];
        record.certificate_fingerprint = parts[4];
        record.signing_public_key = parts[5];
        record.signing_key_id = parts[6];
        record.registration_status = worker_identity_status_from_string(parts[7]);
        record.software_version = parts[8];
        record.build_id = parts[9];
        record.created_at_unix_s = std::stod(parts[10]);
        record.updated_at_unix_s = std::stod(parts[11]);
        record.expires_at_unix_s = std::stod(parts[12]);
        record.suspended_at_unix_s = std::stod(parts[13]);
        record.revoked_at_unix_s = std::stod(parts[14]);
        record.revocation_reason = parts.size() == 16 ? parts[15] : std::string();
    } catch (const WorkerIdentityRegistryError&) {
        throw;
    } catch (const std::exception& error) {
        throw WorkerIdentityRegistryError(
            std::string("worker identity record field parse failure: ") + error.what());
    }
    if (record.schema_version != WorkerIdentityRecord::kSchemaVersion) {
        throw WorkerIdentityRegistryError("unsupported worker identity record schema version " +
                                          std::to_string(record.schema_version));
    }
    return record;
}

}  // namespace

std::string to_string(WorkerIdentityStatus status) {
    switch (status) {
        case WorkerIdentityStatus::kPending:
            return "pending";
        case WorkerIdentityStatus::kActive:
            return "active";
        case WorkerIdentityStatus::kSuspended:
            return "suspended";
        case WorkerIdentityStatus::kRevoked:
            return "revoked";
        case WorkerIdentityStatus::kExpired:
            return "expired";
    }
    return "unknown";
}

WorkerIdentityStatus worker_identity_status_from_string(const std::string& value) {
    if (value == "pending")
        return WorkerIdentityStatus::kPending;
    if (value == "active")
        return WorkerIdentityStatus::kActive;
    if (value == "suspended")
        return WorkerIdentityStatus::kSuspended;
    if (value == "revoked")
        return WorkerIdentityStatus::kRevoked;
    if (value == "expired")
        return WorkerIdentityStatus::kExpired;
    throw WorkerIdentityRegistryError("unknown worker identity status: " + value);
}

WorkerIdentityRegistryError::WorkerIdentityRegistryError(const std::string& what)
    : std::runtime_error(what) {}

WorkerIdentityRegistry::WorkerIdentityRegistry(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw WorkerIdentityRegistryError("failed to open worker identity registry file: " +
                                          persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw WorkerIdentityRegistryError(
            "worker identity registry file is truncated or missing checksum");
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
        throw WorkerIdentityRegistryError(
            "worker identity registry checksum mismatch: file is corrupt or was truncated");
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
            records_.emplace(record.worker_id, record);
            ++found_count;
        }
    }
    if (!has_count) {
        throw WorkerIdentityRegistryError("worker identity registry file missing record_count");
    }
    if (found_count != expected_count) {
        throw WorkerIdentityRegistryError("worker identity registry file truncated: expected " +
                                          std::to_string(expected_count) + " records, found " +
                                          std::to_string(found_count));
    }
}

void WorkerIdentityRegistry::persist() const {
    std::ostringstream body;
    body << "record_count=" << records_.size() << "\n";
    for (const auto& [worker_id, record] : records_) {
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
            throw WorkerIdentityRegistryError(
                "failed to open worker identity registry temp file: " + temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw WorkerIdentityRegistryError(
                "failed to write worker identity registry temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        // std::filesystem::rename does not overwrite an existing file on
        // all platforms (notably Windows) -- see AggregatorCheckpointStore's
        // identical fallback.
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw WorkerIdentityRegistryError(
                "failed to atomically move worker identity registry into place: " +
                error_code.message());
        }
    }
}

WorkerIdentityRecord WorkerIdentityRegistry::register_identity(
    const std::string& worker_id,
    const std::string& certificate_identity,
    const std::string& certificate_serial,
    const std::string& certificate_fingerprint,
    const std::string& signing_public_key,
    const std::string& signing_key_id,
    const std::string& software_version,
    const std::string& build_id,
    double now_unix_s,
    double expires_at_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);

    const auto existing = records_.find(worker_id);
    if (existing != records_.end() &&
        existing->second.registration_status == WorkerIdentityStatus::kRevoked) {
        throw WorkerIdentityRegistryError(
            "worker_id '" + worker_id +
            "' is revoked and cannot be re-registered under the same identity");
    }

    for (const auto& [other_worker_id, other_record] : records_) {
        if (other_worker_id != worker_id &&
            other_record.certificate_fingerprint == certificate_fingerprint) {
            throw WorkerIdentityRegistryError(
                "certificate_fingerprint '" + certificate_fingerprint +
                "' is already bound to a different worker_id ('" + other_worker_id + "')");
        }
    }

    if (existing != records_.end()) {
        WorkerIdentityRecord& record = existing->second;
        record.certificate_identity = certificate_identity;
        record.certificate_serial = certificate_serial;
        record.certificate_fingerprint = certificate_fingerprint;
        record.signing_public_key = signing_public_key;
        record.signing_key_id = signing_key_id;
        record.software_version = software_version;
        record.build_id = build_id;
        record.updated_at_unix_s = now_unix_s;
        record.expires_at_unix_s = expires_at_unix_s;
        persist();
        return record;
    }

    WorkerIdentityRecord record;
    record.worker_id = worker_id;
    record.certificate_identity = certificate_identity;
    record.certificate_serial = certificate_serial;
    record.certificate_fingerprint = certificate_fingerprint;
    record.signing_public_key = signing_public_key;
    record.signing_key_id = signing_key_id;
    record.registration_status = WorkerIdentityStatus::kPending;
    record.software_version = software_version;
    record.build_id = build_id;
    record.created_at_unix_s = now_unix_s;
    record.updated_at_unix_s = now_unix_s;
    record.expires_at_unix_s = expires_at_unix_s;
    records_.emplace(worker_id, record);
    persist();
    return record;
}

std::optional<WorkerIdentityRecord> WorkerIdentityRegistry::find_by_worker_id(
    const std::string& worker_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(worker_id);
    if (it == records_.end()) {
        return std::nullopt;
    }
    return it->second;
}

std::optional<WorkerIdentityRecord> WorkerIdentityRegistry::find_by_certificate_fingerprint(
    const std::string& certificate_fingerprint) const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [worker_id, record] : records_) {
        if (record.certificate_fingerprint == certificate_fingerprint) {
            return record;
        }
    }
    return std::nullopt;
}

std::vector<WorkerIdentityRecord> WorkerIdentityRegistry::list() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<WorkerIdentityRecord> result;
    result.reserve(records_.size());
    for (const auto& [worker_id, record] : records_) {
        result.push_back(record);
    }
    return result;
}

WorkerIdentityRecord WorkerIdentityRegistry::suspend(const std::string& worker_id,
                                                     const std::string& reason,
                                                     double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(worker_id);
    if (it == records_.end()) {
        throw WorkerIdentityRegistryError("unknown worker_id: " + worker_id);
    }
    WorkerIdentityRecord& record = it->second;
    if (record.registration_status == WorkerIdentityStatus::kRevoked) {
        throw WorkerIdentityRegistryError("worker_id '" + worker_id +
                                          "' is revoked; suspension does not apply");
    }
    record.registration_status = WorkerIdentityStatus::kSuspended;
    record.suspended_at_unix_s = now_unix_s;
    record.revocation_reason = reason;
    record.updated_at_unix_s = now_unix_s;
    persist();
    return record;
}

WorkerIdentityRecord WorkerIdentityRegistry::activate(const std::string& worker_id,
                                                      double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(worker_id);
    if (it == records_.end()) {
        throw WorkerIdentityRegistryError("unknown worker_id: " + worker_id);
    }
    WorkerIdentityRecord& record = it->second;
    if (record.registration_status == WorkerIdentityStatus::kRevoked) {
        throw WorkerIdentityRegistryError("worker_id '" + worker_id +
                                          "' is revoked and cannot be activated");
    }
    record.registration_status = WorkerIdentityStatus::kActive;
    record.updated_at_unix_s = now_unix_s;
    persist();
    return record;
}

WorkerIdentityRecord WorkerIdentityRegistry::revoke(const std::string& worker_id,
                                                    const std::string& reason,
                                                    double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(worker_id);
    if (it == records_.end()) {
        throw WorkerIdentityRegistryError("unknown worker_id: " + worker_id);
    }
    WorkerIdentityRecord& record = it->second;
    if (record.registration_status == WorkerIdentityStatus::kRevoked) {
        return record;  // idempotent -- first revocation reason wins
    }
    record.registration_status = WorkerIdentityStatus::kRevoked;
    record.revoked_at_unix_s = now_unix_s;
    record.revocation_reason = reason;
    record.updated_at_unix_s = now_unix_s;
    persist();
    return record;
}

std::vector<std::string> WorkerIdentityRegistry::sweep_expired(double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> transitioned;
    for (auto& [worker_id, record] : records_) {
        if (record.registration_status == WorkerIdentityStatus::kRevoked ||
            record.registration_status == WorkerIdentityStatus::kExpired) {
            continue;
        }
        if (record.expires_at_unix_s <= 0.0) {
            continue;  // does not expire
        }
        if (record.expires_at_unix_s < now_unix_s) {
            record.registration_status = WorkerIdentityStatus::kExpired;
            record.updated_at_unix_s = now_unix_s;
            transitioned.push_back(worker_id);
        }
    }
    if (!transitioned.empty()) {
        persist();
    }
    return transitioned;
}

}  // namespace fl::coordinator
