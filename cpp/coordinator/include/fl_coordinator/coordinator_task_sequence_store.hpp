#pragma once

// Persistent, restart-safe per-(coordinator_signing_key_id, worker_id)
// sequence counters for coordinator-issued tasks -- Coordinator-Signed
// Tasks slice, Work Package H. See docs/signed-coordinator-tasks.md.
//
// Deliberately the *issuing* side's counter (like Python's
// SequenceStateStore: "what's the next value I should hand out"), not
// the *validating* side's store (like ReplayProtectionStore: "does
// this claimed value pass"): the coordinator is the party generating
// this sequence, a worker is the party that must validate it. Kept
// protobuf-free and gRPC-free so it builds and is unit-testable on
// this Windows/MSVC development machine, matching every other
// coordinator persistence class.

#include <cstdint>
#include <map>
#include <mutex>
#include <stdexcept>
#include <string>

namespace fl::coordinator {

class CoordinatorTaskSequenceStoreError : public std::runtime_error {
  public:
    explicit CoordinatorTaskSequenceStoreError(const std::string& what);
};

// One persisted file per instance, atomic temp-file+rename writes --
// same pattern as every other persistence class in this codebase.
class CoordinatorTaskSequenceStore {
  public:
    explicit CoordinatorTaskSequenceStore(std::string persistence_path);

    // Returns the next sequence number for this (signing_key_id,
    // worker_id) pair, and durably persists it as used before
    // returning. The documented starting value for a brand-new track
    // is 1, matching every other sequence stream in this codebase
    // (docs/message-sequences.md).
    std::uint64_t next_sequence(const std::string& signing_key_id, const std::string& worker_id);

    // The last sequence number actually issued (0 if none yet) -- for
    // diagnostics/tests only.
    [[nodiscard]] std::uint64_t peek(const std::string& signing_key_id,
                                     const std::string& worker_id) const;

  private:
    void persist() const;  // caller must hold mutex_

    mutable std::mutex mutex_;
    std::string persistence_path_;
    struct Key {
        std::string signing_key_id;
        std::string worker_id;
        bool operator<(const Key& other) const {
            if (signing_key_id != other.signing_key_id)
                return signing_key_id < other.signing_key_id;
            return worker_id < other.worker_id;
        }
    };
    std::map<Key, std::uint64_t> counters_;
};

}  // namespace fl::coordinator
