#include "fl_core/privacy.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <stdexcept>

namespace fl::core {

std::string to_string(PrivacyMode mode) {
    switch (mode) {
        case PrivacyMode::kNone:
            return "none";
        case PrivacyMode::kSampleLevelDp:
            return "sample_level_dp";
        case PrivacyMode::kUserLevelDp:
            return "user_level_dp";
        case PrivacyMode::kHybridDp:
            return "hybrid_dp";
        default:
            return "unknown";
    }
}

std::string to_string(PrivacyBudgetPolicy policy) {
    switch (policy) {
        case PrivacyBudgetPolicy::kWarnOnly:
            return "warn_only";
        case PrivacyBudgetPolicy::kStopBeforeExceeding:
            return "stop_before_exceeding";
        case PrivacyBudgetPolicy::kStopAfterCurrentRound:
            return "stop_after_current_round";
        case PrivacyBudgetPolicy::kFailRun:
            return "fail_run";
        default:
            return "unknown";
    }
}

namespace {

double log_binomial(int n, int k) {
    return std::lgamma(static_cast<double>(n) + 1.0) - std::lgamma(static_cast<double>(k) + 1.0) -
           std::lgamma(static_cast<double>(n - k) + 1.0);
}

double logsumexp(const std::vector<double>& values) {
    const double max_value = *std::max_element(values.begin(), values.end());
    if (!std::isfinite(max_value)) {
        return max_value;  // all -inf, or a genuine +inf term
    }
    double sum = 0.0;
    for (const double value : values) {
        sum += std::exp(value - max_value);
    }
    return max_value + std::log(sum);
}

// RDP of one subsampled-Gaussian step at integer order alpha — identical
// formula to federated/dp_accountant.py's _compute_rdp_single_step (see
// that module's docstring for the Mironov et al. 2019 derivation), kept
// as an independent C++ implementation so the two can be golden-parity
// tested against each other.
double compute_rdp_single_step(int alpha, double sample_rate, double noise_multiplier) {
    const double q = sample_rate;
    const double sigma = noise_multiplier;
    if (q == 0.0) {
        return 0.0;
    }
    if (sigma == 0.0) {
        return std::numeric_limits<double>::infinity();
    }
    if (q == 1.0) {
        return static_cast<double>(alpha) / (2.0 * sigma * sigma);
    }
    std::vector<double> log_terms;
    log_terms.reserve(static_cast<std::size_t>(alpha) + 1);
    for (int k = 0; k <= alpha; ++k) {
        const double log_binom = log_binomial(alpha, k);
        const double log_term = log_binom + (alpha - k) * std::log1p(-q) +
                                (k > 0 ? k * std::log(q) : 0.0) +
                                (static_cast<double>(k) * (k - 1)) / (2.0 * sigma * sigma);
        log_terms.push_back(log_term);
    }
    return logsumexp(log_terms) / (alpha - 1);
}

std::vector<int> default_orders() {
    std::vector<int> orders;
    for (int alpha = 2; alpha <= 64; ++alpha) {
        orders.push_back(alpha);
    }
    for (int alpha : {80, 96, 128, 256, 512}) {
        orders.push_back(alpha);
    }
    return orders;
}

}  // namespace

double compute_shared_norm(const TensorCollection& delta,
                           const std::set<std::string>& shared_parameter_names) {
    const auto is_shared = [&](const std::string& name) {
        return shared_parameter_names.empty() || shared_parameter_names.contains(name);
    };

    // Global (multi-tensor) L2 norm across only the shared tensors,
    // accumulated in FP64 (TensorBuffer already stores double).
    double sum_squares = 0.0;
    for (const auto& [name, tensor] : delta.tensors()) {
        if (!is_shared(name)) {
            continue;
        }
        for (const double value : tensor.values()) {
            if (!std::isfinite(value)) {
                throw std::invalid_argument(
                    "client delta contains a non-finite (NaN/Inf) value; rejected before "
                    "clipping");
            }
            sum_squares += value * value;
        }
    }
    if (!std::isfinite(sum_squares)) {
        throw std::invalid_argument("client delta norm computation overflowed");
    }
    const double norm = std::sqrt(sum_squares);
    if (!std::isfinite(norm)) {
        throw std::invalid_argument("client delta norm is not finite");
    }
    return norm;
}

TensorCollection clip_client_delta(const TensorCollection& delta,
                                   const ClippingConfig& config,
                                   const std::set<std::string>& shared_parameter_names) {
    if (config.clip_bound <= 0.0) {
        throw std::invalid_argument("clip_bound must be positive");
    }
    if (config.numerical_epsilon < 0.0) {
        throw std::invalid_argument("numerical_epsilon must be non-negative");
    }

    const auto is_shared = [&](const std::string& name) {
        return shared_parameter_names.empty() || shared_parameter_names.contains(name);
    };
    const double norm = compute_shared_norm(delta, shared_parameter_names);

    // scale_i = min(1, C_t / (norm_i + numerical_epsilon)) — the exact
    // required formula. Never returned or logged: only `clipped` leaves
    // this function.
    const double scale_factor =
        std::min(1.0, config.clip_bound / (norm + config.numerical_epsilon));

    TensorCollection clipped;
    for (const auto& [name, tensor] : delta.tensors()) {
        if (is_shared(name)) {
            clipped.insert(scale(tensor, scale_factor));
        } else {
            // Local-head exclusion: personalized/frozen tensors pass
            // through completely unmodified.
            clipped.insert(tensor);
        }
    }
    return clipped;
}

DeterministicNoiseProvider::DeterministicNoiseProvider(std::uint64_t seed) : engine_(seed) {}

double DeterministicNoiseProvider::gaussian_sample(double std_dev) {
    std::normal_distribution<double> distribution(0.0, std_dev);
    return distribution(engine_);
}

SecureNoiseProvider::SecureNoiseProvider() {
    std::random_device entropy_source;
    // seed_seq mixing several random_device draws avoids relying on a
    // single 32-bit draw to seed a 64-bit engine.
    std::seed_seq seed{entropy_source(), entropy_source(), entropy_source(), entropy_source()};
    engine_.seed(seed);
}

double SecureNoiseProvider::gaussian_sample(double std_dev) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::normal_distribution<double> distribution(0.0, std_dev);
    return distribution(engine_);
}

TensorCollection add_central_gaussian_noise(const TensorCollection& aggregated_delta,
                                            double noise_std,
                                            NoiseProvider& noise_provider) {
    if (noise_std < 0.0) {
        throw std::invalid_argument("noise_std must be non-negative");
    }
    TensorCollection noised;
    for (const auto& [name, tensor] : aggregated_delta.tensors()) {
        std::vector<double> values = tensor.values();
        if (noise_std > 0.0) {
            for (double& value : values) {
                value += noise_provider.gaussian_sample(noise_std);
            }
        }
        noised.insert(TensorBuffer(tensor.descriptor(), std::move(values)));
    }
    return noised;
}

UserLevelAccountant::UserLevelAccountant(double noise_multiplier,
                                         double sample_rate,
                                         double target_delta)
    : noise_multiplier_(noise_multiplier), sample_rate_(sample_rate), target_delta_(target_delta) {
    if (sample_rate_ < 0.0 || sample_rate_ > 1.0) {
        throw std::invalid_argument("sample_rate must lie in [0, 1]");
    }
    if (noise_multiplier_ < 0.0) {
        throw std::invalid_argument("noise_multiplier must be >= 0");
    }
    if (!(target_delta_ > 0.0 && target_delta_ < 1.0)) {
        throw std::invalid_argument("target_delta must lie in (0, 1)");
    }
    orders_ = default_orders();
    rdp_per_step_.reserve(orders_.size());
    for (const int alpha : orders_) {
        rdp_per_step_.push_back(compute_rdp_single_step(alpha, sample_rate_, noise_multiplier_));
    }
}

void UserLevelAccountant::step(std::uint64_t num_rounds) {
    steps_ += num_rounds;
}

double UserLevelAccountant::get_epsilon(double delta) const {
    return project_epsilon(0, delta);
}

double UserLevelAccountant::project_epsilon(std::uint64_t additional_steps, double delta) const {
    const double target = delta > 0.0 ? delta : target_delta_;
    const std::uint64_t projected_steps = steps_ + additional_steps;
    if (projected_steps == 0) {
        return 0.0;
    }
    bool all_infinite = true;
    double best_epsilon = std::numeric_limits<double>::infinity();
    for (std::size_t index = 0; index < orders_.size(); ++index) {
        const double rdp = rdp_per_step_[index];
        if (!std::isfinite(rdp)) {
            continue;
        }
        all_infinite = false;
        const double total_rdp = static_cast<double>(projected_steps) * rdp;
        const double epsilon = total_rdp + std::log(1.0 / target) / (orders_[index] - 1);
        best_epsilon = std::min(best_epsilon, epsilon);
    }
    if (all_infinite) {
        return std::numeric_limits<double>::infinity();
    }
    return best_epsilon;
}

AdaptiveClipController::AdaptiveClipController(AdaptiveClippingConfig config,
                                               NoiseProvider& noise_provider)
    : config_(config),
      noise_provider_(noise_provider),
      clip_value_(config.initial_clip),
      accountant_(config.count_noise_multiplier, /*sample_rate=*/1.0, config.target_delta) {
    if (config_.initial_clip <= 0.0) {
        throw std::invalid_argument("initial_clip must be positive");
    }
    if (!(config_.min_clip <= config_.initial_clip && config_.initial_clip <= config_.max_clip)) {
        throw std::invalid_argument("min_clip <= initial_clip <= max_clip must hold");
    }
}

AdaptiveClipStepResult AdaptiveClipController::step(std::uint64_t over_threshold_count,
                                                    std::uint64_t cohort_size) {
    if (cohort_size == 0) {
        throw std::invalid_argument("cohort_size must be positive");
    }
    if (over_threshold_count > cohort_size) {
        throw std::invalid_argument("over_threshold_count must be in [0, cohort_size]");
    }

    // Sensitivity of the count query is 1 (one client can change it by
    // at most 1), so noise std = count_noise_multiplier directly. The
    // raw count is never stored or returned past this point.
    const double noise = noise_provider_.gaussian_sample(config_.count_noise_multiplier);
    const double noisy_count = static_cast<double>(over_threshold_count) + noise;
    const double noisy_fraction =
        std::clamp(noisy_count / static_cast<double>(cohort_size), 0.0, 1.0);

    // over_threshold_fraction > target_quantile means too many clients
    // are being clipped -> the bound is too low -> raise it (positive
    // error increases scale). Mirrors
    // fl_platform.privacy.adaptive_clipping.AdaptiveClipController.step
    // exactly, including this sign convention.
    const double error = noisy_fraction - config_.target_quantile;
    const double scale_factor = 1.0 + config_.clip_learning_rate * error;
    clip_value_ *= std::max(scale_factor, 1e-6);
    clip_value_ = std::min(std::max(clip_value_, config_.min_clip), config_.max_clip);

    accountant_.step(1);
    return AdaptiveClipStepResult{
        .clip_value = clip_value_,
        .noisy_over_threshold_fraction = noisy_fraction,
        .epsilon = accountant_.get_epsilon(),
        .delta = config_.target_delta,
    };
}

void AdaptiveClipController::restore(double clip_value, std::uint64_t steps) {
    clip_value_ = clip_value;
    accountant_.step(steps);
}

}  // namespace fl::core
