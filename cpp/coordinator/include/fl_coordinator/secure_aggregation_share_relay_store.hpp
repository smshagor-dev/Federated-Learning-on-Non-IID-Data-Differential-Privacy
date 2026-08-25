#pragma once

// Restart-safe persistence for encrypted recovery-share relay packages.
//
// The stored protobuf contains only end-to-end encrypted ciphertext, nonce,
// hashes, and public/session metadata. It has no plaintext Shamir-share field
// and the coordinator has no holder private key, so persisting this mailbox
// does not weaken the recovery protocol's plaintext-share boundary.

#include "fl_coordinator/secure_aggregation_crypto.hpp"

#include "recovery/recovery.pb.h"

#include <algorithm>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace fl::coordinator {

class SecureAggregationShareRelayStoreError : public std::runtime_error {
  public:
    explicit SecureAggregationShareRelayStoreError(const std::string& what)
        : std::runtime_error(what) {}
};

enum class RelayStorePutResult {
    kInserted,
    kIdempotent,
    kConflict,
    kDuplicateShareIndex,
    kGlobalLimitExceeded,
    kHolderSessionLimitExceeded,
};

class SecureAggregationShareRelayStore {
  public:
    static constexpr std::size_t kMaxQueuedRelays = 10000;
    static constexpr std::size_t kMaxRelaysPerHolderSession = 256;

    explicit SecureAggregationShareRelayStore(std::string persistence_path)
        : persistence_path_(std::move(persistence_path)) {
        if (persistence_path_.empty()) {
            throw SecureAggregationShareRelayStoreError(
                "encrypted relay persistence path must not be empty");
        }
        load();
    }

    RelayStorePutResult put(const fl::recovery::v1::EncryptedRecoveryShareRelay& relay,
                            double now_unix_s) {
        std::lock_guard<std::mutex> lock(mutex_);
        const bool purged = purge_expired_locked(now_unix_s);
        if (purged) {
            persist_locked();
        }

        const RelayKey key{relay.session_id(),
                           relay.owner_worker_id(),
                           relay.holder_worker_id(),
                           relay.generation()};
        const auto existing = relays_.find(key);
        if (existing != relays_.end()) {
            return existing->second.SerializeAsString() == relay.SerializeAsString()
                       ? RelayStorePutResult::kIdempotent
                       : RelayStorePutResult::kConflict;
        }

        for (const auto& [other_key, other] : relays_) {
            if (other_key.session_id == relay.session_id() &&
                other_key.owner_worker_id == relay.owner_worker_id() &&
                other_key.generation == relay.generation() &&
                other.share_index() == relay.share_index()) {
                return RelayStorePutResult::kDuplicateShareIndex;
            }
        }
        if (relays_.size() >= kMaxQueuedRelays) {
            return RelayStorePutResult::kGlobalLimitExceeded;
        }

        std::size_t holder_session_count = 0;
        for (const auto& [other_key, other] : relays_) {
            (void)other;
            if (other_key.session_id == relay.session_id() &&
                other_key.holder_worker_id == relay.holder_worker_id()) {
                ++holder_session_count;
            }
        }
        if (holder_session_count >= kMaxRelaysPerHolderSession) {
            return RelayStorePutResult::kHolderSessionLimitExceeded;
        }

        relays_.emplace(key, relay);
        try {
            persist_locked();
        } catch (...) {
            relays_.erase(key);
            throw;
        }
        return RelayStorePutResult::kInserted;
    }

    [[nodiscard]] std::vector<fl::recovery::v1::EncryptedRecoveryShareRelay> fetch(
        const std::string& session_id,
        const std::string& holder_worker_id,
        std::size_t limit,
        double now_unix_s) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (purge_expired_locked(now_unix_s)) {
            persist_locked();
        }
        std::vector<fl::recovery::v1::EncryptedRecoveryShareRelay> result;
        result.reserve(std::min(limit, relays_.size()));
        for (const auto& [key, relay] : relays_) {
            if (key.session_id == session_id && key.holder_worker_id == holder_worker_id) {
                result.push_back(relay);
                if (result.size() >= limit) {
                    break;
                }
            }
        }
        return result;
    }

    [[nodiscard]] std::size_t size(double now_unix_s) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (purge_expired_locked(now_unix_s)) {
            persist_locked();
        }
        return relays_.size();
    }

  private:
    struct RelayKey {
        std::string session_id;
        std::string owner_worker_id;
        std::string holder_worker_id;
        std::uint32_t generation = 0;

        bool operator<(const RelayKey& other) const {
            return std::tie(session_id, owner_worker_id, holder_worker_id, generation) <
                   std::tie(other.session_id,
                            other.owner_worker_id,
                            other.holder_worker_id,
                            other.generation);
        }
    };

    static bool lowercase_hex(const std::string& value, std::size_t length) {
        return value.size() == length &&
               std::all_of(value.begin(), value.end(), [](unsigned char ch) {
                   return (ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f');
               });
    }

    static void validate_persisted_relay(
        const fl::recovery::v1::EncryptedRecoveryShareRelay& relay) {
        if (relay.schema_version() != 1 || relay.session_id().empty() ||
            relay.owner_worker_id().empty() || relay.holder_worker_id().empty() ||
            relay.owner_worker_id() == relay.holder_worker_id()) {
            throw SecureAggregationShareRelayStoreError(
                "persisted encrypted relay has invalid identity/session metadata");
        }
        if (!lowercase_hex(relay.nonce_hex(), 24) ||
            !lowercase_hex(relay.ciphertext_hex(), 164) ||
            !lowercase_hex(relay.ciphertext_hash(), 64) ||
            sha256_hex(hex_decode(relay.ciphertext_hex())) != relay.ciphertext_hash()) {
            throw SecureAggregationShareRelayStoreError(
                "persisted encrypted relay failed ciphertext integrity validation");
        }
        if (relay.expires_at() <= relay.issued_at()) {
            throw SecureAggregationShareRelayStoreError(
                "persisted encrypted relay has invalid timestamps");
        }
        if (relay.threshold() < 2 || relay.threshold() > relay.total_shares() ||
            relay.share_index() < 1 || relay.share_index() > relay.total_shares()) {
            throw SecureAggregationShareRelayStoreError(
                "persisted encrypted relay has invalid threshold/share metadata");
        }
    }

    bool purge_expired_locked(double now_unix_s) {
        bool changed = false;
        for (auto it = relays_.begin(); it != relays_.end();) {
            if (now_unix_s >= it->second.expires_at()) {
                it = relays_.erase(it);
                changed = true;
            } else {
                ++it;
            }
        }
        return changed;
    }

    void validate_loaded_cross_record_constraints(
        const fl::recovery::v1::EncryptedRecoveryShareRelay& relay) const {
        std::size_t holder_session_count = 0;
        for (const auto& [other_key, other] : relays_) {
            if (other_key.session_id == relay.session_id() &&
                other_key.owner_worker_id == relay.owner_worker_id() &&
                other_key.generation == relay.generation() &&
                other.share_index() == relay.share_index()) {
                throw SecureAggregationShareRelayStoreError(
                    "encrypted relay store contains a duplicate share index");
            }
            if (other_key.session_id == relay.session_id() &&
                other_key.holder_worker_id == relay.holder_worker_id()) {
                ++holder_session_count;
            }
        }
        if (holder_session_count >= kMaxRelaysPerHolderSession) {
            throw SecureAggregationShareRelayStoreError(
                "encrypted relay store exceeds the holder/session record bound");
        }
    }

    void load() {
        if (!std::filesystem::exists(persistence_path_)) {
            return;
        }
        std::ifstream file(persistence_path_, std::ios::binary);
        if (!file) {
            throw SecureAggregationShareRelayStoreError(
                "failed to open encrypted relay store: " + persistence_path_);
        }
        std::ostringstream buffer;
        buffer << file.rdbuf();
        const std::string payload = buffer.str();
        const auto checksum_marker = payload.rfind("checksum=");
        if (checksum_marker == std::string::npos) {
            throw SecureAggregationShareRelayStoreError(
                "encrypted relay store is missing checksum");
        }
        const std::string body = payload.substr(0, checksum_marker);
        std::string expected = payload.substr(checksum_marker + std::string("checksum=").size());
        while (!expected.empty() && (expected.back() == '\n' || expected.back() == '\r')) {
            expected.pop_back();
        }
        if (sha256_hex(body) != expected) {
            throw SecureAggregationShareRelayStoreError(
                "encrypted relay store checksum mismatch");
        }

        std::stringstream stream(body);
        std::string line;
        std::size_t declared_count = 0;
        bool saw_schema = false;
        bool saw_count = false;
        try {
            while (std::getline(stream, line)) {
                if (line == "schema_version=1") {
                    if (saw_schema) {
                        throw SecureAggregationShareRelayStoreError(
                            "encrypted relay store repeats schema header");
                    }
                    saw_schema = true;
                    continue;
                }
                if (line.rfind("record_count=", 0) == 0) {
                    if (saw_count) {
                        throw SecureAggregationShareRelayStoreError(
                            "encrypted relay store repeats record_count header");
                    }
                    declared_count = std::stoull(line.substr(std::string("record_count=").size()));
                    saw_count = true;
                    if (declared_count > kMaxQueuedRelays) {
                        throw SecureAggregationShareRelayStoreError(
                            "encrypted relay store exceeds global record bound");
                    }
                    continue;
                }
                if (line.rfind("record=", 0) != 0) {
                    if (!line.empty()) {
                        throw SecureAggregationShareRelayStoreError(
                            "encrypted relay store contains an unknown record type");
                    }
                    continue;
                }
                if (!saw_schema || !saw_count) {
                    throw SecureAggregationShareRelayStoreError(
                        "encrypted relay records appeared before required headers");
                }
                fl::recovery::v1::EncryptedRecoveryShareRelay relay;
                const auto raw = hex_decode(line.substr(std::string("record=").size()));
                if (!relay.ParseFromString(raw)) {
                    throw SecureAggregationShareRelayStoreError(
                        "failed to parse persisted encrypted relay protobuf");
                }
                validate_persisted_relay(relay);
                validate_loaded_cross_record_constraints(relay);
                const RelayKey key{relay.session_id(),
                                   relay.owner_worker_id(),
                                   relay.holder_worker_id(),
                                   relay.generation()};
                if (!relays_.emplace(key, relay).second) {
                    throw SecureAggregationShareRelayStoreError(
                        "encrypted relay store contains duplicate relay identity");
                }
            }
        } catch (const SecureAggregationShareRelayStoreError&) {
            throw;
        } catch (const std::exception& error) {
            throw SecureAggregationShareRelayStoreError(
                std::string("encrypted relay store parse failure: ") + error.what());
        }
        if (!saw_schema || !saw_count || relays_.size() != declared_count) {
            throw SecureAggregationShareRelayStoreError(
                "encrypted relay store header/count validation failed");
        }
    }

    void persist_locked() const {
        std::ostringstream body;
        body << "schema_version=1\n";
        body << "record_count=" << relays_.size() << "\n";
        for (const auto& [key, relay] : relays_) {
            (void)key;
            body << "record=" << hex_encode(relay.SerializeAsString()) << "\n";
        }
        const std::string body_text = body.str();
        const std::string final_payload = body_text + "checksum=" + sha256_hex(body_text) + "\n";

        const std::filesystem::path target(persistence_path_);
        if (target.has_parent_path()) {
            std::filesystem::create_directories(target.parent_path());
        }
        const std::string temp_path = persistence_path_ + ".tmp";
        {
            std::ofstream file(temp_path, std::ios::binary | std::ios::trunc);
            if (!file) {
                throw SecureAggregationShareRelayStoreError(
                    "failed to open encrypted relay store temp file");
            }
            file << final_payload;
            file.flush();
            if (!file) {
                throw SecureAggregationShareRelayStoreError(
                    "failed to write encrypted relay store temp file");
            }
        }
        std::error_code ec;
        std::filesystem::rename(temp_path, target, ec);
        if (ec) {
            std::filesystem::remove(target, ec);
            std::filesystem::rename(temp_path, target, ec);
            if (ec) {
                throw SecureAggregationShareRelayStoreError(
                    "failed to atomically replace encrypted relay store: " + ec.message());
            }
        }
    }

    std::string persistence_path_;
    mutable std::mutex mutex_;
    std::map<RelayKey, fl::recovery::v1::EncryptedRecoveryShareRelay> relays_;
};

}  // namespace fl::coordinator
