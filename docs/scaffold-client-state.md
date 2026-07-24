# SCAFFOLD Client State (Coordinator-Level Persistence)

Not to be confused with [scaffold-state.md](scaffold-state.md) (Milestone
2's aggregation-core-level SCAFFOLD math: how `c`/`c_i` deltas combine
during aggregation). This document covers Milestone 3's addition: where
each client's own control variate is *persisted* between rounds, at the
coordinator layer.

## Interface

`cpp/coordinator/include/fl_coordinator/scaffold_client_state.hpp`:

```cpp
struct ClientAlgorithmState {
    fl::core::TensorCollection control_variate;
    std::uint64_t last_round_id;
};

class ClientAlgorithmStateStore {
public:
    virtual std::optional<ClientAlgorithmState> load(const std::string& client_id) const = 0;
    virtual void save(const std::string& client_id, ClientAlgorithmState state) = 0;
};
```

`FilesystemClientAlgorithmStateStore` is the real implementation — one
file per client under a configured root directory, with the same
atomic-write + FNV-1a-checksum + schema-version pattern as
`AggregatorCheckpointStore` (Milestone 2). `ClientAlgorithmStateCorruptionError`
and `StaleClientAlgorithmStateError` are distinct exception types so
callers can distinguish "the file is corrupt" from "the file is for an
older schema."

## How `RunInstance` uses it

`RunInstance::scaffold_control_variates_for(client_id)` returns a pair:
the run's global control variate (in-memory, part of `RunConfig`/round
state) and this client's own control variate — loaded from the store on
first participation (zero-initialized if never seen before), returned
alongside the global one so the transport layer can attach both to the
`ClientTrainingTask` it builds. After a client's result is accepted,
their refreshed control variate (returned by the worker alongside the
training delta) is written back via `save()`.

Only relevant when the run's algorithm is SCAFFOLD
(`AggregationAlgorithm::kScaffold`); other algorithms never touch this
store.

## Testing

`cpp/coordinator/tests/scaffold_client_state_test.cpp` (round-trip
save/load, corruption detection, stale-schema detection) and the
`test_scaffold_two_rounds` cross-language integration test (a real
two-round SCAFFOLD run through the CLI bridge, verifying per-client
control variates persist and update correctly across the process
boundary between round 1 and round 2).
