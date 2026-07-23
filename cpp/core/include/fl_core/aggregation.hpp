#pragma once

#include "fl_core\tensor.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <vector>

namespace fl::core {

enum class AggregationAlgorithm {
    kFedAvg,
    kFedProx,
    kScaffold,
    kFedAdagrad,
    kFedAdam,
    kFedYogi,
};

struct ModelManifest {
    std::string model_id;
    std::string model_version;
    std::vector<TensorDescriptor> tensors;
};

struct ClientUpdate {
    std::string client_id;
    std::string base_model_version;
    std::uint64_t sample_count{0};
    TensorCollection delta;
    TensorCollection control_delta;
};

struct OptimizerState {
    std::uint64_t step{0};
    TensorCollection first_moment;
    TensorCollection second_moment;
};

struct AggregationOptions {
    AggregationAlgorithm algorithm{AggregationAlgorithm::kFedAvg};
    std::size_t total_clients{1};
    double server_lr{1.0};
    double beta1{0.9};
    double beta2{0.99};
    double tau{1e-3};
};

struct AggregationResult {
    TensorCollection model_delta;
    TensorCollection control_delta;
    OptimizerState optimizer_state;
};

class Aggregator {
public:
    virtual ~Aggregator() = default;

    virtual AggregationResult aggregate(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        const OptimizerState& previous_state
    ) const = 0;
};

std::unique_ptr<Aggregator> make_aggregator(AggregationAlgorithm algorithm);
std::string to_string(AggregationAlgorithm algorithm);

}  // namespace fl::core
