#include "fl_core/aggregation.hpp"

#include <iomanip>
#include <iostream>

namespace {

fl::core::ModelManifest make_manifest() {
    return fl::core::ModelManifest{
        .model_id = "toy-model",
        .model_version = "v1",
        .tensors = {
            fl::core::TensorDescriptor{
                .name = "weight",
                .shape = {1},
                .dtype = fl::core::DType::kFloat32,
            },
        },
    };
}

fl::core::ClientUpdate make_update(
    const std::string& client_id,
    std::uint64_t sample_count,
    double delta_value,
    double control_value = 0.0
) {
    const auto descriptor = fl::core::TensorDescriptor{
        .name = "weight",
        .shape = {1},
        .dtype = fl::core::DType::kFloat32,
    };
    fl::core::ClientUpdate update;
    update.client_id = client_id;
    update.base_model_version = "v1";
    update.sample_count = sample_count;
    update.delta.insert(fl::core::TensorBuffer(descriptor, {delta_value}));
    update.control_delta.insert(fl::core::TensorBuffer(descriptor, {control_value}));
    return update;
}

double extract_weight(const fl::core::TensorCollection& collection) {
    return collection.at("weight").values().at(0);
}

}  // namespace

int main() {
    const auto manifest = make_manifest();
    const std::vector<fl::core::ClientUpdate> weighted_updates = {
        make_update("c1", 3, 1.0),
        make_update("c2", 1, 0.0),
    };
    const std::vector<fl::core::ClientUpdate> scaffold_updates = {
        make_update("c1", 3, 1.0, 0.4),
        make_update("c2", 1, 3.0, 0.8),
    };
    const std::vector<fl::core::ClientUpdate> opt_round_one = {
        make_update("c1", 3, 1.0),
        make_update("c2", 1, 0.0),
    };
    const std::vector<fl::core::ClientUpdate> opt_round_two = {
        make_update("c1", 2, 0.5),
        make_update("c2", 2, 0.0),
    };

    std::cout << std::fixed << std::setprecision(6);

    {
        auto aggregator = fl::core::make_aggregator(fl::core::AggregationAlgorithm::kFedAvg);
        const auto result = aggregator->aggregate(
            manifest, weighted_updates, fl::core::AggregationOptions{}, fl::core::OptimizerState{}
        );
        std::cout << "fedavg=" << extract_weight(result.model_delta) << "\n";
    }
    {
        auto aggregator = fl::core::make_aggregator(fl::core::AggregationAlgorithm::kFedProx);
        const auto result = aggregator->aggregate(
            manifest, weighted_updates, fl::core::AggregationOptions{}, fl::core::OptimizerState{}
        );
        std::cout << "fedprox=" << extract_weight(result.model_delta) << "\n";
    }
    {
        auto aggregator = fl::core::make_aggregator(fl::core::AggregationAlgorithm::kScaffold);
        fl::core::AggregationOptions options;
        options.total_clients = 10;
        const auto result = aggregator->aggregate(
            manifest, scaffold_updates, options, fl::core::OptimizerState{}
        );
        std::cout << "scaffold_delta=" << extract_weight(result.model_delta) << "\n";
        std::cout << "scaffold_control=" << extract_weight(result.control_delta) << "\n";
    }

    for (const auto algorithm : {
            fl::core::AggregationAlgorithm::kFedAdagrad,
            fl::core::AggregationAlgorithm::kFedAdam,
            fl::core::AggregationAlgorithm::kFedYogi,
        }) {
        auto aggregator = fl::core::make_aggregator(algorithm);
        fl::core::AggregationOptions options;
        options.algorithm = algorithm;
        options.server_lr = 1.0;
        options.beta1 = algorithm == fl::core::AggregationAlgorithm::kFedAdagrad ? 0.0 : 0.9;
        options.beta2 = 0.99;
        options.tau = 1.0;

        auto state = fl::core::OptimizerState{};
        auto round_one = aggregator->aggregate(manifest, opt_round_one, options, state);
        auto round_two = aggregator->aggregate(manifest, opt_round_two, options, round_one.optimizer_state);
        std::cout << fl::core::to_string(algorithm) << "_round1=" << extract_weight(round_one.model_delta) << "\n";
        std::cout << fl::core::to_string(algorithm) << "_round2=" << extract_weight(round_two.model_delta) << "\n";
    }

    return 0;
}
