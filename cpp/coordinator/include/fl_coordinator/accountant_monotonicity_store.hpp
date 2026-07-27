#pragma once

// Persistent accountant monotonicity state -- Privacy Record
// Authenticity, Signing-Key Lifecycle, and Coordinator-Signed Tasks
// slice, Work Package F. See docs/privacy-accountant-monotonicity.md.
//
// Deliberately protobuf-free and gRPC-free, mirroring
// replay_protection_store.hpp and worker_identity_registry.hpp exactly
// (same atomic temp-file+rename persistence, same FNV-1a checksum
// trailer, same validate()/commit() split so replay-style "commit only
// after domain acceptance" ordering is possible here too) -- this
// header never sees a real fl.privacy.v1.SignedSamplePrivacyRecord; the
// gRPC-gated coordinator_service.cpp builds a MonotonicityCandidate
// from one and calls into this store.
//
// This store answers exactly one question: "does this accountant step
// look like a real continuation of this (run, client, worker,
// accountant) track's own prior history, or does it look like a
// replay, a rollback, or a configuration change smuggled in mid-track?"
// It does NOT recompute or independently verify Opacus accounting --
// see docs/privacy-accountant-monotonicity.md's "What this does not
// prove" section, matching payload-hashing.md's identical disclaimer
// for signature verification in general.

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>

namespace fl::coordinator {

class AccountantMonotonicityStoreError : public std::runtime_error {
  public:
    explicit AccountantMonotonicityStoreError(const std::string& what);
};

enum class MonotonicityRejectionReason {
    kNone,
    kStepNotIncreasing,
    kEpsilonDecreased,
    kDeltaChanged,
    kConfigurationHashChanged,
};

std::string to_string(MonotonicityRejectionReason reason);

// Identifies one accountant track. Deliberately does NOT include
// configuration_hash in the key itself -- a changed configuration_hash
// for an otherwise-identical (run_id, client_id, worker_id,
// accountant_type) track must be *rejected* (kConfigurationHashChanged),
// not silently treated as the start of a brand-new, independent track
// (which is what including it in the key would produce).
struct TrackKey {
    std::string run_id;
    std::string client_id;
    std::string worker_id;
    int accountant_type{0};

    bool operator<(const TrackKey& other) const {
        if (run_id != other.run_id) return run_id < other.run_id;
        if (client_id != other.client_id) return client_id < other.client_id;
        if (worker_id != other.worker_id) return worker_id < other.worker_id;
        return accountant_type < other.accountant_type;
    }
};

struct AccountantMonotonicityRecord {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version{kSchemaVersion};
    std::string run_id;
    std::string client_id;
    std::string worker_id;
    int accountant_type{0};
    std::uint64_t last_accepted_step{0};
    double last_epsilon{0.0};
    double delta{0.0};
    std::string accountant_state_hash;
    std::string configuration_hash;
    std::uint64_t last_round{0};
    std::string last_task;
    double updated_at_unix_s{0.0};
};

// One proposed accountant update, checked against the track's own prior
// history (if any -- a brand-new track always accepts, matching
// ReplayProtectionStore's "documented starting value" convention).
struct MonotonicityCandidate {
    std::string run_id;
    std::string client_id;
    std::string worker_id;
    int accountant_type{0};
    std::uint64_t step{0};
    double epsilon{0.0};
    double delta{0.0};
    std::string accountant_state_hash;
    std::string configuration_hash;
    std::uint64_t round_id{0};
    std::string task_id;
    double now_unix_s{0.0};
};

struct MonotonicityDecision {
    bool accepted = false;
    MonotonicityRejectionReason reason = MonotonicityRejectionReason::kNone;
    std::string detail;  // human-readable; always set, even when accepted ("ok")
};

// Restart-safe accountant monotonicity store. One persisted file per
// instance, atomic temp-file+rename writes -- same pattern as
// WorkerIdentityRegistry/ReplayProtectionStore. Unbounded map is
// acceptable here (unlike ReplayProtectionStore's explicit bounding
// requirement): the natural cardinality is one track per
// (run, client, worker, accountant) combination that has ever actually
// submitted a private result, which is bounded by real run/client
// counts already tracked elsewhere in this coordinator (WorkerRegistry,
// RunManager), not by anything an unauthenticated caller controls
// (every candidate reaching this store has already passed signature
// verification -- see coordinator_service.cpp's SubmitClientResult).
class AccountantMonotonicityStore {
  public:
    explicit AccountantMonotonicityStore(std::string persistence_path);

    // Read-only: does not mutate state. Callers must call commit()
    // separately, and only after domain acceptance has succeeded --
    // same ordering rule as ReplayProtectionStore::validate()/commit().
    [[nodiscard]] MonotonicityDecision validate(const MonotonicityCandidate& candidate) const;

    // Records `candidate` as the track's new last-accepted state.
    // Callers must have already called validate() on this exact
    // candidate and checked MonotonicityDecision::accepted; commit()
    // does not re-validate.
    void commit(const MonotonicityCandidate& candidate);

    [[nodiscard]] std::optional<AccountantMonotonicityRecord> find(const TrackKey& key) const;

    // Work Package F's "explicit accountant reset process": deliberately
    // the only way a track's last_accepted_step/last_epsilon can ever
    // move backward. Store-level only this pass -- no RPC exposes this
    // yet (would need a new ADMIN_CONTROL RPC, out of scope for this
    // pass; see docs/privacy-accountant-monotonicity.md).
    void reset(const TrackKey& key, const std::string& reason, double now_unix_s);

  private:
    void persist() const;  // caller must hold mutex_

    mutable std::mutex mutex_;
    std::string persistence_path_;
    std::map<TrackKey, AccountantMonotonicityRecord> records_;
};

}  // namespace fl::coordinator
