#include "fl_core/secure_random.hpp"

#include <array>
#include <cmath>
#include <cstring>
#include <functional>
#include <iostream>
#include <numeric>
#include <string>
#include <vector>

namespace {

int g_failures = 0;

void check(bool condition, const std::string& label) {
    if (!condition) {
        std::cerr << "FAILED: " << label << "\n";
        ++g_failures;
    }
}

void expect_throw(const std::function<void()>& action, const std::string& label) {
    bool threw = false;
    try {
        action();
    } catch (const std::exception&) {
        threw = true;
    }
    check(threw, label + " (expected an exception, none was thrown)");
}

void run_identity_tests() {
    using fl::core::DeterministicSecureRandomProvider;
    using fl::core::OsEntropySecureRandomProvider;
    using fl::core::SecureRandomProviderIdentity;
    using fl::core::to_string;

    OsEntropySecureRandomProvider os_provider;
    check(os_provider.identity() == SecureRandomProviderIdentity::kOsCsprng,
          "OsEntropySecureRandomProvider reports kOsCsprng");
    check(to_string(SecureRandomProviderIdentity::kOsCsprng) == "os_csprng",
          "kOsCsprng stringifies to 'os_csprng'");

    DeterministicSecureRandomProvider det_provider(42);
    check(det_provider.identity() == SecureRandomProviderIdentity::kDeterministicTestOnly,
          "DeterministicSecureRandomProvider reports kDeterministicTestOnly");
    check(to_string(SecureRandomProviderIdentity::kDeterministicTestOnly) ==
              "deterministic_test_only",
          "kDeterministicTestOnly stringifies to 'deterministic_test_only'");

    // Provider identity must be unambiguous -- a caller building privacy
    // metadata from this string must never be able to mistake a test
    // provider for the real one, or vice versa.
    check(to_string(SecureRandomProviderIdentity::kOsCsprng) !=
              to_string(SecureRandomProviderIdentity::kDeterministicTestOnly),
          "os_csprng and deterministic_test_only identities are distinct strings");
}

void run_determinism_tests() {
    using fl::core::DeterministicSecureRandomProvider;

    // Same seed -> identical byte stream. This is what makes the
    // deterministic provider usable in reproducible tests at all.
    DeterministicSecureRandomProvider a(7);
    DeterministicSecureRandomProvider b(7);
    unsigned char buf_a[32];
    unsigned char buf_b[32];
    a.fill_random_bytes(buf_a, sizeof(buf_a));
    b.fill_random_bytes(buf_b, sizeof(buf_b));
    check(std::memcmp(buf_a, buf_b, sizeof(buf_a)) == 0,
          "same seed produces identical byte streams");

    // Different seeds -> (overwhelmingly likely) different streams.
    DeterministicSecureRandomProvider c(8);
    unsigned char buf_c[32];
    c.fill_random_bytes(buf_c, sizeof(buf_c));
    check(std::memcmp(buf_a, buf_c, sizeof(buf_a)) != 0,
          "different seeds produce different byte streams");
}

void run_os_entropy_variability_tests() {
    using fl::core::OsEntropySecureRandomProvider;

    OsEntropySecureRandomProvider provider;
    unsigned char first[32];
    unsigned char second[32];
    provider.fill_random_bytes(first, sizeof(first));
    provider.fill_random_bytes(second, sizeof(second));
    // Two independent draws from a real entropy source must not be
    // identical (astronomically unlikely by chance) -- this is a
    // regression guard against ever accidentally caching/reusing a
    // single draw across calls, which would silently defeat the "fresh
    // entropy every call" guarantee this class exists to provide.
    check(std::memcmp(first, second, sizeof(first)) != 0,
          "two independent OS-entropy draws are not identical");
    // All-zero output would indicate a broken (but "succeeding") call
    // to the OS API -- a real, if crude, sanity check that bytes were
    // actually written.
    bool all_zero = true;
    for (unsigned char byte : first) {
        if (byte != 0) {
            all_zero = false;
            break;
        }
    }
    check(!all_zero, "OS entropy draw is not all-zero");
}

void run_uniform_distribution_tests() {
    using fl::core::DeterministicSecureRandomProvider;

    DeterministicSecureRandomProvider provider(123);
    constexpr int kSamples = 20000;
    double sum = 0.0;
    double sum_squares = 0.0;
    double min_value = 1.0;
    double max_value = 0.0;
    for (int i = 0; i < kSamples; ++i) {
        const double value = provider.uniform_01();
        check(value >= 0.0 && value < 1.0, "uniform_01() stays within [0, 1)");
        sum += value;
        sum_squares += value * value;
        min_value = std::min(min_value, value);
        max_value = std::max(max_value, value);
    }
    const double mean = sum / kSamples;
    const double variance = sum_squares / kSamples - mean * mean;
    // Uniform(0,1) has mean 0.5, variance 1/12 ~= 0.0833 -- generous
    // tolerances since this is a statistical, not exact, property.
    check(std::abs(mean - 0.5) < 0.02, "uniform_01() sample mean is close to 0.5");
    check(std::abs(variance - (1.0 / 12.0)) < 0.01,
          "uniform_01() sample variance is close to 1/12");
    check(min_value < 0.05, "uniform_01() samples reach near the low end of [0, 1)");
    check(max_value > 0.95, "uniform_01() samples reach near the high end of [0, 1)");
}

void run_gaussian_distribution_tests() {
    using fl::core::DeterministicSecureRandomProvider;

    DeterministicSecureRandomProvider provider(456);
    constexpr int kSamples = 20000;
    constexpr double kStdDev = 2.5;
    std::vector<double> samples;
    samples.reserve(kSamples);
    for (int i = 0; i < kSamples; ++i) {
        samples.push_back(provider.gaussian_sample(kStdDev));
    }
    const double mean = std::accumulate(samples.begin(), samples.end(), 0.0) / samples.size();
    double sum_squared_deviation = 0.0;
    for (const double value : samples) {
        sum_squared_deviation += (value - mean) * (value - mean);
    }
    const double sample_std_dev = std::sqrt(sum_squared_deviation / samples.size());
    check(std::abs(mean) < 0.1, "gaussian_sample() mean is close to 0");
    check(std::abs(sample_std_dev - kStdDev) < 0.1,
          "gaussian_sample() std dev matches the configured std_dev");

    // std_dev=0 must always produce exactly 0, never NaN/inf from a
    // degenerate radius computation.
    const double zero_std_sample = provider.gaussian_sample(0.0);
    check(zero_std_sample == 0.0, "gaussian_sample(0.0) is exactly 0");
}

void run_fill_random_bytes_size_tests() {
    using fl::core::DeterministicSecureRandomProvider;
    using fl::core::OsEntropySecureRandomProvider;

    // Sizes not a multiple of the underlying engine's word size must
    // still fill exactly `count` bytes, not over/under-write.
    DeterministicSecureRandomProvider det(1);
    unsigned char buf[7] = {1, 2, 3, 4, 5, 6, 7};
    unsigned char sentinel_before = 0xAB;
    unsigned char sentinel_after = 0xCD;
    struct Guarded {
        unsigned char before;
        unsigned char data[7];
        unsigned char after;
    } guarded{sentinel_before, {0}, sentinel_after};
    det.fill_random_bytes(guarded.data, sizeof(guarded.data));
    check(guarded.before == sentinel_before && guarded.after == sentinel_after,
          "fill_random_bytes with a non-word-aligned size does not overrun the buffer");
    (void)buf;

    OsEntropySecureRandomProvider os_provider;
    unsigned char odd_size[5];
    os_provider.fill_random_bytes(odd_size, sizeof(odd_size));  // must not throw/crash
    check(true, "OS-entropy fill_random_bytes handles a non-power-of-two size");
}

void run_buffer_refill_tests() {
    using fl::core::OsEntropySecureRandomProvider;

    // The internal buffer is documented as 4096 bytes; draw enough
    // small (16-byte) requests to force several refills and confirm no
    // two requests ever come back identical -- a real regression guard
    // against a refill bug that could re-serve stale buffer content
    // (e.g. an off-by-one in the cursor that stops advancing).
    OsEntropySecureRandomProvider provider;
    constexpr int kRequests = 2000;  // 2000 * 16 = 32000 bytes, ~8 refills
    std::vector<std::array<unsigned char, 16>> draws;
    draws.reserve(kRequests);
    for (int i = 0; i < kRequests; ++i) {
        std::array<unsigned char, 16> buf{};
        provider.fill_random_bytes(buf.data(), buf.size());
        draws.push_back(buf);
    }
    bool any_duplicate = false;
    for (std::size_t i = 0; i < draws.size() && !any_duplicate; ++i) {
        for (std::size_t j = i + 1; j < draws.size(); ++j) {
            if (draws[i] == draws[j]) {
                any_duplicate = true;
                break;
            }
        }
    }
    check(!any_duplicate,
          "2000 buffered 16-byte draws across multiple buffer refills are all distinct");

    // A single request larger than the buffer must still return exactly
    // that many real bytes (the bypass-buffering path).
    std::vector<unsigned char> large(10000, 0);
    provider.fill_random_bytes(large.data(), large.size());
    bool all_zero = true;
    for (unsigned char byte : large) {
        if (byte != 0) {
            all_zero = false;
            break;
        }
    }
    check(!all_zero, "a request larger than the internal buffer still returns real entropy");
}

void run_crypto_secure_noise_provider_tests() {
    using fl::core::CryptoSecureNoiseProvider;
    using fl::core::SecureRandomProviderIdentity;

    CryptoSecureNoiseProvider provider;
    check(provider.identity() == SecureRandomProviderIdentity::kOsCsprng,
          "CryptoSecureNoiseProvider reports kOsCsprng identity");

    // Must satisfy the NoiseProvider interface (privacy.hpp) so it is a
    // drop-in replacement at every existing SecureNoiseProvider call
    // site (add_central_gaussian_noise, AdaptiveClipController).
    fl::core::NoiseProvider& as_noise_provider = provider;
    constexpr int kSamples = 5000;
    double sum = 0.0;
    for (int i = 0; i < kSamples; ++i) {
        sum += as_noise_provider.gaussian_sample(3.0);
    }
    const double mean = sum / kSamples;
    check(std::abs(mean) < 0.3,
          "CryptoSecureNoiseProvider used as a NoiseProvider produces a zero-centered "
          "Gaussian stream");
}

// Secure User-Level DP Operations, Observability, and Release Evidence
// slice, Work Area P: a bounded statistical SMOKE test against the real
// production CSPRNG-backed provider -- distinct from the deterministic-
// provider exactness tests elsewhere in this file/suite, which remain
// unchanged and are what every exact-value assertion in this codebase
// continues to rely on. This is explicitly NOT randomness certification,
// a formal Gaussianity proof, a cryptographic audit, or a NIST
// certification -- it is a bounded-sample sanity check (non-determinism,
// mean near 0, variance near the configured std_dev^2, no accidentally
// identical draws) with every parameter this slice's own documentation
// requirement asks for printed to stdout, captured verbatim into
// docs/secure-user-level-dp-noise-validation.md by the operations report
// (Work Area Y) rather than re-derived by hand.
void run_bounded_statistical_noise_smoke_test() {
    using fl::core::CryptoSecureNoiseProvider;

    CryptoSecureNoiseProvider provider;
    constexpr int kSamples = 20000;
    constexpr double kStdDev = 1.75;
    std::vector<double> samples;
    samples.reserve(kSamples);
    for (int i = 0; i < kSamples; ++i) {
        samples.push_back(provider.gaussian_sample(kStdDev));
    }

    const double mean = std::accumulate(samples.begin(), samples.end(), 0.0) / samples.size();
    double sum_squared_deviation = 0.0;
    for (const double value : samples) {
        sum_squared_deviation += (value - mean) * (value - mean);
    }
    const double variance = sum_squared_deviation / samples.size();
    const double expected_variance = kStdDev * kStdDev;

    // Statistical tolerances, not exact equality -- generous enough that
    // this test is not flaky against a real (non-seeded, therefore
    // sample-to-sample varying) CSPRNG stream, tight enough to catch a
    // genuinely broken generator (e.g. one that silently always returns
    // 0, or one whose scale is off by a large factor).
    constexpr double kMeanTolerance = 0.1;
    constexpr double kVarianceTolerance = 0.15;  // relative

    check(std::abs(mean) < kMeanTolerance, "OS-CSPRNG Gaussian smoke: sample mean is near 0");
    check(std::abs(variance - expected_variance) / expected_variance < kVarianceTolerance,
          "OS-CSPRNG Gaussian smoke: sample variance is near the configured std_dev^2");

    // Non-determinism: two independent draw sequences from the real
    // CSPRNG-backed provider must not collide sample-for-sample (a
    // deterministic/seeded provider would be a real, serious regression
    // for the production noise path).
    CryptoSecureNoiseProvider provider_b;
    bool any_pairwise_equal = false;
    for (int i = 0; i < 100; ++i) {
        if (samples[static_cast<std::size_t>(i)] == provider_b.gaussian_sample(kStdDev)) {
            any_pairwise_equal = true;
            break;
        }
    }
    check(!any_pairwise_equal,
          "OS-CSPRNG Gaussian smoke: two independent provider instances do not produce "
          "coincidentally identical draws");

    // No accidental run of identical consecutive elements (would indicate
    // a stuck/cached generator, not real fresh entropy per call).
    bool any_consecutive_duplicate = false;
    for (std::size_t i = 1; i < samples.size(); ++i) {
        if (samples[i] == samples[i - 1]) {
            any_consecutive_duplicate = true;
            break;
        }
    }
    check(!any_consecutive_duplicate,
          "OS-CSPRNG Gaussian smoke: no accidentally identical consecutive draws");

    // Work Area P's explicit documentation requirement: draw count,
    // expected/observed mean/variance, tolerance, environment, provider,
    // build type -- printed here (not silently discarded) so a fresh run
    // of this exact test binary produces the evidence the operations
    // report cites, rather than a re-typed/hand-derived number.
    std::cout << "bounded statistical noise smoke test report:\n"
              << "  provider=CryptoSecureNoiseProvider (OS-CSPRNG-backed, non-deterministic)\n"
              << "  draw_count=" << kSamples << "\n"
              << "  configured_std_dev=" << kStdDev << "\n"
              << "  expected_mean=0.0 observed_mean=" << mean << " tolerance=+/-" << kMeanTolerance
              << "\n"
              << "  expected_variance=" << expected_variance << " observed_variance=" << variance
              << " relative_tolerance=" << kVarianceTolerance << "\n"
#if defined(NDEBUG)
              << "  build_type=Release\n"
#else
              << "  build_type=Debug\n"
#endif
              << "  scope=bounded statistical smoke test, NOT randomness certification, NOT a "
                 "formal Gaussianity proof, NOT a cryptographic audit, NOT a NIST certification\n";
}

}  // namespace

int main() {
    run_identity_tests();
    run_determinism_tests();
    run_os_entropy_variability_tests();
    run_uniform_distribution_tests();
    run_gaussian_distribution_tests();
    run_fill_random_bytes_size_tests();
    run_buffer_refill_tests();
    run_crypto_secure_noise_provider_tests();
    run_bounded_statistical_noise_smoke_test();
    if (g_failures == 0) {
        std::cout << "all secure random tests passed\n";
    }
    return g_failures == 0 ? 0 : 1;
}
