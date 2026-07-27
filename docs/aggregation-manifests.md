# Aggregation Manifests

**Status: implemented & tested.** Source:
`cpp/coordinator/include/fl_coordinator/run_manager.hpp`'s
`AggregationManifest` struct, enforced in
`cpp/coordinator/src/run_manager.cpp`'s `submit_client_result`. Tests:
`cpp/coordinator/tests/aggregation_manifest_test.cpp` (accepts shared
tensors, rejects personalized/frozen tensor names, permissive when no
manifest is declared). Also exercised from the Python side by
`tests/baseline/test_algorithm_expansion_integration.py`'s
`test_local_head_tensor_rejected_by_coordinator`.

## The problem this solves

A shared-backbone/personalized-head model (see
[shared-backbone-local-head.md](shared-backbone-local-head.md)) has some
parameters that must be aggregated (the backbone) and some that must
never be aggregated (the head — it's local to each client). A buggy or
malicious worker could submit the personalized head as if it were part
of the aggregatable delta, corrupting the global model with
client-specific weights. The coordinator needs to reject that — **without
knowing anything about ML** (see
[algorithm-expansion-architecture.md](algorithm-expansion-architecture.md)'s language
boundary: the C++ coordinator has no PyTorch, no concept of a "head" or
"backbone").

## The solution: a pure parameter-name-list check

```cpp
struct AggregationManifest {
    std::vector<std::string> shared_parameter_names;
    std::vector<std::string> personalized_parameter_names;
    std::vector<std::string> frozen_parameter_names;
    std::string schema_hash;
};
```

`submit_client_result` checks the submitted delta's tensor names against
`personalized_parameter_names`/`frozen_parameter_names` — if any
submitted tensor name appears in either list, the submission is rejected
with a clear reason, before it ever reaches the aggregator. This is
**set membership on strings**, not a training-aware check — genuinely
zero ML knowledge required.

`is_declared()` (`true` if any of the three name lists is non-empty)
controls whether the check applies at all: a run that never declares an
aggregation manifest (every the Foundation, Aggregation Core, and Coordinator Runtime phases FedAvg/FedProx/SCAFFOLD run,
and FedSAM) is fully permissive, exactly as before this phase — this
check is additive, not a new requirement on every run.

## The design pattern that took two iterations to get right

The canonical `ModelManifest.tensors` list (used since the Aggregation Core phase by
`UpdateValidator` to check "does this submission's tensor set exactly
match the manifest?") must contain **only the truly-aggregatable
tensor(s)** — never the personalized/frozen names, even though those
names are also part of the model's full `state_dict()`. Putting a
personalized tensor in both the canonical manifest *and* the
personalized-names list breaks the Aggregation-Core-era "client delta tensor set must
match the manifest" rule as soon as a client correctly omits that tensor
from its submission (since a client legitimately never submits its local
head). This was discovered via manual CLI testing during this
phase's development and is now the documented, tested pattern:
`tensor_specs`/canonical manifest = shared/aggregatable tensors only;
`shared_parameter_names`/`personalized_parameter_names`/
`frozen_parameter_names` = the separate declaration used only by this
manifest check.

## CLI-bridge wire format

The CLI bridge (`coordinator_cli.cpp`, used by the Python worker's
`CliBridgeCoordinatorClient` — see
`python/src/fl_platform/worker/coordinator_client.py`) extends the
existing single-tensor `delta=`/`weight` pattern additively:

* `tensor_specs="backbone:8"` (multi-tensor manifest support, e.g.
  `"backbone:64,head:12"` for a shared-backbone/personalized-head model)
  — falls back to the single `"weight"` tensor shape every pre-Algorithm-Expansion
  FedAvg/FedProx/SCAFFOLD test already uses when empty.
* `shared_parameter_names=`, `personalized_parameter_names=`,
  `frozen_parameter_names=` — the separate aggregation-manifest
  declaration.
* `delta_count=`/`delta_N=` (multi-tensor submission) alongside the
  existing single `delta=` field.

Every existing FedAvg/FedProx/SCAFFOLD CLI-bridge test stayed green
throughout this extension — verified by the full CTest and pytest suites
staying green at every step.

## Checkpoint persistence of the manifest

`AggregationManifest` is part of `RunConfig`, which is already
checkpointed/restored as part of the run's config on every save — no
separate persistence path was needed.
