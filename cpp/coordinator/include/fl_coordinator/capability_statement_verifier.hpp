#pragma once

// Verification of signed worker capability statements -- Coordinator
// Transport Verification and Message Authenticity slice, Work Package
// F. See docs/signed-capabilities.md and
// docs/canonical-security-serialization.md.
//
// The counterpart to python/src/fl_platform/security/capability_statement.py:
// that module signs a CapabilityStatementPayload with a worker's
// Ed25519 signing identity; this module verifies the wire form of the
// same payload (fl.worker.v1.SignedCapabilityStatement) actually
// received over an already mTLS-authenticated RegisterWorker call.
// This is OpenSSL-backed (EVP_PKEY_ED25519), which is why this header
// and its implementation only exist in the gRPC-gated build (gRPC
// already requires OpenSSL, so no new dependency is introduced for
// that build; the non-gRPC-gated fl_coordinator library used on
// Windows-local development stays free of a hard OpenSSL dependency).

#include <string>

namespace fl::worker::v1 {
class SignedCapabilityStatement;
}

namespace fl::coordinator {

struct CapabilityVerificationResult {
    bool valid = false;
    std::string reason;  // always set, even when valid == true ("ok")
};

// Canonical JSON encoding of `statement`'s signed payload fields --
// must byte-for-byte match
// fl_platform.security.capability_statement._canonical_bytes's
// json.dumps(payload, sort_keys=True, separators=(",", ":"),
// ensure_ascii=True) applied to the identical field set (schema_version
// through signing_key_id; never signing_public_key/payload_hash/signature
// themselves). Exposed separately from verify_capability_statement so
// it can be tested directly against fixed cross-language golden
// vectors -- see docs/canonical-security-serialization.md.
[[nodiscard]] std::string canonical_capability_payload_json(
    const fl::worker::v1::SignedCapabilityStatement& statement);

// Verifies, in order: payload_hash matches the recomputed SHA-256 of
// the canonical payload; the Ed25519 signature (verified against
// statement.signing_public_key) is valid over that same canonical
// payload; expires_at is a real value strictly after now_unix_s.
//
// Deliberately does NOT check: worker_id/certificate binding (the
// caller must have already established that via peer_identity.hpp
// before ever calling this), signing-key continuity against a
// previously registered key (WorkerIdentityRegistry's job), or nonce
// replay (a separate, not-yet-implemented store) -- see
// docs/signed-capabilities.md's enforcement-ordering section for why
// these stay separate, composable checks rather than one monolithic
// function.
[[nodiscard]] CapabilityVerificationResult verify_capability_statement(
    const fl::worker::v1::SignedCapabilityStatement& statement, double now_unix_s);

}  // namespace fl::coordinator
