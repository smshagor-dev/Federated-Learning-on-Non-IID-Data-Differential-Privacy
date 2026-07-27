#include "fl_coordinator/idempotency_store.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_idempotency_store_tests(const std::string& scratch_dir) {
    using fl::coordinator::IdempotencyStore;
    using fl::coordinator::IdempotencyStoreError;
    using fl::coordinator::IdempotentMutationRecord;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/idempotency_store.dat";

    {
        IdempotencyStore store(store_path);
        check(!store.find("RotateCoordinatorSigningKey", "req-1").has_value(),
              "an unknown (rpc_name, idempotency_key) pair has no recorded outcome");

        IdempotentMutationRecord record;
        record.rpc_name = "RotateCoordinatorSigningKey";
        record.idempotency_key = "req-1";
        record.accepted = true;
        record.result_key_id = "coord-key-2";
        record.previous_key_id = "coord-key-1";
        record.recorded_at_unix_s = 100.0;
        store.record(record);

        const auto found = store.find("RotateCoordinatorSigningKey", "req-1");
        check(found.has_value() && found->result_key_id == "coord-key-2",
              "a recorded outcome is found again under the same (rpc_name, idempotency_key)");

        expect_throw([&]() { store.record(record); },
                     "recording the same (rpc_name, idempotency_key) pair twice throws");

        check(!store.find("RevokeCoordinatorSigningKey", "req-1").has_value(),
              "a different rpc_name with the same idempotency_key is an independent track");
    }

    // Restart persistence.
    {
        IdempotencyStore restarted(store_path);
        const auto found = restarted.find("RotateCoordinatorSigningKey", "req-1");
        check(found.has_value() && found->accepted && found->previous_key_id == "coord-key-1",
              "a recorded outcome survives a restart");
    }

    // Corruption detection.
    {
        const std::string corrupt_path = scratch_dir + "/corrupt.dat";
        std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
        file << "record_count=1\n";
        file << "record=too\tfew\tfields\n";
        file << "checksum=0000000000000000\n";
        file.close();
        expect_throw([&]() { IdempotencyStore bad(corrupt_path); },
                     "a checksum-mismatched idempotency store file throws");
    }
}

}  // namespace fl::coordinator::testing
