#include "fl_core/aggregation.hpp"

#include <functional>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

fl::core::TensorDescriptor descriptor(const std::string& name = "weight") {
    return fl::core::TensorDescriptor{
        .name = name,
        .shape = {2},
        .dtype = fl::core::DType::kFloat32,
    };
}

fl::core::ModelManifest manifest() {
    return fl::core::ModelManifest{
        .model_id = "model-1",
        .model_version = "v1",
        .tensors = {descriptor()},
    };
}

fl::core::ClientUpdate update(const std::string& client_id) {
    fl::core::ClientUpdate item;
    item.run_id = "run-1";
    item.round_id = 7;
    item.client_id = client_id;
    item.update_id = "update-" + client_id;
    item.nonce = "nonce-" + client_id;
    item.worker_id = "worker-" + client_id;
    item.base_model_version = "v1";
    item.algorithm = fl::core::AggregationAlgorithm::kFedAvg;
    item.sample_count = 10;
    item.delta.insert(fl::core::TensorBuffer(descriptor(), {1.0, 2.0}));
    return item;
}

fl::core::AggregationOptions options() {
    fl::core::AggregationOptions value;
    value.run_id = "run-1";
    value.round_id = 7;
    return value;
}

bool rejects(const std::function<void()>& action) {
    try {
        action();
    } catch (const std::invalid_argument&) {
        return true;
    }
    return false;
}

int require_rejection(const std::string& name, const std::function<void()>& action) {
    if (rejects(action)) {
        return 0;
    }
    std::cerr << "expected rejection: " << name << "\n";
    return 1;
}

}  // namespace

int main() {
    const auto model_manifest = manifest();
    const auto aggregator = fl::core::make_aggregator(
        fl::core::AggregationAlgorithm::kFedAvg
    );
    int failures = 0;

    failures += require_rejection("duplicate client id", [&]() {
        auto first = update("c1");
        auto second = update("c1");
        second.update_id = "update-c2";
        second.nonce = "nonce-c2";
        aggregator->aggregate(model_manifest, {first, second}, options(), {});
    });

    failures += require_rejection("duplicate update id", [&]() {
        auto first = update("c1");
        auto second = update("c2");
        second.update_id = first.update_id;
        aggregator->aggregate(model_manifest, {first, second}, options(), {});
    });

    failures += require_rejection("duplicate nonce", [&]() {
        auto first = update("c1");
        auto second = update("c2");
        second.nonce = first.nonce;
        aggregator->aggregate(model_manifest, {first, second}, options(), {});
    });

    failures += require_rejection("stale model version", [&]() {
        auto item = update("c1");
        item.base_model_version = "v0";
        aggregator->aggregate(model_manifest, {item}, options(), {});
    });

    failures += require_rejection("zero sample count", [&]() {
        auto item = update("c1");
        item.sample_count = 0;
        aggregator->aggregate(model_manifest, {item}, options(), {});
    });

    failures += require_rejection("unexpected tensor", [&]() {
        auto item = update("c1");
        item.delta.insert(fl::core::TensorBuffer(descriptor("bias"), {1.0, 2.0}));
        aggregator->aggregate(model_manifest, {item}, options(), {});
    });

    failures += require_rejection("non-finite tensor", [&]() {
        auto item = update("c1");
        item.delta = fl::core::TensorCollection{};
        item.delta.insert(fl::core::TensorBuffer(
            descriptor(),
            {1.0, std::numeric_limits<double>::infinity()}
        ));
        aggregator->aggregate(model_manifest, {item}, options(), {});
    });

    failures += require_rejection("invalid cap", [&]() {
        auto item = update("c1");
        auto opts = options();
        opts.weighting = fl::core::WeightingStrategyType::kCappedSampleCount;
        opts.contribution_cap = 0.0;
        aggregator->aggregate(model_manifest, {item}, opts, {});
    });

    return failures == 0 ? 0 : 1;
}
