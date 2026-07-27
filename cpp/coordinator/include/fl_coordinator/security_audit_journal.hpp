#pragma once

// Durable, append-only, security-specific audit journal -- Security
// Events, Metrics, and Durable Audit Journal slice, Work Package
// (requirement 8/9: "a durable security-specific audit journal", "keep
// security events and audit records conceptually separate"). See
// docs/security-audit-journal.md.
//
// Conceptually distinct from SecurityEventJournal: an event records "a
// security-relevant thing happened" (a rejection, a state transition); an
// audit record answers "who did what, with what outcome, and when" for
// every ADMIN_CONTROL mutation the coordinator itself authorizes (worker
// lifecycle, signing-key rotation/revocation, etc.) -- an accountability
// trail keyed on actor + action, not a domain-event stream. Shares the
// same JSONL/rotation/skip-and-recover persistence shape as
// SecurityEventJournal (see that header's comment for why this deviates
// from the throw-on-corruption registries elsewhere in this codebase).

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

class SecurityAuditJournalError : public std::runtime_error {
  public:
    explicit SecurityAuditJournalError(const std::string& what);
};

constexpr int kSecurityAuditRecordSchemaVersion = 1;

struct SecurityAuditRecord {
    int schema_version = kSecurityAuditRecordSchemaVersion;
    std::string record_id;  // assigned by the journal on append(), not the caller
    std::string timestamp;  // ISO-8601 UTC, assigned by the journal if empty
    std::string safe_actor_id;   // e.g. the calling service's certificate identity
    std::string actor_role;      // e.g. "go-api", "admin"
    std::string action;          // e.g. "SuspendWorker", "RotateCoordinatorSigningKey"
    std::string resource_type;   // e.g. "worker_identity", "coordinator_signing_key"
    std::string resource_id;
    std::string outcome;         // ACCEPTED / REJECTED / etc. -- reuses SecurityOutcome's strings
    std::string reason;
    std::string request_id;
    std::string trace_id;
    std::map<std::string, std::string> safe_details;
    std::string payload_checksum;  // assigned by the journal on append(), not the caller
};

class SecurityAuditJournal {
  public:
    struct Options {
        std::size_t max_bytes_before_rotation = 10 * 1024 * 1024;
        std::size_t max_retained_files = 5;
    };

    // Two overloads rather than a defaulted parameter -- see
    // SecurityEventJournal's identical constructor comment for why.
    explicit SecurityAuditJournal(std::string persistence_path);
    SecurityAuditJournal(std::string persistence_path, Options options);

    // Fills record_id/timestamp/payload_checksum if not already set,
    // validates bounds, and appends. Never throws -- an audit-logging
    // failure must not block the underlying mutation it is recording.
    void append(SecurityAuditRecord record);

    struct ListFilters {
        std::string after_record_id;  // cursor
        std::size_t limit = 100;
        std::string actor_id;      // exact match if non-empty
        std::string action;        // exact match if non-empty
        std::string resource_type; // exact match if non-empty
        std::string outcome;       // exact match if non-empty
        double since_unix_s = 0.0;   // 0 == no lower bound
        double until_unix_s = 0.0;   // 0 == no upper bound
    };
    struct ListResult {
        std::vector<SecurityAuditRecord> records;
        std::string next_cursor;
    };
    [[nodiscard]] ListResult list(const ListFilters& filters) const;

    [[nodiscard]] std::size_t recovered_line_count() const;
    [[nodiscard]] std::size_t size() const;

    // See SecurityEventJournal's identical accessors for rationale.
    [[nodiscard]] std::string last_record_timestamp() const;
    [[nodiscard]] bool has_rotated() const;

  private:
    void load();
    void append_line(const std::string& line);
    void maybe_rotate();
    [[nodiscard]] std::string next_record_id();

    mutable std::mutex mutex_;
    std::size_t rotations_ = 0;
    std::string persistence_path_;
    Options options_;
    std::uint64_t next_sequence_ = 1;
    std::size_t recovered_line_count_ = 0;
    std::vector<SecurityAuditRecord> in_memory_;
};

}  // namespace fl::coordinator
