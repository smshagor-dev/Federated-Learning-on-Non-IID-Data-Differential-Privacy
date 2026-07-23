#pragma once

#include "fl_core/tensor.hpp"

#include <cstdint>
#include <memory>
#include <optional>
#include <set>
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

enum class WeightingStrategyType {
    kSampleCount,
    kUniform,
    kCappedSampleCount,
    kNormalizedBounded,
};

struct ModelManifest {
    std::string model_id;
    std::string model_version;
    std::vector<TensorDescriptor> tensors;
};

struct ClientUpdate {
    std::string run_id;
    std::uint64_t round_id{0};
    std::string client_id;
    std::string update_id;
    std::string nonce;
    std::string worker_id;
    std::string base_model_version;
    AggregationAlgorithm algorithm{AggregationAlgorithm::kFedAvg};
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
    std::string run_id;
    std::uint64_t round_id{0};
    std::size_t total_clients{1};
    WeightingStrategyType weighting{WeightingStrategyType::kSampleCount};
    double contribution_cap{1.0};
    double minimum_weight{0.0};
    double maximum_weight{1.0};
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

class WeightingStrategy {
public:
    virtual ~WeightingStrategy() = default;
    [[nodiscard]] virtual std::vector<double> weights(
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options
    ) const = 0;
    [[nodiscard]] virtual std::string name() const = 0;
};

class UpdateValidator {
public:
    void validate_cohort(
        const ModelManifest& manifest,
        const std::vector<ClientUpdate>& updates,
        const AggregationOptions& options,
        bool require_control
    ) const;
};

class AggregatorRegistry {
public:
    [[nodiscard]] std::unique_ptr<Aggregator> create(
        AggregationAlgorithm algorithm
    ) const;
};

// A ServerOptimizer owns the per-tensor moment update and resulting model
// delta for a single FedOpt variant (FedAdagrad, FedAdam, FedYogi). Each
// variant is its own class rather than a branch in a shared function so
// that the moment-update formulas stay independently readable and testable.
class ServerOptimizer {
public:
    virtual ~ServerOptimizer() = default;

    virtual OptimizerState apply(
        const ModelManifest& manifest,
        const TensorCollection& aggregated_delta,
        const OptimizerState& previous_state,
        const AggregationOptions& options,
        TensorCollection& out_model_delta
    ) const = 0;

    [[nodiscard]] virtual std::string name() const = 0;
};

std::unique_ptr<ServerOptimizer> make_server_optimizer(AggregationAlgorithm algorithm);

std::unique_ptr<Aggregator> make_aggregator(AggregationAlgorithm algorithm);
std::string to_string(AggregationAlgorithm algorithm);
std::string to_string(WeightingStrategyType strategy);

}  // namespace fl::core
