#pragma once

#include "fl_core/aggregation.hpp"

#include <cstdint>
#include <stdexcept>
#include <string>

namespace fl::core {

// Raised when a checkpoint payload fails structural, schema, or checksum
// validation. Callers should treat this as "do not resume from this
// checkpoint" rather than attempt partial recovery.
class CheckpointCorruptionError : public std::runtime_error {
public:
    explicit CheckpointCorruptionError(const std::string& what);
};

// Persistable state for a single aggregator/optimizer, independent of the
// coordinator run-state machine. This is what a crashed or restarted
// coordinator needs to resume aggregation deterministically.
struct AggregatorCheckpoint {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version{kSchemaVersion};
    AggregationAlgorithm algorithm{AggregationAlgorithm::kFedAvg};
    WeightingStrategyType weighting{WeightingStrategyType::kSampleCount};
    std::string model_version;
    std::string manifest_checksum;
    OptimizerState optimizer_state;
    TensorCollection scaffold_control;
};

std::string compute_manifest_checksum(const ModelManifest& manifest);

class AggregatorCheckpointStore {
public:
    // Pure in-memory (de)serialization. The returned payload embeds a
    // checksum covering everything except the checksum line itself.
    [[nodiscard]] static std::string serialize(const AggregatorCheckpoint& checkpoint);
    [[nodiscard]] static AggregatorCheckpoint deserialize(const std::string& payload);

    // File-based persistence. save_to_file writes to a temporary sibling
    // file and renames it into place so a crash mid-write never leaves a
    // partially-written checkpoint at `path`. load_from_file rejects
    // truncated or checksum-mismatched files with CheckpointCorruptionError.
    static void save_to_file(const std::string& path, const AggregatorCheckpoint& checkpoint);
    [[nodiscard]] static AggregatorCheckpoint load_from_file(const std::string& path);
};

}  // namespace fl::core
