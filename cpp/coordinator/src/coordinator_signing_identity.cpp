#include "fl_coordinator/coordinator_signing_identity.hpp"

#include <openssl/evp.h>

#include <array>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <vector>

#ifndef _WIN32
#include <sys/stat.h>
#endif

namespace fl::coordinator {

namespace {

constexpr std::size_t kEd25519PrivateKeySeedLength = 32;
constexpr std::size_t kEd25519PublicKeyLength = 32;
constexpr std::size_t kEd25519SignatureLength = 64;

std::string hex_encode(const unsigned char* data, std::size_t length) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(length * 2);
    for (std::size_t i = 0; i < length; ++i) {
        out += kHex[(data[i] >> 4) & 0xF];
        out += kHex[data[i] & 0xF];
    }
    return out;
}

std::vector<std::uint8_t> hex_decode(const std::string& hex, bool& ok) {
    ok = true;
    if (hex.size() % 2 != 0) {
        ok = false;
        return {};
    }
    std::vector<std::uint8_t> bytes;
    bytes.reserve(hex.size() / 2);
    auto nibble = [&](char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        const int high = nibble(hex[i]);
        const int low = nibble(hex[i + 1]);
        if (high < 0 || low < 0) {
            ok = false;
            return {};
        }
        bytes.push_back(static_cast<std::uint8_t>((high << 4) | low));
    }
    return bytes;
}

CoordinatorSigningIdentity identity_from_seed(const std::string& seed_raw) {
    if (seed_raw.size() != kEd25519PrivateKeySeedLength) {
        throw CoordinatorSigningIdentityError(
            "coordinator signing-key file does not contain a 32-byte Ed25519 seed");
    }
    EVP_PKEY* pkey =
        EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519,
                                     nullptr,
                                     reinterpret_cast<const unsigned char*>(seed_raw.data()),
                                     seed_raw.size());
    if (pkey == nullptr) {
        throw CoordinatorSigningIdentityError(
            "failed to reconstruct the coordinator's Ed25519 key from its persisted seed");
    }
    std::array<unsigned char, kEd25519PublicKeyLength> public_key{};
    std::size_t public_key_len = public_key.size();
    const int ok = EVP_PKEY_get_raw_public_key(pkey, public_key.data(), &public_key_len);
    EVP_PKEY_free(pkey);
    if (ok != 1) {
        throw CoordinatorSigningIdentityError(
            "failed to derive the coordinator's public key from its persisted seed");
    }
    CoordinatorSigningIdentity identity;
    identity.private_key_raw = seed_raw;
    identity.public_key_hex = hex_encode(public_key.data(), public_key_len);
    identity.key_id = coordinator_key_id_for(identity.public_key_hex);
    return identity;
}

}  // namespace

CoordinatorSigningIdentityError::CoordinatorSigningIdentityError(const std::string& what)
    : std::runtime_error(what) {}

std::string coordinator_key_id_for(const std::string& public_key_hex) {
    bool ok = false;
    const auto bytes = hex_decode(public_key_hex, ok);
    if (!ok || bytes.size() < 8) {
        throw CoordinatorSigningIdentityError("public_key_hex is not a valid Ed25519 public key");
    }
    return hex_encode(bytes.data(), 8);
}

CoordinatorSigningIdentity generate_coordinator_signing_identity() {
    EVP_PKEY_CTX* pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_ED25519, nullptr);
    if (pctx == nullptr || EVP_PKEY_keygen_init(pctx) != 1) {
        if (pctx != nullptr)
            EVP_PKEY_CTX_free(pctx);
        throw CoordinatorSigningIdentityError("failed to initialize Ed25519 key generation");
    }
    EVP_PKEY* pkey = nullptr;
    const int ok = EVP_PKEY_keygen(pctx, &pkey);
    EVP_PKEY_CTX_free(pctx);
    if (ok != 1 || pkey == nullptr) {
        throw CoordinatorSigningIdentityError("failed to generate a fresh Ed25519 keypair");
    }

    std::array<unsigned char, kEd25519PrivateKeySeedLength> seed{};
    std::size_t seed_len = seed.size();
    std::array<unsigned char, kEd25519PublicKeyLength> public_key{};
    std::size_t public_key_len = public_key.size();
    const bool got_private = EVP_PKEY_get_raw_private_key(pkey, seed.data(), &seed_len) == 1;
    const bool got_public =
        EVP_PKEY_get_raw_public_key(pkey, public_key.data(), &public_key_len) == 1;
    EVP_PKEY_free(pkey);
    if (!got_private || !got_public) {
        throw CoordinatorSigningIdentityError(
            "failed to extract raw key material from the generated Ed25519 keypair");
    }

    CoordinatorSigningIdentity identity;
    identity.private_key_raw.assign(reinterpret_cast<const char*>(seed.data()), seed_len);
    identity.public_key_hex = hex_encode(public_key.data(), public_key_len);
    identity.key_id = coordinator_key_id_for(identity.public_key_hex);
    return identity;
}

CoordinatorSigningIdentity load_or_create_coordinator_signing_identity(
    const std::string& private_key_path) {
    if (std::filesystem::exists(private_key_path)) {
        std::ifstream file(private_key_path, std::ios::binary);
        if (!file) {
            throw CoordinatorSigningIdentityError("failed to open coordinator signing-key file: " +
                                                  private_key_path);
        }
        std::ostringstream buffer;
        buffer << file.rdbuf();
        return identity_from_seed(buffer.str());
    }

    const auto identity = generate_coordinator_signing_identity();
    const std::filesystem::path target(private_key_path);
    if (target.has_parent_path()) {
        std::filesystem::create_directories(target.parent_path());
    }
    {
        std::ofstream file(private_key_path, std::ios::binary | std::ios::trunc);
        if (!file) {
            throw CoordinatorSigningIdentityError(
                "failed to write a freshly generated coordinator signing key to: " +
                private_key_path);
        }
        file.write(identity.private_key_raw.data(),
                   static_cast<std::streamsize>(identity.private_key_raw.size()));
    }
    // Best-effort restrictive permissions -- on Windows this call does
    // not exist and is simply skipped (advisory only, exactly the same
    // honest caveat signing_identity.py's save_signing_identity
    // documents for the Python side).
#ifndef _WIN32
    chmod(private_key_path.c_str(), S_IRUSR | S_IWUSR);
#endif
    return identity;
}

std::string sign_with_coordinator_identity(const CoordinatorSigningIdentity& identity,
                                           const std::string& message) {
    if (identity.private_key_raw.size() != kEd25519PrivateKeySeedLength) {
        throw CoordinatorSigningIdentityError(
            "cannot sign: this CoordinatorSigningIdentity has no valid private key material");
    }
    EVP_PKEY* pkey = EVP_PKEY_new_raw_private_key(
        EVP_PKEY_ED25519,
        nullptr,
        reinterpret_cast<const unsigned char*>(identity.private_key_raw.data()),
        identity.private_key_raw.size());
    if (pkey == nullptr) {
        throw CoordinatorSigningIdentityError(
            "failed to reconstruct the coordinator's Ed25519 key for signing");
    }
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    std::array<unsigned char, kEd25519SignatureLength> signature{};
    std::size_t signature_len = signature.size();
    bool ok = false;
    if (EVP_DigestSignInit(ctx, nullptr, nullptr, nullptr, pkey) == 1) {
        ok = EVP_DigestSign(ctx,
                            signature.data(),
                            &signature_len,
                            reinterpret_cast<const unsigned char*>(message.data()),
                            message.size()) == 1;
    }
    EVP_MD_CTX_free(ctx);
    EVP_PKEY_free(pkey);
    if (!ok) {
        throw CoordinatorSigningIdentityError("Ed25519 signing operation failed");
    }
    return hex_encode(signature.data(), signature_len);
}

std::string save_keyed_coordinator_signing_identity(const CoordinatorSigningIdentity& identity,
                                                    const std::string& directory) {
    const std::filesystem::path dir_path(directory);
    std::filesystem::create_directories(dir_path);
    const std::filesystem::path target =
        dir_path / ("coordinator." + identity.key_id + ".signing-key.pem");
    {
        std::ofstream file(target, std::ios::binary | std::ios::trunc);
        if (!file) {
            throw CoordinatorSigningIdentityError("failed to write coordinator signing-key file: " +
                                                  target.string());
        }
        file.write(identity.private_key_raw.data(),
                   static_cast<std::streamsize>(identity.private_key_raw.size()));
    }
#ifndef _WIN32
    chmod(target.string().c_str(), S_IRUSR | S_IWUSR);
#endif
    return target.string();
}

CoordinatorSigningIdentity load_keyed_coordinator_signing_identity(const std::string& key_id,
                                                                   const std::string& directory) {
    const std::filesystem::path target =
        std::filesystem::path(directory) / ("coordinator." + key_id + ".signing-key.pem");
    if (!std::filesystem::exists(target)) {
        throw CoordinatorSigningIdentityError("no coordinator signing key found for key_id '" +
                                              key_id + "' at " + target.string());
    }
    std::ifstream file(target, std::ios::binary);
    if (!file) {
        throw CoordinatorSigningIdentityError("failed to open coordinator signing-key file: " +
                                              target.string());
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    const auto identity = identity_from_seed(buffer.str());
    if (identity.key_id != key_id) {
        throw CoordinatorSigningIdentityError("coordinator signing-key file at " + target.string() +
                                              " does not match the requested key_id '" + key_id +
                                              "' (derived '" + identity.key_id +
                                              "') -- refusing to use a mismatched key");
    }
    return identity;
}

CoordinatorActiveIdentityStore::CoordinatorActiveIdentityStore(CoordinatorSigningIdentity initial)
    : current_(std::make_shared<const CoordinatorSigningIdentity>(std::move(initial))) {}

std::shared_ptr<const CoordinatorSigningIdentity> CoordinatorActiveIdentityStore::current() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return current_;
}

void CoordinatorActiveIdentityStore::set(CoordinatorSigningIdentity new_identity) {
    auto next = std::make_shared<const CoordinatorSigningIdentity>(std::move(new_identity));
    std::lock_guard<std::mutex> lock(mutex_);
    current_ = std::move(next);
}

}  // namespace fl::coordinator
