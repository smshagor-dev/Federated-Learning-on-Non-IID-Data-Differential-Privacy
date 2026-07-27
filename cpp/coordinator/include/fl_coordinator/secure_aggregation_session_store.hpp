#pragma once

// Safe, persistent secure-aggregation session metadata -- Secure
// Cohort Handshake and Signed Roster Runtime slice, Work item 2. See
// docs/secure-cohort-handshake-foundation.md.
//
// Deliberately stores only what Work Package Q's persistence
// prohibition list allows: session identity, lifecycle state, and
// timestamps. Never ephemeral private keys, pairwise shared secrets,
// derived mask keys, generated mask streams, or individual unmasked
// updates -- none of those exist in this store's record shape at all,
// so there is no way for a future field addition here to accidentally
// start persisting one.
//
// Same persistence discipline as WorkerIdentityRegistry (this
// project's established pattern for a small, trust-adjacent,
// file-backed store): tab-separated records, a record_count header, an
// FNV-1a checksum trailer, atomic temp-file-then-rename writes,
// fail-closed on any corruption (never silently starts empty over a
// real, damaged file).
//
// Deliberately independent of CohortState/SecureAggregationAbortReason
// (secure_aggregation_session.hpp) -- state/abort_reason are stored as
// plain strings (the caller passes fl::coordinator::to_string(...)
// output), keeping this store a standalone persistence concern with no
// coupling to the pure-math session-state-machine types.

#include <cstdint>
#include <map>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

class SecureAggregationSessionStoreError : public std::runtime_error {
  public:
    explicit SecureAggregationSessionStoreError(const std::string& what);
};

struct SecureAggregationSessionRecord {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version = kSchemaVersion;
    std::string session_id;
    std::string run_id;
    std::uint64_t round_id = 0;
    std::string state;  // e.g. "COHORT_FORMING" -- fl::coordinator::to_string(CohortState)
    double created_at_unix_s = 0.0;
    double updated_at_unix_s = 0.0;
    double completed_at_unix_s = 0.0;
    std::string abort_reason;    // e.g. "dropout" -- empty if never aborted
    std::string failure_reason;  // empty if never failed
};

[[nodiscard]] bool is_terminal_session_state(const std::string& state);

class SecureAggregationSessionStore {
  public:
    explicit SecureAggregationSessionStore(std::string persistence_path);

    // Upserts by session_id and persists immediately (every mutation is
    // durable before the call returns -- matching WorkerIdentityRegistry's
    // identical "mutate then persist synchronously" convention).
    void record_transition(const SecureAggregationSessionRecord& record);

    [[nodiscard]] std::optional<SecureAggregationSessionRecord> find(const std::string& session_id) const;
    [[nodiscard]] std::vector<SecureAggregationSessionRecord> all() const;

    // Work item 16 (restart abort): scans every persisted record; any
    // whose state is not terminal gets a fresh terminal record appended
    // (state="ABORTED", abort_reason="coordinator_restart",
    // completed_at_unix_s=now) and persisted. There is nothing live to
    // abort against -- the in-memory SecureAggregationSessionManager
    // starts empty on every process restart -- so this is a log-level
    // reconciliation of stale records, not a live CohortStateMachine
    // transition. Returns the session_ids reconciled, so the caller can
    // emit one SecurityEvent per session.
    [[nodiscard]] std::vector<std::string> reconcile_after_restart(double now_unix_s);

  private:
    std::string persistence_path_;
    std::map<std::string, SecureAggregationSessionRecord> records_;

    void persist() const;
};

}  // namespace fl::coordinator
