#include "fl_coordinator/peer_identity.hpp"

#include <grpcpp/grpcpp.h>
#include <grpcpp/security/auth_context.h>
#include <openssl/evp.h>

namespace fl::coordinator {

namespace {

std::string property_value_to_string(const grpc::string_ref& value) {
    return std::string(value.data(), value.size());
}

std::string sha256_hex(const std::string& data) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_len = 0;
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr);
    EVP_DigestUpdate(ctx, data.data(), data.size());
    EVP_DigestFinal_ex(ctx, digest, &digest_len);
    EVP_MD_CTX_free(ctx);
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(digest_len * 2);
    for (unsigned int i = 0; i < digest_len; ++i) {
        out += kHex[(digest[i] >> 4) & 0xF];
        out += kHex[digest[i] & 0xF];
    }
    return out;
}

}  // namespace

PeerIdentity extract_peer_identity(const grpc::ServerContext& context) {
    PeerIdentity identity;

    // const_cast: grpc::ServerContext::auth_context() is not itself
    // marked const in the gRPC C++ API even though it only reads
    // already-computed handshake state -- this function's own
    // parameter is const because callers should never need (and must
    // never use) this to influence the connection, only to read what
    // already happened.
    auto auth_context = const_cast<grpc::ServerContext&>(context).auth_context();  // NOLINT
    if (!auth_context || !auth_context->IsPeerAuthenticated()) {
        return identity;  // authenticated stays false -- no client cert presented/verified.
    }
    identity.authenticated = true;

    // GRPC_X509_SAN_PROPERTY_NAME ("x509_subject_alternative_name") is
    // gRPC's own documented AuthContext property name for every SAN
    // entry on the verified peer certificate, regardless of SAN type
    // (URI/DNS/IP) -- this project's certificates only ever populate
    // URI SANs meaningfully (see docs/development-pki.md), so every
    // value here is treated as a candidate URI identity without
    // separately filtering by SAN type.
    for (const auto& value : auth_context->FindPropertyValues(GRPC_X509_SAN_PROPERTY_NAME)) {
        identity.uri_sans.push_back(property_value_to_string(value));
    }

    const auto common_name_values = auth_context->FindPropertyValues(GRPC_X509_CN_PROPERTY_NAME);
    if (!common_name_values.empty()) {
        identity.subject_common_name = property_value_to_string(common_name_values.front());
    }

    const auto pem_cert_values = auth_context->FindPropertyValues(GRPC_X509_PEM_CERT_PROPERTY_NAME);
    if (!pem_cert_values.empty()) {
        identity.certificate_fingerprint_sha256 =
            sha256_hex(property_value_to_string(pem_cert_values.front()));
    }

    return identity;
}

bool has_service_identity(const PeerIdentity& identity, const std::string& service_name) {
    const std::string expected =
        std::string("spiffe://") + kSpiffeTrustDomain + "/service/" + service_name;
    for (const auto& uri : identity.uri_sans) {
        if (uri == expected) {
            return true;
        }
    }
    return false;
}

bool has_worker_identity(const PeerIdentity& identity, const std::string& worker_id) {
    const std::string expected =
        std::string("spiffe://") + kSpiffeTrustDomain + "/worker/" + worker_id;
    for (const auto& uri : identity.uri_sans) {
        if (uri == expected) {
            return true;
        }
    }
    return false;
}

}  // namespace fl::coordinator
