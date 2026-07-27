#include "fl_coordinator/idempotency_store.hpp"

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

std::string encode_record(const IdempotentMutationRecord& record) {
    std::ostringstream out;
    out << record.rpc_name << "\t" << record.idempotency_key << "\t"
        << (record.accepted ? "1" : "0") << "\t" << record.rejection_code << "\t"
        << record.result_key_id << "\t" << record.previous_key_id << "\t"
        << std::setprecision(17) << record.recorded_at_unix_s;
    return out.str();
}

IdempotentMutationRecord decode_record(const std::string& line) {
    const auto parts = split(line, '\t');
    if (parts.size() != 7 && parts.size() != 6) {
        throw IdempotencyStoreError("malformed idempotency record line");
    }
    IdempotentMutationRecord record;
    try {
        record.rpc_name = parts[0];
        record.idempotency_key = parts[1];
        record.accepted = parts[2] == "1";
        record.rejection_code = parts[3];
        record.result_key_id = parts[4];
        record.previous_key_id = parts[5];
        record.recorded_at_unix_s = parts.size() == 7 ? std::stod(parts[6]) : 0.0;
    } catch (const std::exception& error) {
        throw IdempotencyStoreError(std::string("idempotency record field parse failure: ") +
                                    error.what());
    }
    return record;
}

}  // namespace

IdempotencyStoreError::IdempotencyStoreError(const std::string& what) : std::runtime_error(what) {}

IdempotencyStore::IdempotencyStore(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw IdempotencyStoreError("failed to open idempotency store file: " + persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw IdempotencyStoreError("idempotency store file is truncated or missing checksum");
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
        throw IdempotencyStoreError(
            "idempotency store checksum mismatch: file is corrupt or was truncated");
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
            records_.emplace(Key{record.rpc_name, record.idempotency_key}, record);
            ++found_count;
        }
    }
    if (!has_count) {
        throw IdempotencyStoreError("idempotency store file missing record_count");
    }
    if (found_count != expected_count) {
        throw IdempotencyStoreError("idempotency store file truncated: expected " +
                                    std::to_string(expected_count) + " records, found " +
                                    std::to_string(found_count));
    }
}

void IdempotencyStore::persist() const {
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
            throw IdempotencyStoreError("failed to open idempotency store temp file: " + temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw IdempotencyStoreError("failed to write idempotency store temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw IdempotencyStoreError("failed to atomically move idempotency store into place: " +
                                        error_code.message());
        }
    }
}

std::optional<IdempotentMutationRecord> IdempotencyStore::find(
    const std::string& rpc_name, const std::string& idempotency_key) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = records_.find(Key{rpc_name, idempotency_key});
    if (it == records_.end()) {
        return std::nullopt;
    }
    return it->second;
}

void IdempotencyStore::record(const IdempotentMutationRecord& record) {
    std::lock_guard<std::mutex> lock(mutex_);
    const Key key{record.rpc_name, record.idempotency_key};
    if (records_.find(key) != records_.end()) {
        throw IdempotencyStoreError("an idempotency record already exists for rpc_name='" +
                                    record.rpc_name + "' idempotency_key='" + record.idempotency_key +
                                    "' -- caller must check find() before record()");
    }
    records_.emplace(key, record);
    persist();
}

}  // namespace fl::coordinator
