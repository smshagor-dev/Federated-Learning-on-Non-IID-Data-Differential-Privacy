#include "fl_coordinator/security_audit_journal.hpp"

#include "fl_coordinator/security_event.hpp"  // reuses json_escape_string
#include "fl_coordinator/security_journal_json.hpp"

#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>

namespace fl::coordinator {

namespace {

std::string format_iso8601(double now_unix_s) {
    const auto seconds = static_cast<std::time_t>(now_unix_s);
    std::tm tm_utc{};
#if defined(_WIN32)
    gmtime_s(&tm_utc, &seconds);
#else
    gmtime_r(&seconds, &tm_utc);
#endif
    std::ostringstream out;
    out << std::put_time(&tm_utc, "%Y-%m-%dT%H:%M:%SZ");
    return out.str();
}

std::string format_iso8601_now() {
    const auto now = std::chrono::system_clock::now();
    return format_iso8601(
        static_cast<double>(std::chrono::duration_cast<std::chrono::seconds>(now.time_since_epoch())
                                 .count()));
}

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

// Excludes record_id/timestamp/payload_checksum, same rationale as
// canonical_security_event_payload_json (security_event.hpp).
std::string canonical_audit_payload_json(const SecurityAuditRecord& record) {
    std::ostringstream out;
    out << "{";
    out << "\"action\":" << json_escape_string(record.action) << ",";
    out << "\"actor_role\":" << json_escape_string(record.actor_role) << ",";
    out << "\"outcome\":" << json_escape_string(record.outcome) << ",";
    out << "\"reason\":" << json_escape_string(record.reason) << ",";
    out << "\"request_id\":" << json_escape_string(record.request_id) << ",";
    out << "\"resource_id\":" << json_escape_string(record.resource_id) << ",";
    out << "\"resource_type\":" << json_escape_string(record.resource_type) << ",";
    out << "\"safe_actor_id\":" << json_escape_string(record.safe_actor_id) << ",";
    out << "\"safe_details\":{";
    bool first_detail = true;
    for (const auto& [key, value] : record.safe_details) {
        if (!first_detail) {
            out << ",";
        }
        first_detail = false;
        out << json_escape_string(key) << ":" << json_escape_string(value);
    }
    out << "},";
    out << "\"schema_version\":" << record.schema_version << ",";
    out << "\"trace_id\":" << json_escape_string(record.trace_id);
    out << "}";
    return out.str();
}

std::string compute_audit_checksum(const SecurityAuditRecord& record) {
    return hash_to_hex(fnv1a_hash(canonical_audit_payload_json(record)));
}

std::string serialize_full_audit_json(const SecurityAuditRecord& record) {
    std::ostringstream out;
    out << "{";
    out << "\"action\":" << json_escape_string(record.action) << ",";
    out << "\"actor_role\":" << json_escape_string(record.actor_role) << ",";
    out << "\"outcome\":" << json_escape_string(record.outcome) << ",";
    out << "\"payload_checksum\":" << json_escape_string(record.payload_checksum) << ",";
    out << "\"reason\":" << json_escape_string(record.reason) << ",";
    out << "\"record_id\":" << json_escape_string(record.record_id) << ",";
    out << "\"request_id\":" << json_escape_string(record.request_id) << ",";
    out << "\"resource_id\":" << json_escape_string(record.resource_id) << ",";
    out << "\"resource_type\":" << json_escape_string(record.resource_type) << ",";
    out << "\"safe_actor_id\":" << json_escape_string(record.safe_actor_id) << ",";
    out << "\"safe_details\":{";
    bool first_detail = true;
    for (const auto& [key, value] : record.safe_details) {
        if (!first_detail) {
            out << ",";
        }
        first_detail = false;
        out << json_escape_string(key) << ":" << json_escape_string(value);
    }
    out << "},";
    out << "\"schema_version\":" << record.schema_version << ",";
    out << "\"timestamp\":" << json_escape_string(record.timestamp) << ",";
    out << "\"trace_id\":" << json_escape_string(record.trace_id);
    out << "}";
    return out.str();
}

std::optional<SecurityAuditRecord> parse_audit_line(const std::string& line) {
    if (line.empty()) {
        return std::nullopt;
    }
    const auto parsed = parse_shallow_json_object(line);
    if (!parsed) {
        return std::nullopt;
    }
    const auto& fields = *parsed;

    SecurityAuditRecord record;
    record.schema_version = json_field_int(fields, "schema_version");
    record.record_id = json_field_string(fields, "record_id");
    record.timestamp = json_field_string(fields, "timestamp");
    record.safe_actor_id = json_field_string(fields, "safe_actor_id");
    record.actor_role = json_field_string(fields, "actor_role");
    record.action = json_field_string(fields, "action");
    record.resource_type = json_field_string(fields, "resource_type");
    record.resource_id = json_field_string(fields, "resource_id");
    record.outcome = json_field_string(fields, "outcome");
    record.reason = json_field_string(fields, "reason");
    record.request_id = json_field_string(fields, "request_id");
    record.trace_id = json_field_string(fields, "trace_id");
    record.safe_details = json_field_map(fields, "safe_details");
    record.payload_checksum = json_field_string(fields, "payload_checksum");

    if (record.record_id.empty() || record.timestamp.empty() || record.action.empty()) {
        return std::nullopt;
    }
    if (compute_audit_checksum(record) != record.payload_checksum) {
        return std::nullopt;
    }
    return record;
}

}  // namespace

SecurityAuditJournalError::SecurityAuditJournalError(const std::string& what)
    : std::runtime_error(what) {}

SecurityAuditJournal::SecurityAuditJournal(std::string persistence_path)
    : SecurityAuditJournal(std::move(persistence_path), Options{}) {}

SecurityAuditJournal::SecurityAuditJournal(std::string persistence_path, Options options)
    : persistence_path_(std::move(persistence_path)), options_(options) {
    const std::filesystem::path target(persistence_path_);
    if (target.has_parent_path()) {
        std::filesystem::create_directories(target.parent_path());
    }
    load();
}

void SecurityAuditJournal::load() {
    in_memory_.clear();
    recovered_line_count_ = 0;
    next_sequence_ = 1;
    rotations_ = std::filesystem::exists(persistence_path_ + ".1") ? 1 : 0;
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        throw SecurityAuditJournalError("failed to open security audit journal: " +
                                        persistence_path_);
    }
    std::string line;
    while (std::getline(file, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.empty()) {
            continue;
        }
        const auto record = parse_audit_line(line);
        if (!record) {
            ++recovered_line_count_;
            continue;
        }
        in_memory_.push_back(*record);
        try {
            const auto sequence = std::stoull(record->record_id);
            if (sequence + 1 > next_sequence_) {
                next_sequence_ = sequence + 1;
            }
        } catch (const std::exception&) {
        }
    }
    if (recovered_line_count_ > 0) {
        std::cerr << "security_audit_journal: recovered from " << recovered_line_count_
                  << " corrupt/unparseable line(s) in " << persistence_path_ << "\n"
                  << std::flush;
    }
}

std::string SecurityAuditJournal::next_record_id() {
    std::ostringstream out;
    out << std::setw(20) << std::setfill('0') << next_sequence_++;
    return out.str();
}

void SecurityAuditJournal::append_line(const std::string& line) {
    std::ofstream file(persistence_path_, std::ios::binary | std::ios::app);
    if (!file) {
        std::cerr << "security_audit_journal: failed to open for append: " << persistence_path_
                  << "\n"
                  << std::flush;
        return;
    }
    const std::string data = line + "\n";
    file.write(data.data(), static_cast<std::streamsize>(data.size()));
    file.flush();
    if (!file) {
        std::cerr << "security_audit_journal: failed to append to: " << persistence_path_ << "\n"
                  << std::flush;
    }
}

void SecurityAuditJournal::maybe_rotate() {
    std::error_code error_code;
    const auto current_size = std::filesystem::file_size(persistence_path_, error_code);
    if (error_code || current_size < options_.max_bytes_before_rotation) {
        return;
    }
    for (std::size_t generation = options_.max_retained_files; generation >= 1; --generation) {
        const std::string from = generation == 1
                                      ? persistence_path_
                                      : persistence_path_ + "." + std::to_string(generation - 1);
        const std::string to = persistence_path_ + "." + std::to_string(generation);
        if (generation == options_.max_retained_files && std::filesystem::exists(to)) {
            std::filesystem::remove(to, error_code);
        }
        if (std::filesystem::exists(from)) {
            std::filesystem::rename(from, to, error_code);
        }
        if (generation == 1) {
            break;
        }
    }
    in_memory_.clear();
    ++rotations_;
}

void SecurityAuditJournal::append(SecurityAuditRecord record) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (record.timestamp.empty()) {
        record.timestamp = format_iso8601_now();
    }
    if (record.record_id.empty()) {
        record.record_id = next_record_id();
    }
    if (record.action.empty()) {
        std::cerr << "security_audit_journal: dropping record with empty action\n" << std::flush;
        return;
    }
    record.payload_checksum = compute_audit_checksum(record);

    try {
        maybe_rotate();
        append_line(serialize_full_audit_json(record));
        in_memory_.push_back(record);
    } catch (const std::exception& error) {
        std::cerr << "security_audit_journal: failed to persist record: " << error.what() << "\n"
                  << std::flush;
    }
}

SecurityAuditJournal::ListResult SecurityAuditJournal::list(const ListFilters& filters) const {
    std::lock_guard<std::mutex> lock(mutex_);
    ListResult result;
    const std::string since_ts = filters.since_unix_s > 0.0 ? format_iso8601(filters.since_unix_s) : "";
    const std::string until_ts = filters.until_unix_s > 0.0 ? format_iso8601(filters.until_unix_s) : "";
    bool past_cursor = filters.after_record_id.empty();
    for (const auto& record : in_memory_) {
        if (!past_cursor) {
            if (record.record_id == filters.after_record_id) {
                past_cursor = true;
            }
            continue;
        }
        if (!filters.actor_id.empty() && record.safe_actor_id != filters.actor_id) {
            continue;
        }
        if (!filters.action.empty() && record.action != filters.action) {
            continue;
        }
        if (!filters.resource_type.empty() && record.resource_type != filters.resource_type) {
            continue;
        }
        if (!filters.outcome.empty() && record.outcome != filters.outcome) {
            continue;
        }
        if (!since_ts.empty() && record.timestamp < since_ts) {
            continue;
        }
        if (!until_ts.empty() && record.timestamp > until_ts) {
            continue;
        }
        if (result.records.size() >= filters.limit) {
            result.next_cursor = result.records.back().record_id;
            return result;
        }
        result.records.push_back(record);
    }
    return result;
}

std::size_t SecurityAuditJournal::recovered_line_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return recovered_line_count_;
}

std::size_t SecurityAuditJournal::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return in_memory_.size();
}

std::string SecurityAuditJournal::last_record_timestamp() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (in_memory_.empty()) {
        return "";
    }
    return in_memory_.back().timestamp;
}

bool SecurityAuditJournal::has_rotated() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return rotations_ > 0;
}

}  // namespace fl::coordinator
