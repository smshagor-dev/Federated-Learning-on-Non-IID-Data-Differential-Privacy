#pragma once

// Certificate identity extraction and binding — Coordinator Transport
// Verification and Message Authenticity slice, Work Package C. See
// docs/certificate-identity-binding.md.
//
// Only meaningful once a connection is actually authenticated via
// mTLS (GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY —
// see transport_credentials.hpp). Under INSECURE_DEVELOPMENT or
// TLS_SERVER (no client certificate requested), there is no peer
// identity to bind to, and callers of this module must treat
// PeerIdentity::authenticated == false as "identity binding does not
// apply to this connection" rather than a rejection — this is what
// keeps the existing (insecure-channel-based) coordinator_service_test.cpp
// suite passing unchanged: enforcement only activates once mTLS is
// actually in effect.

#include <string>
#include <vector>

namespace grpc {
class ServerContext;
}

namespace fl::coordinator {

// federated-platform is this project's fixed SPIFFE-style trust
// domain — see docs/development-pki.md.
inline constexpr const char* kSpiffeTrustDomain = "federated-platform";

struct PeerIdentity {
    // True only if the peer actually presented and the server verified
    // a client certificate (mTLS). False for INSECURE_DEVELOPMENT and
    // TLS_SERVER (no client cert requested) connections.
    bool authenticated = false;
    // Every URI SAN entry on the peer's certificate (this project's
    // certificates carry exactly one — spiffe://federated-platform/... —
    // but the extraction itself makes no assumption about count).
    std::vector<std::string> uri_sans;
    // The certificate's subject common name, for logging/diagnostics
    // only — never used for security decisions (URI SAN is the
    // authoritative identity field; see docs/certificate-identity-binding.md
    // for why CN is not trusted for this purpose).
    std::string subject_common_name;
    // SHA-256, hex-encoded, over the peer certificate's raw PEM text as
    // gRPC's AuthContext exposes it (GRPC_X509_PEM_CERT_PROPERTY_NAME) —
    // used by WorkerIdentityRegistry (Work Package E/F) purely as a
    // stable, unique-per-certificate value: the same certificate always
    // produces the same fingerprint, and any two distinct certificates
    // (even sharing the same URI SAN, e.g. after rotation) never
    // collide. NOT claimed to match `openssl x509 -fingerprint -sha256`
    // (that hashes the DER encoding, not the PEM text) — see
    // docs/worker-identity-registry.md. Empty when authenticated is
    // false.
    std::string certificate_fingerprint_sha256;
};

// Extracts what the gRPC/OpenSSL stack already verified about the
// connected peer from `context`'s AuthContext. Never performs its own
// certificate parsing or re-verification — that already happened
// during the TLS handshake (see transport_credentials.cpp); this
// function only reads the result gRPC already computed.
[[nodiscard]] PeerIdentity extract_peer_identity(const grpc::ServerContext& context);

// True if `identity` carries a URI SAN of the exact form
// spiffe://federated-platform/service/{service_name}.
[[nodiscard]] bool has_service_identity(const PeerIdentity& identity,
                                         const std::string& service_name);

// True if `identity` carries a URI SAN of the exact form
// spiffe://federated-platform/worker/{worker_id}.
[[nodiscard]] bool has_worker_identity(const PeerIdentity& identity,
                                       const std::string& worker_id);

}  // namespace fl::coordinator
