// Unit tests for the pure-logic half of peer_identity.hpp
// (has_service_identity/has_worker_identity) — the AuthContext
// extraction half (extract_peer_identity) requires a real gRPC
// ServerContext produced by an actual TLS handshake, which is
// validated separately, live, against a real compiled coordinator and
// real certificates (see docs/certificate-identity-binding.md's
// "Live validation" section) rather than by a unit test double here —
// constructing a fake grpc::AuthContext with the right internal state
// would test the double, not the real gRPC/OpenSSL integration this
// module exists to read the result of.
#include "fl_coordinator/peer_identity.hpp"

#include <iostream>
#include <string>

namespace {

int g_failures = 0;

void check(bool condition, const std::string& label) {
    if (!condition) {
        std::cerr << "FAILED: " << label << "\n";
        ++g_failures;
    }
}

}  // namespace

int main() {
    using fl::coordinator::PeerIdentity;
    using fl::coordinator::has_service_identity;
    using fl::coordinator::has_worker_identity;

    {
        PeerIdentity identity;
        identity.authenticated = true;
        identity.uri_sans = {"spiffe://federated-platform/worker/worker-1"};
        check(has_worker_identity(identity, "worker-1"),
              "matching worker URI SAN is recognized");
        check(!has_worker_identity(identity, "worker-2"),
              "non-matching worker id is rejected");
        check(!has_service_identity(identity, "coordinator"),
              "a worker identity is never mistaken for a service identity");
    }

    {
        PeerIdentity identity;
        identity.authenticated = true;
        identity.uri_sans = {"spiffe://federated-platform/service/coordinator"};
        check(has_service_identity(identity, "coordinator"),
              "matching service URI SAN is recognized");
        check(!has_service_identity(identity, "go-api"),
              "non-matching service name is rejected");
        check(!has_worker_identity(identity, "coordinator"),
              "a service identity is never mistaken for a worker identity");
    }

    {
        // A worker id that is a literal prefix/suffix of another worker
        // id must not match -- exact string equality only, no
        // accidental substring matching.
        PeerIdentity identity;
        identity.authenticated = true;
        identity.uri_sans = {"spiffe://federated-platform/worker/worker-1"};
        check(!has_worker_identity(identity, "worker-1-fake"),
              "a worker id that is a superstring of the real one does not match");
        check(!has_worker_identity(identity, "worker-"),
              "a worker id that is a prefix of the real one does not match");
    }

    {
        PeerIdentity identity;  // authenticated defaults to false, uri_sans empty
        check(!has_worker_identity(identity, "worker-1"),
              "an unauthenticated/empty identity never matches any worker id");
        check(!has_service_identity(identity, "coordinator"),
              "an unauthenticated/empty identity never matches any service name");
    }

    {
        // Multiple URI SANs on one certificate -- has_* must scan all
        // of them, not just the first.
        PeerIdentity identity;
        identity.authenticated = true;
        identity.uri_sans = {
            "spiffe://federated-platform/service/some-other-thing",
            "spiffe://federated-platform/worker/worker-7",
        };
        check(has_worker_identity(identity, "worker-7"),
              "a matching URI SAN later in the list is still found");
    }

    if (g_failures == 0) {
        std::cout << "all peer identity tests passed\n";
    }
    return g_failures == 0 ? 0 : 1;
}
