#include "fl_core/privacy.hpp"

#include <algorithm>
#include <cmath>
#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
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

fl::core::TensorCollection make_delta(const std::vector<double>& weight_values,
                                      const std::vector<double>* head_values = nullptr) {
    fl::core::TensorCollection collection;
    collection.insert(fl::core::TensorBuffer(
        fl::core::TensorDescriptor{.name = "weight",
                                   .shape = {weight_values.size()},
                                   .dtype = fl::core::DType::kFloat32},
        weight_values));
    if (head_values != nullptr) {
        collection.insert(fl::core::TensorBuffer(
            fl::core::TensorDescriptor{.name = "personalized_head",
                                       .shape = {head_values->size()},
                                       .dtype = fl::core::DType::kFloat32},
            *head_values));
    }
    return collection;
}

double l2_norm(const std::vector<double>& values) {
    double sum_squares = 0.0;
    for (const double value : values) {
        sum_squares += value * value;
    }
    return std::sqrt(sum_squares);
}

void run_clipping_tests() {
    using fl::core::ClippingConfig;
    using fl::core::clip_client_delta;

    // norm = 5.0 > clip_bound = 1.0 -> scaled down to exactly norm 1.0.
    {
        auto delta = make_delta({3.0, 4.0});  // ||.||_2 = 5.0
        auto clipped = clip_client_delta(delta, ClippingConfig{.clip_bound = 1.0}, {});
        const auto& values = clipped.at("weight").values();
        check(std::abs(l2_norm(values) - 1.0) < 1e-9, "clipped norm equals clip_bound exactly");
        check(std::abs(values[0] - 0.6) < 1e-9, "clipped value scaled proportionally (x)");
        check(std::abs(values[1] - 0.8) < 1e-9, "clipped value scaled proportionally (y)");
    }

    // norm = 5.0 < clip_bound = 10.0 -> passed through unscaled (min(1, ...) clamps at 1).
    {
        auto delta = make_delta({3.0, 4.0});
        auto clipped = clip_client_delta(delta, ClippingConfig{.clip_bound = 10.0}, {});
        const auto& values = clipped.at("weight").values();
        check(std::abs(values[0] - 3.0) < 1e-9, "unclipped when norm < clip_bound (x)");
        check(std::abs(values[1] - 4.0) < 1e-9, "unclipped when norm < clip_bound (y)");
    }

    // Local-head exclusion: personalized tensor passes through even when
    // the shared tensor is clipped.
    {
        std::vector<double> head = {100.0, 200.0};
        auto delta = make_delta({3.0, 4.0}, &head);
        auto clipped = clip_client_delta(
            delta, ClippingConfig{.clip_bound = 1.0}, {"weight"});  // only "weight" is shared
        check(std::abs(l2_norm(clipped.at("weight").values()) - 1.0) < 1e-9,
              "shared tensor is clipped when a manifest is declared");
        check(clipped.at("personalized_head").values()[0] == 100.0 &&
                  clipped.at("personalized_head").values()[1] == 200.0,
              "personalized_head tensor is passed through completely unmodified");
    }

    // Global multi-tensor norm: two shared tensors contribute to ONE norm,
    // not clipped independently.
    {
        fl::core::TensorCollection delta;
        delta.insert(fl::core::TensorBuffer(
            fl::core::TensorDescriptor{.name = "a", .shape = {1}, .dtype = fl::core::DType::kFloat32},
            {3.0}));
        delta.insert(fl::core::TensorBuffer(
            fl::core::TensorDescriptor{.name = "b", .shape = {1}, .dtype = fl::core::DType::kFloat32},
            {4.0}));
        // Global norm = sqrt(3^2+4^2) = 5.0, clip_bound = 5.0 -> scale = 1 (no clip).
        auto clipped = clip_client_delta(delta, fl::core::ClippingConfig{.clip_bound = 5.0}, {});
        check(std::abs(clipped.at("a").values()[0] - 3.0) < 1e-9,
              "global norm across tensors: 'a' unclipped at exactly the bound");
        check(std::abs(clipped.at("b").values()[0] - 4.0) < 1e-9,
              "global norm across tensors: 'b' unclipped at exactly the bound");
    }

    // NaN/Inf rejection. TensorBuffer's own constructor already validates
    // finiteness (see tensor.cpp's TensorBuffer::validate(), called from
    // the constructor) — so a NaN/Inf value is actually rejected at
    // TensorCollection-construction time, before clip_client_delta ever
    // sees it. clip_client_delta's own NaN/Inf check is defense-in-depth
    // for that invariant, not the primary enforcement point. Both throw
    // sites must be inside the lambda (not before it), since either one
    // throwing outside expect_throw's try/catch would escape uncaught.
    expect_throw(
        [&]() {
            auto delta = make_delta({std::numeric_limits<double>::quiet_NaN(), 1.0});
            (void)clip_client_delta(delta, fl::core::ClippingConfig{.clip_bound = 1.0}, {});
        },
        "NaN in client delta is rejected");
    expect_throw(
        [&]() {
            auto delta = make_delta({std::numeric_limits<double>::infinity(), 1.0});
            (void)clip_client_delta(delta, fl::core::ClippingConfig{.clip_bound = 1.0}, {});
        },
        "Inf in client delta is rejected");

    // Invalid config.
    expect_throw(
        [&]() {
            (void)clip_client_delta(make_delta({1.0}), fl::core::ClippingConfig{.clip_bound = 0.0}, {});
        },
        "non-positive clip_bound is rejected");
}

void run_noise_tests() {
    using fl::core::DeterministicNoiseProvider;
    using fl::core::add_central_gaussian_noise;

    // Deterministic reproducibility: same seed -> identical noise.
    {
        DeterministicNoiseProvider provider_a(42);
        DeterministicNoiseProvider provider_b(42);
        auto delta = make_delta({0.0, 0.0, 0.0});
        auto noised_a = add_central_gaussian_noise(delta, 1.0, provider_a);
        auto noised_b = add_central_gaussian_noise(delta, 1.0, provider_b);
        check(noised_a.at("weight").values() == noised_b.at("weight").values(),
              "same seed produces identical noise (deterministic/research mode)");
    }

    // Different seeds -> different noise.
    {
        DeterministicNoiseProvider provider_a(1);
        DeterministicNoiseProvider provider_b(2);
        auto delta = make_delta({0.0, 0.0, 0.0});
        auto noised_a = add_central_gaussian_noise(delta, 1.0, provider_a);
        auto noised_b = add_central_gaussian_noise(delta, 1.0, provider_b);
        check(noised_a.at("weight").values() != noised_b.at("weight").values(),
              "different seeds produce different noise");
    }

    // noise_std = 0 is a genuine no-op (used by non-private paths that
    // still flow through this function).
    {
        DeterministicNoiseProvider provider(1);
        auto delta = make_delta({1.0, 2.0, 3.0});
        auto noised = add_central_gaussian_noise(delta, 0.0, provider);
        check(noised.at("weight").values() == delta.at("weight").values(),
              "noise_std=0 leaves values unchanged");
    }

    // Statistical sanity: mean ~ 0, std ~ noise_std over many samples.
    {
        DeterministicNoiseProvider provider(7);
        const double noise_std = 2.0;
        const int n = 20000;
        std::vector<double> zeros(static_cast<std::size_t>(n), 0.0);
        auto delta = make_delta(zeros);
        auto noised = add_central_gaussian_noise(delta, noise_std, provider);
        const auto& values = noised.at("weight").values();
        double sum = 0.0;
        for (const double value : values) sum += value;
        const double mean = sum / n;
        double sum_sq_dev = 0.0;
        for (const double value : values) sum_sq_dev += (value - mean) * (value - mean);
        const double sample_std = std::sqrt(sum_sq_dev / n);
        check(std::abs(mean) < 0.1, "sampled noise mean is close to 0");
        check(std::abs(sample_std - noise_std) < 0.1,
              "sampled noise std is close to the configured noise_std");
    }

    expect_throw(
        [&]() {
            fl::core::DeterministicNoiseProvider provider(1);
            (void)add_central_gaussian_noise(make_delta({0.0}), -1.0, provider);
        },
        "negative noise_std is rejected");
}

void run_accountant_tests() {
    using fl::core::UserLevelAccountant;

    // Monotonic growth.
    {
        UserLevelAccountant accountant(1.0, 0.1, 1e-5);
        double previous = accountant.get_epsilon();
        for (int i = 0; i < 10; ++i) {
            accountant.step(1);
            const double current = accountant.get_epsilon();
            check(current >= previous, "epsilon grows monotonically with steps");
            previous = current;
        }
    }

    // Zero steps -> zero epsilon.
    {
        UserLevelAccountant accountant(1.0, 0.1, 1e-5);
        check(accountant.get_epsilon() == 0.0, "zero steps reports epsilon 0");
    }

    // Cross-language golden value: q=0.1, sigma=1.2, steps=100, delta=1e-5
    // was independently computed by the Python legacy accountant
    // (federated/dp_accountant.py, itself validated against Opacus) at
    // approximately 6.415 — see python/tests/test_privacy_accounting.py.
    // This C++ implementation uses the identical formula and identical
    // order set, so it must match to a tight tolerance.
    {
        UserLevelAccountant accountant(1.2, 0.1, 1e-5);
        accountant.step(100);
        const double epsilon = accountant.get_epsilon();
        check(std::abs(epsilon - 6.414998048146023) < 1e-6,
              "cross-language golden parity: matches the Python accountant's value exactly");
    }

    // Invalid construction.
    expect_throw([&]() { UserLevelAccountant(1.0, 1.5, 1e-5); },
                 "sample_rate outside [0,1] is rejected");
    expect_throw([&]() { UserLevelAccountant(-1.0, 0.1, 1e-5); },
                 "negative noise_multiplier is rejected");
    expect_throw([&]() { UserLevelAccountant(1.0, 0.1, 1.5); },
                 "target_delta outside (0,1) is rejected");
}

void run_adaptive_clipping_tests() {
    using fl::core::AdaptiveClipController;
    using fl::core::AdaptiveClippingConfig;
    using fl::core::DeterministicNoiseProvider;

    // Direction: over-threshold fraction above target_quantile means the
    // bound is too low -> it must be raised. Uses count_noise_multiplier
    // near zero so the noisy fraction is effectively exact and the
    // direction assertion isn't flaky.
    {
        DeterministicNoiseProvider provider(1);
        AdaptiveClippingConfig config{.initial_clip = 1.0,
                                      .target_quantile = 0.5,
                                      .clip_learning_rate = 0.2,
                                      .min_clip = 1e-3,
                                      .max_clip = 1e3,
                                      .count_noise_multiplier = 1e-6,
                                      .target_delta = 1e-5};
        AdaptiveClipController controller(config, provider);
        const auto result = controller.step(/*over_threshold_count=*/9, /*cohort_size=*/10);
        check(result.clip_value > 1.0,
              "clip bound rises when the over-threshold fraction exceeds target_quantile");
    }

    // Opposite direction: fraction below target_quantile -> bound falls.
    {
        DeterministicNoiseProvider provider(1);
        AdaptiveClippingConfig config{.initial_clip = 1.0,
                                      .target_quantile = 0.5,
                                      .clip_learning_rate = 0.2,
                                      .min_clip = 1e-3,
                                      .max_clip = 1e3,
                                      .count_noise_multiplier = 1e-6,
                                      .target_delta = 1e-5};
        AdaptiveClipController controller(config, provider);
        const auto result = controller.step(/*over_threshold_count=*/1, /*cohort_size=*/10);
        check(result.clip_value < 1.0,
              "clip bound falls when the over-threshold fraction is below target_quantile");
    }

    // Reproducibility: an independently constructed provider with the
    // same seed predicts the same noise draw used internally.
    {
        DeterministicNoiseProvider provider(42);
        AdaptiveClippingConfig config{.count_noise_multiplier = 1.0, .target_delta = 1e-5};
        AdaptiveClipController controller(config, provider);
        const auto result = controller.step(/*over_threshold_count=*/5, /*cohort_size=*/10);

        DeterministicNoiseProvider independent_provider(42);
        const double expected_noise = independent_provider.gaussian_sample(1.0);
        const double expected_fraction =
            std::clamp((5.0 + expected_noise) / 10.0, 0.0, 1.0);
        check(std::abs(result.noisy_over_threshold_fraction - expected_fraction) < 1e-12,
              "noisy fraction matches an independently seeded prediction exactly");
    }

    // Epsilon grows monotonically across rounds and delta stays fixed.
    {
        DeterministicNoiseProvider provider(3);
        AdaptiveClippingConfig config{.count_noise_multiplier = 1.0, .target_delta = 1e-5};
        AdaptiveClipController controller(config, provider);
        double previous = controller.epsilon();
        for (int i = 0; i < 5; ++i) {
            const auto result = controller.step(3, 10);
            check(result.epsilon >= previous, "adaptive-clipping epsilon grows monotonically");
            check(result.delta == 1e-5, "adaptive-clipping delta stays fixed at target_delta");
            previous = result.epsilon;
        }
        check(controller.steps() == 5, "steps() reflects the number of controller.step() calls");
    }

    // Clip value stays within [min_clip, max_clip] even under many
    // consecutive over-threshold rounds.
    {
        DeterministicNoiseProvider provider(5);
        AdaptiveClippingConfig config{.initial_clip = 1.0,
                                      .target_quantile = 0.5,
                                      .clip_learning_rate = 0.9,
                                      .min_clip = 0.5,
                                      .max_clip = 2.0,
                                      .count_noise_multiplier = 1e-6,
                                      .target_delta = 1e-5};
        AdaptiveClipController controller(config, provider);
        for (int i = 0; i < 50; ++i) {
            const auto result = controller.step(10, 10);
            check(result.clip_value <= 2.0 + 1e-9, "clip_value never exceeds max_clip");
            check(result.clip_value >= 0.5 - 1e-9, "clip_value never falls below min_clip");
        }
    }

    // Invalid construction.
    {
        DeterministicNoiseProvider provider(1);
        expect_throw(
            [&]() {
                (void)AdaptiveClipController(AdaptiveClippingConfig{.initial_clip = -1.0}, provider);
            },
            "non-positive initial_clip is rejected");
        expect_throw(
            [&]() {
                (void)AdaptiveClipController(
                    AdaptiveClippingConfig{.initial_clip = 5.0, .min_clip = 1.0, .max_clip = 2.0},
                    provider);
            },
            "initial_clip outside [min_clip, max_clip] is rejected");
    }

    // Invalid step() calls.
    {
        DeterministicNoiseProvider provider(1);
        AdaptiveClippingConfig config{.count_noise_multiplier = 1.0, .target_delta = 1e-5};
        AdaptiveClipController controller(config, provider);
        expect_throw([&]() { (void)controller.step(0, 0); }, "cohort_size=0 is rejected");
        expect_throw([&]() { (void)controller.step(11, 10); },
                     "over_threshold_count > cohort_size is rejected");
    }
}

}  // namespace

int main() {
    run_clipping_tests();
    run_noise_tests();
    run_accountant_tests();
    run_adaptive_clipping_tests();
    if (g_failures == 0) {
        std::cout << "all privacy tests passed\n";
    }
    return g_failures == 0 ? 0 : 1;
}
