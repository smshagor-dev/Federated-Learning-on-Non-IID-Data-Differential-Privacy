#include "fl_coordinator/transport_credentials.hpp"

#include <cstdlib>
#include <fstream>
#include <sstream>

// The umbrella header, not the narrower <grpcpp/security/server_credentials.h>
// -- matching exactly what main.cpp already includes and successfully
// builds against in CI (see docs/coordinator-runtime.md), since this
// file cannot be compiled locally to verify a narrower include is
// sufficient on the gRPC version CI actually uses.
#include <grpcpp/grpcpp.h>

namespace fl::coordinator {

namespace {

std::string env_or_empty(const char* name) {
    const char* value = std::getenv(name);
    return value != nullptr ? std::string(value) : std::string();
}

std::string read_file_or_throw(const std::string& path, const std::string& label) {
    std::ifstream file(path, std::ios::binary);
    if (!file) {
        throw TransportConfigurationError("failed to open " + label + " at '" + path + "'");
    }
    std::ostringstream contents;
    contents << file.rdbuf();
    if (file.bad()) {
        throw TransportConfigurationError("failed to read " + label + " at '" + path + "'");
    }
    return contents.str();
}

}  // namespace

std::string to_string(TransportMode mode) {
    switch (mode) {
        case TransportMode::kInsecureDevelopment:
            return "insecure_development";
        case TransportMode::kTls:
            return "tls";
        case TransportMode::kMtlsRequired:
            return "mtls";
    }
    throw std::invalid_argument("unknown TransportMode");
}

TransportConfigurationError::TransportConfigurationError(const std::string& what)
    : std::runtime_error(what) {}

TransportConfig transport_config_from_environment() {
    TransportConfig config;
    const std::string mode_str = env_or_empty("FL_TRANSPORT_MODE");

    if (mode_str.empty() || mode_str == "insecure_development") {
        config.mode = TransportMode::kInsecureDevelopment;
    } else if (mode_str == "tls") {
        config.mode = TransportMode::kTls;
    } else if (mode_str == "mtls") {
        config.mode = TransportMode::kMtlsRequired;
    } else {
        throw TransportConfigurationError(
            "unrecognized FL_TRANSPORT_MODE '" + mode_str +
            "' (expected insecure_development, tls, or mtls)");
    }

    if (config.mode == TransportMode::kInsecureDevelopment) {
        // Mirrors the Go and Python worker sides of this same slice —
        // see docs/mtls.md — so a deployment cannot end up insecure by
        // omission in any of the three languages.
        if (env_or_empty("FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT") != "true") {
            throw TransportConfigurationError(
                "FL_TRANSPORT_MODE=insecure_development (or unset) requires "
                "FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT=true to be set explicitly; "
                "refusing to start insecure by omission");
        }
        return config;
    }

    config.server_cert_path = env_or_empty("FL_COORDINATOR_SERVER_CERT");
    config.server_key_path = env_or_empty("FL_COORDINATOR_SERVER_KEY");
    if (config.server_cert_path.empty() || config.server_key_path.empty()) {
        throw TransportConfigurationError(
            "FL_TRANSPORT_MODE=" + mode_str +
            " requires both FL_COORDINATOR_SERVER_CERT and FL_COORDINATOR_SERVER_KEY");
    }

    if (config.mode == TransportMode::kMtlsRequired) {
        config.trusted_client_ca_path = env_or_empty("FL_COORDINATOR_CLIENT_CA");
        if (config.trusted_client_ca_path.empty()) {
            throw TransportConfigurationError(
                "FL_TRANSPORT_MODE=mtls requires FL_COORDINATOR_CLIENT_CA (the CA that "
                "signed trusted Go API / worker client certificates)");
        }
    }

    return config;
}

std::shared_ptr<grpc::ServerCredentials> build_server_credentials(
    const TransportConfig& config) {
    if (config.mode == TransportMode::kInsecureDevelopment) {
        return grpc::InsecureServerCredentials();
    }

    const std::string cert_chain = read_file_or_throw(config.server_cert_path, "server certificate");
    const std::string private_key = read_file_or_throw(config.server_key_path, "server private key");

    grpc::SslServerCredentialsOptions ssl_options(
        config.mode == TransportMode::kMtlsRequired
            ? GRPC_SSL_REQUEST_AND_REQUIRE_CLIENT_CERTIFICATE_AND_VERIFY
            : GRPC_SSL_DONT_REQUEST_CLIENT_CERTIFICATE);

    grpc::SslServerCredentialsOptions::PemKeyCertPair key_cert_pair;
    key_cert_pair.private_key = private_key;
    key_cert_pair.cert_chain = cert_chain;
    ssl_options.pem_key_cert_pairs.push_back(key_cert_pair);

    if (config.mode == TransportMode::kMtlsRequired) {
        ssl_options.pem_root_certs =
            read_file_or_throw(config.trusted_client_ca_path, "trusted client CA");
    }

    auto credentials = grpc::SslServerCredentials(ssl_options);
    if (!credentials) {
        throw TransportConfigurationError(
            "grpc::SslServerCredentials returned null -- malformed certificate/key "
            "material for mode " + to_string(config.mode));
    }
    return credentials;
}

}  // namespace fl::coordinator
