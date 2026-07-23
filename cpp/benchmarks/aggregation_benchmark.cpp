// Benchmark harness for fl::core aggregation.
//
// This is a small std::chrono-based harness rather than a vendored Google
// Benchmark dependency: this repository has no package manager configured
// for C++ (no vcpkg/conan lockfile) and pulling Google Benchmark via CMake
// FetchContent would add a multi-minute source build to every benchmark
// invocation. The measurement methodology (warm-up + repeated timed runs,
// median/mean reporting) mirrors what Google Benchmark would report, and
// the CMake target is named so it can be swapped for a real
// benchmark::State-based harness later without changing how it's invoked.
// See docs/benchmarking.md for full methodology and how to interpret
// results.
#include "fl_core/aggregation.hpp"
#include "fl_core/checkpoint.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

using fl::core::AggregationAlgorithm;
using fl::core::AggregationOptions;
using fl::core::AggregatorCheckpoint;
using fl::core::AggregatorCheckpointStore;
using fl::core::ClientUpdate;
using fl::core::ModelManifest;
using fl::core::OptimizerState;
using fl::core::TensorBuffer;
using fl::core::TensorDescriptor;
using fl::core::WeightingStrategyType;
using Clock = std::chrono::steady_clock;

struct ModelShape {
    std::string label;
    std::vector<std::uint64_t> tensor_sizes;  // element count per tensor
};

// "cnn_sized" and "resnet18_sized" are deliberately scaled down from real
// CNN (~200K-1M params) and ResNet-18 (~11M params) parameter counts so a
// full 10/100/500-client sweep stays within a few GB of memory on a single
// development machine. See docs/benchmarking.md for the exact rationale.
std::vector<ModelShape> model_shapes() {
    return {
        {"tiny", {256, 256}},
        {"cnn_sized", {50000, 25000, 25000}},
        {"resnet18_sized_approx", {200000, 150000, 100000, 50000}},
    };
}

ModelManifest make_manifest(const ModelShape& shape) {
    ModelManifest manifest;
    manifest.model_id = "bench-model";
    manifest.model_version = "v1";
    for (std::size_t index = 0; index < shape.tensor_sizes.size(); ++index) {
        manifest.tensors.push_back(TensorDescriptor{
            .name = "t" + std::to_string(index),
            .shape = {shape.tensor_sizes[index]},
            .dtype = fl::core::DType::kFloat32,
        });
    }
    return manifest;
}

ClientUpdate make_synthetic_update(
    const ModelManifest& manifest,
    std::size_t client_index,
    AggregationAlgorithm algorithm
) {
    ClientUpdate update;
    update.run_id = "bench-run";
    update.round_id = 1;
    update.client_id = "client-" + std::to_string(client_index);
    update.update_id = "update-" + std::to_string(client_index);
    update.nonce = "nonce-" + std::to_string(client_index);
    update.worker_id = "worker-" + std::to_string(client_index);
    update.base_model_version = "v1";
    update.algorithm = algorithm;
    update.sample_count = 1 + (client_index % 32);

    for (const auto& descriptor : manifest.tensors) {
        std::vector<double> values(descriptor.element_count());
        for (std::size_t index = 0; index < values.size(); ++index) {
            // Deterministic, bounded pseudo-random-looking values; avoids
            // depending on <random> for reproducibility across platforms.
            const auto seed = static_cast<double>((client_index + 1) * 2654435761u + index);
            values[index] = std::sin(seed) * 0.01;
        }
        update.delta.insert(TensorBuffer(descriptor, std::move(values)));
    }
    return update;
}

std::size_t total_bytes(const ModelManifest& manifest, std::size_t client_count) {
    std::size_t per_client = 0;
    for (const auto& descriptor : manifest.tensors) {
        per_client += descriptor.byte_length();
    }
    return per_client * client_count;
}

struct TimingSample {
    double milliseconds;
};

double median(std::vector<double> values) {
    std::sort(values.begin(), values.end());
    return values[values.size() / 2];
}

double mean(const std::vector<double>& values) {
    return std::accumulate(values.begin(), values.end(), 0.0) / static_cast<double>(values.size());
}

struct BenchmarkResult {
    std::string model_size;
    std::size_t client_count;
    std::string algorithm;
    std::string weighting;
    double median_ms;
    double mean_ms;
    double updates_per_second;
    std::size_t bytes_processed;
    double checkpoint_serialize_ms;
    double checkpoint_checksum_validate_ms;
};

BenchmarkResult run_one(
    const ModelShape& shape,
    std::size_t client_count,
    AggregationAlgorithm algorithm,
    WeightingStrategyType weighting,
    int repetitions
) {
    const auto manifest = make_manifest(shape);
    std::vector<ClientUpdate> updates;
    updates.reserve(client_count);
    for (std::size_t index = 0; index < client_count; ++index) {
        updates.push_back(make_synthetic_update(manifest, index, algorithm));
    }

    AggregationOptions options;
    options.algorithm = algorithm;
    options.run_id = "bench-run";
    options.round_id = 1;
    options.total_clients = client_count;
    options.weighting = weighting;
    options.server_lr = 1.0;
    options.beta1 = 0.9;
    options.beta2 = 0.99;
    options.tau = 1e-3;

    const auto aggregator = fl::core::make_aggregator(algorithm);

    // One untimed warm-up run to page in memory and avoid first-call bias.
    auto warm_up = aggregator->aggregate(manifest, updates, options, OptimizerState{});

    std::vector<double> timings_ms;
    timings_ms.reserve(static_cast<std::size_t>(repetitions));
    for (int rep = 0; rep < repetitions; ++rep) {
        const auto start = Clock::now();
        auto result = aggregator->aggregate(manifest, updates, options, OptimizerState{});
        const auto end = Clock::now();
        timings_ms.push_back(std::chrono::duration<double, std::milli>(end - start).count());
        warm_up = std::move(result);  // prevent the optimizer from discarding the call
    }

    AggregatorCheckpoint checkpoint;
    checkpoint.algorithm = algorithm;
    checkpoint.weighting = weighting;
    checkpoint.model_version = manifest.model_version;
    checkpoint.manifest_checksum = fl::core::compute_manifest_checksum(manifest);
    checkpoint.optimizer_state = warm_up.optimizer_state;

    const auto serialize_start = Clock::now();
    const auto payload = AggregatorCheckpointStore::serialize(checkpoint);
    const auto serialize_end = Clock::now();

    const auto validate_start = Clock::now();
    static_cast<void>(AggregatorCheckpointStore::deserialize(payload));
    const auto validate_end = Clock::now();

    const auto median_ms = median(timings_ms);
    const auto bytes = total_bytes(manifest, client_count);

    return BenchmarkResult{
        .model_size = shape.label,
        .client_count = client_count,
        .algorithm = fl::core::to_string(algorithm),
        .weighting = fl::core::to_string(weighting),
        .median_ms = median_ms,
        .mean_ms = mean(timings_ms),
        .updates_per_second = median_ms > 0.0
            ? (static_cast<double>(client_count) / (median_ms / 1000.0))
            : 0.0,
        .bytes_processed = bytes,
        .checkpoint_serialize_ms = std::chrono::duration<double, std::milli>(serialize_end - serialize_start).count(),
        .checkpoint_checksum_validate_ms = std::chrono::duration<double, std::milli>(validate_end - validate_start).count(),
    };
}

void print_environment() {
    std::cout << "# environment\n";
    std::cout << "hardware_concurrency=" << std::thread::hardware_concurrency() << "\n";
#if defined(NDEBUG)
    std::cout << "build_type=release_or_ndebug\n";
#else
    std::cout << "build_type=debug\n";
#endif
#if defined(_MSC_VER)
    std::cout << "compiler=msvc_" << _MSC_VER << "\n";
#elif defined(__clang__)
    std::cout << "compiler=clang_" << __clang_major__ << "\n";
#elif defined(__GNUC__)
    std::cout << "compiler=gcc_" << __GNUC__ << "\n";
#endif
}

void print_result_row(const BenchmarkResult& row) {
    std::cout
        << row.model_size << ","
        << row.client_count << ","
        << row.algorithm << ","
        << row.weighting << ","
        << row.median_ms << ","
        << row.mean_ms << ","
        << row.updates_per_second << ","
        << row.bytes_processed << ","
        << row.checkpoint_serialize_ms << ","
        << row.checkpoint_checksum_validate_ms << "\n";
}

}  // namespace

int main(int argc, char** argv) {
    bool full = false;
    for (int index = 1; index < argc; ++index) {
        if (std::string(argv[index]) == "--full") {
            full = true;
        }
    }

    // Quick mode (the default and the one run in CI / local validation)
    // covers every algorithm and weighting at a size where a 500-client
    // run still completes in well under a second. --full additionally
    // sweeps the larger model sizes at 100 and 500 clients; it is slower
    // and intended for manual runs on a maintainer's machine, not CI.
    const std::vector<std::size_t> quick_client_counts = {10, 100, 500};
    const std::vector<std::size_t> full_client_counts = {10, 100, 500};

    const std::vector<AggregationAlgorithm> algorithms = {
        AggregationAlgorithm::kFedAvg,
        AggregationAlgorithm::kFedAdagrad,
        AggregationAlgorithm::kFedAdam,
        AggregationAlgorithm::kFedYogi,
    };
    const std::vector<WeightingStrategyType> weightings = {
        WeightingStrategyType::kUniform,
        WeightingStrategyType::kSampleCount,
    };

    print_environment();
    std::cout << "# columns\n";
    std::cout << "model_size,client_count,algorithm,weighting,median_ms,mean_ms,"
                 "updates_per_second,bytes_processed,checkpoint_serialize_ms,"
                 "checkpoint_checksum_validate_ms\n";

    const auto shapes = model_shapes();
    for (const auto& shape : shapes) {
        const bool is_tiny = shape.label == "tiny";
        const auto& client_counts = full ? full_client_counts : quick_client_counts;
        for (const auto client_count : client_counts) {
            // Skip the larger model sizes at high client counts in quick
            // mode to keep default runs fast; --full runs everything.
            if (!full && !is_tiny && client_count > 100) {
                continue;
            }
            for (const auto algorithm : algorithms) {
                for (const auto weighting : weightings) {
                    const auto result = run_one(shape, client_count, algorithm, weighting, /*repetitions=*/5);
                    print_result_row(result);
                }
            }
        }
    }

    return 0;
}
