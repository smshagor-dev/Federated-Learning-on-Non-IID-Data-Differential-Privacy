# C++ Aggregation Core Architecture

## Status: implemented and tested (Milestone 2 scope: FedAvg, FedProx,
SCAFFOLD, FedAdagrad, FedAdam, FedYogi server aggregation + validation +
checkpointing). Coordinator/scheduler/state-machine pieces referenced here
are Milestone 3 scaffolding and are explicitly out of scope for this
report.

## Source layout

```text
cpp/core/include/fl_core/
    tensor.hpp         TensorDescriptor, TensorBuffer, TensorCollection, tensor ops
    aggregation.hpp     ModelManifest, ClientUpdate, Aggregator, WeightingStrategy,
                        ServerOptimizer, UpdateValidator, AggregatorRegistry
    checkpoint.hpp      AggregatorCheckpoint, AggregatorCheckpointStore
    coordinator.hpp     RunState machine, ClientScheduler, CoordinatorCheckpoint (M3 scaffold)
    build_info.hpp      build metadata (smoke-test only)

cpp/core/src/           implementations of the above
cpp/core/tools/         aggregate_cli.cpp — CLI compatibility runner (Work Package D)
cpp/core/tests/         smoke.cpp, golden.cpp, validation.cpp, checkpoint.cpp
cpp/benchmarks/         aggregation_benchmark.cpp
```

## Core types (required abstractions and what they map to)

| Required name | Implementation |
|---|---|
| `Aggregator` | `fl::core::Aggregator` (pure virtual, `aggregate()` + implicit name via subclass) |
| `AggregatorRegistry` | `fl::core::AggregatorRegistry` + free function `make_aggregator()` |
| `ServerOptimizer` | `fl::core::ServerOptimizer` (FedAdagrad/FedAdam/FedYogi — see [fedopt.md](fedopt.md)) |
| `UpdateValidator` | `fl::core::UpdateValidator` |
| `WeightingStrategy` | `fl::core::WeightingStrategy` (Sample-count/Uniform/Capped/NormalizedBounded) |
| `TensorCollection` | `fl::core::TensorCollection` |
| `ModelManifest` | `fl::core::ModelManifest` |
| `ModelSnapshot` | Not separately modeled — the aggregation path is delta-based (`AggregationResult.model_delta`), and the caller (test harness / future coordinator) applies the delta to whatever snapshot it holds. Introducing a distinct `ModelSnapshot` type was deferred since nothing in this milestone owns global model state end-to-end; see [known-limitations.md](known-limitations.md). |
| `ClientUpdate` | `fl::core::ClientUpdate` |
| `AggregatedUpdate` | `fl::core::AggregationResult` (holds `model_delta`, `control_delta`, `optimizer_state`) |
| `OptimizerState` | `fl::core::OptimizerState` |
| `AggregationContext` | `fl::core::AggregationOptions` (algorithm, run/round id, weighting, hyperparameters) |

## Aggregator implementations

* `WeightedAggregator` — FedAvg and FedProx (identical server-side
  behavior; see [fedopt.md](fedopt.md) and the FedProx note below).
* `ScaffoldAggregator` — SCAFFOLD; see [scaffold-state.md](scaffold-state.md).
* `FedOptAggregator` — shared validation/weighting, delegates the
  algorithm-specific formula to a `ServerOptimizer` (`FedAdagradOptimizer`,
  `FedAdamOptimizer`, `FedYogiOptimizer`); see [fedopt.md](fedopt.md).

`make_aggregator(AggregationAlgorithm)` is the factory (`AggregatorRegistry::create`
delegates to it); adding a new algorithm means adding one `Aggregator`
subclass and one `switch` arm, not touching existing algorithms.

## FedProx

FedProx's client-side objective (proximal term) is entirely a Python/local-training
concern (`python/src/fl_platform/algorithms/`), not implemented in C++.
Server-side, FedProx is byte-for-byte the same aggregation as FedAvg
(`WeightedAggregator` handles both), which is why there is no separate
`FedProxAggregator` class — the requirement is "do not duplicate the
validated aggregation path unnecessarily," and FedAvg/FedProx genuinely
have no server-side difference to express.

## Weighting strategies

`WeightingStrategy::weights(updates, options)` returns one weight per
update, always summing to 1 (`normalize_weights` asserts a finite,
positive denominator and every normalized weight is finite):

* `SampleCountWeighting` — `n_i / sum(n_j)`.
* `UniformWeighting` — `1 / cohort_size`.
* `CappedSampleCountWeighting` — `min(n_i, cap) / sum(min(n_j, cap))`;
  rejects `cap <= 0`.
* `NormalizedBoundedWeighting` — sample-count weights clamped to
  `[minimum_weight, maximum_weight]`, then renormalized; rejects an
  inverted or degenerate `[min, max]` range.

The weighting strategy used is always explicit in `AggregationOptions.weighting`
and is persisted in checkpoints (`AggregatorCheckpoint.weighting`) — it is
never implicit/hardcoded except for SCAFFOLD's model-delta average, which
the SCAFFOLD algorithm definition itself fixes to uniform (documented in
[scaffold-state.md](scaffold-state.md), not silently overridden).

## Validation

Every `Aggregator::aggregate()` call runs `UpdateValidator::validate_cohort`
before any tensor math. See [update-validation.md](update-validation.md)
and [tensor-format.md](tensor-format.md) for the full rejection list.

## What this milestone does not include

* No coordinator round lifecycle, client scheduling, or gRPC service — those
  types (`RunStateMachine`, `ClientScheduler`, `CoordinatorCheckpoint`) exist
  in `coordinator.hpp` as Milestone 3 scaffolding, compiled and smoke-tested
  but not expanded in this work.
* No LibTorch dependency — tensor math is plain `std::vector<double>`
  arithmetic (`cpp/core/src/tensor.cpp`), which is adequate at the scale
  tested (see [benchmarking.md](benchmarking.md)) and keeps the build free
  of a heavy external dependency for Milestone 2's scope.
