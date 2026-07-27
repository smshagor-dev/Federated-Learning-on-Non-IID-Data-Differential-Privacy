#pragma once

// Strengthened trusted-coordinator-key-bundle writer -- Security
// Administration slice, Work Package E. See
// docs/trusted-coordinator-key-bundle.md.
//
// Reusable from both coordinator startup (main.cpp) and the
// rotate/revoke RPC handlers (coordinator_service.cpp) -- one atomic
// writer, one file format, called every time the registry's trusted
// key set could have changed. Protobuf-free and gRPC-free (matching
// CoordinatorSigningKeyRegistry), so it builds and is unit-testable on
// this Windows/MSVC development machine without a local gRPC toolchain.
//
// "Bundle signature or protected-distribution guarantee": this pass
// does not self-sign the bundle. The protected-distribution guarantee
// is the same one docs/development-pki.md already relies on for the
// TLS CA certificate: atomic writes, restrictive file permissions, and
// out-of-band delivery (a mounted volume/secret, never fetched over
// the connection being authenticated) -- not an additional signature
// layered on top. Stated honestly, not silently assumed.

#include "fl_coordinator/coordinator_signing_key_registry.hpp"

#include <cstdint>
#include <stdexcept>
#include <string>

namespace fl::coordinator {

class TrustedKeyBundleError : public std::runtime_error {
  public:
    explicit TrustedKeyBundleError(const std::string& what);
};

struct TrustedKeyBundleWriteResult {
    bool ok = false;
    std::uint64_t bundle_version = 0;  // valid only when ok == true
    std::string reason;                // set only when ok == false
};

// Returns the bundle_version currently on disk at `path` (0 if the
// file does not exist, is unreadable, or does not parse -- a missing
// or unreadable *prior* bundle is not itself an error here; the writer
// simply starts versioning at 1 in that case). Never throws.
[[nodiscard]] std::uint64_t read_bundle_version(const std::string& path);

// Atomically writes a new trusted-key bundle reflecting
// `registry.trusted_public_keys(now_unix_s)` (every key currently
// ACTIVE or GRACE_PERIOD). Bundle fields: schema_version,
// coordinator_identity (a human-readable label, not a secret),
// bundle_version (strictly greater than whatever was previously on
// disk), generated_at_unix_s, active_signing_key_id (empty string if
// none), one record per trusted key (signing_key_id, public_key_hex,
// public_key_fingerprint, status, created_at_unix_s [also serves as
// the activation timestamp -- a coordinator key is ACTIVE immediately
// upon creation, unlike a worker key, so there is no separate
// activation event to record], expires_at_unix_s,
// grace_period_end_unix_s, revoked_at_unix_s), and a checksum (FNV-1a
// hex over every preceding field -- accidental-corruption detection
// only, not a cryptographic integrity guarantee; see this header's
// "Bundle signature" note above). Temp-file + rename: never leaves a
// partially-written file observable to a concurrent reader.
[[nodiscard]] TrustedKeyBundleWriteResult write_trusted_key_bundle(
    const CoordinatorSigningKeyRegistry& registry, const std::string& path,
    const std::string& coordinator_identity_label, double now_unix_s);

}  // namespace fl::coordinator
