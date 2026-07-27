#include "fl_coordinator/secure_aggregation_session_store.hpp"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace fl::coordinator {

namespace {

// Same FNV-1a checksum convention as WorkerIdentityRegistry -- a local
// copy, matching this project's established "each persistence module
// keeps its own small copy of this helper" convention.
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

std::string encode_record(const SecureAggregationSessionRecord& record) {
    std::ostringstream out;
    out << record.schema_version << "\t" << record.session_id << "\t" << record.run_id << "\t"
        << record.round_id << "\t" << record.state << "\t" << std::setprecision(17)
        << record.created_at_unix_s << "\t" << record.updated_at_unix_s << "\t"
        << record.completed_at_unix_s << "\t" << record.abort_reason << "\t" << record.failure_reason;
    return out.str();
}

SecureAggregationSessionRecord decode_record(const std::string& line) {
    const auto parts = split(line, '\t');
    // failure_reason (the last field) may legitimately be empty, and
    // split() drops a trailing empty field after the final delimiter --
    // so a record with an empty failure_reason yields 9 fields, not 10.
    if (parts.size() != 10 && parts.size() != 9) {
        throw SecureAggregationSessionStoreError("malformed secure aggregation session record line");
    }
    SecureAggregationSessionRecord record;
    try {
        record.schema_version = static_cast<std::uint32_t>(std::stoul(parts[0]));
        record.session_id = parts[1];
        record.run_id = parts[2];
        record.round_id = std::stoull(parts[3]);
        record.state = parts[4];
        record.created_at_unix_s = std::stod(parts[5]);
        record.updated_at_unix_s = std::stod(parts[6]);
        record.completed_at_unix_s = std::stod(parts[7]);
        record.abort_reason = parts[8];
        record.failure_reason = parts.size() == 10 ? parts[9] : std::string();
    } catch (const SecureAggregationSessionStoreError&) {
        throw;
    } catch (const std::exception& error) {
        throw SecureAggregationSessionStoreError(
            std::string("secure aggregation session record field parse failure: ") + error.what());
    }
    if (record.schema_version != SecureAggregationSessionRecord::kSchemaVersion) {
        throw SecureAggregationSessionStoreError("unsupported secure aggregation session record schema version " +
                                                  std::to_string(record.schema_version));
    }
    return record;
}

}  // namespace

bool is_terminal_session_state(const std::string& state) {
    return state == "COMPLETED" || state == "ABORTED" || state == "FAILED";
}

SecureAggregationSessionStoreError::SecureAggregationSessionStoreError(const std::string& what)
    : std::runtime_error(what) {}

SecureAggregationSessionStore::SecureAggregationSessionStore(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw SecureAggregationSessionStoreError("failed to open secure aggregation session store file: " +
                                                  persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw SecureAggregationSessionStoreError(
            "secure aggregation session store file is truncated or missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    std::string checksum_line = payload.substr(marker + 1);
    const auto equals = checksum_line.find('=');
    std::string checksum_value = equals == std::string::npos ? "" : checksum_line.substr(equals + 1);
    while (!checksum_value.empty() && (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }
    if (hash_to_hex(fnv1a_hash(body)) != checksum_value) {
        throw SecureAggregationSessionStoreError(
            "secure aggregation session store checksum mismatch: file is corrupt or was truncated");
    }

    std::stringstream stream(body);
    std::string line;
    bool has_count = false;
    std::size_t expected_count = 0;
    std::size_t found_count = 0;
    while (std::getline(stream, line)) {
        if (line.empty()) continue;
        if (line.rfind("record_count=", 0) == 0) {
            expected_count = std::stoull(line.substr(std::string("record_count=").size()));
            has_count = true;
            continue;
        }
        if (line.rfind("record=", 0) == 0) {
            const auto record = decode_record(line.substr(std::string("record=").size()));
            records_[record.session_id] = record;  // last record per session_id wins (latest transition)
            ++found_count;
        }
    }
    if (!has_count) {
        throw SecureAggregationSessionStoreError("secure aggregation session store file missing record_count");
    }
    if (found_count != expected_count) {
        throw SecureAggregationSessionStoreError(
            "secure aggregation session store file truncated: expected " + std::to_string(expected_count) +
            " records, found " + std::to_string(found_count));
    }
}

void SecureAggregationSessionStore::persist() const {
    std::ostringstream body;
    body << "record_count=" << records_.size() << "\n";
    for (const auto& [session_id, record] : records_) {
        (void)session_id;
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
            throw SecureAggregationSessionStoreError("failed to open secure aggregation session store temp file: " +
                                                      temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw SecureAggregationSessionStoreError(
                "failed to write secure aggregation session store temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw SecureAggregationSessionStoreError(
                "failed to atomically move secure aggregation session store into place: " + error_code.message());
        }
    }
}

void SecureAggregationSessionStore::record_transition(const SecureAggregationSessionRecord& record) {
    records_[record.session_id] = record;
    persist();
}

std::optional<SecureAggregationSessionRecord> SecureAggregationSessionStore::find(
    const std::string& session_id) const {
    const auto it = records_.find(session_id);
    if (it == records_.end()) return std::nullopt;
    return it->second;
}

std::vector<SecureAggregationSessionRecord> SecureAggregationSessionStore::all() const {
    std::vector<SecureAggregationSessionRecord> result;
    result.reserve(records_.size());
    for (const auto& [session_id, record] : records_) {
        (void)session_id;
        result.push_back(record);
    }
    return result;
}

std::vector<std::string> SecureAggregationSessionStore::reconcile_after_restart(double now_unix_s) {
    std::vector<std::string> reconciled;
    for (auto& [session_id, record] : records_) {
        if (is_terminal_session_state(record.state)) continue;
        record.state = "ABORTED";
        record.abort_reason = "coordinator_restart";
        record.completed_at_unix_s = now_unix_s;
        record.updated_at_unix_s = now_unix_s;
        reconciled.push_back(session_id);
    }
    if (!reconciled.empty()) {
        persist();
    }
    return reconciled;
}

}  // namespace fl::coordinator
