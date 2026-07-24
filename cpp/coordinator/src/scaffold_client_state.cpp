#include "fl_coordinator/scaffold_client_state.hpp"

#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <system_error>

namespace fl::coordinator {

namespace {

std::uint64_t fnv1a_hash(const std::string& data) {
    std::uint64_t hash = 1469598103934665603ULL;
    for (const unsigned char byte : data) {
        hash ^= byte;
        hash *= 1099511628211ULL;
    }
    return hash;
}

std::string hash_to_hex(std::uint64_t hash) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << hash;
    return out.str();
}

std::string dtype_to_tag(fl::core::DType dtype) {
    switch (dtype) {
        case fl::core::DType::kFloat32:
            return "f32";
        default:
            throw std::invalid_argument("unsupported tensor dtype for client state");
    }
}

fl::core::DType dtype_from_tag(const std::string& tag) {
    if (tag == "f32") {
        return fl::core::DType::kFloat32;
    }
    throw ClientAlgorithmStateCorruptionError("unknown tensor dtype tag in client state: " + tag);
}

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

void write_collection(std::ostringstream& out, const fl::core::TensorCollection& collection) {
    out << "control_variate_count=" << collection.tensors().size() << "\n";
    for (const auto& [name, tensor] : collection.tensors()) {
        out << "control_variate_tensor=" << name << "|" << dtype_to_tag(tensor.descriptor().dtype) << "|";
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

fl::core::TensorBuffer parse_tensor_field(const std::string& field) {
    const auto parts = split(field, '|');
    if (parts.size() != 4) {
        throw ClientAlgorithmStateCorruptionError("malformed control variate tensor field");
    }
    fl::core::TensorDescriptor descriptor;
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
    // TensorBuffer's constructor validates element_count == values.size(),
    // which is exactly the "shape mismatch" detection this store needs to
    // surface, so no separate check is written here.
    return fl::core::TensorBuffer(std::move(descriptor), std::move(values));
}

fl::core::TensorCollection read_collection(const std::vector<std::pair<std::string, std::string>>& fields) {
    fl::core::TensorCollection collection;
    std::size_t expected = 0;
    bool has_count = false;
    std::size_t found = 0;
    for (const auto& [key, value] : fields) {
        if (key == "control_variate_count") {
            expected = std::stoull(value);
            has_count = true;
        } else if (key == "control_variate_tensor") {
            collection.insert(parse_tensor_field(value));
            ++found;
        }
    }
    if (!has_count) {
        throw ClientAlgorithmStateCorruptionError("client state missing control_variate_count");
    }
    if (found != expected) {
        throw ClientAlgorithmStateCorruptionError(
            "client state truncated: expected " + std::to_string(expected) +
            " control variate tensors, found " + std::to_string(found)
        );
    }
    return collection;
}

}  // namespace

ClientAlgorithmStateCorruptionError::ClientAlgorithmStateCorruptionError(const std::string& what)
    : std::runtime_error(what) {}

StaleClientAlgorithmStateError::StaleClientAlgorithmStateError(const std::string& what)
    : std::runtime_error(what) {}

std::string FilesystemClientAlgorithmStateStore::serialize(const ClientAlgorithmState& state) {
    std::ostringstream body;
    body << "schema_version=" << state.schema_version << "\n";
    body << "run_id=" << state.run_id << "\n";
    body << "client_id=" << state.client_id << "\n";
    body << "algorithm=" << state.algorithm << "\n";
    body << "model_version=" << state.model_version << "\n";
    write_collection(body, state.control_variate);

    const auto body_str = body.str();
    std::ostringstream out;
    out << body_str;
    out << "checksum=" << hash_to_hex(fnv1a_hash(body_str)) << "\n";
    return out.str();
}

ClientAlgorithmState FilesystemClientAlgorithmStateStore::deserialize(const std::string& payload) {
    const auto marker = payload.rfind("\nchecksum=");
    if (marker == std::string::npos) {
        throw ClientAlgorithmStateCorruptionError("client state payload is truncated or missing checksum");
    }
    const std::string body = payload.substr(0, marker + 1);
    const std::string checksum_line = payload.substr(marker + 1);

    const auto equals = checksum_line.find('=');
    if (equals == std::string::npos) {
        throw ClientAlgorithmStateCorruptionError("client state checksum line is malformed");
    }
    std::string checksum_value = checksum_line.substr(equals + 1);
    while (!checksum_value.empty() && (checksum_value.back() == '\n' || checksum_value.back() == '\r')) {
        checksum_value.pop_back();
    }

    const auto computed = hash_to_hex(fnv1a_hash(body));
    if (computed != checksum_value) {
        throw ClientAlgorithmStateCorruptionError("client state checksum mismatch: file is corrupt or was truncated");
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
            throw ClientAlgorithmStateCorruptionError("invalid client state line: " + line);
        }
        fields.emplace_back(line.substr(0, position), line.substr(position + 1));
    }

    ClientAlgorithmState state;
    bool has_schema_version = false;
    try {
        for (const auto& [key, value] : fields) {
            if (key == "schema_version") {
                state.schema_version = static_cast<std::uint32_t>(std::stoul(value));
                has_schema_version = true;
            } else if (key == "run_id") {
                state.run_id = value;
            } else if (key == "client_id") {
                state.client_id = value;
            } else if (key == "algorithm") {
                state.algorithm = value;
            } else if (key == "model_version") {
                state.model_version = value;
            }
        }
    } catch (const ClientAlgorithmStateCorruptionError&) {
        throw;
    } catch (const std::exception& error) {
        throw ClientAlgorithmStateCorruptionError(std::string("client state field parse failure: ") + error.what());
    }

    if (!has_schema_version) {
        throw ClientAlgorithmStateCorruptionError("client state missing schema_version");
    }
    if (state.schema_version != ClientAlgorithmState::kSchemaVersion) {
        throw ClientAlgorithmStateCorruptionError(
            "unsupported client state schema version " + std::to_string(state.schema_version)
        );
    }

    try {
        state.control_variate = read_collection(fields);
    } catch (const ClientAlgorithmStateCorruptionError&) {
        throw;
    } catch (const std::exception& error) {
        throw ClientAlgorithmStateCorruptionError(std::string("client state tensor parse failure: ") + error.what());
    }

    return state;
}

FilesystemClientAlgorithmStateStore::FilesystemClientAlgorithmStateStore(std::string root_directory)
    : root_directory_(std::move(root_directory)) {}

std::string FilesystemClientAlgorithmStateStore::path_for(
    const std::string& run_id, const std::string& client_id
) const {
    // Client/run identifiers are trusted call-site inputs (already
    // validated as non-empty, ASCII identifiers by the aggregation core's
    // UpdateValidator before ever reaching here); no path sanitization
    // beyond directory separation is performed.
    return (std::filesystem::path(root_directory_) / run_id / (client_id + ".state")).string();
}

std::optional<ClientAlgorithmState> FilesystemClientAlgorithmStateStore::load(
    const std::string& run_id,
    const std::string& client_id,
    const std::string& model_version
) {
    const auto path = path_for(run_id, client_id);
    if (!std::filesystem::exists(path)) {
        return std::nullopt;
    }

    std::ifstream in(path, std::ios::binary);
    if (!in) {
        throw ClientAlgorithmStateCorruptionError("client state file exists but could not be opened: " + path);
    }
    std::ostringstream buffer;
    buffer << in.rdbuf();
    auto state = deserialize(buffer.str());

    if (state.run_id != run_id || state.client_id != client_id) {
        throw ClientAlgorithmStateCorruptionError(
            "client state file identity mismatch: expected run_id=" + run_id + " client_id=" + client_id +
            ", found run_id=" + state.run_id + " client_id=" + state.client_id
        );
    }
    if (state.model_version != model_version) {
        throw StaleClientAlgorithmStateError(
            "client state for '" + client_id + "' was saved against model_version='" + state.model_version +
            "' but model_version='" + model_version + "' was requested"
        );
    }
    return state;
}

void FilesystemClientAlgorithmStateStore::save(
    const std::string& run_id,
    const std::string& client_id,
    const ClientAlgorithmState& state
) {
    const auto path = path_for(run_id, client_id);
    const std::filesystem::path target(path);
    std::filesystem::create_directories(target.parent_path());

    const auto payload = serialize(state);
    const std::filesystem::path temp_path = target.string() + ".tmp";
    {
        std::ofstream out(temp_path, std::ios::binary | std::ios::trunc);
        if (!out) {
            throw std::runtime_error("failed to open client state temp file for write: " + temp_path.string());
        }
        out << payload;
        out.flush();
        if (!out) {
            throw std::runtime_error("failed to write client state temp file: " + temp_path.string());
        }
    }

    std::error_code error_code;
    std::filesystem::rename(temp_path, target, error_code);
    if (error_code) {
        std::filesystem::remove(target, error_code);
        std::filesystem::rename(temp_path, target, error_code);
        if (error_code) {
            throw std::runtime_error("failed to atomically move client state into place: " + error_code.message());
        }
    }
}

}  // namespace fl::coordinator
