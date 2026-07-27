#pragma once

// Persisted idempotency ledger for security-administration mutations --
// Security Administration slice, Work Package A/K. See
// docs/coordinator-signing-key-rotation.md's "Idempotent retry"
// requirement.
//
// Protobuf-free and gRPC-free, matching every other coordinator
// persistence class in this codebase, so it builds and is
// unit-testable on this Windows/MSVC development machine without a
// local gRPC toolchain.

#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>

namespace fl::coordinator {

class IdempotencyStoreError : public std::runtime_error {
  public:
    explicit IdempotencyStoreError(const std::string& what);
};

// Outcome of a previously-completed mutation, replayed verbatim on a
// retried request presenting the same idempotency_key. Deliberately
// narrow (a handful of scalar fields) rather than a serialized copy of
// a full RPC response -- only the coordinator-signing-key
// rotation/revocation RPCs use this store this slice, and both fit
// this shape.
struct IdempotentMutationRecord {
    std::string rpc_name;
    std::string idempotency_key;
    bool accepted = false;
    std::string rejection_code;   // "" when accepted
    std::string result_key_id;    // the new key_id (rotation) or the affected key_id (revocation)
    std::string previous_key_id;  // rotation only; "" otherwise
    double recorded_at_unix_s = 0.0;
};

// Restart-safe. One persisted file per instance, atomic temp-file+rename
// writes -- same pattern as every other store in this codebase (FNV-1a
// checksum trailer, throws rather than silently starting empty on
// corruption).
class IdempotencyStore {
  public:
    explicit IdempotencyStore(std::string persistence_path);

    [[nodiscard]] std::optional<IdempotentMutationRecord> find(
        const std::string& rpc_name, const std::string& idempotency_key) const;

    // Throws IdempotencyStoreError if a record already exists for this
    // (rpc_name, idempotency_key) pair -- callers must call find()
    // first and only record() a genuinely new outcome; recording twice
    // would silently overwrite the original, semantically-authoritative
    // outcome a retried caller is entitled to see again.
    void record(const IdempotentMutationRecord& record);

  private:
    void persist() const;  // caller must hold mutex_

    mutable std::mutex mutex_;
    std::string persistence_path_;
    struct Key {
        std::string rpc_name;
        std::string idempotency_key;
        bool operator<(const Key& other) const {
            if (rpc_name != other.rpc_name) return rpc_name < other.rpc_name;
            return idempotency_key < other.idempotency_key;
        }
    };
    std::map<Key, IdempotentMutationRecord> records_;
};

}  // namespace fl::coordinator
