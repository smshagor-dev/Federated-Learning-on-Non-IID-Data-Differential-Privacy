#include "fl_coordinator/scaffold_client_state.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>

namespace fl::coordinator::testing {

namespace {

// Duplicated from scaffold_client_state.cpp (kept private there) so this
// test can construct a payload with a *correct* checksum around
// deliberately-invalid field data — otherwise every hand-edited payload
// would be rejected at the checksum-mismatch stage before ever reaching
// the check under test (e.g. shape validation).
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

fl::core::TensorDescriptor descriptor() {
    return fl::core::TensorDescriptor{.name = "weight", .shape = {2}, .dtype = fl::core::DType::kFloat32};
}

fl::coordinator::ClientAlgorithmState make_state(const std::string& run_id, const std::string& client_id, const std::string& model_version) {
    fl::coordinator::ClientAlgorithmState state;
    state.run_id = run_id;
    state.client_id = client_id;
    state.algorithm = "scaffold";
    state.model_version = model_version;
    state.control_variate.insert(fl::core::TensorBuffer(descriptor(), {0.1, 0.2}));
    return state;
}

}  // namespace

void run_scaffold_client_state_tests(const std::string& scratch_dir) {
    using fl::coordinator::ClientAlgorithmStateCorruptionError;
    using fl::coordinator::FilesystemClientAlgorithmStateStore;
    using fl::coordinator::StaleClientAlgorithmStateError;

    std::filesystem::remove_all(scratch_dir);
    FilesystemClientAlgorithmStateStore store(scratch_dir);

    {
        // Missing initial state: normal "first participation" case, must
        // return std::nullopt, not throw.
        const auto loaded = store.load("run-1", "client-never-seen", "v0");
        check(!loaded.has_value(), "missing client state returns std::nullopt, not an error");
    }

    {
        // Save/load round trip.
        const auto state = make_state("run-1", "client-a", "v0");
        store.save("run-1", "client-a", state);
        const auto loaded = store.load("run-1", "client-a", "v0");
        check(loaded.has_value(), "a saved client state is found on load");
        check(loaded->control_variate.at("weight").values() == state.control_variate.at("weight").values(),
              "loaded control variate values match what was saved");

        // Overwrite (round 2) and confirm the new value wins.
        auto next_state = state;
        next_state.model_version = "v1";
        next_state.control_variate.assign(fl::core::TensorBuffer(descriptor(), {0.5, 0.6}));
        store.save("run-1", "client-a", next_state);
        const auto loaded_v1 = store.load("run-1", "client-a", "v1");
        check(loaded_v1.has_value() && loaded_v1->control_variate.at("weight").values()[0] == 0.5,
              "a second save overwrites the client's state (not append-only)");
    }

    {
        // Stale version: state was saved against model_version "v1" but
        // the caller asks to load it against "v2".
        store.save("run-1", "client-b", make_state("run-1", "client-b", "v1"));
        expect_throw(
            [&]() { (void)store.load("run-1", "client-b", "v2"); },
            "loading with a mismatched model_version throws StaleClientAlgorithmStateError"
        );
        bool correct_type = false;
        try {
            (void)store.load("run-1", "client-b", "v2");
        } catch (const StaleClientAlgorithmStateError&) {
            correct_type = true;
        } catch (...) {
        }
        check(correct_type, "the stale-version rejection is specifically StaleClientAlgorithmStateError");
    }

    {
        // Wrong client: manually place a state file saved under one
        // client_id at another client's path.
        const auto wrong_client_state = make_state("run-1", "client-c", "v0");
        store.save("run-1", "client-d", wrong_client_state);  // path is client-d's, contents say client-c
        expect_throw(
            [&]() { (void)store.load("run-1", "client-d", "v0"); },
            "a state file whose identity doesn't match the requested client_id is rejected"
        );
    }

    {
        // Checksum corruption: flip a byte in the on-disk file.
        store.save("run-1", "client-e", make_state("run-1", "client-e", "v0"));
        const auto path = store.path_for("run-1", "client-e");
        {
            std::fstream file(path, std::ios::in | std::ios::out | std::ios::binary);
            file.seekp(5);
            char original = 0;
            file.get(original);
            file.seekp(5);
            file.put(original == 'a' ? 'b' : 'a');
        }
        expect_throw(
            [&]() { (void)store.load("run-1", "client-e", "v0"); },
            "a checksum-corrupted client state file is rejected"
        );
    }

    {
        // Shape mismatch: values count doesn't match declared shape.
        // Correctly checksummed so the failure is specifically shape
        // validation (TensorBuffer's own element_count check), not a
        // checksum rejection.
        const auto path = store.path_for("run-1", "client-f");
        std::filesystem::create_directories(std::filesystem::path(path).parent_path());
        const std::string body =
            "schema_version=1\nrun_id=run-1\nclient_id=client-f\nalgorithm=scaffold\nmodel_version=v0\n"
            "control_variate_count=1\ncontrol_variate_tensor=weight|f32|2|0.1\n";  // shape=2 but only 1 value
        std::ofstream file(path, std::ios::binary | std::ios::trunc);
        file << body << "checksum=" << hash_to_hex(fnv1a_hash(body)) << "\n";
        file.close();
        expect_throw(
            [&]() { (void)store.load("run-1", "client-f", "v0"); },
            "a shape-inconsistent (but checksum-valid) client state file is rejected"
        );
    }
}

}  // namespace fl::coordinator::testing
