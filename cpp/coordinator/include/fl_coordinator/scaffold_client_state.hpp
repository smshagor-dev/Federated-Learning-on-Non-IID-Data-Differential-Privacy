#pragma once

#include "fl_core/aggregation.hpp"

#include <cstdint>
#include <optional>
#include <stdexcept>
#include <string>

namespace fl::coordinator {

// Raised when a persisted client state fails structural, identity,
// checksum, or schema validation. Mirrors
// fl::core::CheckpointCorruptionError's contract: never return a partial
// or best-guess state, always throw.
class ClientAlgorithmStateCorruptionError : public std::runtime_error {
public:
    explicit ClientAlgorithmStateCorruptionError(const std::string& what);
};

// Raised when a persisted client state's model_version does not match
// the model_version the caller is asking to load for. This is distinct
// from "missing" (returns std::nullopt) and from corruption (throws
// ClientAlgorithmStateCorruptionError) because a stale client state is
// structurally valid data that is simply no longer safe to reuse as-is.
class StaleClientAlgorithmStateError : public std::runtime_error {
public:
    explicit StaleClientAlgorithmStateError(const std::string& what);
};

struct ClientAlgorithmState {
    static constexpr std::uint32_t kSchemaVersion = 1;

    std::uint32_t schema_version{kSchemaVersion};
    std::string run_id;
    std::string client_id;
    std::string algorithm;
    std::string model_version;
    fl::core::TensorCollection control_variate;
};

// Storage abstraction for per-client federated-algorithm state (initially
// just SCAFFOLD's control variate c_i, but general enough for any future
// per-client persistent state). Implementations must not require every
// client's state to be held in coordinator RAM simultaneously — a
// filesystem-backed implementation naturally satisfies this by loading
// only the state for the client currently being processed.
class ClientAlgorithmStateStore {
public:
    virtual ~ClientAlgorithmStateStore() = default;

    // Returns std::nullopt if no state has ever been saved for this
    // (run_id, client_id) pair — the normal "first participation" case,
    // which callers should handle by zero-initializing, not treat as an
    // error. Throws StaleClientAlgorithmStateError if a state exists but
    // was saved against a different model_version. Throws
    // ClientAlgorithmStateCorruptionError if the persisted state is
    // corrupt, truncated, or was saved under a different run_id/client_id
    // than requested.
    virtual std::optional<ClientAlgorithmState> load(
        const std::string& run_id,
        const std::string& client_id,
        const std::string& model_version
    ) = 0;

    virtual void save(
        const std::string& run_id,
        const std::string& client_id,
        const ClientAlgorithmState& state
    ) = 0;
};

// Filesystem-backed implementation. One file per (run_id, client_id),
// atomically written (temp file + rename, same pattern as
// fl::core::AggregatorCheckpointStore) and checksum-validated on load.
class FilesystemClientAlgorithmStateStore final : public ClientAlgorithmStateStore {
public:
    explicit FilesystemClientAlgorithmStateStore(std::string root_directory);

    std::optional<ClientAlgorithmState> load(
        const std::string& run_id,
        const std::string& client_id,
        const std::string& model_version
    ) override;

    void save(
        const std::string& run_id,
        const std::string& client_id,
        const ClientAlgorithmState& state
    ) override;

    // Exposed for tests: the exact path a given (run_id, client_id) is
    // stored at, without needing to duplicate the naming scheme.
    [[nodiscard]] std::string path_for(const std::string& run_id, const std::string& client_id) const;

    [[nodiscard]] static std::string serialize(const ClientAlgorithmState& state);
    [[nodiscard]] static ClientAlgorithmState deserialize(const std::string& payload);

private:
    std::string root_directory_;
};

}  // namespace fl::coordinator
