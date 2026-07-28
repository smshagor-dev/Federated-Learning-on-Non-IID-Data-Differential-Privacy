#include "fl_coordinator/signing_key_registry.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>

namespace fl::coordinator::testing {

void run_signing_key_registry_tests(const std::string& scratch_dir) {
    using fl::coordinator::InitialSigningKeyRegistration;
    using fl::coordinator::SigningKeyRegistry;
    using fl::coordinator::SigningKeyRegistryError;
    using fl::coordinator::SigningKeyRotationRejectionReason;
    using fl::coordinator::SigningKeyRotationRequest;
    using fl::coordinator::SigningKeyStatus;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string store_path = scratch_dir + "/signing_key_registry.dat";

    auto make_initial = [](const std::string& worker_id,
                           const std::string& key_id,
                           const std::string& public_key_hex,
                           double now) {
        InitialSigningKeyRegistration request;
        request.worker_id = worker_id;
        request.signing_key_id = key_id;
        request.public_key_hex = public_key_hex;
        request.public_key_fingerprint = "fp-" + public_key_hex;
        request.now_unix_s = now;
        request.expires_at_unix_s = 0.0;
        request.registration_source = "registration";
        return request;
    };

    const std::string kKeyA(64, 'a');
    const std::string kKeyB(64, 'b');
    const std::string kKeyC(64, 'c');
    const std::string kKeyD(64, 'd');

    {
        SigningKeyRegistry registry(store_path);

        const auto record =
            registry.register_initial_key(make_initial("worker-1", "key-1", kKeyA, 100.0));
        check(record.status == SigningKeyStatus::kActive,
              "a worker's first-ever registered key is ACTIVE immediately");
        check(record.registration_source == "registration",
              "registration_source is recorded as given");

        // Idempotent refresh: same worker/key/public key -> no-op, same record.
        const auto refreshed =
            registry.register_initial_key(make_initial("worker-1", "key-1", kKeyA, 101.0));
        check(refreshed.created_at_unix_s == 100.0,
              "re-registering the identical key is idempotent (created_at unchanged)");

        // A different public key under the SAME key_id must be rejected outright.
        expect_throw(
            [&]() {
                registry.register_initial_key(make_initial("worker-1", "key-1", kKeyB, 102.0));
            },
            "a key-swap attempt under the same signing_key_id is rejected");

        // A second, brand-new worker gets its own independent first key.
        const auto worker2 =
            registry.register_initial_key(make_initial("worker-2", "key-2", kKeyB, 103.0));
        check(worker2.status == SigningKeyStatus::kActive,
              "a second worker's first key is independently ACTIVE");

        // Duplicate signing_key_id across different workers is rejected.
        expect_throw(
            [&]() {
                registry.register_initial_key(make_initial("worker-3", "key-1", kKeyC, 104.0));
            },
            "a signing_key_id already used by a different worker is rejected");

        // Attempting a second "initial" registration for a worker that
        // already has an ACTIVE key (a different key_id) must be
        // rejected -- that is a rotation, not an initial registration.
        expect_throw(
            [&]() {
                registry.register_initial_key(make_initial("worker-1", "key-99", kKeyD, 105.0));
            },
            "registering a second key for a worker with an existing ACTIVE key is rejected "
            "(rotation required instead)");
    }

    {
        // Rotation: valid flow.
        SigningKeyRegistry registry(store_path);

        SigningKeyRotationRequest rotation;
        rotation.worker_id = "worker-1";
        rotation.current_signing_key_id = "key-1";
        rotation.new_signing_key_id = "key-1-rotated";
        rotation.new_public_key_hex = kKeyC;
        rotation.new_public_key_fingerprint = "fp-" + kKeyC;
        rotation.new_key_expires_at_unix_s = 0.0;
        rotation.grace_period_seconds = 3600.0;
        rotation.now_unix_s = 200.0;

        const auto validated = registry.validate_rotation(rotation);
        check(validated.accepted, "a well-formed rotation request validates successfully");

        const auto committed = registry.commit_rotation(rotation);
        check(committed.accepted, "commit_rotation succeeds after a valid validate_rotation");
        check(committed.new_key.status == SigningKeyStatus::kActive,
              "the new key becomes ACTIVE after rotation");
        check(committed.previous_key.status == SigningKeyStatus::kGracePeriod,
              "the previous key becomes GRACE_PERIOD after rotation");
        check(committed.previous_key.grace_period_end_unix_s == 200.0 + 3600.0,
              "grace_period_end is now + requested grace period");
        check(committed.new_key.rotated_from_key_id == "key-1",
              "the new key records rotated_from_key_id");
        check(committed.previous_key.rotated_to_key_id == "key-1-rotated",
              "the previous key records rotated_to_key_id");

        // Rotating again using the now-superseded (GRACE_PERIOD) key must fail --
        // only the currently-ACTIVE key can authorize a rotation.
        SigningKeyRotationRequest second_rotation = rotation;
        second_rotation.new_signing_key_id = "key-1-rotated-again";
        second_rotation.new_public_key_hex = kKeyD;
        second_rotation.new_public_key_fingerprint = "fp-" + kKeyD;
        const auto stale_key_rotation = registry.validate_rotation(second_rotation);
        check(!stale_key_rotation.accepted,
              "a rotation request signed by a GRACE_PERIOD (no longer ACTIVE) key is rejected");
        check(stale_key_rotation.reason == SigningKeyRotationRejectionReason::kCurrentKeyNotActive,
              "the rejection reason is kCurrentKeyNotActive");

        // Unknown current key.
        SigningKeyRotationRequest unknown_current = rotation;
        unknown_current.current_signing_key_id = "no-such-key";
        const auto unknown_result = registry.validate_rotation(unknown_current);
        check(!unknown_result.accepted &&
                  unknown_result.reason == SigningKeyRotationRejectionReason::kUnknownCurrentKey,
              "an unknown current_signing_key_id is rejected as kUnknownCurrentKey");

        // Duplicate new key id (reuse worker-2's existing key-2).
        SigningKeyRotationRequest dup_id = rotation;
        dup_id.current_signing_key_id = "key-1-rotated";
        dup_id.new_signing_key_id = "key-2";
        dup_id.new_public_key_hex = kKeyD;
        dup_id.new_public_key_fingerprint = "fp-" + kKeyD;
        const auto dup_id_result = registry.validate_rotation(dup_id);
        check(!dup_id_result.accepted &&
                  dup_id_result.reason == SigningKeyRotationRejectionReason::kDuplicateNewKeyId,
              "a new_signing_key_id already registered elsewhere is rejected as "
              "kDuplicateNewKeyId");

        // Invalid key length.
        SigningKeyRotationRequest bad_length = rotation;
        bad_length.current_signing_key_id = "key-1-rotated";
        bad_length.new_signing_key_id = "key-1-bad-length";
        bad_length.new_public_key_hex = "deadbeef";
        const auto bad_length_result = registry.validate_rotation(bad_length);
        check(!bad_length_result.accepted &&
                  bad_length_result.reason == SigningKeyRotationRejectionReason::kInvalidKeyLength,
              "an invalid-length public key is rejected as kInvalidKeyLength");

        // Excessive grace period.
        SigningKeyRotationRequest excessive_grace = rotation;
        excessive_grace.current_signing_key_id = "key-1-rotated";
        excessive_grace.new_signing_key_id = "key-1-excessive";
        excessive_grace.new_public_key_hex = kKeyD;
        excessive_grace.new_public_key_fingerprint = "fp-" + kKeyD;
        excessive_grace.grace_period_seconds = SigningKeyRegistry::kMaxGracePeriodSeconds + 1.0;
        const auto excessive_result = registry.validate_rotation(excessive_grace);
        check(
            !excessive_result.accepted &&
                excessive_result.reason == SigningKeyRotationRejectionReason::kExcessiveGracePeriod,
            "a grace period beyond the maximum is rejected as kExcessiveGracePeriod");
    }

    {
        // Grace-period expiry and lazy evaluation at find()/has_any_valid_key() time.
        SigningKeyRegistry registry(store_path);

        const auto before_expiry = registry.find("worker-1", "key-1", 250.0);
        check(before_expiry.has_value() && before_expiry->status == SigningKeyStatus::kGracePeriod,
              "before grace_period_end, the previous key is still reported as GRACE_PERIOD");

        const auto after_expiry = registry.find("worker-1", "key-1", 200.0 + 3600.0 + 1.0);
        check(after_expiry.has_value() && after_expiry->status == SigningKeyStatus::kExpired,
              "after grace_period_end, find() lazily reports EXPIRED without requiring "
              "sweep_expired()");

        check(registry.has_any_valid_key("worker-1", 250.0),
              "worker-1 has a valid key (its rotated-to ACTIVE key) well before any expiry");

        const auto active = registry.find_active("worker-1", 250.0);
        check(active.has_value() && active->signing_key_id == "key-1-rotated",
              "find_active() returns the current ACTIVE key");
    }

    {
        // Revocation.
        SigningKeyRegistry registry(store_path);

        const auto revoked = registry.revoke_key("worker-2", "key-2", "compromised", 300.0);
        check(revoked.status == SigningKeyStatus::kRevoked, "revoke_key() sets status to REVOKED");
        check(!registry.has_any_valid_key("worker-2", 300.0),
              "a worker whose only key was revoked has no valid key");

        // Idempotent.
        const auto revoked_again =
            registry.revoke_key("worker-2", "key-2", "different reason", 301.0);
        check(revoked_again.revocation_reason == "compromised",
              "revoking an already-revoked key is idempotent (first reason wins)");

        expect_throw([&]() { registry.revoke_key("worker-2", "no-such-key", "x", 302.0); },
                     "revoking an unknown signing_key_id throws");
    }

    {
        // Restart persistence: reopening the store from disk preserves
        // ACTIVE/GRACE_PERIOD/REVOKED state.
        SigningKeyRegistry registry(store_path);
        const auto worker1_key = registry.find("worker-1", "key-1-rotated", 250.0);
        check(worker1_key.has_value() && worker1_key->status == SigningKeyStatus::kActive,
              "restart-persisted ACTIVE status survives reopening the store from disk");
        const auto worker2_key = registry.find("worker-2", "key-2", 302.0);
        check(worker2_key.has_value() && worker2_key->status == SigningKeyStatus::kRevoked,
              "restart-persisted REVOKED status survives reopening the store from disk");

        const auto all_for_worker1 = registry.list_for_worker("worker-1", 250.0);
        check(
            all_for_worker1.size() == 2,
            "list_for_worker returns every key ever registered for a worker (ACTIVE + superseded)");
    }

    {
        // sweep_expired persists a lazily-computed transition.
        SigningKeyRegistry registry(store_path);
        const auto transitioned = registry.sweep_expired(200.0 + 3600.0 + 1.0);
        bool found = false;
        for (const auto& [worker_id, key_id] : transitioned) {
            if (worker_id == "worker-1" && key_id == "key-1")
                found = true;
        }
        check(found, "sweep_expired() transitions and persists the expired GRACE_PERIOD key");

        SigningKeyRegistry reopened(store_path);
        const auto after_sweep = reopened.find("worker-1", "key-1", 200.0 + 3600.0 + 1.0);
        check(after_sweep.has_value() && after_sweep->status == SigningKeyStatus::kExpired,
              "the persisted (not just lazily-computed) status is EXPIRED after reopening");
    }

    {
        // Corruption detection.
        const std::string corrupt_path = scratch_dir + "/corrupt.dat";
        {
            std::ofstream file(corrupt_path, std::ios::binary | std::ios::trunc);
            file << "record_count=1\nrecord=not-enough-fields\nchecksum=0000000000000000\n";
        }
        expect_throw([&]() { SigningKeyRegistry registry(corrupt_path); },
                     "a structurally malformed record throws SigningKeyRegistryError");

        const std::string bad_checksum_path = scratch_dir + "/bad_checksum.dat";
        {
            std::ofstream file(bad_checksum_path, std::ios::binary | std::ios::trunc);
            file << "record_count=0\nchecksum=deadbeefdeadbeef\n";
        }
        expect_throw([&]() { SigningKeyRegistry registry(bad_checksum_path); },
                     "a checksum mismatch throws SigningKeyRegistryError");
    }
}

}  // namespace fl::coordinator::testing
