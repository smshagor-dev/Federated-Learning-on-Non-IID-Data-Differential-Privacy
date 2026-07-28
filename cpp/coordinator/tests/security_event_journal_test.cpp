#include "fl_coordinator/security_event_journal.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_security_event_journal_tests(const std::string& scratch_dir) {
    using fl::coordinator::SecurityEvent;
    using fl::coordinator::SecurityEventJournal;
    using fl::coordinator::SecurityEventType;
    using fl::coordinator::SecuritySeverity;
    using fl::coordinator::SecuritySubjectType;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string journal_path = scratch_dir + "/security_events.jsonl";

    // Emit + round-trip.
    {
        SecurityEventJournal journal(journal_path);
        check(journal.size() == 0, "a fresh journal starts empty");

        SecurityEvent event;
        event.source_service = "coordinator";
        event.source_component = "worker_registry";
        event.event_type = SecurityEventType::kWorkerSuspended;
        event.worker_id = "worker-1";
        event.safe_actor_id = "go-api";
        event.reason_code = "administrative_suspension";
        journal.emit(event);

        check(journal.size() == 1, "emitting one event grows the journal to size 1");

        const auto listed = journal.list({});
        check(listed.events.size() == 1, "list() returns the emitted event");
        check(!listed.events[0].event_id.empty(), "emit() assigns a non-empty event_id");
        check(!listed.events[0].timestamp.empty(), "emit() assigns a non-empty timestamp");
        check(!listed.events[0].payload_checksum.empty(), "emit() assigns a payload_checksum");
        check(listed.events[0].worker_id == "worker-1", "round-tripped event preserves worker_id");
        check(listed.events[0].event_type == SecurityEventType::kWorkerSuspended,
              "round-tripped event preserves event_type");
    }

    // Restart persistence.
    {
        SecurityEventJournal restarted(journal_path);
        check(restarted.size() == 1, "a restarted journal reloads its one persisted event");
        check(restarted.recovered_line_count() == 0,
              "a clean file recovers zero corrupt lines on reload");
    }

    // Cursor pagination.
    {
        SecurityEventJournal journal(journal_path);
        SecurityEvent second;
        second.source_service = "coordinator";
        second.event_type = SecurityEventType::kWorkerActivated;
        second.worker_id = "worker-1";
        journal.emit(second);

        const auto first_page = journal.list({.limit = 1});
        check(first_page.events.size() == 1 && !first_page.next_cursor.empty(),
              "a page smaller than the journal returns a non-empty next_cursor");

        SecurityEventJournal::ListFilters next_filters;
        next_filters.after_event_id = first_page.next_cursor;
        const auto second_page = journal.list(next_filters);
        check(second_page.events.size() == 1 &&
                  second_page.events[0].event_type == SecurityEventType::kWorkerActivated,
              "the cursor from page one correctly resumes at the second event");
    }

    // Filtering.
    {
        SecurityEventJournal journal(journal_path);
        SecurityEventJournal::ListFilters type_filter;
        type_filter.event_type = SecurityEventType::kWorkerActivated;
        const auto filtered = journal.list(type_filter);
        check(filtered.events.size() == 1 &&
                  filtered.events[0].event_type == SecurityEventType::kWorkerActivated,
              "event_type filter returns only matching events");

        SecurityEventJournal::ListFilters severity_filter;
        severity_filter.min_severity = SecuritySeverity::kCritical;
        const auto none = journal.list(severity_filter);
        check(none.events.empty(),
              "a min_severity filter above every emitted event's severity returns nothing");
    }

    // Validation failure is dropped, not thrown or persisted.
    {
        const std::string invalid_path = scratch_dir + "/invalid.jsonl";
        SecurityEventJournal journal(invalid_path);
        SecurityEvent invalid;
        invalid.source_service = "";  // required field missing
        expect_no_throw([&]() { journal.emit(invalid); }, "emitting an invalid event never throws");
        check(journal.size() == 0, "an invalid event is dropped, not persisted");
    }

    // Corruption recovery: a hand-corrupted line is skipped, not fatal.
    {
        const std::string corrupt_path = scratch_dir + "/corrupt.jsonl";
        {
            SecurityEventJournal journal(corrupt_path);
            SecurityEvent event;
            event.source_service = "coordinator";
            event.event_type = SecurityEventType::kWorkerRegistered;
            journal.emit(event);
        }
        {
            std::ofstream file(corrupt_path, std::ios::app | std::ios::binary);
            file << "{not valid json at all\n";
            file << "{\"schema_version\":1,\"event_id\":\"x\"}\n";  // valid JSON, bad
                                                                    // checksum/fields
        }
        SecurityEventJournal reloaded(corrupt_path);
        check(reloaded.size() == 1,
              "a journal with two corrupt trailing lines keeps the one valid record");
        check(reloaded.recovered_line_count() == 2,
              "both corrupt lines are counted as recovered, not fatal");
    }

    // Rotation and retention.
    {
        const std::string rotate_path = scratch_dir + "/rotate.jsonl";
        SecurityEventJournal::Options options;
        options.max_bytes_before_rotation = 200;  // small, forces rotation quickly
        options.max_retained_files = 2;
        SecurityEventJournal journal(rotate_path, options);
        for (int i = 0; i < 20; ++i) {
            SecurityEvent event;
            event.source_service = "coordinator";
            event.event_type = SecurityEventType::kHeartbeatAccepted;
            event.worker_id = "worker-" + std::to_string(i);
            journal.emit(event);
        }
        check(std::filesystem::exists(rotate_path + ".1"), "rotation produces a .1 rotated file");
        check(!std::filesystem::exists(rotate_path + ".3"),
              "retention count of 2 never keeps a third rotated generation");
        check(
            journal.size() < 20,
            "the active file's in-memory view no longer holds every event once rotation occurred");
    }
}

}  // namespace fl::coordinator::testing
