#include "fl_core/aggregation.hpp"
#include "fl_core/build_info.hpp"
#include "fl_core/coordinator.hpp"

#include <cmath>
#include <iostream>
#include <stdexcept>

namespace {

fl::core::ModelManifest make_manifest() {
    return fl::core::ModelManifest{
        .model_id = "toy",
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
    double delta,
    double control = 0.0,
    fl::core::AggregationAlgorithm algorithm = fl::core::AggregationAlgorithm::kFedAvg
) {
    const auto descriptor = fl::core::TensorDescriptor{
        .name = "weight",
        .shape = {1},
        .dtype = fl::core::DType::kFloat32,
    };
    fl::core::ClientUpdate update;
    update.run_id = "run-smoke";
    update.round_id = 1;
    update.client_id = client_id;
    update.update_id = "update-" + client_id;
    update.nonce = "nonce-" + client_id;
    update.worker_id = "worker-" + client_id;
    update.base_model_version = "v1";
    update.algorithm = algorithm;
    update.sample_count = sample_count;
    update.delta.insert(fl::core::TensorBuffer(descriptor, {delta}));
    update.control_delta.insert(fl::core::TensorBuffer(descriptor, {control}));
    return update;
}

void ensure_close(double actual, double expected, double tolerance, const std::string& label) {
    if (std::abs(actual - expected) > tolerance) {
        throw std::runtime_error(label + " mismatch");
    }
}

}  // namespace

int main() {
    const auto build = fl::core::current_build();
    if (build.name.empty() || build.version.empty()) {
        std::cerr << "build metadata missing\n";
        return 1;
    }

    const auto manifest = make_manifest();
    const std::vector<fl::core::ClientUpdate> weighted_updates = {
        make_update("c1", 3, 1.0),
        make_update("c2", 1, 0.0),
    };
    const std::vector<fl::core::ClientUpdate> scaffold_updates = {
        make_update("c1", 3, 1.0, 0.4),
        make_update("c2", 1, 3.0, 0.8),
    };

    {
        auto aggregator = fl::core::make_aggregator(fl::core::AggregationAlgorithm::kFedAvg);
        fl::core::AggregationOptions options;
        options.run_id = "run-smoke";
        options.round_id = 1;
        const auto result = aggregator->aggregate(
            manifest, weighted_updates, options, fl::core::OptimizerState{}
        );
        ensure_close(result.model_delta.at("weight").values().at(0), 0.75, 1e-9, "fedavg");
    }

    {
        auto aggregator = fl::core::make_aggregator(fl::core::AggregationAlgorithm::kScaffold);
        auto scaffold_algorithm_updates = scaffold_updates;
        for (auto& update : scaffold_algorithm_updates) {
            update.algorithm = fl::core::AggregationAlgorithm::kScaffold;
        }
        fl::core::AggregationOptions options;
        options.algorithm = fl::core::AggregationAlgorithm::kScaffold;
        options.run_id = "run-smoke";
        options.round_id = 1;
        options.total_clients = 10;
        const auto result = aggregator->aggregate(
            manifest, scaffold_algorithm_updates, options, fl::core::OptimizerState{}
        );
        ensure_close(result.model_delta.at("weight").values().at(0), 2.0, 1e-9, "scaffold delta");
        ensure_close(result.control_delta.at("weight").values().at(0), 0.12, 1e-9, "scaffold control");
    }

    {
        fl::core::RunStateMachine machine;
        machine.transition_to(fl::core::RunState::kValidating, "unit-test", "2026-07-22T19:45:00Z");
        machine.transition_to(fl::core::RunState::kInitializing, "unit-test", "2026-07-22T19:45:01Z");
        machine.transition_to(fl::core::RunState::kReady, "unit-test", "2026-07-22T19:45:02Z");
        if (machine.state() != fl::core::RunState::kReady) {
            throw std::runtime_error("state machine mismatch");
        }
    }

    std::cout << build.name << " " << build.version << "\n";
    return 0;
}
