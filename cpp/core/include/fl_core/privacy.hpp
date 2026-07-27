#pragma once

// Privacy Engineering phase: user-level differential privacy, applied
// centrally by the (trusted) coordinator to a round's already-collected
// client contributions. See docs/user-level-dp.md.
//
// CRITICAL PRIVACY RULE (docs/privacy-mathematics.md): this mechanism's
// epsilon/delta protect a *different* neighboring relation (one client's
// complete round contribution) than sample-level DP (one training
// example, accounted entirely in Python) or adaptive clipping (the
// clip-bound statistic itself). Nothing in this file computes or exposes
// a combined epsilon across mechanisms, and UserLevelAccountant is a
// dedicated implementation — it does not reuse or wrap any sample-level
// accounting code.

#include "fl_core/aggregation.hpp"
#include "fl_core/tensor.hpp"

#include <cstdint>
#include <mutex>
#include <random>
#include <set>
#include <string>
#include <vector>

namespace fl::core {

// Domain-level privacy mode — mirrors fl.privacy.v1.PrivacyMode without
// pulling generated protobuf types into fl_core (same pattern as
// AggregationAlgorithm/WeightingStrategyType vs. their wire enums; see
// coordinator_service.cpp's algorithm_from_wire).
enum class PrivacyMode {
    kNone,
    kSampleLevelDp,
    kUserLevelDp,
    kHybridDp,
};

[[nodiscard]] std::string to_string(PrivacyMode mode);

// How a mechanism's epsilon_budget (see *DPConfig::epsilon_budget) is
// enforced once it's reached — mirrors fl.privacy.v1.PrivacyBudgetPolicy.
// Applied independently per mechanism by RunInstance::finalize_round
// (docs/privacy-budget-policies.md); a mechanism with epsilon_budget
// unset (0) is never affected by any policy value, including kFailRun.
enum class PrivacyBudgetPolicy {
    // Never blocks anything — only emits a kPrivacyBudgetWarning/
    // kPrivacyBudgetExceeded event. The safe default: a caller who
    // configures a budget without picking a policy gets visibility, not
    // a surprise run stoppage.
    kWarnOnly,
    // Preventive: checked BEFORE this round's mechanism is applied,
    // using the *projected* epsilon it would produce. If applying it
    // would meet-or-exceed the budget, this round's private release is
    // refused entirely (never partially applied) and the run ends
    // (kCompleted) without releasing that round's aggregate — the only
    // policy that guarantees the budget is never actually exceeded.
    kStopBeforeExceeding,
    // Reactive: this round's mechanism is applied and released
    // normally (may cross the budget by up to one round's worth); if
    // the resulting epsilon has met-or-exceeded budget, no further
    // round is started (kCompleted instead of kRunning).
    kStopAfterCurrentRound,
    // Same reactive check as kStopAfterCurrentRound, but treats
    // crossing the budget as a run failure (kFailed) rather than a
    // graceful stop.
    kFailRun,
};

[[nodiscard]] std::string to_string(PrivacyBudgetPolicy policy);

// Sample-level DP is computed and applied entirely in Python (Opacus,
// per-training-example — see python/src/fl_platform/privacy/). The C++
// coordinator never touches gradients or applies this mechanism itself;
// this struct exists only so RunConfig/ClientTaskDescriptor can carry
// the parameters through to a ClientTrainingTask a worker actually
// trains with, and so the coordinator can validate/relay them without
// pulling protobuf types into fl_core (same reasoning as
// UserLevelDPConfig above). See docs/hybrid-dp.md.
struct SampleLevelDPConfig {
    double noise_multiplier{1.0};
    double max_grad_norm{1.0};
    double target_delta{1e-5};
    // "rdp" | "prv" | "gdp" — mirrors
    // fl_platform.privacy.accounting.SUPPORTED_ACCOUNTANTS; validated
    // against that exact set at the CreateRun wire-mapping boundary, not
    // re-validated here (fl_core has no opinion on accountant names).
    std::string accountant{"rdp"};
    bool poisson_sampling{true};
    // 0 (unset, the default): no budget enforced beyond target_delta
    // itself. See PrivacyBudgetPolicy (docs/privacy-budget-policies.md)
    // for what "enforced" means in practice.
    double epsilon_budget{0.0};
};

// Advertised by a worker at registration time (mirrors
// fl.privacy.v1.WorkerPrivacyCapabilities) so the coordinator only
// assigns sample-level/hybrid-DP tasks to workers that can actually
// execute them — see docs/worker-privacy-capabilities.md. A worker that
// doesn't advertise supports_sample_level_dp simply never receives a
// task from a sample-level/hybrid-DP run; there is no silent fallback to
// non-private training.
struct WorkerPrivacyCapabilities {
    bool supports_sample_level_dp{false};
    std::string opacus_version;
    std::vector<std::string> supported_accountants;  // "rdp" | "prv" | "gdp"
    bool supports_secure_random{false};
};

// Runtime configuration for the coordinator-side user-level DP pipeline
// (clip -> aggregate -> noise), owned by RunConfig — see
// run_manager.hpp. Only consulted when mode is kUserLevelDp or
// kHybridDp.
struct UserLevelDPConfig {
    double noise_multiplier{1.0};
    double target_delta{1e-5};
    // Used only when adaptive clipping is disabled; see
    // AdaptiveClippingConfig (adaptive-clipping.md) for the alternative.
    double initial_clipping_bound{1.0};
    double numerical_epsilon{1e-12};
    bool secure_random{false};
    // 0 (unset, the default): no budget enforced. See
    // docs/privacy-budget-policies.md.
    double epsilon_budget{0.0};
};

// ---------------------------------------------------------------------
// Clipping: bounds one client's complete round contribution to L2 norm
// clip_bound, per the required formula:
//   norm_i = ||delta_i||_2
//   scale_i = min(1, C_t / (norm_i + numerical_epsilon))
//   clipped_delta_i = scale_i * delta_i
// computed as a single global norm across all participating tensors
// together (not per-tensor), in FP64 (TensorBuffer's values() are
// already double throughout this codebase).
// ---------------------------------------------------------------------

struct ClippingConfig {
    double clip_bound{1.0};
    // Prevents division blow-up when norm_i is exactly (or extremely
    // close to) zero. Configurable rather than hardcoded so callers can
    // tune it relative to their tensor value scale.
    double numerical_epsilon{1e-12};
};

// Computes the same global (multi-tensor) L2 norm across only the
// tensors named in `shared_parameter_names` that clip_client_delta uses
// internally to decide its scale factor — exposed separately so callers
// (e.g. adaptive clipping's over-threshold count, see
// AdaptiveClipController below) can compare a client's norm against a
// candidate bound without duplicating this logic. Never store or expose
// the returned value beyond a boolean "did this exceed some bound"
// comparison — see docs/user-level-dp.md's "no per-client norm
// exposure" requirement. Throws std::invalid_argument on any non-finite
// (NaN/Inf) value or a non-finite accumulated norm (overflow), matching
// clip_client_delta's own validation.
[[nodiscard]] double compute_shared_norm(const TensorCollection& delta,
                                         const std::set<std::string>& shared_parameter_names);

// Clips only the tensors named in `shared_parameter_names` (the
// aggregatable/shared-backbone tensors — see docs/aggregation-manifests.md);
// any other tensor present in `delta` (e.g. a personalized head) is
// copied through completely unmodified — the "local-head exclusion"
// requirement. An empty `shared_parameter_names` means "every tensor in
// `delta` is shared" (matches AggregationManifest::is_declared()'s
// existing permissive-when-undeclared convention).
//
// Throws std::invalid_argument on any non-finite (NaN/Inf) value or a
// non-finite accumulated norm (overflow). Never returns or logs the raw
// per-client norm — callers must not add that themselves either; see
// docs/user-level-dp.md's "no per-client norm exposure" requirement.
[[nodiscard]] TensorCollection clip_client_delta(
    const TensorCollection& delta,
    const ClippingConfig& config,
    const std::set<std::string>& shared_parameter_names);

// ---------------------------------------------------------------------
// Central Gaussian noise, added once to the already-aggregated (clipped,
// weighted) result — never per-client. See docs/user-level-dp.md's
// "Do not reuse the legacy per-client noise mechanism" requirement:
// federated/dp_accountant.py's Python sibling documents the same
// constraint on the Python side (that mechanism is local DP, applied
// per-client before transmission; this one is central DP, applied once
// after the trusted coordinator aggregates).
// ---------------------------------------------------------------------

// Abstraction so a real secure RNG can replace either implementation
// later without touching call sites — see docs/user-level-dp.md's noise
// generation requirements.
class NoiseProvider {
  public:
    virtual ~NoiseProvider() = default;
    [[nodiscard]] virtual double gaussian_sample(double std_dev) = 0;
};

// Deterministic, seeded — research/test mode ONLY. Never construct this
// with an experiment's ordinary seed (e.g. client_selection_seed)
// directly; callers must domain-separate (e.g. hash the seed together
// with a distinct salt) so privacy noise and ordinary experiment
// randomness never share a seed. Not thread-safe (single mt19937_64,
// no locking) — deterministic tests are expected to be single-threaded
// per accountant/provider instance.
class DeterministicNoiseProvider final : public NoiseProvider {
  public:
    explicit DeterministicNoiseProvider(std::uint64_t seed);
    [[nodiscard]] double gaussian_sample(double std_dev) override;

  private:
    std::mt19937_64 engine_;
};

// Non-deterministic runtime default, seeded from std::random_device (OS
// entropy). Thread-safe: a single shared engine protected by a mutex,
// since noise is drawn once per tensor element and per-call
// random_device access would be far too slow. This is *not* a
// cryptographically secure RNG — see docs/user-level-dp.md's trust-
// boundary documentation for what "secure_random" actually means today
// (a config hook, not an implemented CSPRNG) — do not claim
// cryptographic security beyond what secure_mode_enabled documents.
class SecureNoiseProvider final : public NoiseProvider {
  public:
    SecureNoiseProvider();
    [[nodiscard]] double gaussian_sample(double std_dev) override;

  private:
    std::mutex mutex_;
    std::mt19937_64 engine_;
};

// Adds independent N(0, noise_std^2) to every element of every tensor in
// `aggregated_delta`. `noise_std` must already be computed by the caller
// (see docs/user-level-dp.md for the sensitivity derivation — this
// function has no opinion on sum-vs-average, weighting, or the
// contribution cap; it only adds noise of the std it's given).
[[nodiscard]] TensorCollection add_central_gaussian_noise(const TensorCollection& aggregated_delta,
                                                           double noise_std,
                                                           NoiseProvider& noise_provider);

// ---------------------------------------------------------------------
// Dedicated user-level (client-level) RDP accountant. Deliberately not
// shared with any sample-level accounting code — see the module-level
// Critical Privacy Rule note above. Implements the same subsampled-
// Gaussian RDP formula as the Python prototype's
// federated/dp_accountant.py (validated there against Opacus's own
// implementation — see python/tests/test_privacy_accounting.py) so the
// two independent implementations can be golden-parity-tested against
// each other: see cpp/core/tests/user_level_dp_test.cpp.
// ---------------------------------------------------------------------

class UserLevelAccountant {
  public:
    UserLevelAccountant(double noise_multiplier, double sample_rate, double target_delta = 1e-5);

    void step(std::uint64_t num_rounds = 1);
    [[nodiscard]] double get_epsilon(double delta = -1.0) const;
    // Non-mutating: what get_epsilon() would report if step(additional_steps)
    // were called first, without actually advancing state. Used for
    // "projected next epsilon" reporting (see GetPrivacyProjection in
    // coordinator_service.cpp) — never used to make an actual privacy
    // decision retroactively, only to preview one.
    [[nodiscard]] double project_epsilon(std::uint64_t additional_steps, double delta = -1.0) const;
    [[nodiscard]] std::uint64_t steps() const { return steps_; }
    [[nodiscard]] double noise_multiplier() const { return noise_multiplier_; }
    [[nodiscard]] double sample_rate() const { return sample_rate_; }
    [[nodiscard]] double target_delta() const { return target_delta_; }

  private:
    double noise_multiplier_;
    double sample_rate_;
    double target_delta_;
    std::uint64_t steps_{0};
    std::vector<int> orders_;
    std::vector<double> rdp_per_step_;
};

// ---------------------------------------------------------------------
// Adaptive clipping (Andrew et al., 2021) — quantile-based dynamic clip
// bound, made a real DP mechanism by privatizing the over-threshold
// count with Gaussian noise before it ever influences the clip bound.
// See docs/adaptive-clipping.md. Mirrors
// fl_platform.privacy.adaptive_clipping.AdaptiveClipController exactly
// (same formula, same sign convention) so the two independent
// implementations stay behaviorally identical — see
// cpp/core/tests/privacy_test.cpp's cross-language parity case.
//
// CRITICAL PRIVACY RULE: the count query's epsilon/delta protect a
// *different* neighboring relation (the clip-bound statistic) than
// sample-level or user-level DP. This mechanism's accountant is
// entirely separate from UserLevelAccountant's own instance/state, even
// though both happen to reuse the identical Gaussian-mechanism RDP math
// (accounting formula reuse is not epsilon reuse — see
// docs/privacy-mathematics.md).
// ---------------------------------------------------------------------

struct AdaptiveClippingConfig {
    double initial_clip{1.0};
    double target_quantile{0.5};
    double clip_learning_rate{0.2};
    double min_clip{1e-3};
    double max_clip{1e3};
    double count_noise_multiplier{1.0};
    double target_delta{1e-5};
    // 0 (unset, the default): no budget enforced. See
    // docs/privacy-budget-policies.md.
    double epsilon_budget{0.0};
};

struct AdaptiveClipStepResult {
    double clip_value{0.0};
    double noisy_over_threshold_fraction{0.0};
    double epsilon{0.0};
    double delta{0.0};
};

class AdaptiveClipController {
  public:
    // `noise_provider` is borrowed, not owned — callers pass the same
    // NoiseProvider they already use for central Gaussian noise (or a
    // dedicated one); see RunInstance's ownership in run_manager.hpp.
    // Not thread-safe: a round's clip-bound update must happen on the
    // coordinator's single round-finalization path, matching
    // finalize_round's existing single-threaded assumption.
    AdaptiveClipController(AdaptiveClippingConfig config, NoiseProvider& noise_provider);

    // `over_threshold_count` (how many of `cohort_size` clients' updates
    // exceeded the current clip bound) is the sensitive quantity — it is
    // immediately privatized and never returned or logged in raw form.
    // Throws std::invalid_argument if cohort_size <= 0 or
    // over_threshold_count is not in [0, cohort_size].
    [[nodiscard]] AdaptiveClipStepResult step(std::uint64_t over_threshold_count,
                                               std::uint64_t cohort_size);

    [[nodiscard]] double clip_value() const { return clip_value_; }
    [[nodiscard]] double epsilon() const { return accountant_.get_epsilon(); }
    [[nodiscard]] std::uint64_t steps() const { return accountant_.steps(); }
    // See UserLevelAccountant::project_epsilon — same non-mutating preview
    // semantics, one round ahead.
    [[nodiscard]] double projected_epsilon_after_one_more_round() const {
        return accountant_.project_epsilon(1);
    }

    // Restores clip_value_ and the accountant's step count directly from
    // checkpointed state (see RunInstance::restore_from_checkpoint),
    // bypassing step()'s normal privatized-count-driven update entirely
    // — there is no historical over-threshold count to replay, only the
    // last known clip_value and step count. Must be called on a freshly
    // constructed controller (steps() == 0) — calling it a second time,
    // or after any real step() call, would double-count.
    void restore(double clip_value, std::uint64_t steps);

  private:
    AdaptiveClippingConfig config_;
    NoiseProvider& noise_provider_;
    double clip_value_;
    // sample_rate=1.0: reuses UserLevelAccountant's plain-Gaussian branch
    // (q == 1.0 -> alpha / (2 sigma^2), the standard Gaussian-mechanism
    // RDP with no subsampling amplification) rather than a second
    // from-scratch implementation of the same well-known formula — same
    // reasoning as the Python sibling's AdaptiveClippingAccountant.
    UserLevelAccountant accountant_;
};

}  // namespace fl::core
