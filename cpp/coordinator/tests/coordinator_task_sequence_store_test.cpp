#include "fl_coordinator/coordinator_task_sequence_store.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_coordinator_task_sequence_store_tests(const std::string& scratch_dir) {
    using fl::coordinator::CoordinatorTaskSequenceStore;
    using fl::coordinator::CoordinatorTaskSequenceStoreError;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/coordinator_task_sequence_store.dat";

    {
        CoordinatorTaskSequenceStore store(store_path);
        check(store.peek("key-1", "worker-1") == 0, "a brand-new track has no prior sequence");
        check(store.next_sequence("key-1", "worker-1") == 1,
              "the first sequence number issued for a track is 1");
        check(store.next_sequence("key-1", "worker-1") == 2,
              "the second sequence number issued for the same track is 2");
        check(store.next_sequence("key-1", "worker-2") == 1,
              "a different worker_id under the same signing key starts its own track at 1");
        check(store.next_sequence("key-2", "worker-1") == 1,
              "a different signing_key_id for the same worker starts its own track at 1");
        check(store.peek("key-1", "worker-1") == 2, "peek reflects the last issued value");
    }

    // Restart persistence: sequence numbers already handed out must never be reissued.
    {
        CoordinatorTaskSequenceStore restarted(store_path);
        check(restarted.peek("key-1", "worker-1") == 2,
              "sequence state for (key-1, worker-1) survives a restart");
        check(restarted.next_sequence("key-1", "worker-1") == 3,
              "the next sequence issued after restart continues from the persisted value");
    }

    // Corruption detection.
    {
        const std::string corrupt_path = scratch_dir + "/corrupt.dat";
        {
            std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
            file << "record=key\tworker\tnot-a-number\n";
            file << "checksum=0000000000000000\n";
        }
        expect_throw([&]() { CoordinatorTaskSequenceStore bad(corrupt_path); },
                     "a checksum-mismatched coordinator task sequence store throws");
    }
}

}  // namespace fl::coordinator::testing
