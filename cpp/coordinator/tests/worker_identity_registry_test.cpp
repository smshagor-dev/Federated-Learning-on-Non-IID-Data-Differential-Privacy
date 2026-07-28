#include "fl_coordinator/worker_identity_registry.hpp"
#include "test_support.hpp"

#include <filesystem>
#include <fstream>
#include <sstream>

namespace fl::coordinator::testing {

void run_worker_identity_registry_tests(const std::string& scratch_dir) {
    using fl::coordinator::WorkerIdentityRecord;
    using fl::coordinator::WorkerIdentityRegistry;
    using fl::coordinator::WorkerIdentityRegistryError;
    using fl::coordinator::WorkerIdentityStatus;

    std::filesystem::remove_all(scratch_dir);
    std::filesystem::create_directories(scratch_dir);
    const std::string registry_path = scratch_dir + "/worker_identity_registry.dat";

    {
        WorkerIdentityRegistry registry(registry_path);
        check(registry.list().empty(), "a fresh registry with no file yet starts empty");

        const auto record =
            registry.register_identity("worker-1",
                                       "spiffe://federated-platform/worker/worker-1",
                                       "1002",
                                       "fp-worker-1",
                                       "pubkey-worker-1",
                                       "key-1",
                                       "0.1.0",
                                       "build-1",
                                       /*now=*/100.0,
                                       /*expires_at=*/1000.0);
        check(record.registration_status == WorkerIdentityStatus::kPending,
              "a newly registered identity starts PENDING");
        check(record.created_at_unix_s == 100.0,
              "created_at is set from the caller-supplied clock");
        check(record.updated_at_unix_s == 100.0,
              "updated_at matches created_at on first registration");
    }

    {
        // Re-open against the same file -- persistence must survive a
        // fresh WorkerIdentityRegistry instance the way a coordinator
        // restart would create one.
        WorkerIdentityRegistry registry(registry_path);
        const auto found = registry.find_by_worker_id("worker-1");
        check(found.has_value(), "worker-1 is found after reloading from disk");
        check(found->certificate_fingerprint == "fp-worker-1",
              "reloaded record preserves certificate_fingerprint");
        check(found->signing_public_key == "pubkey-worker-1",
              "reloaded record preserves signing_public_key");

        const auto by_fingerprint = registry.find_by_certificate_fingerprint("fp-worker-1");
        check(by_fingerprint.has_value() && by_fingerprint->worker_id == "worker-1",
              "find_by_certificate_fingerprint resolves back to worker-1");

        check(!registry.find_by_worker_id("never-registered").has_value(),
              "an unknown worker_id is not found");
    }

    {
        WorkerIdentityRegistry registry(registry_path);
        // Idempotent re-registration refreshes fields rather than
        // creating a duplicate or erroring.
        const auto refreshed =
            registry.register_identity("worker-1",
                                       "spiffe://federated-platform/worker/worker-1",
                                       "1099",
                                       "fp-worker-1-v2",
                                       "pubkey-worker-1-v2",
                                       "key-2",
                                       "0.2.0",
                                       "build-2",
                                       /*now=*/200.0,
                                       /*expires_at=*/2000.0);
        check(refreshed.certificate_serial == "1099",
              "re-registration refreshes certificate_serial");
        check(refreshed.updated_at_unix_s == 200.0, "re-registration refreshes updated_at");
        check(refreshed.created_at_unix_s == 100.0,
              "re-registration does NOT change the original created_at");
        check(registry.list().size() == 1, "re-registration does not create a duplicate record");
    }

    {
        WorkerIdentityRegistry registry(registry_path);
        registry.register_identity("worker-2",
                                   "spiffe://federated-platform/worker/worker-2",
                                   "2000",
                                   "fp-worker-2",
                                   "pubkey-worker-2",
                                   "key-3",
                                   "0.1.0",
                                   "build-1",
                                   100.0,
                                   1000.0);
        expect_throw(
            [&]() {
                registry.register_identity("worker-3",
                                           "spiffe://federated-platform/worker/worker-3",
                                           "3000",
                                           "fp-worker-2",
                                           "pubkey-worker-3",
                                           "key-4",
                                           "0.1.0",
                                           "build-1",
                                           100.0,
                                           1000.0);
            },
            "registering a certificate_fingerprint already bound to a different worker_id is "
            "rejected");
    }

    {
        WorkerIdentityRegistry registry(registry_path);
        expect_throw([&]() { registry.suspend("never-registered", "test", 0.0); },
                     "suspending an unknown worker_id is rejected");
        expect_throw([&]() { registry.activate("never-registered", 0.0); },
                     "activating an unknown worker_id is rejected");
        expect_throw([&]() { registry.revoke("never-registered", "test", 0.0); },
                     "revoking an unknown worker_id is rejected");
    }

    {
        WorkerIdentityRegistry registry(registry_path);
        registry.activate("worker-1", 300.0);
        const auto active = registry.find_by_worker_id("worker-1");
        check(active->registration_status == WorkerIdentityStatus::kActive,
              "activate() transitions PENDING -> ACTIVE");

        const auto suspended = registry.suspend("worker-1", "manual review", 400.0);
        check(suspended.registration_status == WorkerIdentityStatus::kSuspended,
              "suspend() transitions ACTIVE -> SUSPENDED");
        check(suspended.suspended_at_unix_s == 400.0, "suspend() records suspended_at");
        check(suspended.revocation_reason == "manual review",
              "suspend() records the reason (shared revocation_reason field)");

        // Idempotent re-suspend refreshes the reason/timestamp.
        const auto re_suspended = registry.suspend("worker-1", "second reason", 450.0);
        check(re_suspended.suspended_at_unix_s == 450.0,
              "re-suspending an already-suspended worker refreshes the timestamp");
        check(re_suspended.revocation_reason == "second reason",
              "re-suspending an already-suspended worker refreshes the reason");

        const auto reactivated = registry.activate("worker-1", 500.0);
        check(reactivated.registration_status == WorkerIdentityStatus::kActive,
              "activate() transitions SUSPENDED -> ACTIVE");
    }

    {
        WorkerIdentityRegistry registry(registry_path);
        const auto revoked = registry.revoke("worker-1", "key compromise suspected", 600.0);
        check(revoked.registration_status == WorkerIdentityStatus::kRevoked,
              "revoke() transitions ACTIVE -> REVOKED");
        check(revoked.revoked_at_unix_s == 600.0, "revoke() records revoked_at");

        expect_throw([&]() { registry.activate("worker-1", 700.0); },
                     "a revoked worker cannot be activated");
        expect_throw([&]() { registry.suspend("worker-1", "irrelevant", 700.0); },
                     "a revoked worker cannot be suspended (revocation is terminal)");

        // Idempotent re-revoke does not overwrite the original reason.
        const auto re_revoked = registry.revoke("worker-1", "a different reason", 800.0);
        check(re_revoked.revocation_reason == "key compromise suspected",
              "re-revoking an already-revoked worker keeps the original reason");
        check(re_revoked.revoked_at_unix_s == 600.0,
              "re-revoking an already-revoked worker does not move revoked_at");

        expect_throw(
            [&]() {
                registry.register_identity("worker-1",
                                           "spiffe://federated-platform/worker/worker-1",
                                           "9999",
                                           "fp-worker-1-v3",
                                           "pubkey-v3",
                                           "key-9",
                                           "0.3.0",
                                           "build-3",
                                           900.0,
                                           9999.0);
            },
            "a revoked worker_id cannot be re-registered under the same identity");
    }

    {
        // sweep_expired: worker-2 was registered above with
        // expires_at_unix_s=1000.0 and never suspended/revoked/activated
        // (still PENDING).
        WorkerIdentityRegistry registry(registry_path);
        const auto before = registry.find_by_worker_id("worker-2");
        check(before->registration_status == WorkerIdentityStatus::kPending,
              "worker-2 is still PENDING before the expiry sweep");

        const auto not_yet_expired = registry.sweep_expired(999.0);
        check(not_yet_expired.empty(), "sweep_expired reports nothing before the expiry time");

        const auto newly_expired = registry.sweep_expired(1001.0);
        check(newly_expired.size() == 1 && newly_expired[0] == "worker-2",
              "sweep_expired transitions worker-2 to EXPIRED once past expires_at_unix_s");
        check(registry.find_by_worker_id("worker-2")->registration_status ==
                  WorkerIdentityStatus::kExpired,
              "worker-2's status is persisted as EXPIRED");

        // worker-1 is REVOKED (terminal) -- must never be swept to EXPIRED
        // even though its original expiry-bearing registration is long past.
        check(registry.find_by_worker_id("worker-1")->registration_status ==
                  WorkerIdentityStatus::kRevoked,
              "a revoked worker is never overwritten by the expiry sweep");

        const auto second_sweep = registry.sweep_expired(2000.0);
        check(second_sweep.empty(), "sweep_expired does not re-report an already-expired worker");
    }

    {
        // Corruption detection: truncate the persisted file mid-write
        // and confirm the next load refuses to trust it rather than
        // silently starting fresh (which would un-revoke worker-1).
        const std::string corrupt_path = scratch_dir + "/corrupt_registry.dat";
        {
            WorkerIdentityRegistry registry(corrupt_path);
            registry.register_identity("worker-x",
                                       "spiffe://federated-platform/worker/worker-x",
                                       "1",
                                       "fp-x",
                                       "pubkey-x",
                                       "key-x",
                                       "0.1.0",
                                       "build-1",
                                       0.0,
                                       0.0);
        }
        {
            std::ifstream in(corrupt_path, std::ios::binary);
            std::ostringstream buffer;
            buffer << in.rdbuf();
            std::string content = buffer.str();
            in.close();
            content.resize(content.size() / 2);  // truncate mid-record
            std::ofstream out(corrupt_path, std::ios::binary | std::ios::trunc);
            out << content;
        }
        expect_throw([&]() { WorkerIdentityRegistry registry(corrupt_path); },
                     "loading a truncated/corrupt registry file throws rather than starting fresh");
    }
}

}  // namespace fl::coordinator::testing
