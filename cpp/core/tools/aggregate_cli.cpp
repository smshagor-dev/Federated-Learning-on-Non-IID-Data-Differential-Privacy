// Small line-based CLI compatibility runner for the C++ aggregation core.
//
// This exists so the Python compatibility adapter
// (python/src/fl_platform/compat/cpp_bridge.py) can invoke the exact same
// validated aggregation path used in production without requiring gRPC or
// pybind11 bindings. It reads a request from stdin, runs it through
// fl::core::make_aggregator, and writes the result to stdout in the same
// line-based "key=value" / "name|dtype|shape|values" format described in
// docs/tensor-format.md.
#include "fl_core/aggregation.hpp"

#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace {

using fl::core::AggregationAlgorithm;
using fl::core::AggregationOptions;
using fl::core::ClientUpdate;
using fl::core::DType;
using fl::core::ModelManifest;
using fl::core::OptimizerState;
using fl::core::TensorBuffer;
using fl::core::TensorCollection;
using fl::core::TensorDescriptor;
using fl::core::WeightingStrategyType;

// Splits on every occurrence of `delimiter`, including a trailing empty
// field (e.g. "a|b|" -> {"a", "b", ""}). std::getline-based splitting drops
// that trailing empty field, which matters here because manifest tensor
// descriptors intentionally encode an empty values segment.
std::vector<std::string> split(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::size_t start = 0;
    while (true) {
        const auto position = value.find(delimiter, start);
        if (position == std::string::npos) {
            parts.push_back(value.substr(start));
            break;
        }
        parts.push_back(value.substr(start, position - start));
        start = position + 1;
    }
    return parts;
}

DType parse_dtype(const std::string& tag) {
    if (tag == "f32") {
        return DType::kFloat32;
    }
    throw std::invalid_argument("unsupported dtype tag: " + tag);
}

std::string dtype_tag(DType dtype) {
    switch (dtype) {
        case DType::kFloat32:
            return "f32";
        default:
            throw std::invalid_argument("unsupported dtype");
    }
}

// Parses "name|dtype|shape-dash-joined|values-comma-joined" into just the
// descriptor portion, ignoring any values segment. Used for bare manifest
// tensor descriptors, which have no associated values.
TensorDescriptor parse_tensor_descriptor_field(const std::string& field) {
    const auto parts = split(field, '|');
    if (parts.size() != 4) {
        throw std::invalid_argument("malformed tensor field: " + field);
    }
    TensorDescriptor descriptor;
    descriptor.name = parts[0];
    descriptor.dtype = parse_dtype(parts[1]);
    if (!parts[2].empty()) {
        for (const auto& dim : split(parts[2], '-')) {
            descriptor.shape.push_back(std::stoull(dim));
        }
    }
    return descriptor;
}

// Parses a full tensor field including its values segment, which must be
// present and consistent with the descriptor's element count.
TensorBuffer parse_tensor_field(const std::string& field) {
    const auto parts = split(field, '|');
    if (parts.size() != 4) {
        throw std::invalid_argument("malformed tensor field: " + field);
    }
    auto descriptor = parse_tensor_descriptor_field(field);
    std::vector<double> values;
    if (!parts[3].empty()) {
        for (const auto& raw : split(parts[3], ',')) {
            values.push_back(std::stod(raw));
        }
    }
    if (values.empty()) {
        throw std::invalid_argument("tensor field missing values: " + field);
    }
    return TensorBuffer(std::move(descriptor), std::move(values));
}

std::string write_tensor_field(const std::string& name, const TensorBuffer& tensor) {
    std::ostringstream out;
    out << name << "|" << dtype_tag(tensor.descriptor().dtype) << "|";
    const auto& shape = tensor.descriptor().shape;
    for (std::size_t index = 0; index < shape.size(); ++index) {
        if (index > 0) {
            out << "-";
        }
        out << shape[index];
    }
    out << "|";
    const auto& values = tensor.values();
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index > 0) {
            out << ",";
        }
        out << std::setprecision(17) << values[index];
    }
    return out.str();
}

void write_collection(std::ostream& out, const std::string& key, const TensorCollection& collection) {
    for (const auto& [name, tensor] : collection.tensors()) {
        out << key << "=" << write_tensor_field(name, tensor) << "\n";
    }
}

AggregationAlgorithm parse_algorithm(const std::string& value) {
    if (value == "fedavg") return AggregationAlgorithm::kFedAvg;
    if (value == "fedprox") return AggregationAlgorithm::kFedProx;
    if (value == "scaffold") return AggregationAlgorithm::kScaffold;
    if (value == "fedadagrad") return AggregationAlgorithm::kFedAdagrad;
    if (value == "fedadam") return AggregationAlgorithm::kFedAdam;
    if (value == "fedyogi") return AggregationAlgorithm::kFedYogi;
    throw std::invalid_argument("unknown algorithm: " + value);
}

WeightingStrategyType parse_weighting(const std::string& value) {
    if (value == "sample_count") return WeightingStrategyType::kSampleCount;
    if (value == "uniform") return WeightingStrategyType::kUniform;
    if (value == "capped_sample_count") return WeightingStrategyType::kCappedSampleCount;
    if (value == "normalized_bounded") return WeightingStrategyType::kNormalizedBounded;
    throw std::invalid_argument("unknown weighting strategy: " + value);
}

struct Request {
    ModelManifest manifest;
    std::vector<ClientUpdate> updates;
    AggregationOptions options;
    OptimizerState previous_state;
};

Request parse_request(std::istream& in) {
    Request request;
    std::string line;
    ClientUpdate* current_update = nullptr;

    while (std::getline(in, line)) {
        if (line.empty() || line == "\r") {
            continue;
        }
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line == "update_begin") {
            request.updates.emplace_back();
            current_update = &request.updates.back();
            continue;
        }
        if (line == "update_end") {
            current_update = nullptr;
            continue;
        }
        const auto position = line.find('=');
        if (position == std::string::npos) {
            throw std::invalid_argument("invalid request line: " + line);
        }
        const auto key = line.substr(0, position);
        const auto value = line.substr(position + 1);

        if (current_update != nullptr) {
            if (key == "client_id") current_update->client_id = value;
            else if (key == "update_id") current_update->update_id = value;
            else if (key == "nonce") current_update->nonce = value;
            else if (key == "worker_id") current_update->worker_id = value;
            else if (key == "base_model_version") current_update->base_model_version = value;
            else if (key == "run_id") current_update->run_id = value;
            else if (key == "round_id") current_update->round_id = std::stoull(value);
            else if (key == "algorithm") current_update->algorithm = parse_algorithm(value);
            else if (key == "sample_count") current_update->sample_count = std::stoull(value);
            else if (key == "delta") current_update->delta.insert(parse_tensor_field(value));
            else if (key == "control_delta") current_update->control_delta.insert(parse_tensor_field(value));
            else throw std::invalid_argument("unknown update field: " + key);
            continue;
        }

        if (key == "algorithm") request.options.algorithm = parse_algorithm(value);
        else if (key == "weighting") request.options.weighting = parse_weighting(value);
        else if (key == "run_id") request.options.run_id = value;
        else if (key == "round_id") request.options.round_id = std::stoull(value);
        else if (key == "total_clients") request.options.total_clients = std::stoull(value);
        else if (key == "contribution_cap") request.options.contribution_cap = std::stod(value);
        else if (key == "minimum_weight") request.options.minimum_weight = std::stod(value);
        else if (key == "maximum_weight") request.options.maximum_weight = std::stod(value);
        else if (key == "server_lr") request.options.server_lr = std::stod(value);
        else if (key == "beta1") request.options.beta1 = std::stod(value);
        else if (key == "beta2") request.options.beta2 = std::stod(value);
        else if (key == "tau") request.options.tau = std::stod(value);
        else if (key == "model_id") request.manifest.model_id = value;
        else if (key == "model_version") request.manifest.model_version = value;
        else if (key == "manifest_tensor") {
            request.manifest.tensors.push_back(parse_tensor_descriptor_field(value));
        } else if (key == "prev_step") {
            request.previous_state.step = std::stoull(value);
        } else if (key == "prev_first") {
            request.previous_state.first_moment.insert(parse_tensor_field(value));
        } else if (key == "prev_second") {
            request.previous_state.second_moment.insert(parse_tensor_field(value));
        } else {
            throw std::invalid_argument("unknown request field: " + key);
        }
    }
    return request;
}

}  // namespace

int main() {
    try {
        const auto request = parse_request(std::cin);
        const auto aggregator = fl::core::make_aggregator(request.options.algorithm);
        const auto result = aggregator->aggregate(
            request.manifest, request.updates, request.options, request.previous_state
        );

        std::cout << "status=ok\n";
        write_collection(std::cout, "model_delta", result.model_delta);
        write_collection(std::cout, "control_delta", result.control_delta);
        std::cout << "optimizer_step=" << result.optimizer_state.step << "\n";
        write_collection(std::cout, "first_moment", result.optimizer_state.first_moment);
        write_collection(std::cout, "second_moment", result.optimizer_state.second_moment);
        return 0;
    } catch (const std::exception& error) {
        std::cout << "status=error\n";
        std::cout << "message=" << error.what() << "\n";
        return 1;
    }
}
