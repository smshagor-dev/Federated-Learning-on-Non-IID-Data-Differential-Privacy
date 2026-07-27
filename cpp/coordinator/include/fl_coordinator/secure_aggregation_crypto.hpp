#pragma once

// Cryptographic primitive wrappers for the Secure Aggregation Protocol
// Foundation and No-Dropout Masked-Sum Core slice -- Work Packages G
// (cohort commitment hashing only), O, P, Q, S. See
// docs/secure-aggregation-cryptographic-provider.md for the vetted-
// provider decision this file implements (OpenSSL EVP: X25519, HKDF-
// SHA-256, ChaCha20 IETF/RFC 8439, SHA-256) and
// docs/secure-aggregation-protocol-foundation.md for why this lives in
// the gRPC-gated build only.
//
// Deliberately separate from secure_aggregation_encoding.hpp,
// secure_aggregation_mask.hpp, and secure_aggregation_session.hpp
// (all non-gRPC-gated, pure math/state, unit-testable on this Windows/
// MSVC development machine without Docker): every function here needs
// OpenSSL, which this repository's CMakeLists.txt only finds inside
// the `if(Protobuf_FOUND AND gRPC_FOUND)` branch (gRPC::grpc++ already
// links OpenSSL transitively for its own TLS implementation -- see
// coordinator_signing_identity.hpp's identical header comment for the
// established precedent this file follows). Consequently this module
// only builds and is only unit-tested in Docker/CI, not on this local
// machine -- see docs/secure-aggregation-protocol-foundation.md's
// honest completion accounting for what "Tier 1, gRPC-gated" means in
// practice for this slice.
//
// This module is deliberately protobuf-free and does not link the
// fl_coordinator library: every function takes/returns plain
// std::string byte buffers (never a wire message), so it can be
// unit-tested in complete isolation (see
// coordinator/tests/secure_aggregation_crypto_test.cpp and the
// fl_secure_aggregation_crypto_tests CMake target, mirroring
// fl_peer_identity_tests' identical minimal-link pattern). Callers
// wire it up to the pure-math modules and the (Tier 2, not yet
// written) wire contracts.

#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace fl::coordinator {

class SecureAggregationCryptoError : public std::runtime_error {
  public:
    explicit SecureAggregationCryptoError(const std::string& what);
};

constexpr std::size_t kX25519KeyLength = 32;
constexpr std::size_t kSha256DigestLength = 32;
constexpr std::size_t kChaCha20KeyLength = 32;
// RFC 8439 IETF variant: 12-byte nonce + 32-bit little-endian block
// counter (the counter is passed separately, not part of this length)
// -- see docs/secure-aggregation-cryptographic-provider.md Section 6
// for why the IETF variant (not the original 64-bit-nonce Bernstein
// construction) was selected for OpenSSL/`cryptography` cross-language
// parity.
constexpr std::size_t kChaCha20NonceLength = 12;

// Lowercase hex, matching every other hex encoding already used in
// this codebase (coordinator_signing_identity.cpp,
// coordinator_task_signing.cpp). Provided here as a public utility
// (rather than each caller re-deriving its own copy) because both
// fixture-loading test code and any future wire-serialization code
// need the identical encoding.
[[nodiscard]] std::string hex_encode(const std::string& raw);
// Throws SecureAggregationCryptoError on malformed input (odd length
// or a non-hex character) -- never silently truncates or skips bytes.
[[nodiscard]] std::string hex_decode(const std::string& hex);

struct X25519KeyPair {
    std::string private_key_raw;  // 32 bytes
    std::string public_key_raw;   // 32 bytes
};

// Real OpenSSL keygen (EVP_PKEY_X25519), never a hand-rolled/
// deterministic-for-tests generator -- same discipline as
// generate_coordinator_signing_identity() for Ed25519.
[[nodiscard]] X25519KeyPair generate_x25519_keypair();

// Work Package P: ephemeral X25519 shared-secret derivation. Throws
// SecureAggregationCryptoError if either key is not exactly 32 bytes,
// or if the derived shared secret is all-zero -- the known degenerate
// result of a low-order/small-subgroup peer public key, which must be
// rejected rather than silently used as a mask-generation seed (a
// well-known X25519 pitfall; RFC 7748 Section 6.1 explicitly calls out
// checking for this).
[[nodiscard]] std::string derive_x25519_shared_secret(const std::string& self_private_key_raw,
                                                        const std::string& peer_public_key_raw);

// RFC 5869 extract-then-expand HKDF-SHA-256 (OpenSSL's default HKDF
// mode). `output_length` may exceed a single SHA-256 block; OpenSSL
// performs the multi-block expand internally.
[[nodiscard]] std::string hkdf_sha256(const std::string& salt, const std::string& ikm, const std::string& info,
                                       std::size_t output_length);

// Fixed, public domain-separation salt for every purpose-specific key
// this protocol derives -- not secret (HKDF's salt need not be secret;
// RFC 5869 Section 3.1), but fixed and versioned so a future protocol
// revision with different derivation semantics gets its own salt
// rather than silently reusing this one's derived key space.
inline constexpr char kHkdfDomainSalt[] = "FL_PLATFORM_SECURE_AGGREGATION_HKDF_SALT_V1";

// Work Package Q: distinct keys per purpose. Each label is mixed into
// HKDF's `info` parameter (never the `salt`, which stays fixed above)
// alongside a caller-supplied canonical context string (typically
// session/round/model-version/participant-pair-ordering material) --
// see derive_purpose_key's own doc comment for why the context is a
// caller responsibility, not computed inside this crypto module.
inline constexpr char kHkdfPurposeTensorMaskStream[] = "tensor_mask_stream";
inline constexpr char kHkdfPurposeWeightMaskStream[] = "weight_mask_stream";

// Derives a purpose-specific key from a pairwise shared secret.
// `canonical_context` is supplied by the caller (not computed here)
// deliberately: this module is protobuf-free and does not link
// secure_aggregation_mask.hpp's participant-ordering logic, so the
// canonical, sign-rule-consistent ordering of a participant pair (and
// any session/round/model-version binding) is the caller's
// responsibility to construct once, in one place, using
// participant_sorts_before -- duplicating that ordering logic inside
// this crypto module would risk the two copies silently drifting apart
// over time.
[[nodiscard]] std::string derive_purpose_key(const std::string& shared_secret, const std::string& purpose_label,
                                              const std::string& canonical_context,
                                              std::size_t output_length = kChaCha20KeyLength);

// RFC 8439 IETF ChaCha20 keystream (OpenSSL EVP_chacha20, encrypting
// an all-zero plaintext of the requested length -- the standard way to
// obtain a raw keystream from a stream-cipher primitive). `key` must
// be exactly kChaCha20KeyLength bytes and `nonce` exactly
// kChaCha20NonceLength bytes; `initial_counter` is the 32-bit block
// counter OpenSSL encodes as the first 4 bytes of its internal 16-byte
// IV (little-endian), matching RFC 8439's counter placement exactly.
[[nodiscard]] std::string chacha20_keystream(const std::string& key, const std::string& nonce,
                                              std::uint32_t initial_counter, std::size_t length);

[[nodiscard]] std::string sha256_digest(const std::string& data);  // raw kSha256DigestLength bytes
[[nodiscard]] std::string sha256_hex(const std::string& data);

// Work Package O: a canonical, domain-separated hash over exactly the
// fields that define a frozen cohort's identity -- session_id, run_id,
// round_id, model_version, and the ordered participant-ID list (order
// matters and is part of the commitment, since the pairwise sign rule
// itself depends on a consistent ordering -- see
// secure_aggregation_mask.hpp). Every worker must independently
// recompute this same commitment over the coordinator-signed roster it
// receives and verify it matches before deriving any pairwise mask --
// this is the binding that makes "the roster I'm masking against" and
// "the roster the coordinator actually signed" the same roster,
// cryptographically, not just by trusting the RPC response.
[[nodiscard]] std::string compute_cohort_commitment(const std::string& session_id, const std::string& run_id,
                                                      std::uint64_t round_id, const std::string& model_version,
                                                      const std::vector<std::string>& ordered_participant_ids);

}  // namespace fl::coordinator
