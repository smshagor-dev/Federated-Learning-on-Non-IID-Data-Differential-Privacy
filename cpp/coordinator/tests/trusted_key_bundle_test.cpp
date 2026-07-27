#include "fl_coordinator/trusted_key_bundle.hpp"
#include "fl_coordinator/coordinator_signing_key_registry.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_trusted_key_bundle_tests(const std::string& scratch_dir) {
    using fl::coordinator::CoordinatorSigningKeyRegistry;
    using fl::coordinator::InitialCoordinatorSigningKeyRegistration;
    using fl::coordinator::read_bundle_version;
    using fl::coordinator::write_trusted_key_bundle;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string registry_path = scratch_dir + "/registry.dat";
    const std::string bundle_path = scratch_dir + "/bundle.json";

    CoordinatorSigningKeyRegistry registry(registry_path);
    InitialCoordinatorSigningKeyRegistration initial;
    initial.signing_key_id = "coord-key-1";
    initial.public_key_hex = std::string(64, 'a');
    initial.public_key_fingerprint = "fp-a";
    initial.now_unix_s = 100.0;
    registry.register_initial_key(initial);

    check(read_bundle_version(bundle_path) == 0, "a nonexistent bundle file reports version 0");

    const auto first_write = write_trusted_key_bundle(registry, bundle_path, "coordinator", 100.0);
    check(first_write.ok && first_write.bundle_version == 1,
          "the first bundle write succeeds and starts at version 1");
    check(std::filesystem::exists(bundle_path), "the bundle file is actually created");

    {
        std::ifstream file(bundle_path, std::ios::binary);
        std::ostringstream buffer;
        buffer << file.rdbuf();
        const auto content = buffer.str();
        check(content.find("\"coord-key-1\"") != std::string::npos,
              "the bundle contains the registered key's signing_key_id");
        check(content.find("\"active\"") != std::string::npos,
              "the bundle contains the ACTIVE status string");
        check(content.find("\"checksum\"") != std::string::npos,
              "the bundle contains a checksum field");
        check(content.find("private_key") == std::string::npos &&
                 content.find("private_key_raw") == std::string::npos,
              "the bundle never contains any private-key field");
    }

    check(read_bundle_version(bundle_path) == 1,
          "read_bundle_version reflects the just-written version");

    const auto second_write = write_trusted_key_bundle(registry, bundle_path, "coordinator", 101.0);
    check(second_write.ok && second_write.bundle_version == 2,
          "a second write increments the bundle version");

    // Corrupted bundle: read_bundle_version must not throw, just report 0.
    {
        const std::string corrupt_path = scratch_dir + "/corrupt_bundle.json";
        std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
        file << "not json at all";
        file.close();
        check(read_bundle_version(corrupt_path) == 0,
              "read_bundle_version returns 0 (never throws) for an unparseable file");
    }

    // Revoke the only key and confirm the bundle reflects zero trusted keys.
    registry.revoke_key("coord-key-1", "test", 200.0);
    const auto after_revoke = write_trusted_key_bundle(registry, bundle_path, "coordinator", 200.0);
    check(after_revoke.ok, "writing a bundle with zero trusted keys still succeeds");
    {
        std::ifstream file(bundle_path, std::ios::binary);
        std::ostringstream buffer;
        buffer << file.rdbuf();
        const auto content = buffer.str();
        check(content.find("\"keys\":[]") != std::string::npos,
              "the bundle's keys array is empty once the sole key is revoked");
        check(content.find("\"active_signing_key_id\":\"\"") != std::string::npos,
              "active_signing_key_id is empty once no ACTIVE key remains");
    }
}

}  // namespace fl::coordinator::testing
