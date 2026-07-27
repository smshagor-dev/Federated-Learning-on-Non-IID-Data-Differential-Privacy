#pragma once

// Persistent, multi-key signing-key registry -- Signing-Key Lifecycle
// slice, Work Package B. See docs/signing-key-management.md.
//
// Deliberately separate from WorkerIdentityRegistry (which still owns
// one "current/preferred" signing_public_key/signing_key_id per worker
// as a convenience cache, refreshed on rotation): a worker's identity
// (certificate binding, suspend/activate/revoke status) and a worker's
// signing-key history (which keys have ever existed, their individual
// ACTIVE/GRACE_PERIOD/REVOKED/EXPIRED status) are different concerns
// with different lifecycles -- a worker can have exactly one identity
// but, over its lifetime, many signing keys.
//
// Protobuf-free and gRPC-free, mirroring WorkerIdentityRegistry/
// ReplayProtectionStore exactly (same atomic temp-file+rename
// persistence, same FNV-1a checksum trailer, throws rather than
// silently starting empty on corruption) so it builds and is
// unit-testable on this Windows/MSVC development machine like every
// other coordinator persistence class in this codebase.

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

enum class SigningKeyStatus {
    kPending,
    kActive,
    kGracePeriod,
    kRevoked,
    kExpired,
};

std::string to_string(SigningKeyStatus status);
SigningKeyStatus signing_key_status_from_string(const std::string& value);

class SigningKeyRegistryError : public std::runtime_error {
  public:
    explicit SigningKeyRegistryError(const std::string& what);
};

struct SigningKeyRecord {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version{kSchemaVersion};
    std::string worker_id;
    std::string signing_key_id;
    // Hex-encoded raw Ed25519 public key (32 bytes -> 64 hex chars),
    // matching WorkerSigningIdentity.public_key_hex()'s encoding.
    std::string public_key_hex;
    // SHA-256 hex digest of the raw public key bytes -- a stronger,
    // independent uniqueness check than signing_key_id (which is only
    // the first 8 bytes of the same key, per the Python side's
    // existing _key_id_for convention).
    std::string public_key_fingerprint;
    SigningKeyStatus status{SigningKeyStatus::kPending};
    double created_at_unix_s{0.0};
    double activated_at_unix_s{0.0};
    // 0.0 means "does not expire".
    double expires_at_unix_s{0.0};
    // 0.0 means "never entered grace period".
    double grace_period_start_unix_s{0.0};
    double grace_period_end_unix_s{0.0};
    std::string rotated_from_key_id;
    std::string rotated_to_key_id;
    double revoked_at_unix_s{0.0};
    std::string revocation_reason;
    // "migration" | "registration" | "rotation" -- see
    // docs/signing-key-migration.md.
    std::string registration_source;
};

// Everything needed to register a worker's very first signing key
// (trust-on-first-use, same convention already established for
// signed capability statements) -- always becomes ACTIVE immediately,
// no grace period (there is no prior key to grace-period against).
// public_key_fingerprint is computed by the caller (a real SHA-256 hex
// digest over the raw public key bytes, via signed_envelope_verifier.cpp's
// existing sha256_hex helper in the gRPC-gated build) and passed in
// rather than computed here -- this header stays OpenSSL-free and
// protobuf-free, matching WorkerIdentityRegistry's identical design
// (it too just stores whatever certificate_fingerprint string
// peer_identity.cpp already computed, never deriving one itself).
struct InitialSigningKeyRegistration {
    std::string worker_id;
    std::string signing_key_id;
    std::string public_key_hex;
    std::string public_key_fingerprint;
    double now_unix_s{0.0};
    double expires_at_unix_s{0.0};
    std::string registration_source{"registration"};
};

struct SigningKeyRotationRequest {
    std::string worker_id;
    std::string current_signing_key_id;
    std::string new_signing_key_id;
    std::string new_public_key_hex;
    std::string new_public_key_fingerprint;
    double new_key_expires_at_unix_s{0.0};
    double grace_period_seconds{0.0};
    double now_unix_s{0.0};
};

enum class SigningKeyRotationRejectionReason {
    kNone,
    kUnknownCurrentKey,
    kCurrentKeyNotActive,
    kDuplicateNewKeyId,
    kDuplicatePublicKey,
    kInvalidKeyLength,
    kExcessiveGracePeriod,
    kInvalidExpiry,
    kMaxActiveKeysExceeded,
};

std::string to_string(SigningKeyRotationRejectionReason reason);

struct SigningKeyRotationResult {
    bool accepted = false;
    SigningKeyRotationRejectionReason reason = SigningKeyRotationRejectionReason::kNone;
    std::string detail;  // human-readable; always set, even when accepted ("ok")
    SigningKeyRecord new_key;       // valid only when accepted
    SigningKeyRecord previous_key;  // valid only when accepted
};

// Restart-safe, multi-key-per-worker signing-key registry. One
// persisted file per instance, atomic temp-file+rename writes -- same
// pattern as WorkerIdentityRegistry.
//
// Policy enforced here (docs/signing-key-management.md "Signing-key
// policy"): at most one ACTIVE key and at most one GRACE_PERIOD key
// per worker at any time; a maximum grace period
// (kMaxGracePeriodSeconds, default 24h) rejects an excessive request
// rather than silently clamping it (a caller that wanted a shorter
// grace period should get a clear rejection, not a silently different
// value it never asked for).
class SigningKeyRegistry {
  public:
    static constexpr double kMaxGracePeriodSeconds = 86400.0;  // 24 hours
    static constexpr std::size_t kExpectedEd25519PublicKeyHexLength = 64;

    explicit SigningKeyRegistry(std::string persistence_path);

    // Registers a worker's first-ever signing key as ACTIVE
    // immediately. Throws SigningKeyRegistryError if signing_key_id is
    // already registered for a *different* worker_id, if the public
    // key fingerprint is already registered for a different worker_id,
    // or if public_key_hex is not a valid Ed25519 public key encoding
    // (exactly 64 lowercase/uppercase hex characters). Idempotent
    // refresh: calling again for the same worker_id/signing_key_id
    // with the same public key is a no-op returning the existing
    // record (matches WorkerIdentityRegistry's identical convention).
    SigningKeyRecord register_initial_key(const InitialSigningKeyRegistration& request);

    // Read-only: does not mutate state. See docs/signing-key-management.md's
    // rotation flow for the full accept/reject rule list. Only rejects
    // (never partially applies) -- callers must call commit_rotation()
    // separately after this returns accepted == true.
    [[nodiscard]] SigningKeyRotationResult validate_rotation(
        const SigningKeyRotationRequest& request) const;

    // Persists the rotation validated by a prior validate_rotation()
    // call: the new key becomes ACTIVE, the old (current) key becomes
    // GRACE_PERIOD with grace_period_end = now + grace_period_seconds
    // (or EXPIRED immediately if grace_period_seconds <= 0). Callers
    // must have already checked SigningKeyRotationResult::accepted.
    SigningKeyRotationResult commit_rotation(const SigningKeyRotationRequest& request);

    // Any non-kRevoked status -> kRevoked. Idempotent if already
    // kRevoked. Throws SigningKeyRegistryError if worker_id/signing_key_id
    // is unknown -- revocation itself never fails once a key exists,
    // matching WorkerIdentityRegistry::revoke's identical
    // "unconditional emergency action" convention.
    SigningKeyRecord revoke_key(const std::string& worker_id, const std::string& signing_key_id,
                               const std::string& reason, double now_unix_s);

    // Read-only, lazily evaluates expiry relative to now_unix_s without
    // requiring sweep_expired() to have run first (Work Package I:
    // "evaluate expiry during every verification") -- if the persisted
    // status is kActive/kGracePeriod but the relevant expiry timestamp
    // has passed, the *returned* record's status reflects kExpired even
    // though the persisted file is not rewritten by this call.
    [[nodiscard]] std::optional<SigningKeyRecord> find(const std::string& worker_id,
                                                       const std::string& signing_key_id,
                                                       double now_unix_s) const;

    // Convenience: the worker's current ACTIVE key, if any (lazily
    // expiry-evaluated, same as find()).
    [[nodiscard]] std::optional<SigningKeyRecord> find_active(const std::string& worker_id,
                                                              double now_unix_s) const;

    // True if the worker has at least one key that is currently ACTIVE
    // or GRACE_PERIOD (lazily expiry-evaluated) -- used by AcquireTask
    // to refuse a worker with no valid signing key at all.
    [[nodiscard]] bool has_any_valid_key(const std::string& worker_id, double now_unix_s) const;

    [[nodiscard]] std::vector<SigningKeyRecord> list_for_worker(const std::string& worker_id,
                                                                double now_unix_s) const;

    // Maintenance: persists every lazily-computed GRACE_PERIOD->EXPIRED
    // and ACTIVE->EXPIRED transition as of now_unix_s. Not required for
    // correct verification (find()/find_active()/has_any_valid_key()
    // already evaluate expiry lazily) -- exists so list_for_worker()
    // and any administration surface reflect a durable, persisted
    // status rather than only a transient computed one. Returns the
    // (worker_id, signing_key_id) pairs that transitioned.
    std::vector<std::pair<std::string, std::string>> sweep_expired(double now_unix_s);

  private:
    void persist() const;  // caller must hold mutex_
    [[nodiscard]] SigningKeyRecord effective_record(const SigningKeyRecord& record,
                                                     double now_unix_s) const;

    mutable std::mutex mutex_;
    std::string persistence_path_;
    // Keyed by (worker_id, signing_key_id) -- see .cpp for the
    // composite-key struct.
    struct Key {
        std::string worker_id;
        std::string signing_key_id;
        bool operator<(const Key& other) const {
            if (worker_id != other.worker_id) return worker_id < other.worker_id;
            return signing_key_id < other.signing_key_id;
        }
    };
    std::map<Key, SigningKeyRecord> records_;
};

}  // namespace fl::coordinator
