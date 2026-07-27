#include "fl_coordinator/coordinator_task_sequence_store.hpp"

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

}  // namespace

CoordinatorTaskSequenceStoreError::CoordinatorTaskSequenceStoreError(const std::string& what)
    : std::runtime_error(what) {}

CoordinatorTaskSequenceStore::CoordinatorTaskSequenceStore(std::string persistence_path)
    : persistence_path_(std::move(persistence_path)) {
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw CoordinatorTaskSequenceStoreError("failed to open coordinator task sequence store: " +
                                                persistence_path_);
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const std::string payload = buffer.str();

    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw CoordinatorTaskSequenceStoreError(
            "coordinator task sequence store is truncated or missing checksum");
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
        throw CoordinatorTaskSequenceStoreError(
            "coordinator task sequence store checksum mismatch: file is corrupt or was truncated");
    }

    std::stringstream stream(body);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty() || line.rfind("record=", 0) != 0) {
            continue;
        }
        const auto parts = split(line.substr(std::string("record=").size()), '\t');
        if (parts.size() != 3) {
            throw CoordinatorTaskSequenceStoreError("malformed coordinator task sequence record");
        }
        try {
            counters_[Key{parts[0], parts[1]}] = std::stoull(parts[2]);
        } catch (const std::exception& error) {
            throw CoordinatorTaskSequenceStoreError(
                std::string("coordinator task sequence record parse failure: ") + error.what());
        }
    }
}

void CoordinatorTaskSequenceStore::persist() const {
    std::ostringstream body;
    for (const auto& [key, value] : counters_) {
        body << "record=" << key.signing_key_id << "\t" << key.worker_id << "\t" << value << "\n";
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
            throw CoordinatorTaskSequenceStoreError(
                "failed to open coordinator task sequence store temp file: " + temp_path);
        }
        file << out.str();
        file.flush();
        if (!file) {
            throw CoordinatorTaskSequenceStoreError(
                "failed to write coordinator task sequence store temp file: " + temp_path);
        }
    }
    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw CoordinatorTaskSequenceStoreError(
                "failed to atomically move coordinator task sequence store into place: " +
                error_code.message());
        }
    }
}

std::uint64_t CoordinatorTaskSequenceStore::next_sequence(const std::string& signing_key_id,
                                                          const std::string& worker_id) {
    std::lock_guard<std::mutex> lock(mutex_);
    const Key key{signing_key_id, worker_id};
    const auto next_value = counters_[key] + 1;
    counters_[key] = next_value;
    persist();
    return next_value;
}

std::uint64_t CoordinatorTaskSequenceStore::peek(const std::string& signing_key_id,
                                                 const std::string& worker_id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const auto it = counters_.find(Key{signing_key_id, worker_id});
    return it == counters_.end() ? 0 : it->second;
}

}  // namespace fl::coordinator
