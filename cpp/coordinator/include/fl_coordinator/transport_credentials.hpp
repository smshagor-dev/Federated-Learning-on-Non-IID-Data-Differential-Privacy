#pragma once

// gRPC server transport-credential construction for the Secure
// Transport and Worker Identity Hardening slice — see docs/mtls.md.
//
// IMPORTANT: this header/its .cpp are only compiled as part of
// fl_coordinator_grpc_server, which this Windows development machine
// cannot build locally (no local gRPC C++ toolchain — see
// docs/coordinator-runtime.md). This code has been written carefully
// against the documented, stable gRPC C++ SSL credentials API
// (grpcpp/security/server_credentials.h) but has NOT been compiled or
// run in this environment. It is verified in CI/Docker
// (ubuntu-latest / infra/docker/cpp-coordinator.Dockerfile), the same
// place fl_coordinator_grpc_server itself is verified — see
// docs/transport-identity-report.md for the exact, honest status of
// this specific file.

#include <memory>
#include <stdexcept>
#include <string>

namespace grpc {
class ServerCredentials;
}

namespace fl::coordinator {

enum class TransportMode {
    kInsecureDevelopment,
    kTls,
    kMtlsRequired,
};

[[nodiscard]] std::string to_string(TransportMode mode);

// Thrown when transport credential construction fails (missing/unreadable
// certificate files, an insecure mode not explicitly allowed, an
// unrecognized mode). Never caught to silently fall back to insecure
// credentials — see the closure-gate requirement "never log private keys
// or certificate contents" and "structured startup errors": this
// exception's message never includes file contents, only paths and the
// nature of the failure.
class TransportConfigurationError : public std::runtime_error {
  public:
    explicit TransportConfigurationError(const std::string& what);
};

struct TransportConfig {
    TransportMode mode = TransportMode::kInsecureDevelopment;
    std::string server_cert_path;
    std::string server_key_path;
    // Trusted CA for verifying *client* certificates — required for
    // kMtlsRequired, unused for kTls/kInsecureDevelopment.
    std::string trusted_client_ca_path;
};

// Reads FL_TRANSPORT_MODE / FL_COORDINATOR_SERVER_CERT /
// FL_COORDINATOR_SERVER_KEY / FL_COORDINATOR_CLIENT_CA /
// FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT from the process environment
// — see docs/mtls.md for the exact contract, mirroring the Go and
// Python worker environment-variable conventions in this same slice.
// Throws TransportConfigurationError if kInsecureDevelopment is
// selected (explicitly or by omission — the default) without
// FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT=true, matching the
// closure-gate requirement that insecure mode is never a silent
// default.
[[nodiscard]] TransportConfig transport_config_from_environment();

// Builds real grpc::ServerCredentials from `config`:
//   kInsecureDevelopment -> grpc::InsecureServerCredentials()
//   kTls                 -> grpc::SslServerCredentials() with no client
//                            certificate requested
//   kMtlsRequired         -> grpc::SslServerCredentials() with
//                            GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY
// Throws TransportConfigurationError (never silently returns insecure
// credentials) if certificate/key files cannot be read for kTls/kMtlsRequired.
[[nodiscard]] std::shared_ptr<grpc::ServerCredentials> build_server_credentials(
    const TransportConfig& config);

}  // namespace fl::coordinator
