#include "fl_coordinator/coordinator_signing_key_registry.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_coordinator_signing_key_registry_tests(const std::string& scratch_dir) {
    using fl::coordinator::CoordinatorSigningKeyRegistry;
    using fl::coordinator::CoordinatorSigningKeyRegistryError;
    using fl::coordinator::CoordinatorSigningKeyRotationRejectionReason;
    using fl::coordinator::CoordinatorSigningKeyRotationRequest;
    using fl::coordinator::CoordinatorSigningKeyStatus;
    using fl::coordinator::InitialCoordinatorSigningKeyRegistration;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/coordinator_signing_key_registry.dat";

    auto make_initial =
        [](const std::string& key_id, const std::string& public_key_hex, double now) {
            InitialCoordinatorSigningKeyRegistration request;
            request.signing_key_id = key_id;
            request.public_key_hex = public_key_hex;
            request.public_key_fingerprint = "fp-" + public_key_hex;
            request.now_unix_s = now;
            request.expires_at_unix_s = 0.0;
            return request;
        };

    const std::string kKeyA(64, 'a');
    const std::string kKeyB(64, 'b');
    const std::string kKeyC(64, 'c');

    {
        CoordinatorSigningKeyRegistry registry(store_path);

        const auto record =
            registry.register_initial_key(make_initial("coord-key-1", kKeyA, 100.0));
        check(record.status == CoordinatorSigningKeyStatus::kActive,
              "the coordinator's first-ever signing key is ACTIVE immediately");

        const auto refreshed =
            registry.register_initial_key(make_initial("coord-key-1", kKeyA, 101.0));
        check(refreshed.created_at_unix_s == 100.0,
              "re-registering the identical key is idempotent (created_at unchanged)");

        expect_throw(
            [&]() { registry.register_initial_key(make_initial("coord-key-1", kKeyB, 102.0)); },
            "a key-swap attempt under the same signing_key_id is rejected");

        expect_throw(
            [&]() { registry.register_initial_key(make_initial("coord-key-2", kKeyB, 103.0)); },
            "registering a second initial key while one is already ACTIVE is rejected");

        const auto active = registry.active_key(150.0);
        check(active.has_value() && active->signing_key_id == "coord-key-1",
              "active_key() returns the coordinator's current ACTIVE key");

        // Rotation: validate then commit.
        CoordinatorSigningKeyRotationRequest rotation;
        rotation.current_signing_key_id = "coord-key-1";
        rotation.new_signing_key_id = "coord-key-2";
        rotation.new_public_key_hex = kKeyB;
        rotation.new_public_key_fingerprint = "fp-" + kKeyB;
        rotation.new_key_expires_at_unix_s = 0.0;
        rotation.grace_period_seconds = 3600.0;
        rotation.now_unix_s = 200.0;

        const auto validated = registry.validate_rotation(rotation);
        check(validated.accepted, "a well-formed rotation request validates as accepted");

        const auto committed = registry.commit_rotation(rotation);
        check(committed.accepted, "commit_rotation succeeds after a validated rotation");
        check(committed.new_key.status == CoordinatorSigningKeyStatus::kActive,
              "the new key becomes ACTIVE on commit");
        check(committed.previous_key.status == CoordinatorSigningKeyStatus::kGracePeriod,
              "the previous key becomes GRACE_PERIOD on commit (grace_period_seconds > 0)");

        const auto trusted = registry.trusted_public_keys(210.0);
        check(
            trusted.size() == 2,
            "trusted_public_keys() includes both the new ACTIVE key and the old GRACE_PERIOD key");

        const auto trusted_after_grace = registry.trusted_public_keys(4000.0);
        check(trusted_after_grace.size() == 1,
              "trusted_public_keys() excludes a lazily-expired GRACE_PERIOD key");

        // Rejection reasons.
        CoordinatorSigningKeyRotationRequest bad_current = rotation;
        bad_current.current_signing_key_id = "unknown-key";
        bad_current.new_signing_key_id = "coord-key-3";
        const auto bad_current_result = registry.validate_rotation(bad_current);
        check(!bad_current_result.accepted &&
                  bad_current_result.reason ==
                      CoordinatorSigningKeyRotationRejectionReason::kUnknownCurrentKey,
              "rotation from an unknown current key is rejected");

        CoordinatorSigningKeyRotationRequest stale_current = rotation;
        stale_current.current_signing_key_id = "coord-key-1";  // now GRACE_PERIOD, not ACTIVE
        stale_current.new_signing_key_id = "coord-key-3";
        const auto stale_result = registry.validate_rotation(stale_current);
        check(!stale_result.accepted &&
                  stale_result.reason ==
                      CoordinatorSigningKeyRotationRejectionReason::kCurrentKeyNotActive,
              "rotation authorized by a non-ACTIVE (grace-period) key is rejected");

        CoordinatorSigningKeyRotationRequest dup_id = rotation;
        dup_id.current_signing_key_id = "coord-key-2";
        dup_id.new_signing_key_id = "coord-key-1";  // already exists
        const auto dup_result = registry.validate_rotation(dup_id);
        check(!dup_result.accepted &&
                  dup_result.reason ==
                      CoordinatorSigningKeyRotationRejectionReason::kDuplicateNewKeyId,
              "rotation to an already-registered key_id is rejected");

        CoordinatorSigningKeyRotationRequest excessive_grace = rotation;
        excessive_grace.current_signing_key_id = "coord-key-2";
        excessive_grace.new_signing_key_id = "coord-key-3";
        excessive_grace.new_public_key_hex = kKeyC;
        excessive_grace.new_public_key_fingerprint = "fp-" + kKeyC;
        excessive_grace.grace_period_seconds =
            CoordinatorSigningKeyRegistry::kMaxGracePeriodSeconds + 1.0;
        const auto excessive_result = registry.validate_rotation(excessive_grace);
        check(!excessive_result.accepted &&
                  excessive_result.reason ==
                      CoordinatorSigningKeyRotationRejectionReason::kExcessiveGracePeriod,
              "an excessive grace period is rejected");

        CoordinatorSigningKeyRotationRequest bad_expiry = rotation;
        bad_expiry.current_signing_key_id = "coord-key-2";
        bad_expiry.new_signing_key_id = "coord-key-4";
        bad_expiry.new_public_key_hex = std::string(64, 'd');
        bad_expiry.new_public_key_fingerprint = "fp-d";
        bad_expiry.new_key_expires_at_unix_s = 150.0;  // <= now_unix_s (200.0)
        const auto bad_expiry_result = registry.validate_rotation(bad_expiry);
        check(!bad_expiry_result.accepted &&
                  bad_expiry_result.reason ==
                      CoordinatorSigningKeyRotationRejectionReason::kInvalidExpiry,
              "a new-key expiry not strictly after now is rejected");

        CoordinatorSigningKeyRotationRequest excessive_lifetime = rotation;
        excessive_lifetime.current_signing_key_id = "coord-key-2";
        excessive_lifetime.new_signing_key_id = "coord-key-5";
        excessive_lifetime.new_public_key_hex = std::string(64, 'e');
        excessive_lifetime.new_public_key_fingerprint = "fp-e";
        excessive_lifetime.new_key_expires_at_unix_s =
            rotation.now_unix_s + CoordinatorSigningKeyRegistry::kMaxCoordinatorKeyLifetimeSeconds +
            1.0;
        const auto excessive_lifetime_result = registry.validate_rotation(excessive_lifetime);
        check(!excessive_lifetime_result.accepted &&
                  excessive_lifetime_result.reason ==
                      CoordinatorSigningKeyRotationRejectionReason::kExcessiveKeyLifetime,
              "a new-key expiry beyond the maximum coordinator-key lifetime is rejected");

        // Revocation.
        const auto revoked = registry.revoke_key("coord-key-2", "compromised", 300.0);
        check(revoked.status == CoordinatorSigningKeyStatus::kRevoked,
              "revoke_key transitions the key to REVOKED");
        const auto revoked_again = registry.revoke_key("coord-key-2", "different reason", 301.0);
        check(revoked_again.revocation_reason == "compromised",
              "revocation is idempotent -- the first reason wins");
        expect_throw([&]() { registry.revoke_key("unknown", "x", 302.0); },
                     "revoking an unknown key throws");

        check(!registry.active_key(310.0).has_value(),
              "no ACTIVE key remains after the sole rotated-in key is revoked");
    }

    // Restart persistence.
    {
        CoordinatorSigningKeyRegistry restarted(store_path);
        const auto keys = restarted.list(400.0);
        check(keys.size() == 2, "both coordinator signing keys survive a restart");
        const auto revoked_key = restarted.find("coord-key-2", 400.0);
        check(
            revoked_key.has_value() && revoked_key->status == CoordinatorSigningKeyStatus::kRevoked,
            "the revoked key's status survives a restart");
    }

    // Corruption detection.
    {
        const std::string corrupt_path = scratch_dir + "/corrupt.dat";
        {
            std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
            file << "record_count=1\n";
            file << "record=not\tenough\tfields\n";
            file << "checksum=0000000000000000\n";
        }
        expect_throw([&]() { CoordinatorSigningKeyRegistry bad(corrupt_path); },
                     "a checksum-mismatched coordinator signing-key registry file throws");
    }
}

}  // namespace fl::coordinator::testing
