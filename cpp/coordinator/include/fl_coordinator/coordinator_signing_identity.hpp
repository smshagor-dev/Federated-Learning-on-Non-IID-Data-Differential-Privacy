#pragma once

// The coordinator's own persistent Ed25519 signing identity --
// Coordinator-Signed Tasks slice, Work Package B. See
// docs/coordinator-signing-identity.md.
//
// Deliberately a *different* keypair, in a *different* file, from the
// TLS server credential (FL_COORDINATOR_SERVER_KEY): the TLS key
// authenticates the transport connection; this key authenticates
// individual signed tasks, independent of which connection carried
// them -- the same separation of concerns docs/worker-identity.md
// already establishes for worker signing keys. This is the first place
// the C++ coordinator *signs* rather than only *verifies* -- OpenSSL-
// backed (EVP_PKEY_ED25519), gRPC-gated build only, same reasoning as
// signed_envelope_verifier.hpp's header comment (real crypto needs
// OpenSSL, which is only found in the gRPC-gated CMake branch on this
// Windows/MSVC development machine).

#include <cstdint>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>

namespace fl::coordinator {

class CoordinatorSigningIdentityError : public std::runtime_error {
  public:
    explicit CoordinatorSigningIdentityError(const std::string& what);
};

// The coordinator's Ed25519 keypair. The private key is never exposed
// as a field of this struct outside the process's own memory -- only
// public_key_hex/key_id are ever written to the trusted-key bundle
// file or an RPC response; sign_with_coordinator_identity is the only
// sanctioned way to use the private material.
struct CoordinatorSigningIdentity {
    std::string key_id;
    std::string public_key_hex;
    // Raw 32-byte private key seed. Never logged, never included in
    // any RPC response or the trusted-key bundle file -- see
    // load_or_create_coordinator_signing_identity's persistence
    // contract for the only place this leaves memory.
    std::string private_key_raw;
};

// A short, stable identifier for a public signing key -- the first 8
// raw bytes of the public key, hex-encoded. Matches
// WorkerSigningIdentity._key_id_for's (Python) identical convention
// byte-for-byte, so both sides of this codebase compute the same
// key_id for the same key.
[[nodiscard]] std::string coordinator_key_id_for(const std::string& public_key_hex);

// Generates a fresh Ed25519 keypair. Real OpenSSL keygen (EVP_PKEY_ED25519),
// never a hand-rolled/deterministic-for-tests generator.
[[nodiscard]] CoordinatorSigningIdentity generate_coordinator_signing_identity();

// Loads a previously-persisted identity from `private_key_path` (the
// raw 32-byte private key seed, written with restrictive permissions
// -- best-effort on Windows, same honest caveat as
// signing_identity.py's save_signing_identity) -- or, if the file does
// not exist, generates a fresh one and persists it there. Throws
// CoordinatorSigningIdentityError on a malformed existing file --
// never silently regenerates over a real one (a silent regeneration
// would change the coordinator's signing identity, and therefore which
// key every already-deployed worker trusts, without anyone deciding
// that on purpose).
[[nodiscard]] CoordinatorSigningIdentity load_or_create_coordinator_signing_identity(
    const std::string& private_key_path);

// Raw Ed25519 signature over `message`, hex-encoded (128 hex chars).
[[nodiscard]] std::string sign_with_coordinator_identity(const CoordinatorSigningIdentity& identity,
                                                         const std::string& message);

// Security Administration slice, Work Package A/B: keyed persistence
// for a coordinator identity created by rotation (as opposed to the
// single fixed-path "genesis" identity load_or_create_coordinator_signing_identity
// manages). Mirrors signing_key_rotation.py's
// save_keyed_signing_identity/load_keyed_signing_identity convention
// for workers, one directory level up (per-coordinator instead of
// per-worker): `{directory}/coordinator.{key_id}.signing-key.pem`.
[[nodiscard]] std::string save_keyed_coordinator_signing_identity(
    const CoordinatorSigningIdentity& identity, const std::string& directory);

// Throws CoordinatorSigningIdentityError if the file is missing, or if
// the loaded key's derived key_id does not match `key_id` (the same
// real mismatch detection signing_key_rotation.py's
// load_keyed_signing_identity performs for workers).
[[nodiscard]] CoordinatorSigningIdentity load_keyed_coordinator_signing_identity(
    const std::string& key_id, const std::string& directory);

// A thread-safe, swappable holder for "the identity AcquireTask should
// sign with right now." Needed because real rotation must change which
// identity is used for signing while the gRPC server keeps handling
// concurrent requests on other threads -- current() returns an
// immutable snapshot (never partially updated); set() atomically
// replaces it. Deliberately a concrete class, not an interface: exactly
// one implementation is needed, matching this codebase's established
// convention for its other persistence/state classes.
class CoordinatorActiveIdentityStore {
  public:
    explicit CoordinatorActiveIdentityStore(CoordinatorSigningIdentity initial);

    [[nodiscard]] std::shared_ptr<const CoordinatorSigningIdentity> current() const;
    void set(CoordinatorSigningIdentity new_identity);

  private:
    mutable std::mutex mutex_;
    std::shared_ptr<const CoordinatorSigningIdentity> current_;
};

}  // namespace fl::coordinator
