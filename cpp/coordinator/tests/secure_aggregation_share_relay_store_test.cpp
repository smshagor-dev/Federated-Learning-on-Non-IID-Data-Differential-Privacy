#include "fl_coordinator/secure_aggregation_crypto.hpp"
#include "fl_coordinator/secure_aggregation_share_relay_store.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void check(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

fl::recovery::v1::EncryptedRecoveryShareRelay make_relay(const std::string& owner,
                                                          const std::string& holder,
                                                          std::uint32_t index,
                                                          double expires_at = 2000.0) {
    fl::recovery::v1::EncryptedRecoveryShareRelay relay;
    relay.set_schema_version(1);
    relay.set_session_id("session-relay-test");
    relay.set_run_id("run-relay-test");
    relay.set_round_id(4);
    relay.set_model_version("v4");
    relay.set_cohort_commitment(std::string(64, 'a'));
    relay.set_owner_worker_id(owner);
    relay.set_holder_worker_id(holder);
    relay.set_generation(0);
    relay.set_threshold(2);
    relay.set_total_shares(2);
    relay.set_share_index(index);
    relay.set_secret_digest(std::string(64, 'b'));
    relay.set_secret_length(32);
    relay.set_field_id("mersenne-521-v1");
    relay.set_nonce_hex(std::string(24, 'c'));
    const std::string ciphertext(82, static_cast<char>(0x5a + index));
    relay.set_ciphertext_hex(fl::coordinator::hex_encode(ciphertext));
    relay.set_ciphertext_hash(fl::coordinator::sha256_hex(ciphertext));
    relay.set_issued_at(1000.0);
    relay.set_expires_at(expires_at);
    return relay;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const std::filesystem::path scratch =
            argc > 1 ? std::filesystem::path(argv[1])
                     : std::filesystem::temp_directory_path() / "fl-share-relay-store-test";
        std::filesystem::remove_all(scratch);
        std::filesystem::create_directories(scratch);
        const auto store_path = (scratch / "relay-store.dat").string();

        const auto first = make_relay("worker-owner", "worker-a", 1);
        const auto second = make_relay("worker-owner", "worker-b", 2);

        {
            fl::coordinator::SecureAggregationShareRelayStore store(store_path);
            check(store.put(first, 1100.0) == fl::coordinator::RelayStorePutResult::kInserted,
                  "first encrypted relay must insert");
            check(store.put(first, 1101.0) == fl::coordinator::RelayStorePutResult::kIdempotent,
                  "identical encrypted relay must be idempotent");
            check(store.put(second, 1102.0) == fl::coordinator::RelayStorePutResult::kInserted,
                  "second holder relay must insert");
            check(store.size(1103.0) == 2, "two relays must be present before restart");
        }

        {
            fl::coordinator::SecureAggregationShareRelayStore restarted(store_path);
            const auto holder_a = restarted.fetch("session-relay-test", "worker-a", 10, 1200.0);
            check(holder_a.size() == 1, "holder relay must survive coordinator-store restart");
            check(holder_a.front().SerializeAsString() == first.SerializeAsString(),
                  "restarted relay must be byte-for-byte identical");

            auto conflicting = first;
            conflicting.set_ciphertext_hex(std::string(164, 'd'));
            conflicting.set_ciphertext_hash(
                fl::coordinator::sha256_hex(fl::coordinator::hex_decode(conflicting.ciphertext_hex())));
            check(restarted.put(conflicting, 1201.0) ==
                      fl::coordinator::RelayStorePutResult::kConflict,
                  "conflicting ciphertext for one owner/holder/generation must fail closed");

            auto duplicate_index = second;
            duplicate_index.set_holder_worker_id("worker-c");
            duplicate_index.set_share_index(1);
            check(restarted.put(duplicate_index, 1202.0) ==
                      fl::coordinator::RelayStorePutResult::kDuplicateShareIndex,
                  "one share index cannot be reused for another holder");
        }

        const auto expiring_path = (scratch / "expiring.dat").string();
        {
            fl::coordinator::SecureAggregationShareRelayStore expiring(expiring_path);
            check(expiring.put(make_relay("owner-x", "holder-x", 1, 1010.0), 1001.0) ==
                      fl::coordinator::RelayStorePutResult::kInserted,
                  "expiring relay must initially insert");
            check(expiring.size(2000.0) == 0, "expired encrypted relays must be purged");
        }
        {
            fl::coordinator::SecureAggregationShareRelayStore restarted(expiring_path);
            check(restarted.size(2001.0) == 0,
                  "expiry purge must be durable across another restart");
        }

        const auto corrupt_path = (scratch / "corrupt.dat").string();
        {
            fl::coordinator::SecureAggregationShareRelayStore store(corrupt_path);
            check(store.put(first, 1100.0) == fl::coordinator::RelayStorePutResult::kInserted,
                  "corruption fixture insert must succeed");
        }
        {
            std::ofstream out(corrupt_path, std::ios::binary | std::ios::app);
            out << "tamper";
        }
        bool corruption_rejected = false;
        try {
            fl::coordinator::SecureAggregationShareRelayStore corrupted(corrupt_path);
            (void)corrupted;
        } catch (const fl::coordinator::SecureAggregationShareRelayStoreError&) {
            corruption_rejected = true;
        }
        check(corruption_rejected, "tampered relay store must fail closed on restart");

        std::filesystem::remove_all(scratch);
        std::cout << "secure aggregation share relay store tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "secure aggregation share relay store test failure: " << error.what() << "\n";
        return 1;
    }
}
