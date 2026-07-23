#include "fl_core/aggregation.hpp"

#include <algorithm>
#include <cmath>
#include <numeric>
#include <stdexcept>
#include <unordered_set>

namespace fl::core {

namespace {

void validate_manifest_against_update(
    const ModelManifest& manifest,
    const ClientUpdate& update,
    const AggregationOptions& options,
    bool require_control
) {
    if (!options.run_id.empty() && update.run_id != options.run_id) {
        throw std::invalid_argument("client update run_id does not match context");
    }
    if (options.round_id != 0 && update.round_id != options.round_id) {
        throw std::invalid_argument("client update round_id does not match context");
    }
    if (update.client_id.empty()) {
        throw std::invalid_argument("client_id must not be empty");
    }
    if (update.update_id.empty()) {
        throw std::invalid_argument("update_id must not be empty");
    }
    if (update.nonce.empty()) {
        throw std::invalid_argument("nonce must not be empty");
    }
    if (update.base_model_version != manifest.model_version) {
        throw std::invalid_argument("client update base model version is stale or future");
    }
    if (update.algorithm != options.algorithm) {
        throw std::invalid_argument("client update algorithm does not match context");
    }
    if (update.sample_count == 0) {
        throw std::invalid_argument("sample_count must be positive");
    }
    if (update.delta.tensors().size() != manifest.tensors.size()) {
        throw std::invalid_argument("client delta tensor set does not match manifest");
    }
    for (const auto& descriptor : manifest.tensors) {
        if (!update.delta.contains(descriptor.name)) {
            throw std::invalid_argument("missing tensor in client delta");
        }
        update.delta.at(descriptor.name).validate();
        if (update.delta.at(descriptor.name).descriptor().shape != descriptor.shape) {
            throw std::invalid_argument("client tensor shape does not match manifest");
        }
        if (update.delta.at(descriptor.name).descriptor().dtype != descriptor.dtype) {
            throw std::invalid_argument("client tensor dtype does not match manifest");
        }
        if (require_control && !update.control_delta.contains(descriptor.name)) {
            throw std::invalid_argument("missing control tensor in client update");
        }
        if (require_control) {
            update.control_delta.at(descriptor.name).validate();
        }
    }
}

TensorCollection make_zero_collection(const ModelManifest& manifest) {
    TensorCollection collection;
    for (const auto& descriptor : manifest.tensors) {
        collection.insert(zeros_like(descriptor));
    }
    return collection;
}

TensorCollection weighted_average(
    const ModelManifest& manifest,
    const std::vector<ClientUpdate>& updates,
    const std::vector<double>& weights
) {
    if (updates.empty()) {
        throw std::invalid_argument("updates must not be empty");
    }
    if (updates.size() != weights.size()) {
        throw std::invalid_argument("weights size must match update count");
    }

    TensorCollection aggregate = make_zero_collection(manifest);
    for (std::size_t update_index = 0; update_index < updates.size(); ++update_index) {
        const auto& update = updates[update_index];
        const double weight = weights[update_index];
        if (!std::isfinite(weight) || weight < 0.0) {
            throw std::invalid_argument("aggregation weight must be finite and non-negative");
        }
        for (const auto& descriptor : manifest.tensors) {
            const auto scaled = scale(update.delta.at(descriptor.name), weight);
            aggregate.assign(add(aggregate.at(descriptor.name), scaled));
        }
    }

    return aggregate;
}

std::vector<double> normalize_weights(std::vector<double> raw) {
    const double denominator = std::accumulate(raw.begin(), raw.end(), 0.0);
    if (!std::isfinite(denominator) || denominator <= 0.0) {
        throw std::invalid_argument("weight denominator must be positive");
    }
    for (auto& weight : raw) {
        weight /= denominator;
        if (!std::isfinite(weight)) {
            throw std::invalid_argument("normalized weight must be finite");
        }
    }
    return raw;
}

class SampleCountWeighting final : public WeightingStrategy {
public:
    std::vector<double> weights(
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions&
    ) const override {
        std::vector<double> raw;
        raw.reserve(updates.size());
        for (const auto& update : updates) {
            raw.push_back(static_cast<double>(update.sample_count));
        }
        return normalize_weights(std::move(raw));
    }

    std::string name() const override { return "sample_count"; }
};

class UniformWeighting final : public WeightingStrategy {
public:
    std::vector<double> weights(
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions&
    ) const override {
        if (updates.empty()) {
            throw std::invalid_argument("updates must not be empty");
        }
        return std::vector<double>(updates.size(), 1.0 / static_cast<double>(updates.size()));
    }

    std::string name() const override { return "uniform"; }
};

class CappedSampleCountWeighting final : public WeightingStrategy {
public:
    std::vector<double> weights(
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options
    ) const override {
        if (options.contribution_cap <= 0.0) {
            throw std::invalid_argument("contribution cap must be positive");
        }
        std::vector<double> raw;
        raw.reserve(updates.size());
        for (const auto& update : updates) {
            raw.push_back(std::min(
                static_cast<double>(update.sample_count),
                options.contribution_cap
            ));
        }
        return normalize_weights(std::move(raw));
    }

    std::string name() const override { return "capped_sample_count"; }
};

class NormalizedBoundedWeighting final : public WeightingStrategy {
public:
    std::vector<double> weights(
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options
    ) const override {
        if (options.minimum_weight < 0.0 || options.maximum_weight <= 0.0 ||
            options.minimum_weight > options.maximum_weight) {
            throw std::invalid_argument("invalid normalized bounded weight range");
        }
        auto raw = SampleCountWeighting().weights(updates, options);
        for (auto& weight : raw) {
            weight = std::clamp(weight, options.minimum_weight, options.maximum_weight);
        }
        return normalize_weights(std::move(raw));
    }

    std::string name() const override { return "normalized_bounded"; }
};

std::unique_ptr<WeightingStrategy> make_weighting_strategy(WeightingStrategyType strategy) {
    switch (strategy) {
        case WeightingStrategyType::kSampleCount:
            return std::make_unique<SampleCountWeighting>();
        case WeightingStrategyType::kUniform:
            return std::make_unique<UniformWeighting>();
        case WeightingStrategyType::kCappedSampleCount:
            return std::make_unique<CappedSampleCountWeighting>();
        case WeightingStrategyType::kNormalizedBounded:
            return std::make_unique<NormalizedBoundedWeighting>();
        default:
            throw std::invalid_argument("unsupported weighting strategy");
    }
}

class WeightedAggregator final : public Aggregator {
public:
    AggregationResult aggregate(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        const OptimizerState& previous_state
    ) const override {
        UpdateValidator().validate_cohort(manifest, updates, options, false);
        auto weighting = make_weighting_strategy(options.weighting);
        AggregationResult result;
        result.model_delta = weighted_average(manifest, updates, weighting->weights(updates, options));
        for (const auto& descriptor : manifest.tensors) {
            result.model_delta.assign(
                scale(result.model_delta.at(descriptor.name), options.server_lr)
            );
        }
        result.optimizer_state = previous_state;
        return result;
    }
};

class ScaffoldAggregator final : public Aggregator {
public:
    AggregationResult aggregate(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        const OptimizerState& previous_state
    ) const override {
        UpdateValidator().validate_cohort(manifest, updates, options, true);
        auto weighting = make_weighting_strategy(WeightingStrategyType::kUniform);
        AggregationResult result;
        result.model_delta = weighted_average(manifest, updates, weighting->weights(updates, options));
        for (const auto& descriptor : manifest.tensors) {
            result.model_delta.assign(
                scale(result.model_delta.at(descriptor.name), options.server_lr)
            );
        }

        result.control_delta = make_zero_collection(manifest);
        const auto control_average = weighted_average(
            manifest,
            [&]() {
                std::vector<ClientUpdate> remapped = updates;
                for (auto& update : remapped) {
                    update.delta = update.control_delta;
                }
                return remapped;
            }(),
            weighting->weights(updates, options)
        );
        const auto control_scale = static_cast<double>(updates.size()) /
            static_cast<double>(options.total_clients);
        for (const auto& descriptor : manifest.tensors) {
            result.control_delta.assign(
                scale(control_average.at(descriptor.name), control_scale)
            );
        }
        result.optimizer_state = previous_state;
        return result;
    }
};

// Shared setup used by every FedOpt server optimizer: resolve the previous
// moment tensors (or zero-initialize them on the first step) and advance
// the step counter. Each optimizer subclass owns only its moment-update
// and denominator formula.
struct FedOptStepInputs {
    TensorCollection first_moment;
    TensorCollection second_moment;
    std::uint64_t step;
};

FedOptStepInputs prepare_fedopt_step(
    const ModelManifest& manifest,
    const OptimizerState& previous_state
) {
    FedOptStepInputs inputs;
    inputs.first_moment = previous_state.first_moment.empty()
        ? make_zero_collection(manifest)
        : previous_state.first_moment;
    inputs.second_moment = previous_state.second_moment.empty()
        ? make_zero_collection(manifest)
        : previous_state.second_moment;
    inputs.step = previous_state.step + 1;
    return inputs;
}

class FedAdagradOptimizer final : public ServerOptimizer {
public:
    OptimizerState apply(
        const ModelManifest& manifest,
        const TensorCollection& aggregated_delta,
        const OptimizerState& previous_state,
        const AggregationOptions& options,
        TensorCollection& out_model_delta
    ) const override {
        auto step_inputs = prepare_fedopt_step(manifest, previous_state);
        out_model_delta = make_zero_collection(manifest);

        for (const auto& descriptor : manifest.tensors) {
            const auto& delta = aggregated_delta.at(descriptor.name);
            step_inputs.first_moment.assign(add(
                scale(step_inputs.first_moment.at(descriptor.name), options.beta1),
                scale(delta, 1.0 - options.beta1)
            ));
            step_inputs.second_moment.assign(add(
                step_inputs.second_moment.at(descriptor.name), hadamard_square(delta)
            ));

            const auto& m_hat = step_inputs.first_moment.at(descriptor.name);
            const auto& v_hat = step_inputs.second_moment.at(descriptor.name);
            const auto denom = add_scalar(hadamard_sqrt(v_hat), options.tau);
            out_model_delta.assign(
                scale(divide_elementwise(m_hat, denom), options.server_lr)
            );
        }

        OptimizerState result;
        result.step = step_inputs.step;
        result.first_moment = std::move(step_inputs.first_moment);
        result.second_moment = std::move(step_inputs.second_moment);
        return result;
    }

    std::string name() const override { return "fedadagrad"; }
};

class FedAdamOptimizer final : public ServerOptimizer {
public:
    OptimizerState apply(
        const ModelManifest& manifest,
        const TensorCollection& aggregated_delta,
        const OptimizerState& previous_state,
        const AggregationOptions& options,
        TensorCollection& out_model_delta
    ) const override {
        auto step_inputs = prepare_fedopt_step(manifest, previous_state);
        out_model_delta = make_zero_collection(manifest);

        for (const auto& descriptor : manifest.tensors) {
            const auto& delta = aggregated_delta.at(descriptor.name);
            step_inputs.first_moment.assign(add(
                scale(step_inputs.first_moment.at(descriptor.name), options.beta1),
                scale(delta, 1.0 - options.beta1)
            ));
            step_inputs.second_moment.assign(add(
                scale(step_inputs.second_moment.at(descriptor.name), options.beta2),
                scale(hadamard_square(delta), 1.0 - options.beta2)
            ));

            const auto m_hat = divide(
                step_inputs.first_moment.at(descriptor.name),
                1.0 - std::pow(options.beta1, static_cast<int>(step_inputs.step))
            );
            const auto v_hat = divide(
                step_inputs.second_moment.at(descriptor.name),
                1.0 - std::pow(options.beta2, static_cast<int>(step_inputs.step))
            );
            const auto denom = add_scalar(hadamard_sqrt(v_hat), options.tau);
            out_model_delta.assign(
                scale(divide_elementwise(m_hat, denom), options.server_lr)
            );
        }

        OptimizerState result;
        result.step = step_inputs.step;
        result.first_moment = std::move(step_inputs.first_moment);
        result.second_moment = std::move(step_inputs.second_moment);
        return result;
    }

    std::string name() const override { return "fedadam"; }
};

class FedYogiOptimizer final : public ServerOptimizer {
public:
    OptimizerState apply(
        const ModelManifest& manifest,
        const TensorCollection& aggregated_delta,
        const OptimizerState& previous_state,
        const AggregationOptions& options,
        TensorCollection& out_model_delta
    ) const override {
        auto step_inputs = prepare_fedopt_step(manifest, previous_state);
        out_model_delta = make_zero_collection(manifest);

        for (const auto& descriptor : manifest.tensors) {
            const auto& delta = aggregated_delta.at(descriptor.name);
            step_inputs.first_moment.assign(add(
                scale(step_inputs.first_moment.at(descriptor.name), options.beta1),
                scale(delta, 1.0 - options.beta1)
            ));

            // Yogi's second moment moves toward grad^2 by a signed step
            // rather than Adam's convex combination, which keeps it more
            // stable when gradients are heavy-tailed.
            const auto grad_sq = hadamard_square(delta);
            auto descriptor_copy = grad_sq.descriptor();
            std::vector<double> values;
            values.reserve(grad_sq.values().size());
            for (std::size_t index = 0; index < grad_sq.values().size(); ++index) {
                const auto previous_v = step_inputs.second_moment.at(descriptor.name).values()[index];
                const auto current = grad_sq.values()[index];
                const auto sign = ((previous_v - current) > 0.0) - ((previous_v - current) < 0.0);
                values.push_back(previous_v - (1.0 - options.beta2) * sign * current);
            }
            step_inputs.second_moment.assign(TensorBuffer(std::move(descriptor_copy), std::move(values)));

            const auto m_hat = divide(
                step_inputs.first_moment.at(descriptor.name),
                1.0 - std::pow(options.beta1, static_cast<int>(step_inputs.step))
            );
            const auto v_hat = divide(
                step_inputs.second_moment.at(descriptor.name),
                1.0 - std::pow(options.beta2, static_cast<int>(step_inputs.step))
            );
            const auto denom = add_scalar(hadamard_sqrt(v_hat), options.tau);
            out_model_delta.assign(
                scale(divide_elementwise(m_hat, denom), options.server_lr)
            );
        }

        OptimizerState result;
        result.step = step_inputs.step;
        result.first_moment = std::move(step_inputs.first_moment);
        result.second_moment = std::move(step_inputs.second_moment);
        return result;
    }

    std::string name() const override { return "fedyogi"; }
};

class FedOptAggregator final : public Aggregator {
public:
    explicit FedOptAggregator(AggregationAlgorithm algorithm)
        : algorithm_(algorithm), optimizer_(make_server_optimizer(algorithm)) {}

    AggregationResult aggregate(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        const OptimizerState& previous_state
    ) const override {
        UpdateValidator().validate_cohort(manifest, updates, options, false);
        if (options.server_lr <= 0.0) {
            throw std::invalid_argument("server_lr must be positive");
        }
        if (options.tau < 0.0) {
            throw std::invalid_argument("tau must be non-negative");
        }

        auto weighting = make_weighting_strategy(options.weighting);
        const auto aggregate_delta = weighted_average(
            manifest, updates, weighting->weights(updates, options)
        );

        TensorCollection model_delta;
        auto optimizer_state = optimizer_->apply(
            manifest, aggregate_delta, previous_state, options, model_delta
        );

        AggregationResult result;
        result.model_delta = std::move(model_delta);
        result.optimizer_state = std::move(optimizer_state);
        return result;
    }

private:
    AggregationAlgorithm algorithm_;
    std::unique_ptr<ServerOptimizer> optimizer_;
};

}  // namespace

std::unique_ptr<ServerOptimizer> make_server_optimizer(AggregationAlgorithm algorithm) {
    switch (algorithm) {
        case AggregationAlgorithm::kFedAdagrad:
            return std::make_unique<FedAdagradOptimizer>();
        case AggregationAlgorithm::kFedAdam:
            return std::make_unique<FedAdamOptimizer>();
        case AggregationAlgorithm::kFedYogi:
            return std::make_unique<FedYogiOptimizer>();
        default:
            throw std::invalid_argument("unsupported server optimizer");
    }
}

void UpdateValidator::validate_cohort(
    const ModelManifest& manifest,
    const std::vector<ClientUpdate>& updates,
    const AggregationOptions& options,
    bool require_control
) const {
    if (manifest.model_id.empty() || manifest.model_version.empty()) {
        throw std::invalid_argument("model manifest identity must not be empty");
    }
    if (updates.empty()) {
        throw std::invalid_argument("updates must not be empty");
    }
    std::unordered_set<std::string> client_ids;
    std::unordered_set<std::string> update_ids;
    std::unordered_set<std::string> nonces;
    for (const auto& update : updates) {
        validate_manifest_against_update(manifest, update, options, require_control);
        if (!client_ids.insert(update.client_id).second) {
            throw std::invalid_argument("duplicate client_id in aggregation cohort");
        }
        if (!update_ids.insert(update.update_id).second) {
            throw std::invalid_argument("duplicate update_id in aggregation cohort");
        }
        if (!nonces.insert(update.nonce).second) {
            throw std::invalid_argument("duplicate nonce in aggregation cohort");
        }
    }
}

std::unique_ptr<Aggregator> AggregatorRegistry::create(
    AggregationAlgorithm algorithm
) const {
    return make_aggregator(algorithm);
}

std::unique_ptr<Aggregator> make_aggregator(AggregationAlgorithm algorithm) {
    switch (algorithm) {
        case AggregationAlgorithm::kFedAvg:
        case AggregationAlgorithm::kFedProx:
            return std::make_unique<WeightedAggregator>();
        case AggregationAlgorithm::kScaffold:
            return std::make_unique<ScaffoldAggregator>();
        case AggregationAlgorithm::kFedAdagrad:
        case AggregationAlgorithm::kFedAdam:
        case AggregationAlgorithm::kFedYogi:
            return std::make_unique<FedOptAggregator>(algorithm);
        default:
            throw std::invalid_argument("unsupported aggregation algorithm");
    }
}

std::string to_string(AggregationAlgorithm algorithm) {
    switch (algorithm) {
        case AggregationAlgorithm::kFedAvg:
            return "fedavg";
        case AggregationAlgorithm::kFedProx:
            return "fedprox";
        case AggregationAlgorithm::kScaffold:
            return "scaffold";
        case AggregationAlgorithm::kFedAdagrad:
            return "fedadagrad";
        case AggregationAlgorithm::kFedAdam:
            return "fedadam";
        case AggregationAlgorithm::kFedYogi:
            return "fedyogi";
        default:
            return "unknown";
    }
}

std::string to_string(WeightingStrategyType strategy) {
    switch (strategy) {
        case WeightingStrategyType::kSampleCount:
            return "sample_count";
        case WeightingStrategyType::kUniform:
            return "uniform";
        case WeightingStrategyType::kCappedSampleCount:
            return "capped_sample_count";
        case WeightingStrategyType::kNormalizedBounded:
            return "normalized_bounded";
        default:
            return "unknown";
    }
}

}  // namespace fl::core
