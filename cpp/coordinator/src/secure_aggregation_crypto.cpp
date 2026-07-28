#include "fl_coordinator/secure_aggregation_crypto.hpp"

#include "fl_coordinator/secure_aggregation_session.hpp"

#include <openssl/evp.h>
#include <openssl/kdf.h>

#include <algorithm>
#include <array>
#include <cstring>
#include <sstream>

namespace fl::coordinator {

namespace {

// Domain-separation prefixes for the two canonical hashes below.
// Deliberately NOT written via `ostringstream << "...\x00"` (that
// would silently stop at the embedded NUL byte, since
// operator<<(ostream&, const char*) treats its argument as a
// NUL-terminated C string and never actually writes the terminator --
// a real bug caught during this file's own review, not a documented-
// but-untested assumption). Instead, mirroring
// coordinator_task_signing.cpp's identical `kDomainSeparationPrefix`/
// `kDomainSeparationPrefixLength` pattern: `sizeof(...) - 1` counts
// every byte up to and including the explicit \x00, excluding only the
// compiler-added terminating NUL, and the prefix is appended via
// `ostream::write` (which takes an explicit length and copies every
// byte, embedded NULs included) rather than `operator<<`.
constexpr char kCohortCommitmentPrefix[] = "FL_PLATFORM_SECAGG_COHORT_COMMITMENT_V1\x00";
constexpr std::size_t kCohortCommitmentPrefixLength = sizeof(kCohortCommitmentPrefix) - 1;
constexpr char kSessionConfigHashPrefix[] = "FL_PLATFORM_SECAGG_SESSION_CONFIG_V1\x00";
constexpr std::size_t kSessionConfigHashPrefixLength = sizeof(kSessionConfigHashPrefix) - 1;

// -- hex helpers -- deliberately a local copy, matching this
// codebase's established convention (see coordinator_task_signing.cpp's
// identical header comment) of each file keeping its own small
// serialization helpers rather than a shared utility header. Exposed
// publicly from this file (unlike most of those private copies)
// because fixture-loading test code needs them too.

std::string hex_encode_impl(const unsigned char* data, std::size_t length) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(length * 2);
    for (std::size_t i = 0; i < length; ++i) {
        out += kHex[(data[i] >> 4) & 0xF];
        out += kHex[data[i] & 0xF];
    }
    return out;
}

int hex_nibble(char c) {
    if (c >= '0' && c <= '9')
        return c - '0';
    if (c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

}  // namespace

SecureAggregationCryptoError::SecureAggregationCryptoError(const std::string& what)
    : std::runtime_error(what) {}

std::string hex_encode(const std::string& raw) {
    return hex_encode_impl(reinterpret_cast<const unsigned char*>(raw.data()), raw.size());
}

std::string hex_decode(const std::string& hex) {
    if (hex.size() % 2 != 0) {
        throw SecureAggregationCryptoError("hex_decode: input has odd length, not valid hex");
    }
    std::string out;
    out.reserve(hex.size() / 2);
    for (std::size_t i = 0; i < hex.size(); i += 2) {
        const int high = hex_nibble(hex[i]);
        const int low = hex_nibble(hex[i + 1]);
        if (high < 0 || low < 0) {
            throw SecureAggregationCryptoError("hex_decode: input contains a non-hex character");
        }
        out += static_cast<char>((high << 4) | low);
    }
    return out;
}

X25519KeyPair generate_x25519_keypair() {
    EVP_PKEY_CTX* pctx = EVP_PKEY_CTX_new_id(EVP_PKEY_X25519, nullptr);
    if (pctx == nullptr || EVP_PKEY_keygen_init(pctx) != 1) {
        if (pctx != nullptr)
            EVP_PKEY_CTX_free(pctx);
        throw SecureAggregationCryptoError("failed to initialize X25519 key generation");
    }
    EVP_PKEY* pkey = nullptr;
    const int ok = EVP_PKEY_keygen(pctx, &pkey);
    EVP_PKEY_CTX_free(pctx);
    if (ok != 1 || pkey == nullptr) {
        throw SecureAggregationCryptoError("failed to generate a fresh X25519 keypair");
    }

    std::array<unsigned char, kX25519KeyLength> priv{};
    std::size_t priv_len = priv.size();
    std::array<unsigned char, kX25519KeyLength> pub{};
    std::size_t pub_len = pub.size();
    const bool got_private = EVP_PKEY_get_raw_private_key(pkey, priv.data(), &priv_len) == 1;
    const bool got_public = EVP_PKEY_get_raw_public_key(pkey, pub.data(), &pub_len) == 1;
    EVP_PKEY_free(pkey);
    if (!got_private || !got_public || priv_len != kX25519KeyLength ||
        pub_len != kX25519KeyLength) {
        throw SecureAggregationCryptoError(
            "failed to extract raw key material from the generated X25519 keypair");
    }

    X25519KeyPair result;
    result.private_key_raw.assign(reinterpret_cast<const char*>(priv.data()), priv_len);
    result.public_key_raw.assign(reinterpret_cast<const char*>(pub.data()), pub_len);
    return result;
}

std::string derive_x25519_shared_secret(const std::string& self_private_key_raw,
                                        const std::string& peer_public_key_raw) {
    if (self_private_key_raw.size() != kX25519KeyLength) {
        throw SecureAggregationCryptoError(
            "derive_x25519_shared_secret: self private key is not 32 bytes");
    }
    if (peer_public_key_raw.size() != kX25519KeyLength) {
        throw SecureAggregationCryptoError(
            "derive_x25519_shared_secret: peer public key is not 32 bytes");
    }

    EVP_PKEY* self_pkey = EVP_PKEY_new_raw_private_key(
        EVP_PKEY_X25519,
        nullptr,
        reinterpret_cast<const unsigned char*>(self_private_key_raw.data()),
        self_private_key_raw.size());
    if (self_pkey == nullptr) {
        throw SecureAggregationCryptoError(
            "derive_x25519_shared_secret: failed to load self private key");
    }
    EVP_PKEY* peer_pkey = EVP_PKEY_new_raw_public_key(
        EVP_PKEY_X25519,
        nullptr,
        reinterpret_cast<const unsigned char*>(peer_public_key_raw.data()),
        peer_public_key_raw.size());
    if (peer_pkey == nullptr) {
        EVP_PKEY_free(self_pkey);
        throw SecureAggregationCryptoError(
            "derive_x25519_shared_secret: failed to load peer public key");
    }

    EVP_PKEY_CTX* dctx = EVP_PKEY_CTX_new(self_pkey, nullptr);
    std::string secret;
    bool ok = false;
    if (dctx != nullptr && EVP_PKEY_derive_init(dctx) == 1 &&
        EVP_PKEY_derive_set_peer(dctx, peer_pkey) == 1) {
        std::size_t secret_len = 0;
        if (EVP_PKEY_derive(dctx, nullptr, &secret_len) == 1 && secret_len == kX25519KeyLength) {
            std::array<unsigned char, kX25519KeyLength> buffer{};
            if (EVP_PKEY_derive(dctx, buffer.data(), &secret_len) == 1) {
                secret.assign(reinterpret_cast<const char*>(buffer.data()), secret_len);
                ok = true;
            }
        }
    }
    if (dctx != nullptr)
        EVP_PKEY_CTX_free(dctx);
    EVP_PKEY_free(peer_pkey);
    EVP_PKEY_free(self_pkey);

    if (!ok) {
        throw SecureAggregationCryptoError(
            "derive_x25519_shared_secret: X25519 shared-secret derivation failed");
    }

    // Work Package P: reject a degenerate all-zero shared secret (the
    // known result of a low-order/small-subgroup peer public key --
    // RFC 7748 Section 6.1). Constant-time-ness is not required here:
    // this check runs on the *output* of the key agreement, not on
    // secret key material being compared against attacker-supplied
    // data, so there is no timing side channel to defend against that
    // is not already implicit in "the shared secret happened to be
    // zero."
    const bool all_zero =
        std::all_of(secret.begin(), secret.end(), [](char byte) { return byte == '\0'; });
    if (all_zero) {
        throw SecureAggregationCryptoError(
            "derive_x25519_shared_secret: derived an all-zero shared secret -- this indicates a "
            "degenerate/low-order peer public key and is rejected, never used as mask-generation "
            "seed material");
    }

    return secret;
}

std::string hkdf_sha256(const std::string& salt,
                        const std::string& ikm,
                        const std::string& info,
                        std::size_t output_length) {
    EVP_PKEY_CTX* kctx = EVP_PKEY_CTX_new_id(EVP_PKEY_HKDF, nullptr);
    if (kctx == nullptr) {
        throw SecureAggregationCryptoError("hkdf_sha256: failed to create HKDF context");
    }
    bool ok = EVP_PKEY_derive_init(kctx) == 1;
    ok = ok && EVP_PKEY_CTX_set_hkdf_md(kctx, EVP_sha256()) == 1;
    ok = ok && EVP_PKEY_CTX_set1_hkdf_salt(kctx,
                                           reinterpret_cast<const unsigned char*>(salt.data()),
                                           static_cast<int>(salt.size())) == 1;
    ok = ok && EVP_PKEY_CTX_set1_hkdf_key(kctx,
                                          reinterpret_cast<const unsigned char*>(ikm.data()),
                                          static_cast<int>(ikm.size())) == 1;
    ok = ok && EVP_PKEY_CTX_add1_hkdf_info(kctx,
                                           reinterpret_cast<const unsigned char*>(info.data()),
                                           static_cast<int>(info.size())) == 1;

    std::string out;
    if (ok) {
        out.resize(output_length);
        std::size_t actual_len = output_length;
        ok =
            EVP_PKEY_derive(kctx, reinterpret_cast<unsigned char*>(out.data()), &actual_len) == 1 &&
            actual_len == output_length;
    }
    EVP_PKEY_CTX_free(kctx);
    if (!ok) {
        throw SecureAggregationCryptoError("hkdf_sha256: HKDF derivation failed");
    }
    return out;
}

std::string derive_purpose_key(const std::string& shared_secret,
                               const std::string& purpose_label,
                               const std::string& canonical_context,
                               std::size_t output_length) {
    // The purpose label and caller-supplied context are concatenated
    // with an explicit NUL separator into HKDF's `info` parameter --
    // never into `salt` (which stays the one fixed, protocol-wide
    // value kHkdfDomainSalt) -- so two different purposes, or the same
    // purpose under two different contexts, always derive
    // cryptographically distinct keys even though they share the same
    // input keying material (the pairwise shared secret).
    std::string info;
    info.reserve(purpose_label.size() + 1 + canonical_context.size());
    info += purpose_label;
    info += '\0';
    info += canonical_context;
    return hkdf_sha256(std::string(kHkdfDomainSalt), shared_secret, info, output_length);
}

std::string chacha20_keystream(const std::string& key,
                               const std::string& nonce,
                               std::uint32_t initial_counter,
                               std::size_t length) {
    if (key.size() != kChaCha20KeyLength) {
        throw SecureAggregationCryptoError("chacha20_keystream: key is not 32 bytes");
    }
    if (nonce.size() != kChaCha20NonceLength) {
        throw SecureAggregationCryptoError("chacha20_keystream: nonce is not 12 bytes");
    }

    // OpenSSL's EVP_chacha20 takes a 16-byte IV: the first 4 bytes are
    // the little-endian block counter, the remaining 12 are the RFC
    // 8439 IETF nonce -- exactly the layout
    // docs/secure-aggregation-cryptographic-provider.md Section 6
    // documents as the reason the IETF variant (not the original
    // 64-bit-nonce construction) was selected for cross-language
    // parity with Python's `cryptography` ChaCha20 implementation.
    std::array<unsigned char, 16> iv{};
    iv[0] = static_cast<unsigned char>(initial_counter & 0xFF);
    iv[1] = static_cast<unsigned char>((initial_counter >> 8) & 0xFF);
    iv[2] = static_cast<unsigned char>((initial_counter >> 16) & 0xFF);
    iv[3] = static_cast<unsigned char>((initial_counter >> 24) & 0xFF);
    std::memcpy(iv.data() + 4, nonce.data(), kChaCha20NonceLength);

    EVP_CIPHER_CTX* ctx = EVP_CIPHER_CTX_new();
    if (ctx == nullptr) {
        throw SecureAggregationCryptoError("chacha20_keystream: failed to create cipher context");
    }
    bool ok = EVP_EncryptInit_ex(ctx,
                                 EVP_chacha20(),
                                 nullptr,
                                 reinterpret_cast<const unsigned char*>(key.data()),
                                 iv.data()) == 1;

    std::string zero_plaintext(length, '\0');
    std::string keystream(length, '\0');
    int out_len = 0;
    ok = ok && EVP_EncryptUpdate(ctx,
                                 reinterpret_cast<unsigned char*>(keystream.data()),
                                 &out_len,
                                 reinterpret_cast<const unsigned char*>(zero_plaintext.data()),
                                 static_cast<int>(length)) == 1;
    ok = ok && static_cast<std::size_t>(out_len) == length;
    EVP_CIPHER_CTX_free(ctx);
    if (!ok) {
        throw SecureAggregationCryptoError(
            "chacha20_keystream: ChaCha20 keystream generation failed");
    }
    return keystream;
}

std::string sha256_digest(const std::string& data) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digest_len = 0;
    EVP_MD_CTX* ctx = EVP_MD_CTX_new();
    bool ok = ctx != nullptr && EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) == 1 &&
              EVP_DigestUpdate(ctx, data.data(), data.size()) == 1 &&
              EVP_DigestFinal_ex(ctx, digest, &digest_len) == 1;
    if (ctx != nullptr)
        EVP_MD_CTX_free(ctx);
    if (!ok || digest_len != kSha256DigestLength) {
        throw SecureAggregationCryptoError("sha256_digest: SHA-256 computation failed");
    }
    return std::string(reinterpret_cast<const char*>(digest), digest_len);
}

std::string sha256_hex(const std::string& data) {
    return hex_encode(sha256_digest(data));
}

std::string compute_cohort_commitment(const std::string& session_id,
                                      const std::string& run_id,
                                      std::uint64_t round_id,
                                      const std::string& model_version,
                                      const std::vector<std::string>& ordered_participant_ids) {
    // Domain-separated, canonical, order-preserving serialization --
    // deliberately not alphabetically-sorted JSON (unlike this
    // codebase's task-configuration-hash convention): the participant
    // *order itself* is part of what is being committed to here (Work
    // Package O), since the pairwise sign rule's correctness depends
    // on every participant observing the identical ordering, so this
    // serialization preserves the caller-supplied list order exactly
    // rather than normalizing it away.
    std::ostringstream out;
    out.write(kCohortCommitmentPrefix, static_cast<std::streamsize>(kCohortCommitmentPrefixLength));
    out << "session_id=" << session_id << '\x1e';
    out << "run_id=" << run_id << '\x1e';
    out << "round_id=" << round_id << '\x1e';
    out << "model_version=" << model_version << '\x1e';
    out << "participant_count=" << ordered_participant_ids.size() << '\x1e';
    for (std::size_t i = 0; i < ordered_participant_ids.size(); ++i) {
        out << "participant[" << i << "]=" << ordered_participant_ids[i] << '\x1e';
    }
    return sha256_hex(out.str());
}

std::string compute_session_configuration_hash(const SecureAggregationSessionConfig& config) {
    // Same canonical-serialization discipline as compute_cohort_commitment
    // above: a fixed field order (declaration order, matching the
    // struct itself -- there is no ambiguity to resolve via
    // alphabetical sorting since every field is named exactly once),
    // a field separator that cannot appear in any legal field value
    // (\x1e, ASCII Record Separator), and a distinct domain-separation
    // prefix so this hash's byte-string can never collide with
    // compute_cohort_commitment's, even for numerically-identical
    // field values.
    std::ostringstream out;
    out.write(kSessionConfigHashPrefix,
              static_cast<std::streamsize>(kSessionConfigHashPrefixLength));
    out << "schema_version=" << config.schema_version << '\x1e';
    out << "protocol_version=" << config.protocol_version << '\x1e';
    out << "provider=" << to_string(config.provider) << '\x1e';
    out << "session_id=" << config.session_id << '\x1e';
    out << "run_id=" << config.run_id << '\x1e';
    out << "round_id=" << config.round_id << '\x1e';
    out << "model_version=" << config.model_version << '\x1e';
    out << "aggregation_algorithm=" << config.aggregation_algorithm << '\x1e';
    out << "cohort_size=" << config.cohort_size << '\x1e';
    out << "minimum_cohort_size=" << config.minimum_cohort_size << '\x1e';
    out << "ordered_participant_count=" << config.ordered_participant_ids.size() << '\x1e';
    for (std::size_t i = 0; i < config.ordered_participant_ids.size(); ++i) {
        out << "ordered_participant[" << i << "]=" << config.ordered_participant_ids[i] << '\x1e';
    }
    out << "tensor_manifest_hash=" << config.tensor_manifest_hash << '\x1e';
    out << "model_manifest_hash=" << config.model_manifest_hash << '\x1e';
    out << "domain_profile=" << config.domain_profile << '\x1e';
    out << "scale_factor=" << config.scale_factor << '\x1e';
    out << "max_absolute_update_bound=" << config.max_absolute_update_bound << '\x1e';
    out << "max_client_weight=" << config.max_client_weight << '\x1e';
    out << "max_aggregate_bound=" << config.max_aggregate_bound << '\x1e';
    out << "mask_generator_profile=" << config.mask_generator_profile << '\x1e';
    out << "key_agreement_profile=" << config.key_agreement_profile << '\x1e';
    out << "key_derivation_profile=" << config.key_derivation_profile << '\x1e';
    out << "session_created_at_unix_s=" << config.session_created_at_unix_s << '\x1e';
    out << "key_advertisement_deadline_unix_s=" << config.key_advertisement_deadline_unix_s
        << '\x1e';
    out << "masked_update_deadline_unix_s=" << config.masked_update_deadline_unix_s << '\x1e';
    out << "session_expiry_unix_s=" << config.session_expiry_unix_s << '\x1e';
    out << "coordinator_signing_key_id=" << config.coordinator_signing_key_id << '\x1e';
    return sha256_hex(out.str());
}

}  // namespace fl::coordinator
