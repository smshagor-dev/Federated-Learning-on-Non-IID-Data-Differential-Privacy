#include "fl_coordinator/accountant_monotonicity_store.hpp"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <vector>

namespace fl::coordinator {

namespace {

// Same FNV-1a checksum convention as WorkerIdentityRegistry/
// ReplayProtectionStore -- duplicated locally rather than shared,
// matching this codebase's established per-file-copy convention for
// this small helper.
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
// worker_identity_registry.cpp's encode_record/decode_record.
std::string encode_record(const AccountantMonotonicityRecord& record) {
    std::ostringstream out;
    out << record.schema_version << "\t" << record.run_id << "\t" << record.client_id << "\t"
        << record.worker_id << "\t" << record.accountant_type << "\t" << record.last_accepted_step
        << "\t" << std::setprecision(17) << record.last_epsilon << "\t" << record.delta << "\t"
        << record.accountant_state_hash << "\t" << record.configuration_hash << "\t"
        << record.last_round << "\t" << record.last_task << "\t" << record.updated_at_unix_s;
    return out.str();
}

AccountantMonotonicityRecord decode_record(const std::string& line) {
    const auto parts = split(line, '\t');
    // last_task may legitimately be empty, and split() drops a trailing
    // empty field after the final delimiter -- accept both 13 and 12.
    if (parts.size() != 13 && parts.size() != 12) {
        throw AccountantMonotonicityStoreError("malformed accountant monotonicity record line");
    }
    AccountantMonotonicityRecord record;
    try {
        record.schema_version = static_cast<std::uint32_t>(std::stoul(parts[0]));
        record.run_id = parts[1];
        record.client_id = parts[2];
        record.worker_id = parts[3];
        record.accountant_type = std::stoi(parts[4]);
        record.last_accepted_step = std::stoull(parts[5]);
        record.last_epsilon = std::stod(parts[6]);
        record.delta = std::stod(parts[7]);
        record.accountant_state_hash = parts[8];
        record.configuration_hash = parts[9];
        record.last_round = std::stoull(parts[10]);
        record.last_task = parts.size() == 13 ? parts[11] : std::string();
        record.updated_at_unix_s = std::stod(parts[parts.size() == 13 ? 12 : 11]);
    } catch (const AccountantMonotonicityStoreError&) {
        throw;
    } catch (const std::exception& error) {
        throw AccountantMonotonicityStoreError(
            std::string("accountant monotonicity record field parse failure: ") + error.what());
    }
    if (record.schema_version != AccountantMonotonicityRecord::kSchemaVersion) {
        throw AccountantMonotonicityStoreError("unsupported accountant monotonicity schema version " +
                                               std::to_string(record.schema_version));
    }
    return record;
}

}  // namespace

std::string to_string(MonotonicityRejectionReason reason) {
    switch (reason) {
        case MonotonicityRejectionReason::kNone:
            return "none";
        case MonotonicityRejectionReason::kStepNotIncreasing:
            return "accountant_step_not_increasing";
        case MonotonicityRejectionReason::kEpsilonDecreased:
            return "epsilon_decreased";
        case MonotonicityRejectionReason::kDeltaChanged:
            return "delta_changed";
        case MonotonicityRejectionReason::kConfigurationHashChanged:
            return "configuration_hash_changed";
    }
    return "unknown";
}

AccountantMonotonicityStoreError::AccountantMonotonicityStoreError(const std::string& what)
    : std::runtime_error(what) {}

AccountantMonotonicityStore::AccountantMonotonicityStore(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw AccountantMonotonicityStoreError("failed to open accountant monotonicity store file: " +
                                               persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw AccountantMonotonicityStoreError(
            "accountant monotonicity store file is truncated or missing checksum");
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
        throw AccountantMonotonicityStoreError(
            "accountant monotonicity store checksum mismatch: file is corrupt or was truncated");
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
            TrackKey key{record.run_id, record.client_id, record.worker_id, record.accountant_type};
            records_.emplace(key, record);
            ++found_count;
        }
    }
    if (!has_count) {
        throw AccountantMonotonicityStoreError("accountant monotonicity store file missing record_count");
    }
    if (found_count != expected_count) {
        throw AccountantMonotonicityStoreError(
            "accountant monotonicity store file truncated: expected " +
            std::to_string(expected_count) + " records, found " + std::to_string(found_count));
    }
}

void AccountantMonotonicityStore::persist() const {
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
            throw AccountantMonotonicityStoreError(
                "failed to open accountant monotonicity store temp file: " + temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw AccountantMonotonicityStoreError(
                "failed to write accountant monotonicity store temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw AccountantMonotonicityStoreError(
                "failed to atomically move accountant monotonicity store into place: " +
                error_code.message());
        }
    }
}

MonotonicityDecision AccountantMonotonicityStore::validate(const MonotonicityCandidate& candidate) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const TrackKey key{candidate.run_id, candidate.client_id, candidate.worker_id,
                       candidate.accountant_type};
    const auto it = records_.find(key);
    if (it == records_.end()) {
        // Documented starting behavior, matching ReplayProtectionStore's
        // "a brand-new track's first message is still checked, but
        // there is nothing to be lower/higher than yet" convention.
        return {true, MonotonicityRejectionReason::kNone, "ok: new accountant track"};
    }
    const auto& record = it->second;
    if (candidate.configuration_hash != record.configuration_hash) {
        return {false, MonotonicityRejectionReason::kConfigurationHashChanged,
               "configuration_hash '" + candidate.configuration_hash +
                   "' does not match this track's established configuration_hash '" +
                   record.configuration_hash + "'"};
    }
    if (candidate.delta != record.delta) {
        return {false, MonotonicityRejectionReason::kDeltaChanged,
               "delta changed from " + std::to_string(record.delta) + " to " +
                   std::to_string(candidate.delta) + " within the same accountant track"};
    }
    if (candidate.step <= record.last_accepted_step) {
        return {false, MonotonicityRejectionReason::kStepNotIncreasing,
               "accountant_step " + std::to_string(candidate.step) +
                   " does not exceed the last accepted step " +
                   std::to_string(record.last_accepted_step)};
    }
    if (candidate.epsilon < record.last_epsilon) {
        return {false, MonotonicityRejectionReason::kEpsilonDecreased,
               "epsilon " + std::to_string(candidate.epsilon) +
                   " is lower than the last accepted epsilon " + std::to_string(record.last_epsilon)};
    }
    return {true, MonotonicityRejectionReason::kNone, "ok"};
}

void AccountantMonotonicityStore::commit(const MonotonicityCandidate& candidate) {
    std::lock_guard<std::mutex> lock(mutex_);
    const TrackKey key{candidate.run_id, candidate.client_id, candidate.worker_id,
                       candidate.accountant_type};
    AccountantMonotonicityRecord record;
    record.run_id = candidate.run_id;
    record.client_id = candidate.client_id;
    record.worker_id = candidate.worker_id;
    record.accountant_type = candidate.accountant_type;
    record.last_accepted_step = candidate.step;
    record.last_epsilon = candidate.epsilon;
    record.delta = candidate.delta;
    record.accountant_state_hash = candidate.accountant_state_hash;
    record.configuration_hash = candidate.configuration_hash;
    record.last_round = candidate.round_id;
    record.last_task = candidate.task_id;
    record.updated_at_unix_s = candidate.now_unix_s;
    records_[key] = record;
    persist();
}

std::optional<AccountantMonotonicityRecord> AccountantMonotonicityStore::find(const TrackKey& key) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(key);
    if (it == records_.end()) {
        return std::nullopt;
    }
    return it->second;
}

void AccountantMonotonicityStore::reset(const TrackKey& key, const std::string& reason,
                                        double now_unix_s) {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(key);
    if (it == records_.end()) {
        return;  // nothing to reset -- not an error, matching purge_worker's tolerant convention
    }
    (void)reason;  // not persisted as a distinct field this pass -- see header comment
    records_.erase(it);
    (void)now_unix_s;
    persist();
}

}  // namespace fl::coordinator
