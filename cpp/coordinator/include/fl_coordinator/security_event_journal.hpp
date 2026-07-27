#pragma once

// Durable, append-only, cross-language-readable security-event journal --
// Security Events, Metrics, and Durable Audit Journal slice, Work Package
// D/E. See docs/security-events.md.
//
// Deliberately NOT built on the tab-separated/whole-file-checksum pattern
// used by IdempotencyStore/ReplayProtectionStore/etc: those stores are
// small, bounded, and must fail closed on any corruption because a
// silently-wrong trust decision is dangerous. A security-event journal is
// unbounded/append-only and exists purely for observability -- it must
// survive a crash mid-append and keep serving the records that *are*
// intact rather than refusing to start. See this file's .cpp for the
// corruption-recovery policy.

#include "fl_coordinator/security_event.hpp"

#include <cstdint>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

class SecurityEventJournalError : public std::runtime_error {
  public:
    explicit SecurityEventJournalError(const std::string& what);
};

// Work Package D's producer interface. emit() must never throw and must
// never block the caller's actual security decision on journal I/O
// failure -- a failed persist is logged locally (stderr) and dropped.
class SecurityEventSink {
  public:
    virtual ~SecurityEventSink() = default;
    // Returns the event_id assigned to this record (whether supplied by
    // the caller or freshly assigned here), or "" if the event was
    // dropped (failed validate_security_event, or a persist failure).
    // Existing call sites that only care about the side effect, not the
    // id, are unaffected by this return value -- ignoring it is legal
    // and every call site written before this return value existed still
    // compiles as-is.
    virtual std::string emit(SecurityEvent event) = 0;
};

// A no-op sink for call sites/tests that don't wire a real journal --
// avoids every call site needing a null check.
class NullSecurityEventSink : public SecurityEventSink {
  public:
    std::string emit(SecurityEvent /*event*/) override { return ""; }
};

class SecurityEventJournal : public SecurityEventSink {
  public:
    struct Options {
        std::size_t max_bytes_before_rotation = 10 * 1024 * 1024;  // 10 MiB
        std::size_t max_retained_files = 5;
    };

    // Two overloads rather than a defaulted `Options options = Options{}`
    // parameter: GCC (correctly, per the standard's rules on nested-class
    // default member initializers) rejects using a nested class's
    // aggregate-with-defaults as a default argument value inside its own
    // enclosing class's member declaration -- MSVC accepts it, but this
    // codebase's gRPC-gated build only actually compiles under GCC (see
    // docs/known-limitations.md), so the standard-conformant form is
    // required, not optional.
    explicit SecurityEventJournal(std::string persistence_path);
    SecurityEventJournal(std::string persistence_path, Options options);

    // Fills event_id/timestamp/payload_checksum if not already set
    // (mirrors EventBus::publish's exact semantics), validates the event
    // (validate_security_event), and appends it. An invalid event or a
    // filesystem failure is logged to stderr and the event is dropped --
    // never thrown, per the SecurityEventSink contract. Returns the
    // assigned event_id, or "" if dropped.
    std::string emit(SecurityEvent event) override;

    struct ListFilters {
        std::string after_event_id;  // cursor; empty = from the beginning of retained history
        std::size_t limit = 100;
        std::optional<SecuritySeverity> min_severity;
        std::optional<SecuritySubjectType> subject_type;
        std::optional<SecurityEventType> event_type;
    };
    struct ListResult {
        std::vector<SecurityEvent> events;
        std::string next_cursor;  // empty if no further events beyond this page
    };

    // Serves only the currently-active (not yet rotated) file's records --
    // rotated files remain on disk for out-of-band inspection but are not
    // queried here. See the corruption-recovery note above for why this
    // journal favors availability over an exhaustive multi-file scan.
    [[nodiscard]] ListResult list(const ListFilters& filters) const;

    // Number of lines dropped during the most recent load() (construction
    // or reload) due to a checksum mismatch, malformed JSON, or an
    // unrecognized enum value -- exposed for tests and startup logging.
    [[nodiscard]] std::size_t recovered_line_count() const;

    [[nodiscard]] std::size_t size() const;

    // Most recently appended event's timestamp, or "" if empty -- used
    // for journal health/lag reporting (security overview endpoint).
    [[nodiscard]] std::string last_record_timestamp() const;

    // Whether this journal has ever rotated (this process's lifetime,
    // or evidenced by a surviving .1 file across a restart) -- see the
    // identical rationale in
    // go/internal/observability/security_event_journal.go's HasRotated.
    [[nodiscard]] bool has_rotated() const;

  private:
    void load();
    void append_line(const std::string& line);
    void maybe_rotate();
    [[nodiscard]] std::string next_event_id();

    mutable std::mutex mutex_;
    std::size_t rotations_ = 0;
    std::string persistence_path_;
    Options options_;
    std::uint64_t next_sequence_ = 1;
    std::size_t recovered_line_count_ = 0;
    std::vector<SecurityEvent> in_memory_;
};

}  // namespace fl::coordinator
