#include "fl_coordinator/security_audit_journal.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_security_audit_journal_tests(const std::string& scratch_dir) {
    using fl::coordinator::SecurityAuditJournal;
    using fl::coordinator::SecurityAuditRecord;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string journal_path = scratch_dir + "/security_audit.jsonl";

    // Append + round-trip.
    {
        SecurityAuditJournal journal(journal_path);
        check(journal.size() == 0, "a fresh audit journal starts empty");

        SecurityAuditRecord record;
        record.safe_actor_id = "go-api";
        record.actor_role = "service";
        record.action = "SuspendWorker";
        record.resource_type = "worker_identity";
        record.resource_id = "worker-1";
        record.outcome = "ACCEPTED";
        record.reason = "administrative_suspension";
        journal.append(record);

        check(journal.size() == 1, "appending one record grows the journal to size 1");
        const auto listed = journal.list({});
        check(listed.records.size() == 1, "list() returns the appended record");
        check(!listed.records[0].record_id.empty(), "append() assigns a non-empty record_id");
        check(!listed.records[0].payload_checksum.empty(), "append() assigns a payload_checksum");
        check(listed.records[0].action == "SuspendWorker", "round-tripped record preserves action");
    }

    // Restart persistence.
    {
        SecurityAuditJournal restarted(journal_path);
        check(restarted.size() == 1, "a restarted audit journal reloads its one persisted record");
    }

    // Filtering (actor, action, resource_type, outcome).
    {
        SecurityAuditJournal journal(journal_path);
        SecurityAuditRecord second;
        second.safe_actor_id = "worker-service";
        second.actor_role = "service";
        second.action = "RevokeWorkerSigningKey";
        second.resource_type = "worker_signing_key";
        second.resource_id = "key-1";
        second.outcome = "REJECTED";
        second.reason = "not_authorized";
        journal.append(second);

        SecurityAuditJournal::ListFilters by_action;
        by_action.action = "RevokeWorkerSigningKey";
        const auto by_action_result = journal.list(by_action);
        check(by_action_result.records.size() == 1 &&
                  by_action_result.records[0].resource_id == "key-1",
              "action filter isolates the matching record");

        SecurityAuditJournal::ListFilters by_outcome;
        by_outcome.outcome = "REJECTED";
        const auto by_outcome_result = journal.list(by_outcome);
        check(by_outcome_result.records.size() == 1, "outcome filter isolates the matching record");

        SecurityAuditJournal::ListFilters by_actor;
        by_actor.actor_id = "go-api";
        const auto by_actor_result = journal.list(by_actor);
        check(by_actor_result.records.size() == 1 &&
                  by_actor_result.records[0].action == "SuspendWorker",
              "actor filter isolates the matching record");
    }

    // Pagination.
    {
        SecurityAuditJournal journal(journal_path);
        const auto first_page = journal.list({.limit = 1});
        check(first_page.records.size() == 1 && !first_page.next_cursor.empty(),
              "a page smaller than the journal returns a non-empty next_cursor");
    }

    // Corruption recovery.
    {
        const std::string corrupt_path = scratch_dir + "/corrupt_audit.jsonl";
        {
            SecurityAuditJournal journal(corrupt_path);
            SecurityAuditRecord record;
            record.action = "RotateCoordinatorSigningKey";
            record.outcome = "ACCEPTED";
            journal.append(record);
        }
        {
            std::ofstream file(corrupt_path, std::ios::app | std::ios::binary);
            file << "not json\n";
        }
        SecurityAuditJournal reloaded(corrupt_path);
        check(reloaded.size() == 1, "a trailing corrupt line does not remove the valid record");
        check(reloaded.recovered_line_count() == 1, "the corrupt line is counted as recovered");
    }

    // Rotation and retention.
    {
        const std::string rotate_path = scratch_dir + "/rotate_audit.jsonl";
        SecurityAuditJournal::Options options;
        options.max_bytes_before_rotation = 200;
        options.max_retained_files = 2;
        SecurityAuditJournal journal(rotate_path, options);
        for (int i = 0; i < 20; ++i) {
            SecurityAuditRecord record;
            record.action = "Heartbeat";
            record.resource_id = "worker-" + std::to_string(i);
            record.outcome = "ACCEPTED";
            journal.append(record);
        }
        check(std::filesystem::exists(rotate_path + ".1"), "rotation produces a .1 rotated file");
        check(!std::filesystem::exists(rotate_path + ".3"),
              "retention count of 2 never keeps a third rotated generation");
    }
}

}  // namespace fl::coordinator::testing
