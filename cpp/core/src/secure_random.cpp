#include "fl_core/secure_random.hpp"

#include <algorithm>
#include <cmath>
#include <cstring>

#if defined(_WIN32)
// NOMINMAX / WIN32_LEAN_AND_MEAN keep <windows.h> (pulled in
// transitively by <bcrypt.h>) from polluting the translation unit with
// macros that collide with std::min/max or other identifiers used
// elsewhere in fl_core.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <bcrypt.h>
#pragma comment(lib, "bcrypt.lib")
#else
#include <cerrno>
#include <cstdio>
#endif

namespace fl::core {

std::string to_string(SecureRandomProviderIdentity identity) {
    switch (identity) {
        case SecureRandomProviderIdentity::kDeterministicTestOnly:
            return "deterministic_test_only";
        case SecureRandomProviderIdentity::kOsCsprng:
            return "os_csprng";
        case SecureRandomProviderIdentity::kLibsodium:
            return "libsodium";
    }
    throw std::invalid_argument("unknown SecureRandomProviderIdentity");
}

SecureRandomUnavailableError::SecureRandomUnavailableError(const std::string& what)
    : std::runtime_error(what) {}

double SecureRandomProvider::uniform_01() {
    // 53 bits: the full mantissa precision of an IEEE-754 double, the
    // same bit budget std::generate_canonical<double, 53> targets.
    std::uint64_t raw = 0;
    fill_random_bytes(reinterpret_cast<unsigned char*>(&raw), sizeof(raw));
    const std::uint64_t mantissa_bits = raw >> (64 - 53);
    constexpr double denominator = static_cast<double>(std::uint64_t{1} << 53);
    return static_cast<double>(mantissa_bits) / denominator;
}

double SecureRandomProvider::gaussian_sample(double std_dev) {
    // M_PI is a POSIX/MSVC extension, not standard C++ (requires
    // _USE_MATH_DEFINES before <cmath> on MSVC) -- a local constant
    // avoids that portability trap entirely.
    constexpr double kTwoPi = 6.283185307179586476925286766559;
    // Box-Muller transform over two independent secure uniforms. u1 must
    // be drawn from (0, 1], not [0, 1) — log(0) is undefined — so a
    // uniform_01() result of exactly 0.0 (astronomically unlikely, but
    // not impossible) is resampled rather than silently producing NaN.
    double u1 = 0.0;
    do {
        u1 = uniform_01();
    } while (u1 <= 0.0);
    const double u2 = uniform_01();
    const double radius = std::sqrt(-2.0 * std::log(u1));
    const double angle = kTwoPi * u2;
    return std_dev * radius * std::cos(angle);
}

SecureRandomProviderIdentity OsEntropySecureRandomProvider::identity() const {
    return SecureRandomProviderIdentity::kOsCsprng;
}

#if defined(_WIN32)

void OsEntropySecureRandomProvider::draw_from_os(unsigned char* out, std::size_t count) {
    // BCryptGenRandom with the system preferred RNG algorithm (no
    // BCRYPT_ALG_HANDLE) is the modern, non-deprecated Win32 CSPRNG
    // entry point (CryptGenRandom/wincrypt.h is deprecated). A non-
    // STATUS_SUCCESS return is a hard failure -- never fall back to a
    // weaker source.
    const NTSTATUS status = BCryptGenRandom(
        nullptr, out, static_cast<ULONG>(count), BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    if (status != 0 /* STATUS_SUCCESS */) {
        throw SecureRandomUnavailableError(
            "BCryptGenRandom failed with NTSTATUS " + std::to_string(status));
    }
}

#else

void OsEntropySecureRandomProvider::draw_from_os(unsigned char* out, std::size_t count) {
    // /dev/urandom: portable across every POSIX platform this project
    // targets (Linux/macOS), never blocks after boot-time entropy is
    // available (unlike historical /dev/random behavior), and is what
    // std::random_device itself is commonly backed by on these
    // platforms -- reading it directly here (rather than trusting
    // std::random_device, which the C++ standard does not actually
    // guarantee is non-deterministic) is the whole point of this class.
    std::FILE* source = std::fopen("/dev/urandom", "rb");
    if (source == nullptr) {
        throw SecureRandomUnavailableError(
            "failed to open /dev/urandom: errno " + std::to_string(errno));
    }
    const std::size_t read_count = std::fread(out, 1, count, source);
    std::fclose(source);
    if (read_count != count) {
        throw SecureRandomUnavailableError(
            "short read from /dev/urandom (" + std::to_string(read_count) + " of " +
            std::to_string(count) + " bytes)");
    }
}

#endif

void OsEntropySecureRandomProvider::fill_random_bytes(unsigned char* out, std::size_t count) {
    std::lock_guard<std::mutex> lock(mutex_);
    // Large requests (>= the whole buffer) bypass buffering entirely and
    // draw directly from the OS -- buffering exists to amortize the cost
    // of many *small* requests (uniform_01/gaussian_sample draw 8-16
    // bytes at a time), not to add an extra copy for large ones.
    if (count >= kBufferSize) {
        draw_from_os(out, count);
        return;
    }
    std::size_t filled = 0;
    while (filled < count) {
        if (buffer_cursor_ >= buffer_.size()) {
            // Refill policy: exactly on exhaustion. There is no derived
            // PRNG state to age out here -- every buffered byte is raw
            // OS entropy, consumed exactly once and never reused, so
            // "refill when empty" is the entire policy needed to
            // guarantee no reuse.
            buffer_.assign(kBufferSize, 0);
            draw_from_os(buffer_.data(), buffer_.size());
            buffer_cursor_ = 0;
        }
        const std::size_t available = buffer_.size() - buffer_cursor_;
        const std::size_t chunk = std::min(available, count - filled);
        std::memcpy(out + filled, buffer_.data() + buffer_cursor_, chunk);
        // Zero consumed bytes so a stale buffer never lingers in memory
        // holding entropy that has already been handed out -- a defensive
        // measure against ever accidentally reading buffer_ a second time
        // through a future bug, not something the current logic requires.
        std::memset(buffer_.data() + buffer_cursor_, 0, chunk);
        buffer_cursor_ += chunk;
        filled += chunk;
    }
}

DeterministicSecureRandomProvider::DeterministicSecureRandomProvider(std::uint64_t seed)
    : engine_(seed) {}

SecureRandomProviderIdentity DeterministicSecureRandomProvider::identity() const {
    return SecureRandomProviderIdentity::kDeterministicTestOnly;
}

void DeterministicSecureRandomProvider::fill_random_bytes(unsigned char* out, std::size_t count) {
    // Not thread-safe (single mt19937_64, no locking) -- matches
    // DeterministicNoiseProvider's identical, deliberate, test-only
    // scope in privacy.hpp.
    std::size_t filled = 0;
    while (filled < count) {
        const std::uint64_t word = engine_();
        const std::size_t chunk = std::min(sizeof(word), count - filled);
        std::memcpy(out + filled, &word, chunk);
        filled += chunk;
    }
}

double CryptoSecureNoiseProvider::gaussian_sample(double std_dev) {
    return provider_.gaussian_sample(std_dev);
}

SecureRandomProviderIdentity CryptoSecureNoiseProvider::identity() const {
    return provider_.identity();
}

}  // namespace fl::core
