#include "fl_coordinator/secure_aggregation_session_store.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_secure_aggregation_session_store_tests(const std::string& scratch_dir) {
    using fl::coordinator::is_terminal_session_state;
    using fl::coordinator::SecureAggregationSessionRecord;
    using fl::coordinator::SecureAggregationSessionStore;
    using fl::coordinator::SecureAggregationSessionStoreError;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/secure_aggregation_sessions.dat";

    check(!is_terminal_session_state("COHORT_FORMING"), "COHORT_FORMING is not a terminal state");
    check(!is_terminal_session_state("KEY_ADVERTISEMENT"),
          "KEY_ADVERTISEMENT is not a terminal state");
    check(is_terminal_session_state("COMPLETED"), "COMPLETED is terminal");
    check(is_terminal_session_state("ABORTED"), "ABORTED is terminal");
    check(is_terminal_session_state("FAILED"), "FAILED is terminal");

    {
        SecureAggregationSessionStore store(store_path);
        check(!store.find("session-1").has_value(), "a fresh store with no file yet starts empty");

        SecureAggregationSessionRecord record;
        record.session_id = "session-1";
        record.run_id = "run-1";
        record.round_id = 3;
        record.state = "COHORT_FORMING";
        record.created_at_unix_s = 100.0;
        record.updated_at_unix_s = 100.0;
        store.record_transition(record);

        record.state = "KEY_ADVERTISEMENT";
        record.updated_at_unix_s = 101.0;
        store.record_transition(record);

        const auto found = store.find("session-1");
        check(found.has_value(), "session-1 is found after recording transitions");
        check(found->state == "KEY_ADVERTISEMENT",
              "the latest transition's state is what is stored, not the first");
        check(found->run_id == "run-1", "run_id is preserved");
        check(found->round_id == 3, "round_id is preserved");
    }

    {
        // Re-open against the same file -- persistence must survive a
        // fresh SecureAggregationSessionStore instance the way a
        // coordinator restart would create one.
        SecureAggregationSessionStore store(store_path);
        const auto found = store.find("session-1");
        check(found.has_value(), "session-1 survives a reload from disk");
        check(found->state == "KEY_ADVERTISEMENT",
              "reloaded record preserves its latest recorded state");

        SecureAggregationSessionRecord completed;
        completed.session_id = "session-2";
        completed.run_id = "run-1";
        completed.round_id = 4;
        completed.state = "COMPLETED";
        completed.created_at_unix_s = 200.0;
        completed.updated_at_unix_s = 205.0;
        completed.completed_at_unix_s = 205.0;
        store.record_transition(completed);

        check(store.all().size() == 2, "all() returns every distinct session_id ever recorded");
    }

    {
        // Work item 16: restart-abort reconciliation. session-1 was left
        // in KEY_ADVERTISEMENT (non-terminal) by the previous "process" --
        // a fresh store simulating coordinator restart must mark it
        // ABORTED. session-2 was already COMPLETED and must be left
        // alone.
        SecureAggregationSessionStore store(store_path);
        const auto reconciled = store.reconcile_after_restart(/*now=*/300.0);
        check(reconciled.size() == 1, "exactly one non-terminal session is reconciled");
        check(reconciled.front() == "session-1",
              "the reconciled session is the non-terminal one (session-1)");

        const auto session1 = store.find("session-1");
        check(session1.has_value() && session1->state == "ABORTED",
              "session-1 is marked ABORTED after restart reconciliation");
        check(session1->abort_reason == "coordinator_restart",
              "session-1's abort_reason records that this was a restart-triggered abort");

        const auto session2 = store.find("session-2");
        check(session2.has_value() && session2->state == "COMPLETED",
              "session-2 (already terminal) is left untouched by restart reconciliation");

        // Idempotent: calling it again finds nothing left to reconcile.
        const auto reconciled_again = store.reconcile_after_restart(400.0);
        check(reconciled_again.empty(), "a second reconciliation pass finds nothing left to do");
    }

    {
        // Restart-persisted: the ABORTED/coordinator_restart state from
        // the previous block must itself survive yet another reload.
        SecureAggregationSessionStore store(store_path);
        const auto session1 = store.find("session-1");
        check(session1.has_value() && session1->state == "ABORTED",
              "the restart-abort reconciliation itself is durably persisted, not just in-memory");
    }

    {
        // Fail-closed on corruption -- same discipline as
        // WorkerIdentityRegistry: a damaged file must never silently be
        // treated as empty.
        const std::string corrupt_path = scratch_dir + "/corrupt.dat";
        {
            std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
            file << "record_count=1\n";
            file << "record=not-a-valid-record\n";
            file << "checksum=0000000000000000\n";
        }
        expect_throw([&]() { SecureAggregationSessionStore store(corrupt_path); },
                     "a corrupt secure aggregation session store file is rejected, never silently "
                     "treated as empty");
    }
}

}  // namespace fl::coordinator::testing
