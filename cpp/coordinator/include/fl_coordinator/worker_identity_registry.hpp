#pragma once

// Persistent worker identity registry -- Coordinator Transport
// Verification and Message Authenticity slice, Work Package E. See
// docs/worker-identity-registry.md.
//
// This is the coordinator's own durable record of "which worker_ids
// exist, what certificate/signing-key material each one is bound to,
// and whether each is allowed to participate right now" -- distinct
// from (and a prerequisite for) the certificate-identity-binding check
// in peer_identity.hpp, which only asks "does this connection's
// authenticated certificate match the worker_id it claims." This
// registry is what suspend/activate/revoke (Work Package L) actually
// operate on, and what a restarted coordinator process reloads so
// revocation state survives a crash/restart rather than resetting to
// "everyone is trusted again."
//
// Deliberately NOT a claim of full PKI revocation enforcement: this is
// an application-level registry the coordinator consults at RPC time,
// not a CRL/OCSP check wired into the TLS stack itself (see
// docs/worker-revocation.md for the distinction and its limits).

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

enum class WorkerIdentityStatus {
    kPending,
    kActive,
    kSuspended,
    kRevoked,
    kExpired,
};

std::string to_string(WorkerIdentityStatus status);
WorkerIdentityStatus worker_identity_status_from_string(const std::string& value);

// Raised when persisted registry state fails structural, schema, or
// checksum validation, or when a caller requests an operation this
// registry's state machine does not allow (see register_identity,
// suspend, activate, revoke below). Callers should treat a
// corruption-shaped error as "do not trust this registry file", not
// attempt partial recovery.
class WorkerIdentityRegistryError : public std::runtime_error {
  public:
    explicit WorkerIdentityRegistryError(const std::string& what);
};

struct WorkerIdentityRecord {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version{kSchemaVersion};
    std::string worker_id;
    // The URI SAN identity from the worker's TLS certificate, e.g.
    // spiffe://federated-platform/worker/worker-1 -- see peer_identity.hpp.
    std::string certificate_identity;
    std::string certificate_serial;
    std::string certificate_fingerprint;
    // The worker's Ed25519 signing public key (separate key material
    // from its TLS certificate -- see docs/worker-identity.md), used to
    // verify signed capability statements and message envelopes.
    // Hex-encoded, matching the canonical serialization used elsewhere
    // in the security stack.
    std::string signing_public_key;
    std::string signing_key_id;
    WorkerIdentityStatus registration_status{WorkerIdentityStatus::kPending};
    std::string software_version;
    std::string build_id;
    double created_at_unix_s{0.0};
    double updated_at_unix_s{0.0};
    double expires_at_unix_s{0.0};
    // 0.0 means "never suspended" / "never revoked" -- these are only
    // meaningful once registration_status has actually transitioned to
    // kSuspended / kRevoked at least once.
    double suspended_at_unix_s{0.0};
    double revoked_at_unix_s{0.0};
    std::string revocation_reason;
};

// Filesystem-backed, restart-safe registry of every worker identity
// this coordinator process has ever registered. Every mutating call
// rewrites the entire persisted file atomically (temp file + rename,
// same pattern as AggregatorCheckpointStore and RunInstance's own
// checkpointing) rather than appending a log -- simpler to reason
// about and to detect corruption in, and the expected record count
// (one per worker, not one per event) keeps a full rewrite cheap.
//
// Thread-safe: every public method takes an internal mutex. Time is
// always passed in explicitly (unix seconds), never read from the wall
// clock internally, so status-transition behavior is deterministically
// testable without sleeping in tests -- same convention as
// WorkerRegistry.
class WorkerIdentityRegistry {
  public:
    // Loads existing state from `persistence_path` if the file exists;
    // starts empty otherwise. Throws WorkerIdentityRegistryError if the
    // file exists but fails checksum/schema validation -- this
    // constructor deliberately never silently discards a corrupt
    // registry and starts fresh, since that would silently un-revoke
    // every previously revoked worker.
    explicit WorkerIdentityRegistry(std::string persistence_path);

    // Registers a new worker identity, or -- if worker_id already
    // exists and is not kRevoked -- refreshes its certificate/signing
    // fields in place (idempotent re-registration, e.g. after a worker
    // restarts and re-presents its still-valid certificate; same
    // "idempotent refresh" convention as WorkerRegistry::register_worker).
    // A refresh does not change registration_status; a brand-new
    // worker_id starts at kPending.
    //
    // Throws WorkerIdentityRegistryError if:
    //  - worker_id already exists with registration_status kRevoked
    //    (revocation is terminal -- see revoke() below; re-admitting a
    //    revoked worker_id is an operator action outside this method's
    //    scope, not an automatic side effect of re-registration).
    //  - certificate_fingerprint is already bound to a different
    //    worker_id (a fingerprint identifies exactly one worker
    //    identity at a time).
    WorkerIdentityRecord register_identity(const std::string& worker_id,
                                           const std::string& certificate_identity,
                                           const std::string& certificate_serial,
                                           const std::string& certificate_fingerprint,
                                           const std::string& signing_public_key,
                                           const std::string& signing_key_id,
                                           const std::string& software_version,
                                           const std::string& build_id,
                                           double now_unix_s,
                                           double expires_at_unix_s);

    [[nodiscard]] std::optional<WorkerIdentityRecord> find_by_worker_id(
        const std::string& worker_id) const;
    [[nodiscard]] std::optional<WorkerIdentityRecord> find_by_certificate_fingerprint(
        const std::string& certificate_fingerprint) const;
    [[nodiscard]] std::vector<WorkerIdentityRecord> list() const;

    // kPending/kActive -> kSuspended. Idempotent if already kSuspended
    // (reason/timestamp are refreshed). Throws WorkerIdentityRegistryError
    // if worker_id is unknown or already kRevoked (revocation is terminal).
    WorkerIdentityRecord suspend(const std::string& worker_id,
                                 const std::string& reason,
                                 double now_unix_s);

    // kPending/kSuspended -> kActive. Idempotent if already kActive.
    // Throws WorkerIdentityRegistryError if worker_id is unknown or
    // already kRevoked.
    WorkerIdentityRecord activate(const std::string& worker_id, double now_unix_s);

    // Any non-kRevoked status -> kRevoked. Idempotent if already
    // kRevoked (reason is not overwritten by a later, potentially
    // less-specific, call). Throws WorkerIdentityRegistryError only if
    // worker_id is unknown -- revocation itself never fails once a
    // worker_id exists, since it is meant to be usable as an
    // unconditional emergency action.
    WorkerIdentityRecord revoke(const std::string& worker_id,
                               const std::string& reason,
                               double now_unix_s);

    // Marks every kPending/kActive/kSuspended record whose
    // expires_at_unix_s has passed as kExpired. Returns the worker_ids
    // that transitioned in this sweep. kRevoked records are left
    // alone (revocation is a stronger, terminal statement than
    // expiry). A record with expires_at_unix_s <= 0 is treated as
    // "does not expire" and is never swept.
    std::vector<std::string> sweep_expired(double now_unix_s);

  private:
    void persist() const;  // caller must hold mutex_

    mutable std::mutex mutex_;
    std::string persistence_path_;
    std::map<std::string, WorkerIdentityRecord> records_;  // keyed by worker_id
};

}  // namespace fl::coordinator
