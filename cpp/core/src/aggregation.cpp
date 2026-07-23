#include "fl_core/aggregation.hpp"

#include <cmath>
#include <stdexcept>

namespace fl::core {

namespace {

void validate_manifest_against_update(
    const ModelManifest& manifest,
    const ClientUpdate& update,
    bool require_control
) {
    if (update.sample_count == 0) {
        throw std::invalid_argument("sample_count must be positive");
    }
    for (const auto& descriptor : manifest.tensors) {
        if (!update.delta.contains(descriptor.name)) {
            throw std::invalid_argument("missing tensor in client delta");
        }
        update.delta.at(descriptor.name).validate();
        if (require_control && !update.control_delta.contains(descriptor.name)) {
            throw std::invalid_argument("missing control tensor in client update");
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
    bool uniform
) {
    if (updates.empty()) {
        throw std::invalid_argument("updates must not be empty");
    }

    TensorCollection aggregate = make_zero_collection(manifest);
    double total_weight = 0.0;
    for (const auto& update : updates) {
        total_weight += uniform ? 1.0 : static_cast<double>(update.sample_count);
    }

    for (const auto& update : updates) {
        const double weight = (uniform ? 1.0 : static_cast<double>(update.sample_count)) / total_weight;
        for (const auto& descriptor : manifest.tensors) {
            const auto scaled = scale(update.delta.at(descriptor.name), weight);
            aggregate.insert(add(aggregate.at(descriptor.name), scaled));
        }
    }

    return aggregate;
}

class WeightedAggregator final : public Aggregator {
public:
    AggregationResult aggregate(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        const OptimizerState& previous_state
    ) const override {
        for (const auto& update : updates) {
            validate_manifest_against_update(manifest, update, false);
        }
        AggregationResult result;
        result.model_delta = weighted_average(manifest, updates, false);
        for (const auto& descriptor : manifest.tensors) {
            result.model_delta.insert(
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
        for (const auto& update : updates) {
            validate_manifest_against_update(manifest, update, true);
        }
        AggregationResult result;
        result.model_delta = weighted_average(manifest, updates, true);
        for (const auto& descriptor : manifest.tensors) {
            result.model_delta.insert(
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
            true
        );
        const auto control_scale = static_cast<double>(updates.size()) /
            static_cast<double>(options.total_clients);
        for (const auto& descriptor : manifest.tensors) {
            result.control_delta.insert(
                scale(control_average.at(descriptor.name), control_scale)
            );
        }
        result.optimizer_state = previous_state;
        return result;
    }
};

class FedOptAggregator final : public Aggregator {
public:
    explicit FedOptAggregator(AggregationAlgorithm algorithm) : algorithm_(algorithm) {}

    AggregationResult aggregate(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        const OptimizerState& previous_state
    ) const override {
        for (const auto& update : updates) {
            validate_manifest_against_update(manifest, update, false);
        }
        if (options.server_lr <= 0.0) {
            throw std::invalid_argument("server_lr must be positive");
        }
        if (options.tau < 0.0) {
            throw std::invalid_argument("tau must be non-negative");
        }

        const auto aggregate_delta = weighted_average(manifest, updates, false);
        TensorCollection first = previous_state.first_moment.empty()
            ? make_zero_collection(manifest)
            : previous_state.first_moment;
        TensorCollection second = previous_state.second_moment.empty()
            ? make_zero_collection(manifest)
            : previous_state.second_moment;

        const auto step = previous_state.step + 1;
        TensorCollection model_delta = make_zero_collection(manifest);

        for (const auto& descriptor : manifest.tensors) {
            const auto& delta = aggregate_delta.at(descriptor.name);
            const auto first_new = add(
                scale(first.at(descriptor.name), options.beta1),
                scale(delta, 1.0 - options.beta1)
            );
            first.insert(first_new);

            TensorBuffer second_new = second.at(descriptor.name);
            if (algorithm_ == AggregationAlgorithm::kFedAdagrad) {
                second_new = add(second.at(descriptor.name), hadamard_square(delta));
            } else if (algorithm_ == AggregationAlgorithm::kFedAdam) {
                second_new = add(
                    scale(second.at(descriptor.name), options.beta2),
                    scale(hadamard_square(delta), 1.0 - options.beta2)
                );
            } else {
                const auto grad_sq = hadamard_square(delta);
                auto descriptor_copy = grad_sq.descriptor();
                std::vector<double> values;
                values.reserve(grad_sq.values().size());
                for (std::size_t index = 0; index < grad_sq.values().size(); ++index) {
                    const auto previous = second.at(descriptor.name).values()[index];
                    const auto current = grad_sq.values()[index];
                    const auto sign = ((previous - current) > 0.0) - ((previous - current) < 0.0);
                    values.push_back(previous - (1.0 - options.beta2) * sign * current);
                }
                second_new = TensorBuffer(std::move(descriptor_copy), std::move(values));
            }
            second.insert(second_new);

            TensorBuffer m_hat = first.at(descriptor.name);
            TensorBuffer v_hat = second.at(descriptor.name);
            if (algorithm_ != AggregationAlgorithm::kFedAdagrad) {
                m_hat = divide(m_hat, 1.0 - std::pow(options.beta1, static_cast<int>(step)));
                v_hat = divide(v_hat, 1.0 - std::pow(options.beta2, static_cast<int>(step)));
            }

            const auto denom = add_scalar(hadamard_sqrt(v_hat), options.tau);
            model_delta.insert(
                scale(divide_elementwise(m_hat, denom), options.server_lr)
            );
        }

        AggregationResult result;
        result.model_delta = std::move(model_delta);
        result.optimizer_state.step = step;
        result.optimizer_state.first_moment = std::move(first);
        result.optimizer_state.second_moment = std::move(second);
        return result;
    }

private:
    AggregationAlgorithm algorithm_;
};

}  // namespace

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

}  // namespace fl::core
