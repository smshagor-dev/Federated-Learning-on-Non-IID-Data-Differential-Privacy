// Coordinator signing-key recovery/administration tool -- Security
// Administration slice, Work Package G. See
// docs/coordinator-key-recovery.md.
//
// A command-line administration tool operating directly on the
// coordinator's persisted files (CoordinatorSigningKeyRegistry, the
// keyed private-key directory, and the trusted-key bundle) -- no
// running gRPC server, no network connection, and (unlike
// coordinator_cli.cpp) no protobuf/gRPC dependency at all, so this
// builds and runs on this Windows/MSVC development machine without a
// local gRPC toolchain. Intended for the documented recovery
// scenarios: a lost active private key, corrupted key metadata, a
// corrupted trusted bundle, an expired active key, or a revoked-only-
// active-key situation -- see docs/coordinator-key-recovery.md for the
// full operator runbook this tool implements.
#include "fl_coordinator/coordinator_signing_identity.hpp"
#include "fl_coordinator/coordinator_signing_key_registry.hpp"
#include "fl_coordinator/signed_envelope_verifier.hpp"
#include "fl_coordinator/trusted_key_bundle.hpp"

#include <chrono>
#include <iomanip>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace {

double now_unix_s() {
    return static_cast<double>(std::chrono::duration_cast<std::chrono::milliseconds>(
                                   std::chrono::system_clock::now().time_since_epoch())
                                   .count()) /
           1000.0;
}

std::map<std::string, std::string> parse_flags(int argc, char** argv, int start) {
    std::map<std::string, std::string> flags;
    for (int i = start; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg.rfind("--", 0) != 0) {
            continue;
        }
        arg = arg.substr(2);
        const auto equals = arg.find('=');
        if (equals != std::string::npos) {
            flags[arg.substr(0, equals)] = arg.substr(equals + 1);
            continue;
        }
        if (i + 1 < argc) {
            flags[arg] = argv[i + 1];
            ++i;
        } else {
            flags[arg] = "";
        }
    }
    return flags;
}

std::string flag(const std::map<std::string, std::string>& flags, const std::string& name,
                 const std::string& default_value = "") {
    const auto it = flags.find(name);
    return it != flags.end() ? it->second : default_value;
}

void print_key_record(const fl::coordinator::CoordinatorSigningKeyRecord& record) {
    std::cout << "  signing_key_id=" << record.signing_key_id
              << " status=" << fl::coordinator::to_string(record.status)
              << " fingerprint=" << record.public_key_fingerprint.substr(0, 16) << "..."
              << " created_at=" << record.created_at_unix_s
              << " expires_at=" << record.expires_at_unix_s
              << " grace_period_end=" << record.grace_period_end_unix_s;
    if (record.status == fl::coordinator::CoordinatorSigningKeyStatus::kRevoked) {
        std::cout << " revoked_at=" << record.revoked_at_unix_s
                  << " revocation_reason=\"" << record.revocation_reason << "\"";
    }
    std::cout << "\n";
}

int run_show(const std::map<std::string, std::string>& flags) {
    const auto registry_path = flag(flags, "registry-path", "coordinator_signing_key_registry.dat");
    try {
        fl::coordinator::CoordinatorSigningKeyRegistry registry(registry_path);
        const auto now = now_unix_s();
        const auto keys = registry.list(now);
        std::cout << "coordinator signing keys (" << keys.size() << " total):\n";
        for (const auto& record : keys) {
            print_key_record(record);
        }
        const auto active = registry.active_key(now);
        std::cout << (active.has_value() ? "ACTIVE key present: " + active->signing_key_id
                                        : std::string("no ACTIVE key -- production task issuance "
                                                      "is currently stopped"))
                  << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
}

int run_rotate(const std::map<std::string, std::string>& flags) {
    const auto registry_path = flag(flags, "registry-path", "coordinator_signing_key_registry.dat");
    const auto key_dir = flag(flags, "key-dir", "coordinator_signing_keys");
    const auto bundle_path = flag(flags, "bundle-path");
    const auto identity_label = flag(flags, "identity-label", "coordinator");
    const auto expected_current = flag(flags, "expected-current-key-id");
    const auto reason = flag(flags, "reason", "manual recovery rotation");
    const double grace_period_seconds = std::stod(flag(flags, "grace-period-seconds", "3600"));
    const double expires_in_seconds = std::stod(flag(flags, "expires-in-seconds", "0"));

    try {
        fl::coordinator::CoordinatorSigningKeyRegistry registry(registry_path);
        const auto now = now_unix_s();

        std::string current_key_id = expected_current;
        if (current_key_id.empty()) {
            const auto active = registry.active_key(now);
            if (active.has_value()) {
                current_key_id = active->signing_key_id;
            }
        }

        const auto new_identity = fl::coordinator::generate_coordinator_signing_identity();

        fl::coordinator::CoordinatorSigningKeyRotationRequest rotation;
        rotation.current_signing_key_id = current_key_id;
        rotation.new_signing_key_id = new_identity.key_id;
        rotation.new_public_key_hex = new_identity.public_key_hex;
        rotation.new_public_key_fingerprint =
            fl::coordinator::public_key_fingerprint_hex(new_identity.public_key_hex);
        rotation.new_key_expires_at_unix_s =
            expires_in_seconds > 0.0 ? now + expires_in_seconds : 0.0;
        rotation.grace_period_seconds = grace_period_seconds;
        rotation.now_unix_s = now;

        // No current key at all (e.g. the "lost active private key" or
        // "revoked only active key" recovery scenario, per
        // docs/coordinator-key-recovery.md): register the new key as a
        // fresh initial key instead of a rotation, since there is
        // nothing valid to rotate *from*.
        if (current_key_id.empty() || !registry.find(current_key_id, now).has_value() ||
            registry.find(current_key_id, now)->status !=
                fl::coordinator::CoordinatorSigningKeyStatus::kActive) {
            std::cerr << "no ACTIVE current key found -- registering the new key as a fresh "
                        "initial key (recovery path), not a rotation\n";
            (void)fl::coordinator::save_keyed_coordinator_signing_identity(new_identity, key_dir);
            fl::coordinator::InitialCoordinatorSigningKeyRegistration registration;
            registration.signing_key_id = new_identity.key_id;
            registration.public_key_hex = new_identity.public_key_hex;
            registration.public_key_fingerprint = rotation.new_public_key_fingerprint;
            registration.now_unix_s = now;
            registration.expires_at_unix_s = rotation.new_key_expires_at_unix_s;
            registry.register_initial_key(registration);
            std::cout << "registered a new initial coordinator signing key: " << new_identity.key_id
                      << "\n";
        } else {
            const auto validation = registry.validate_rotation(rotation);
            if (!validation.accepted) {
                std::cerr << "rotation rejected: " << to_string(validation.reason) << " -- "
                          << validation.detail << "\n";
                return 1;
            }
            (void)fl::coordinator::save_keyed_coordinator_signing_identity(new_identity, key_dir);
            const auto committed = registry.commit_rotation(rotation);
            std::cout << "rotated coordinator signing key: " << committed.previous_key.signing_key_id
                      << " (now " << to_string(committed.previous_key.status) << ") -> "
                      << committed.new_key.signing_key_id << " (ACTIVE)\n"
                      << "reason: " << reason << "\n";
        }

        if (!bundle_path.empty()) {
            const auto bundle_result =
                fl::coordinator::write_trusted_key_bundle(registry, bundle_path, identity_label, now);
            if (!bundle_result.ok) {
                std::cerr << "warning: trusted-key bundle regeneration failed: "
                          << bundle_result.reason << "\n";
                return 1;
            }
            std::cout << "trusted-key bundle regenerated (version " << bundle_result.bundle_version
                      << ") at " << bundle_path << "\n";
        } else {
            std::cout << "no --bundle-path given -- trusted-key bundle was NOT regenerated; "
                        "workers will not see the new key until it is\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
}

int run_revoke(const std::map<std::string, std::string>& flags) {
    const auto registry_path = flag(flags, "registry-path", "coordinator_signing_key_registry.dat");
    const auto bundle_path = flag(flags, "bundle-path");
    const auto identity_label = flag(flags, "identity-label", "coordinator");
    const auto key_id = flag(flags, "key-id");
    const auto reason = flag(flags, "reason", "manual recovery revocation");
    if (key_id.empty()) {
        std::cerr << "error: --key-id is required\n";
        return 1;
    }

    try {
        fl::coordinator::CoordinatorSigningKeyRegistry registry(registry_path);
        const auto now = now_unix_s();
        const auto record = registry.revoke_key(key_id, reason, now);
        std::cout << "revoked coordinator signing key: " << record.signing_key_id << "\n";
        if (!registry.active_key(now).has_value()) {
            std::cout << "WARNING: no ACTIVE key remains -- production task issuance is now "
                        "stopped until a new key is rotated in (see the 'rotate' subcommand)\n";
        }
        if (!bundle_path.empty()) {
            const auto bundle_result =
                fl::coordinator::write_trusted_key_bundle(registry, bundle_path, identity_label, now);
            if (!bundle_result.ok) {
                std::cerr << "warning: trusted-key bundle regeneration failed: "
                          << bundle_result.reason << "\n";
                return 1;
            }
            std::cout << "trusted-key bundle regenerated (version " << bundle_result.bundle_version
                      << ") at " << bundle_path << "\n";
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
}

int run_regenerate_bundle(const std::map<std::string, std::string>& flags) {
    const auto registry_path = flag(flags, "registry-path", "coordinator_signing_key_registry.dat");
    const auto bundle_path = flag(flags, "bundle-path");
    const auto identity_label = flag(flags, "identity-label", "coordinator");
    if (bundle_path.empty()) {
        std::cerr << "error: --bundle-path is required\n";
        return 1;
    }
    try {
        fl::coordinator::CoordinatorSigningKeyRegistry registry(registry_path);
        const auto result =
            fl::coordinator::write_trusted_key_bundle(registry, bundle_path, identity_label,
                                                       now_unix_s());
        if (!result.ok) {
            std::cerr << "error: " << result.reason << "\n";
            return 1;
        }
        std::cout << "trusted-key bundle regenerated (version " << result.bundle_version << ") at "
                  << bundle_path << "\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 1;
    }
}

void print_usage() {
    std::cout << "usage: coordinator_key_admin_cli <command> [--flag value ...]\n"
                "commands:\n"
                "  show               --registry-path <path>\n"
                "  rotate             --registry-path <path> --key-dir <dir> "
                "[--bundle-path <path>] [--expected-current-key-id <id>] "
                "[--grace-period-seconds <n>] [--expires-in-seconds <n>] [--reason <text>]\n"
                "  revoke             --registry-path <path> --key-id <id> "
                "[--bundle-path <path>] [--reason <text>]\n"
                "  regenerate-bundle  --registry-path <path> --bundle-path <path>\n";
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        print_usage();
        return 1;
    }
    const std::string command = argv[1];
    const auto flags = parse_flags(argc, argv, 2);

    if (command == "show") return run_show(flags);
    if (command == "rotate") return run_rotate(flags);
    if (command == "revoke") return run_revoke(flags);
    if (command == "regenerate-bundle") return run_regenerate_bundle(flags);

    std::cerr << "unknown command: " << command << "\n";
    print_usage();
    return 1;
}
