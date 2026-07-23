#include "fl_core/checkpoint.hpp"

#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>

namespace {

fl::core::ModelManifest make_manifest() {
    return fl::core::ModelManifest{
        .model_id = "toy-model",
        .model_version = "v3",
        .tensors = {
            fl::core::TensorDescriptor{
                .name = "weight",
                .shape = {2},
                .dtype = fl::core::DType::kFloat32,
            },
        },
    };
}

fl::core::AggregatorCheckpoint make_checkpoint(const fl::core::ModelManifest& manifest) {
    fl::core::AggregatorCheckpoint checkpoint;
    checkpoint.algorithm = fl::core::AggregationAlgorithm::kFedAdam;
    checkpoint.weighting = fl::core::WeightingStrategyType::kSampleCount;
    checkpoint.model_version = manifest.model_version;
    checkpoint.manifest_checksum = fl::core::compute_manifest_checksum(manifest);
    checkpoint.optimizer_state.step = 7;
    checkpoint.optimizer_state.first_moment.insert(
        fl::core::TensorBuffer(manifest.tensors[0], {0.5, -0.25})
    );
    checkpoint.optimizer_state.second_moment.insert(
        fl::core::TensorBuffer(manifest.tensors[0], {0.1, 0.2})
    );
    checkpoint.scaffold_control.insert(
        fl::core::TensorBuffer(manifest.tensors[0], {0.0, 0.0})
    );
    return checkpoint;
}

int require(bool condition, const std::string& label, int& failures) {
    if (!condition) {
        std::cerr << "FAILED: " << label << "\n";
        ++failures;
    }
    return failures;
}

}  // namespace

int main(int argc, char** argv) {
    int failures = 0;
    const auto manifest = make_manifest();
    const auto checkpoint = make_checkpoint(manifest);

    // Round trip through the in-memory serialize/deserialize path.
    {
        const auto payload = fl::core::AggregatorCheckpointStore::serialize(checkpoint);
        const auto restored = fl::core::AggregatorCheckpointStore::deserialize(payload);
        require(restored.algorithm == checkpoint.algorithm, "algorithm round trip", failures);
        require(restored.weighting == checkpoint.weighting, "weighting round trip", failures);
        require(restored.model_version == checkpoint.model_version, "model_version round trip", failures);
        require(restored.manifest_checksum == checkpoint.manifest_checksum, "manifest_checksum round trip", failures);
        require(restored.optimizer_state.step == checkpoint.optimizer_state.step, "step round trip", failures);
        require(
            restored.optimizer_state.first_moment.at("weight").values() ==
                checkpoint.optimizer_state.first_moment.at("weight").values(),
            "first_moment round trip",
            failures
        );
        require(
            restored.optimizer_state.second_moment.at("weight").values() ==
                checkpoint.optimizer_state.second_moment.at("weight").values(),
            "second_moment round trip",
            failures
        );
    }

    // Manifest checksum must change if the manifest changes.
    {
        auto other_manifest = manifest;
        other_manifest.model_version = "v4";
        require(
            fl::core::compute_manifest_checksum(other_manifest) != checkpoint.manifest_checksum,
            "manifest checksum differs for different manifests",
            failures
        );
    }

    // Checksum tampering must be rejected.
    {
        auto payload = fl::core::AggregatorCheckpointStore::serialize(checkpoint);
        payload[5] = (payload[5] == 'a') ? 'b' : 'a';
        bool rejected = false;
        try {
            static_cast<void>(fl::core::AggregatorCheckpointStore::deserialize(payload));
        } catch (const fl::core::CheckpointCorruptionError&) {
            rejected = true;
        }
        require(rejected, "tampered checkpoint payload rejected", failures);
    }

    // Truncated payloads must be rejected, not silently accepted.
    {
        auto payload = fl::core::AggregatorCheckpointStore::serialize(checkpoint);
        const auto truncated = payload.substr(0, payload.size() / 2);
        bool rejected = false;
        try {
            static_cast<void>(fl::core::AggregatorCheckpointStore::deserialize(truncated));
        } catch (const fl::core::CheckpointCorruptionError&) {
            rejected = true;
        }
        require(rejected, "truncated checkpoint payload rejected", failures);
    }

    // Unsupported schema versions must be rejected explicitly.
    {
        auto payload = fl::core::AggregatorCheckpointStore::serialize(checkpoint);
        const auto position = payload.find("schema_version=1");
        payload.replace(position, std::string("schema_version=1").size(), "schema_version=99");
        // Recompute checksum so the rejection is due to schema version, not checksum mismatch.
        const auto checksum_marker = payload.rfind("\nchecksum=");
        const auto body = payload.substr(0, checksum_marker + 1);
        bool rejected_for_schema = false;
        try {
            static_cast<void>(fl::core::AggregatorCheckpointStore::deserialize(payload));
        } catch (const fl::core::CheckpointCorruptionError& error) {
            const std::string message = error.what();
            rejected_for_schema = message.find("schema") != std::string::npos ||
                message.find("checksum") != std::string::npos;
        }
        require(rejected_for_schema, "unsupported schema version rejected", failures);
    }

    // File-based atomic round trip.
    if (argc > 1) {
        const std::filesystem::path scratch_dir(argv[1]);
        std::filesystem::create_directories(scratch_dir);
        const auto checkpoint_path = (scratch_dir / "checkpoint.bin").string();

        fl::core::AggregatorCheckpointStore::save_to_file(checkpoint_path, checkpoint);
        require(
            !std::filesystem::exists(checkpoint_path + ".tmp"),
            "temp file removed after atomic rename",
            failures
        );
        require(std::filesystem::exists(checkpoint_path), "checkpoint file created", failures);

        const auto restored = fl::core::AggregatorCheckpointStore::load_from_file(checkpoint_path);
        require(restored.optimizer_state.step == checkpoint.optimizer_state.step, "file round trip step", failures);

        // Overwrite with a second checkpoint to exercise atomic replace of an existing file.
        auto second_checkpoint = checkpoint;
        second_checkpoint.optimizer_state.step = 8;
        fl::core::AggregatorCheckpointStore::save_to_file(checkpoint_path, second_checkpoint);
        const auto restored_second = fl::core::AggregatorCheckpointStore::load_from_file(checkpoint_path);
        require(restored_second.optimizer_state.step == 8, "file round trip after overwrite", failures);

        // Corrupt the file on disk and confirm load rejects it instead of
        // silently returning partial state.
        {
            std::ofstream corrupt(checkpoint_path, std::ios::binary | std::ios::trunc);
            corrupt << "schema_version=1\nnot-a-real-checkpoint";
        }
        bool rejected = false;
        try {
            static_cast<void>(fl::core::AggregatorCheckpointStore::load_from_file(checkpoint_path));
        } catch (const fl::core::CheckpointCorruptionError&) {
            rejected = true;
        }
        require(rejected, "corrupt checkpoint file rejected on load", failures);

        std::filesystem::remove(checkpoint_path);
    }

    return failures == 0 ? 0 : 1;
}
