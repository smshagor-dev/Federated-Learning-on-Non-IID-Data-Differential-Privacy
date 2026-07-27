#pragma once

// Cryptographically-sourced randomness for the Secure Aggregation and
// Cryptographic Protocols category's closure-gate work — see
// docs/secure-aggregation-architecture.md's "Closure-gate:
// cryptographically secure noise" section and
// docs/cryptographic-primitives.md for the full dependency-selection
// context.
//
// Distinct from (and a lower-level primitive than) NoiseProvider in
// privacy.hpp: NoiseProvider is specifically the abstraction runtime DP
// noise is drawn through (SecureNoiseProvider seeds one mt19937_64 once
// from std::random_device and reuses it for every element, which the
// existing privacy.hpp header already documents as *not* a CSPRNG).
// SecureRandomProvider instead draws fresh OS-CSPRNG bytes on every
// single call — no PRNG state is ever seeded once and reused — which is
// what "cryptographically secure" actually requires. The libsodium-
// backed provider that will eventually supersede the OS-entropy-only
// implementation here is a *documented, not-yet-linked* upgrade path
// (see cryptographic-primitives.md); this header's OsEntropySecureRandomProvider
// is real and tested today, not a placeholder.

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

#include "fl_core/privacy.hpp"

namespace fl::core {

// Which entropy source actually produced a given draw — recorded so
// privacy metadata can state provider identity truthfully (see the
// closure-gate requirement "provider identity recorded in privacy
// metadata") rather than always implying the strongest available
// source was used. Matches the Secure Transport and Worker Identity
// Hardening slice's provider-interface naming (OS_CSPRNG / LIBSODIUM /
// DETERMINISTIC_TEST). kLibsodium is reserved for the libsodium-backed
// provider documented as a not-yet-linked upgrade path in
// docs/cryptographic-primitives.md — no implementation returns it today;
// it exists so the enum doesn't need another breaking change once one
// does.
enum class SecureRandomProviderIdentity {
    kDeterministicTestOnly,
    kOsCsprng,
    kLibsodium,
};

[[nodiscard]] std::string to_string(SecureRandomProviderIdentity identity);

// Thrown when the underlying OS entropy source itself fails (e.g.
// BCryptGenRandom returns a non-success status, or /dev/urandom cannot
// be opened/read). Callers must never catch this and silently fall back
// to a weaker source — a caller that cannot tolerate this exception
// should not have selected a secure-random-requiring privacy mode in
// the first place. See the closure-gate requirement "failure on
// entropy-provider failure".
class SecureRandomUnavailableError : public std::runtime_error {
  public:
    explicit SecureRandomUnavailableError(const std::string& what);
};

// Abstract base: fills `out` with `count` cryptographically-sourced
// random bytes, then exposes uniform/Gaussian convenience draws built
// directly on top of those bytes (not std::normal_distribution, which
// would require exposing a full URBG bit-generator interface and would
// obscure the "always fresh entropy, never a seeded-once PRNG"
// guarantee this class exists to make explicit).
class SecureRandomProvider {
  public:
    virtual ~SecureRandomProvider() = default;

    [[nodiscard]] virtual SecureRandomProviderIdentity identity() const = 0;

    // Thread-safe in every concrete implementation below. Throws
    // SecureRandomUnavailableError on entropy-source failure.
    virtual void fill_random_bytes(unsigned char* out, std::size_t count) = 0;

    // A uniform double drawn from [0, 1), built from 53 bits of secure
    // entropy (the full mantissa precision of a double), never logged.
    [[nodiscard]] double uniform_01();

    // A single N(0, std_dev^2) sample via the Box-Muller transform over
    // two independent secure uniform draws.
    [[nodiscard]] double gaussian_sample(double std_dev);
};

// The runtime default: bytes are always genuine OS-CSPRNG output
// (BCryptGenRandom on Windows, /dev/urandom on POSIX) — never a PRNG
// seeded once and reused — but drawn through an internal buffer rather
// than one raw OS call per request, for throughput. This is
// deliberately *not* a derived stream cipher (ChaCha20 or otherwise):
// no vetted CSPRNG-stream library is linked yet (see
// docs/cryptographic-primitives.md) and this project does not hand-roll
// one. Buffering is instead just batching real OS-entropy reads —
// every byte ever returned is still raw, unreduced entropy, only fetched
// `kBufferSize` bytes at a time instead of one small syscall per
// caller. Documented refill policy: refill exactly when the buffer is
// exhausted (no time- or use-count-based rotation beyond that, since
// there is no derived state to age out — bytes are consumed exactly
// once and never reused). Thread-safe via a mutex guarding both the
// buffer and the cursor. Fails closed: a refill failure throws
// SecureRandomUnavailableError, never silently serving fewer bytes or
// falling back to a weaker source.
class OsEntropySecureRandomProvider final : public SecureRandomProvider {
  public:
    OsEntropySecureRandomProvider() = default;

    [[nodiscard]] SecureRandomProviderIdentity identity() const override;
    void fill_random_bytes(unsigned char* out, std::size_t count) override;

  private:
    // Raw, unbuffered OS draw -- the actual syscall/API boundary,
    // called only to (re)fill the buffer below.
    static void draw_from_os(unsigned char* out, std::size_t count);

    static constexpr std::size_t kBufferSize = 4096;

    std::mutex mutex_;
    std::vector<unsigned char> buffer_{};
    std::size_t buffer_cursor_ = 0;
};

// A NoiseProvider (see privacy.hpp) backed by OsEntropySecureRandomProvider
// — the concrete class RunInstance constructs for user-level DP's central
// Gaussian noise and adaptive clipping's privatized count, replacing the
// non-CSPRNG SecureNoiseProvider for that live path. Kept as a thin
// adapter (not a reimplementation) so NoiseProvider's existing interface
// and every call site that already depends on it (add_central_gaussian_noise,
// AdaptiveClipController) are completely unchanged.
class CryptoSecureNoiseProvider final : public NoiseProvider {
  public:
    CryptoSecureNoiseProvider() = default;

    [[nodiscard]] double gaussian_sample(double std_dev) override;

    // For privacy metadata -- see the closure-gate requirement "provider
    // identity recorded in privacy metadata".
    [[nodiscard]] SecureRandomProviderIdentity identity() const;

  private:
    OsEntropySecureRandomProvider provider_;
};

// Deterministic, seeded — test-only, exactly like privacy.hpp's
// DeterministicNoiseProvider. Never construct this with an experiment's
// ordinary seed directly; callers must domain-separate. Not a secure
// source at all (deliberately: tests need reproducibility, not
// security) — identity() truthfully reports kDeterministicTestOnly so
// this can never be mistaken for the real provider in recorded
// metadata.
class DeterministicSecureRandomProvider final : public SecureRandomProvider {
  public:
    explicit DeterministicSecureRandomProvider(std::uint64_t seed);

    [[nodiscard]] SecureRandomProviderIdentity identity() const override;
    void fill_random_bytes(unsigned char* out, std::size_t count) override;

  private:
    std::mt19937_64 engine_;
};

}  // namespace fl::core
