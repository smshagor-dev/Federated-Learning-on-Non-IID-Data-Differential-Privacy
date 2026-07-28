#pragma once

// Persistent coordinator signing-key registry -- Coordinator-Signed
// Tasks slice, Work Package C. See docs/coordinator-signing-key-management.md.
//
// Mirrors SigningKeyRegistry's design (lazy expiry evaluation, atomic
// persistence, validate/commit transaction split for rotation) but
// tracks the coordinator's *own* key(s), keyed by signing_key_id
// alone -- there is exactly one coordinator, not one row per
// (worker_id, signing_key_id). No kPending status: the coordinator's
// own identity is ACTIVE from the moment it is registered (unlike a
// worker's key, there is no separate "registered but not yet the
// preferred key" state to represent).
//
// Protobuf-free and gRPC-free, mirroring SigningKeyRegistry exactly,
// so it builds and is unit-testable on this Windows/MSVC development
// machine without a local gRPC toolchain.

#include <cstdint>
#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

enum class CoordinatorSigningKeyStatus {
    kActive,
    kGracePeriod,
    kRevoked,
    kExpired,
};

std::string to_string(CoordinatorSigningKeyStatus status);
CoordinatorSigningKeyStatus coordinator_signing_key_status_from_string(const std::string& value);

class CoordinatorSigningKeyRegistryError : public std::runtime_error {
  public:
    explicit CoordinatorSigningKeyRegistryError(const std::string& what);
};

struct CoordinatorSigningKeyRecord {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version{kSchemaVersion};
    std::string signing_key_id;
    // Hex-encoded raw Ed25519 public key (32 bytes -> 64 hex chars).
    std::string public_key_hex;
    // SHA-256 hex digest of the raw public key bytes -- computed
    // externally (via signed_envelope_verifier.cpp's public_key_fingerprint_hex
    // in the gRPC-gated build) and passed in, same convention as
    // SigningKeyRecord::public_key_fingerprint.
    std::string public_key_fingerprint;
    CoordinatorSigningKeyStatus status{CoordinatorSigningKeyStatus::kActive};
    double created_at_unix_s{0.0};
    // 0.0 means "does not expire".
    double expires_at_unix_s{0.0};
    // 0.0 means "never entered grace period".
    double grace_period_end_unix_s{0.0};
    std::string rotated_from_key_id;
    std::string rotated_to_key_id;
    double revoked_at_unix_s{0.0};
    std::string revocation_reason;
};

struct InitialCoordinatorSigningKeyRegistration {
    std::string signing_key_id;
    std::string public_key_hex;
    std::string public_key_fingerprint;
    double now_unix_s{0.0};
    double expires_at_unix_s{0.0};
};

struct CoordinatorSigningKeyRotationRequest {
    std::string current_signing_key_id;
    std::string new_signing_key_id;
    std::string new_public_key_hex;
    std::string new_public_key_fingerprint;
    double new_key_expires_at_unix_s{0.0};
    double grace_period_seconds{0.0};
    double now_unix_s{0.0};
};

enum class CoordinatorSigningKeyRotationRejectionReason {
    kNone,
    kUnknownCurrentKey,
    kCurrentKeyNotActive,
    kDuplicateNewKeyId,
    kDuplicatePublicKey,
    kInvalidKeyLength,
    kExcessiveGracePeriod,
    // Security Administration slice, Work Package A: the prior slice's
    // registry only rejected an expiry that was not strictly in the
    // future (kInvalidExpiry, folded into the invalid-expiry check
    // below); this is a genuinely new rule -- a maximum coordinator-key
    // lifetime, not just a minimum one.
    kExcessiveKeyLifetime,
    kInvalidExpiry,
};

std::string to_string(CoordinatorSigningKeyRotationRejectionReason reason);

struct CoordinatorSigningKeyRotationResult {
    bool accepted = false;
    CoordinatorSigningKeyRotationRejectionReason reason =
        CoordinatorSigningKeyRotationRejectionReason::kNone;
    std::string detail;
    CoordinatorSigningKeyRecord new_key;
    CoordinatorSigningKeyRecord previous_key;
};

// Restart-safe registry of the coordinator's own signing key(s). One
// persisted file per instance, atomic temp-file+rename writes -- same
// pattern as SigningKeyRegistry. At most one ACTIVE key and at most one
// GRACE_PERIOD key at any time (a single coordinator identity in
// rotation, not a multi-key-per-principal model like the worker
// registry -- there is only ever one coordinator).
class CoordinatorSigningKeyRegistry {
  public:
    static constexpr double kMaxGracePeriodSeconds = 86400.0;  // 24 hours
    // Security Administration slice, Work Package A: a new maximum
    // coordinator-key lifetime (90 days) -- a policy ceiling, not a
    // cryptographic requirement; distinct from kMaxGracePeriodSeconds
    // above (that bounds how long a *retired* key stays acceptable,
    // this bounds how long a *new* key may be requested to remain
    // ACTIVE for in the first place).
    static constexpr double kMaxCoordinatorKeyLifetimeSeconds = 90.0 * 86400.0;
    static constexpr std::size_t kExpectedEd25519PublicKeyHexLength = 64;

    explicit CoordinatorSigningKeyRegistry(std::string persistence_path);

    // Registers the coordinator's first-ever signing key as ACTIVE
    // immediately. Idempotent refresh: calling again with the same
    // signing_key_id and the same public key is a no-op returning the
    // existing record. Throws if signing_key_id already exists with a
    // *different* public key, or public_key_hex is not valid Ed25519
    // hex (64 chars).
    CoordinatorSigningKeyRecord register_initial_key(
        const InitialCoordinatorSigningKeyRegistration& request);

    [[nodiscard]] CoordinatorSigningKeyRotationResult validate_rotation(
        const CoordinatorSigningKeyRotationRequest& request) const;

    CoordinatorSigningKeyRotationResult commit_rotation(
        const CoordinatorSigningKeyRotationRequest& request);

    CoordinatorSigningKeyRecord revoke_key(const std::string& signing_key_id,
                                           const std::string& reason,
                                           double now_unix_s);

    // Lazily expiry-evaluated, matching SigningKeyRegistry::find.
    [[nodiscard]] std::optional<CoordinatorSigningKeyRecord> find(const std::string& signing_key_id,
                                                                  double now_unix_s) const;

    // The coordinator's current ACTIVE key, if any.
    [[nodiscard]] std::optional<CoordinatorSigningKeyRecord> active_key(double now_unix_s) const;

    // Every key a worker should currently trust: ACTIVE and
    // GRACE_PERIOD keys (lazily expiry-evaluated) -- the set written to
    // the trusted-key bundle file. See docs/signed-coordinator-tasks.md's
    // "Trusted coordinator key bundle" section.
    [[nodiscard]] std::vector<CoordinatorSigningKeyRecord> trusted_public_keys(
        double now_unix_s) const;

    [[nodiscard]] std::vector<CoordinatorSigningKeyRecord> list(double now_unix_s) const;

    std::vector<std::string> update_expired_keys(double now_unix_s);

  private:
    void persist() const;  // caller must hold mutex_
    [[nodiscard]] CoordinatorSigningKeyRecord effective_record(
        const CoordinatorSigningKeyRecord& record, double now_unix_s) const;

    mutable std::mutex mutex_;
    std::string persistence_path_;
    std::map<std::string, CoordinatorSigningKeyRecord> records_;  // keyed by signing_key_id
};

}  // namespace fl::coordinator
