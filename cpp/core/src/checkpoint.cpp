#include "fl_core/checkpoint.hpp"

#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace fl::core {

namespace {

std::uint64_t fnv1a_hash(const std::string& data) {
    std::uint64_t hash = 1469598103934665603ULL;  // FNV offset basis
    for (const unsigned char byte : data) {
        hash ^= byte;
        hash *= 1099511628211ULL;  // FNV prime
    }
    return hash;
}

std::string hash_to_hex(std::uint64_t hash) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

std::string dtype_to_tag(DType dtype) {
    switch (dtype) {
        case DType::kFloat32:
            return "f32";
        default:
            throw std::invalid_argument("unsupported tensor dtype for checkpoint");
    }
}

DType dtype_from_tag(const std::string& tag) {
    if (tag == "f32") {
        return DType::kFloat32;
    }
    throw CheckpointCorruptionError("unknown tensor dtype tag in checkpoint: " + tag);
}

void write_collection(std::ostringstream& out, const std::string& key, const TensorCollection& collection) {
    out << key << "_count=" << collection.tensors().size() << "\n";
    for (const auto& [name, tensor] : collection.tensors()) {
        out << key << "_tensor=" << name << "|" << dtype_to_tag(tensor.descriptor().dtype) << "|";
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
        out << "\n";
    }
}

std::vector<std::string> split(const std::string& value, char delimiter) {
    std::vector<std::string> parts;
    std::stringstream stream(value);
    std::string item;
    while (std::getline(stream, item, delimiter)) {
        parts.push_back(item);
    }
    return parts;
}

TensorBuffer parse_tensor_field(const std::string& field) {
    const auto parts = split(field, '|');
    if (parts.size() != 4) {
        throw CheckpointCorruptionError("malformed tensor field in checkpoint");
    }
    TensorDescriptor descriptor;
    descriptor.name = parts[0];
    descriptor.dtype = dtype_from_tag(parts[1]);
    if (!parts[2].empty()) {
        for (const auto& dim : split(parts[2], '-')) {
            descriptor.shape.push_back(std::stoull(dim));
        }
    }
    std::vector<double> values;
    if (!parts[3].empty()) {
        for (const auto& raw_value : split(parts[3], ',')) {
            values.push_back(std::stod(raw_value));
        }
    }
    return TensorBuffer(std::move(descriptor), std::move(values));
}

TensorCollection read_collection(
    const std::vector<std::pair<std::string, std::string>>& fields,
    const std::string& key
) {
    TensorCollection collection;
    std::size_t expected = 0;
    bool has_count = false;
    std::size_t found = 0;
    for (const auto& [field_key, value] : fields) {
        if (field_key == key + "_count") {
            expected = std::stoull(value);
            has_count = true;
        } else if (field_key == key + "_tensor") {
            collection.insert(parse_tensor_field(value));
            ++found;
        }
    }
    if (!has_count) {
        throw CheckpointCorruptionError("checkpoint missing count for " + key);
    }
    if (found != expected) {
        throw CheckpointCorruptionError("checkpoint truncated: expected " +
            std::to_string(expected) + " tensors for " + key + ", found " + std::to_string(found));
    }
    return collection;
}

}  // namespace

CheckpointCorruptionError::CheckpointCorruptionError(const std::string& what)
    : std::runtime_error(what) {}

std::string compute_manifest_checksum(const ModelManifest& manifest) {
    std::ostringstream out;
    out << manifest.model_id << "|" << manifest.model_version << "|";
    for (const auto& descriptor : manifest.tensors) {
        out << descriptor.name << ":" << dtype_to_tag(descriptor.dtype) << ":";
        for (const auto dim : descriptor.shape) {
            out << dim << ",";
        }
        out << ";";
    }
    return hash_to_hex(fnv1a_hash(out.str()));
}

std::string AggregatorCheckpointStore::serialize(const AggregatorCheckpoint& checkpoint) {
    std::ostringstream body;
    body << "schema_version=" << checkpoint.schema_version << "\n";
    body << "algorithm=" << to_string(checkpoint.algorithm) << "\n";
    body << "weighting=" << to_string(checkpoint.weighting) << "\n";
    body << "model_version=" << checkpoint.model_version << "\n";
    body << "manifest_checksum=" << checkpoint.manifest_checksum << "\n";
    body << "optimizer_step=" << checkpoint.optimizer_state.step << "\n";
    write_collection(body, "first_moment", checkpoint.optimizer_state.first_moment);
    write_collection(body, "second_moment", checkpoint.optimizer_state.second_moment);
    write_collection(body, "scaffold_control", checkpoint.scaffold_control);

    const auto body_str = body.str();
    std::ostringstream out;
    out << body_str;
    out << "checksum=" << hash_to_hex(fnv1a_hash(body_str)) << "\n";
    return out.str();
}

AggregatorCheckpoint AggregatorCheckpointStore::deserialize(const std::string& payload) {
    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw CheckpointCorruptionError("checkpoint payload is truncated or missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    const std::string checksum_line = payload.substr(marker + 1);

    const auto equals = checksum_line.find('=');
    if (equals == std::string::npos) {
        throw CheckpointCorruptionError("checkpoint checksum line is malformed");
    }
    std::string checksum_value = checksum_line.substr(equals + 1);
    while (!checksum_value.empty() && (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }

    const auto computed = hash_to_hex(fnv1a_hash(body));
    if (computed != checksum_value) {
        throw CheckpointCorruptionError("checkpoint checksum mismatch: file is corrupt or was truncated");
    }

    std::vector<std::pair<std::string, std::string>> fields;
    std::stringstream stream(body);
    std::string line;
    while (std::getline(stream, line)) {
        if (line.empty()) {
            continue;
        }
        const auto position = line.find('=');
        if (position == std::string::npos) {
            throw CheckpointCorruptionError("invalid checkpoint line: " + line);
        }
        fields.emplace_back(line.substr(0, position), line.substr(position + 1));
    }

    AggregatorCheckpoint checkpoint;
    bool has_schema_version = false;
    try {
        for (const auto& [key, value] : fields) {
            if (key == "schema_version") {
                checkpoint.schema_version = static_cast<std::uint32_t>(std::stoul(value));
                has_schema_version = true;
            } else if (key == "algorithm") {
                if (value == "fedavg") {
                    checkpoint.algorithm = AggregationAlgorithm::kFedAvg;
                } else if (value == "fedprox") {
                    checkpoint.algorithm = AggregationAlgorithm::kFedProx;
                } else if (value == "scaffold") {
                    checkpoint.algorithm = AggregationAlgorithm::kScaffold;
                } else if (value == "fedadagrad") {
                    checkpoint.algorithm = AggregationAlgorithm::kFedAdagrad;
                } else if (value == "fedadam") {
                    checkpoint.algorithm = AggregationAlgorithm::kFedAdam;
                } else if (value == "fedyogi") {
                    checkpoint.algorithm = AggregationAlgorithm::kFedYogi;
                } else {
                    throw CheckpointCorruptionError("unknown algorithm in checkpoint: " + value);
                }
            } else if (key == "weighting") {
                if (value == "sample_count") {
                    checkpoint.weighting = WeightingStrategyType::kSampleCount;
                } else if (value == "uniform") {
                    checkpoint.weighting = WeightingStrategyType::kUniform;
                } else if (value == "capped_sample_count") {
                    checkpoint.weighting = WeightingStrategyType::kCappedSampleCount;
                } else if (value == "normalized_bounded") {
                    checkpoint.weighting = WeightingStrategyType::kNormalizedBounded;
                } else {
                    throw CheckpointCorruptionError("unknown weighting strategy in checkpoint: " + value);
                }
            } else if (key == "model_version") {
                checkpoint.model_version = value;
            } else if (key == "manifest_checksum") {
                checkpoint.manifest_checksum = value;
            } else if (key == "optimizer_step") {
                checkpoint.optimizer_state.step = std::stoull(value);
            }
        }
    } catch (const CheckpointCorruptionError&) {
        throw;
    } catch (const std::exception& error) {
        throw CheckpointCorruptionError(std::string("checkpoint field parse failure: ") + error.what());
    }

    if (!has_schema_version) {
        throw CheckpointCorruptionError("checkpoint missing schema_version");
    }
    if (checkpoint.schema_version != AggregatorCheckpoint::kSchemaVersion) {
        throw CheckpointCorruptionError(
            "unsupported checkpoint schema version " + std::to_string(checkpoint.schema_version)
        );
    }

    try {
        checkpoint.optimizer_state.first_moment = read_collection(fields, "first_moment");
        checkpoint.optimizer_state.second_moment = read_collection(fields, "second_moment");
        checkpoint.scaffold_control = read_collection(fields, "scaffold_control");
    } catch (const CheckpointCorruptionError&) {
        throw;
    } catch (const std::exception& error) {
        throw CheckpointCorruptionError(std::string("checkpoint tensor parse failure: ") + error.what());
    }

    return checkpoint;
}

void AggregatorCheckpointStore::save_to_file(const std::string& path, const AggregatorCheckpoint& checkpoint) {
    const auto payload = serialize(checkpoint);
    const std::filesystem::path target(path);
    const std::filesystem::path temp_path = target.string() + ".tmp";

    {
        std::ofstream out(temp_path, std::ios::binary | std::ios::trunc);
        if (!out) {
            throw std::runtime_error("failed to open checkpoint temp file for write: " + temp_path.string());
        }
        out << payload;
        out.flush();
        if (!out) {
            throw std::runtime_error("failed to write checkpoint temp file: " + temp_path.string());
        }
    }

    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        // std::filesystem::rename does not overwrite an existing file on
        // all platforms (notably Windows). Fall back to remove-then-rename;
        // this narrows, but does not eliminate, the atomicity window.
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw std::runtime_error(
                "failed to atomically move checkpoint into place: " + error_code.message()
            );
        }
    }
}

AggregatorCheckpoint AggregatorCheckpointStore::load_from_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw std::runtime_error("checkpoint file not found: " + path);
    }
    std::ostringstream buffer;
    buffer << in.rdbuf();
    return deserialize(buffer.str());
}

}  // namespace fl::core
