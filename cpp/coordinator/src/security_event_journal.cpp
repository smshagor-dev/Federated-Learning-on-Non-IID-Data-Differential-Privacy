#include "fl_coordinator/security_event_journal.hpp"

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

std::string format_iso8601_now() {
    const auto now = std::chrono::system_clock::now();
    const auto seconds = std::chrono::system_clock::to_time_t(now);
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

std::string serialize_full_event_json(const SecurityEvent& event) {
    std::ostringstream out;
    out << "{";
    out << "\"actor_type\":" << json_escape_string(to_string(event.actor_type)) << ",";
    out << "\"event_id\":" << json_escape_string(event.event_id) << ",";
    out << "\"event_type\":" << json_escape_string(to_string(event.event_type)) << ",";
    out << "\"outcome\":" << json_escape_string(to_string(event.outcome)) << ",";
    out << "\"payload_checksum\":" << json_escape_string(event.payload_checksum) << ",";
    out << "\"reason_code\":" << json_escape_string(event.reason_code) << ",";
    out << "\"request_id\":" << json_escape_string(event.request_id) << ",";
    out << "\"round_id\":" << event.round_id << ",";
    out << "\"run_id\":" << json_escape_string(event.run_id) << ",";
    out << "\"safe_actor_id\":" << json_escape_string(event.safe_actor_id) << ",";
    out << "\"safe_details\":{";
    bool first_detail = true;
    for (const auto& [key, value] : event.safe_details) {
        if (!first_detail) {
            out << ",";
        }
        first_detail = false;
        out << json_escape_string(key) << ":" << json_escape_string(value);
    }
    out << "},";
    out << "\"safe_signing_key_id\":" << json_escape_string(event.safe_signing_key_id) << ",";
    out << "\"safe_subject_id\":" << json_escape_string(event.safe_subject_id) << ",";
    out << "\"schema_version\":" << event.schema_version << ",";
    out << "\"severity\":" << json_escape_string(to_string(event.severity)) << ",";
    out << "\"source_component\":" << json_escape_string(event.source_component) << ",";
    out << "\"source_service\":" << json_escape_string(event.source_service) << ",";
    out << "\"subject_type\":" << json_escape_string(to_string(event.subject_type)) << ",";
    out << "\"task_id\":" << json_escape_string(event.task_id) << ",";
    out << "\"timestamp\":" << json_escape_string(event.timestamp) << ",";
    out << "\"trace_id\":" << json_escape_string(event.trace_id) << ",";
    out << "\"worker_id\":" << json_escape_string(event.worker_id);
    out << "}";
    return out.str();
}

// Returns std::nullopt if the line is malformed, has an unrecognized
// enum value, or fails its own payload_checksum -- the caller treats
// this as "skip and recover", never a fatal error.
std::optional<SecurityEvent> parse_event_line(const std::string& line) {
    if (line.empty()) {
        return std::nullopt;
    }
    const auto parsed = parse_shallow_json_object(line);
    if (!parsed) {
        return std::nullopt;
    }
    const auto& fields = *parsed;

    SecurityEvent event;
    event.schema_version = json_field_int(fields, "schema_version");
    event.event_id = json_field_string(fields, "event_id");
    event.timestamp = json_field_string(fields, "timestamp");
    event.source_service = json_field_string(fields, "source_service");
    event.source_component = json_field_string(fields, "source_component");
    event.safe_actor_id = json_field_string(fields, "safe_actor_id");
    event.safe_subject_id = json_field_string(fields, "safe_subject_id");
    event.worker_id = json_field_string(fields, "worker_id");
    event.run_id = json_field_string(fields, "run_id");
    event.round_id = json_field_uint(fields, "round_id");
    event.task_id = json_field_string(fields, "task_id");
    event.safe_signing_key_id = json_field_string(fields, "safe_signing_key_id");
    event.request_id = json_field_string(fields, "request_id");
    event.trace_id = json_field_string(fields, "trace_id");
    event.reason_code = json_field_string(fields, "reason_code");
    event.safe_details = json_field_map(fields, "safe_details");
    event.payload_checksum = json_field_string(fields, "payload_checksum");

    if (!security_event_type_from_string(json_field_string(fields, "event_type"), event.event_type)) {
        return std::nullopt;
    }
    if (!security_severity_from_string(json_field_string(fields, "severity"), event.severity)) {
        return std::nullopt;
    }
    if (!security_outcome_from_string(json_field_string(fields, "outcome"), event.outcome)) {
        return std::nullopt;
    }
    if (!security_actor_type_from_string(json_field_string(fields, "actor_type"), event.actor_type)) {
        return std::nullopt;
    }
    if (!security_subject_type_from_string(json_field_string(fields, "subject_type"),
                                           event.subject_type)) {
        return std::nullopt;
    }
    if (event.event_id.empty() || event.timestamp.empty()) {
        return std::nullopt;
    }
    if (compute_security_event_checksum(event) != event.payload_checksum) {
        return std::nullopt;
    }
    return event;
}

}  // namespace

SecurityEventJournalError::SecurityEventJournalError(const std::string& what)
    : std::runtime_error(what) {}

SecurityEventJournal::SecurityEventJournal(std::string persistence_path)
    : SecurityEventJournal(std::move(persistence_path), Options{}) {}

SecurityEventJournal::SecurityEventJournal(std::string persistence_path, Options options)
    : persistence_path_(std::move(persistence_path)), options_(options) {
    const std::filesystem::path target(persistence_path_);
    if (target.has_parent_path()) {
        std::filesystem::create_directories(target.parent_path());
    }
    load();
}

void SecurityEventJournal::load() {
    in_memory_.clear();
    recovered_line_count_ = 0;
    next_sequence_ = 1;
    // A rotated .1 file surviving a restart is itself evidence rotation
    // has happened at least once -- see the identical rationale in
    // go/internal/observability/security_event_journal.go's HasRotated.
    rotations_ = std::filesystem::exists(persistence_path_ + ".1") ? 1 : 0;
    if (!std::filesystem::exists(persistence_path_)) {
        return;
    }
    std::ifstream file(persistence_path_, std::ios::binary);
    if (!file) {
        // The path exists but couldn't be opened (permissions, etc.) --
        // this is an environment problem the operator must fix, not a
        // corrupt-content situation the journal can recover from itself.
        throw SecurityEventJournalError("failed to open security event journal: " +
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
        const auto event = parse_event_line(line);
        if (!event) {
            ++recovered_line_count_;
            continue;
        }
        in_memory_.push_back(*event);
        try {
            const auto sequence = std::stoull(event->event_id);
            if (sequence + 1 > next_sequence_) {
                next_sequence_ = sequence + 1;
            }
        } catch (const std::exception&) {
            // event_id wasn't one of our own zero-padded sequence
            // strings (e.g. a hand-edited or foreign line); keep the
            // record but don't let it influence sequence assignment.
        }
    }
    if (recovered_line_count_ > 0) {
        std::cerr << "security_event_journal: recovered from " << recovered_line_count_
                  << " corrupt/unparseable line(s) in " << persistence_path_ << "\n"
                  << std::flush;
    }
}

std::string SecurityEventJournal::next_event_id() {
    std::ostringstream out;
    out << std::setw(20) << std::setfill('0') << next_sequence_++;
    return out.str();
}

void SecurityEventJournal::append_line(const std::string& line) {
    std::ofstream file(persistence_path_, std::ios::binary | std::ios::app);
    if (!file) {
        std::cerr << "security_event_journal: failed to open for append: " << persistence_path_
                  << "\n"
                  << std::flush;
        return;
    }
    const std::string data = line + "\n";
    file.write(data.data(), static_cast<std::streamsize>(data.size()));
    file.flush();
    if (!file) {
        std::cerr << "security_event_journal: failed to append to: " << persistence_path_ << "\n"
                  << std::flush;
    }
}

void SecurityEventJournal::maybe_rotate() {
    std::error_code error_code;
    const auto current_size = std::filesystem::file_size(persistence_path_, error_code);
    if (error_code || current_size < options_.max_bytes_before_rotation) {
        return;
    }
    // Shift .N -> .N+1 for existing rotated files, oldest beyond
    // max_retained_files is dropped, then the active file becomes .1.
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
    // next_sequence_ intentionally continues rather than resetting to 1:
    // event_id must stay monotonic within one journal's lifetime even
    // across a rotation, so cursors issued against the pre-rotation
    // active file remain meaningfully ordered relative to new records.
}

std::string SecurityEventJournal::emit(SecurityEvent event) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (event.timestamp.empty()) {
        event.timestamp = format_iso8601_now();
    }
    if (event.event_id.empty()) {
        event.event_id = next_event_id();
    }
    const auto validation = validate_security_event(event);
    if (!validation.valid) {
        std::cerr << "security_event_journal: dropping invalid event ("
                  << to_string(event.event_type) << "): " << validation.reason << "\n"
                  << std::flush;
        return "";
    }
    event.payload_checksum = compute_security_event_checksum(event);

    try {
        maybe_rotate();
        append_line(serialize_full_event_json(event));
        in_memory_.push_back(event);
        return event.event_id;
    } catch (const std::exception& error) {
        std::cerr << "security_event_journal: failed to persist event: " << error.what() << "\n"
                  << std::flush;
        return "";
    }
}

SecurityEventJournal::ListResult SecurityEventJournal::list(const ListFilters& filters) const {
    std::lock_guard<std::mutex> lock(mutex_);
    ListResult result;
    bool past_cursor = filters.after_event_id.empty();
    for (const auto& event : in_memory_) {
        if (!past_cursor) {
            if (event.event_id == filters.after_event_id) {
                past_cursor = true;
            }
            continue;
        }
        if (filters.min_severity.has_value() &&
            static_cast<int>(event.severity) < static_cast<int>(*filters.min_severity)) {
            continue;
        }
        if (filters.subject_type.has_value() && event.subject_type != *filters.subject_type) {
            continue;
        }
        if (filters.event_type.has_value() && event.event_type != *filters.event_type) {
            continue;
        }
        if (result.events.size() >= filters.limit) {
            result.next_cursor = result.events.back().event_id;
            return result;
        }
        result.events.push_back(event);
    }
    return result;
}

std::size_t SecurityEventJournal::recovered_line_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return recovered_line_count_;
}

std::size_t SecurityEventJournal::size() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return in_memory_.size();
}

std::string SecurityEventJournal::last_record_timestamp() const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (in_memory_.empty()) {
        return "";
    }
    return in_memory_.back().timestamp;
}

bool SecurityEventJournal::has_rotated() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return rotations_ > 0;
}

}  // namespace fl::coordinator
