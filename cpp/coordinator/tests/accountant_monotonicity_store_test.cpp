#include "fl_coordinator/accountant_monotonicity_store.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_accountant_monotonicity_store_tests(const std::string& scratch_dir) {
    using fl::coordinator::AccountantMonotonicityStore;
    using fl::coordinator::AccountantMonotonicityStoreError;
    using fl::coordinator::MonotonicityCandidate;
    using fl::coordinator::MonotonicityRejectionReason;
    using fl::coordinator::TrackKey;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/monotonicity_store.dat";

    auto make_candidate = [](const std::string& run_id,
                             const std::string& client_id,
                             const std::string& worker_id,
                             std::uint64_t step,
                             double epsilon,
                             double delta,
                             const std::string& config_hash,
                             double now) {
        MonotonicityCandidate candidate;
        candidate.run_id = run_id;
        candidate.client_id = client_id;
        candidate.worker_id = worker_id;
        candidate.accountant_type = 1;
        candidate.step = step;
        candidate.epsilon = epsilon;
        candidate.delta = delta;
        candidate.accountant_state_hash = "hash-" + std::to_string(step);
        candidate.configuration_hash = config_hash;
        candidate.round_id = step;
        candidate.task_id = "task-" + std::to_string(step);
        candidate.now_unix_s = now;
        return candidate;
    };

    {
        AccountantMonotonicityStore store(store_path);

        const auto first =
            make_candidate("run-1", "client-1", "worker-1", 1, 0.5, 1e-5, "cfg-a", 100.0);
        const auto first_decision = store.validate(first);
        check(first_decision.accepted, "a brand-new track accepts its first candidate");
        store.commit(first);

        const auto second =
            make_candidate("run-1", "client-1", "worker-1", 2, 0.8, 1e-5, "cfg-a", 101.0);
        check(store.validate(second).accepted,
              "step 2 with non-decreasing epsilon following step 1 is accepted");
        store.commit(second);

        const auto lower_step =
            make_candidate("run-1", "client-1", "worker-1", 2, 1.0, 1e-5, "cfg-a", 102.0);
        const auto lower_step_decision = store.validate(lower_step);
        check(!lower_step_decision.accepted, "a non-increasing (duplicate) step is rejected");
        check(lower_step_decision.reason == MonotonicityRejectionReason::kStepNotIncreasing,
              "a non-increasing step is reported as kStepNotIncreasing");

        const auto lower_epsilon =
            make_candidate("run-1", "client-1", "worker-1", 3, 0.3, 1e-5, "cfg-a", 103.0);
        const auto lower_epsilon_decision = store.validate(lower_epsilon);
        check(!lower_epsilon_decision.accepted, "a lower epsilon at a higher step is rejected");
        check(lower_epsilon_decision.reason == MonotonicityRejectionReason::kEpsilonDecreased,
              "a lower epsilon is reported as kEpsilonDecreased");

        const auto changed_delta =
            make_candidate("run-1", "client-1", "worker-1", 3, 0.9, 2e-5, "cfg-a", 104.0);
        const auto changed_delta_decision = store.validate(changed_delta);
        check(!changed_delta_decision.accepted,
              "a changed delta within the same track is rejected");
        check(changed_delta_decision.reason == MonotonicityRejectionReason::kDeltaChanged,
              "a changed delta is reported as kDeltaChanged");

        const auto changed_config =
            make_candidate("run-1", "client-1", "worker-1", 3, 0.9, 1e-5, "cfg-b", 105.0);
        const auto changed_config_decision = store.validate(changed_config);
        check(!changed_config_decision.accepted,
              "a changed configuration_hash within the same track is rejected");
        check(changed_config_decision.reason ==
                  MonotonicityRejectionReason::kConfigurationHashChanged,
              "a changed configuration_hash is reported as kConfigurationHashChanged");

        // A different client_id/worker_id/accountant_type starts its own
        // independent track, unaffected by client-1's history.
        const auto other_client =
            make_candidate("run-1", "client-2", "worker-1", 1, 0.1, 1e-5, "cfg-a", 106.0);
        check(store.validate(other_client).accepted,
              "a different client_id starts its own independent track");

        const auto found = store.find(TrackKey{"run-1", "client-1", "worker-1", 1});
        check(found.has_value(), "find() locates a committed track");
        check(found->last_accepted_step == 2, "find() reports the last committed step");
        check(found->last_epsilon == 0.8, "find() reports the last committed epsilon");
    }

    {
        // Restart persistence: reopening the store from disk preserves
        // the committed track's monotonicity state.
        AccountantMonotonicityStore store(store_path);
        const auto after_restart =
            make_candidate("run-1", "client-1", "worker-1", 2, 1.0, 1e-5, "cfg-a", 200.0);
        const auto decision = store.validate(after_restart);
        check(!decision.accepted,
              "a non-increasing step is still rejected after reopening from disk");
        check(decision.reason == MonotonicityRejectionReason::kStepNotIncreasing,
              "restart-persisted state still enforces step monotonicity");

        const auto reset_key = TrackKey{"run-1", "client-1", "worker-1", 1};
        store.reset(reset_key, "explicit test reset", 201.0);
        check(!store.find(reset_key).has_value(), "reset() clears a track's history");
        const auto after_reset =
            make_candidate("run-1", "client-1", "worker-1", 1, 0.1, 1e-5, "cfg-a", 202.0);
        check(store.validate(after_reset).accepted,
              "after an explicit reset, the track behaves like a brand-new one");
    }

    {
        // Corruption detection: a truncated/corrupt file throws rather
        // than silently starting empty (matching WorkerIdentityRegistry/
        // ReplayProtectionStore's identical policy).
        const std::string corrupt_path = scratch_dir + "/corrupt.dat";
        {
            std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
            file << "record_count=1\nrecord=not-enough-fields\nchecksum=0000000000000000\n";
        }
        expect_throw([&]() { AccountantMonotonicityStore store(corrupt_path); },
                     "a structurally malformed record throws AccountantMonotonicityStoreError");

        const std::string bad_checksum_path = scratch_dir + "/bad_checksum.dat";
        {
            std::ofstream file(bad_checksum_path, std::ios::binary | std::ios::trunc);
            file << "record_count=0\nchecksum=deadbeefdeadbeef\n";
        }
        expect_throw([&]() { AccountantMonotonicityStore store(bad_checksum_path); },
                     "a checksum mismatch throws AccountantMonotonicityStoreError");
    }
}

}  // namespace fl::coordinator::testing
