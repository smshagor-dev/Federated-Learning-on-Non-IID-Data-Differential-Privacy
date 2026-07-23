# SCAFFOLD Aggregation and Control-Variate State

## Status: implemented and tested (server-side aggregation); distributed
client-side control-variate storage is scaffolded/deferred

Reference: Karimireddy et al., *SCAFFOLD: Stochastic Controlled Averaging
for Federated Learning*, 2020.

## Server-side aggregation rule

Implemented in `ScaffoldAggregator::aggregate` (`cpp/core/src/aggregation.cpp`):

```text
x <- x + server_lr * (1/|S|) * sum_{i in S} delta_i
c <- c + (|S| / N) * (1/|S|) * sum_{i in S} (c_i^+ - c_i)
```

where `S` is the sampled cohort, `N` is `AggregationOptions.total_clients`,
`x` is the global model, and `c` is the global control variate. Notes:

* The model delta always uses **uniform** weighting
  (`WeightingStrategyType::kUniform`), regardless of what
  `AggregationOptions.weighting` is set to — SCAFFOLD's server update is
  defined as a uniform cohort average in the original paper, so the
  aggregator hard-codes this rather than trusting caller configuration to
  get it right.
* `require_control = true` is passed to `UpdateValidator::validate_cohort`
  for SCAFFOLD, so every client update in the cohort must carry a
  `control_delta` tensor for every manifest tensor, or the whole cohort is
  rejected before any aggregation happens.
* The control-variate scale factor is `|S| / N` — computed from
  `updates.size()` and `options.total_clients` — matching the paper exactly
  (not `1/N` or `1/|S|` alone).

## Parity with the legacy Python server

`federated/server.py`'s `Server._aggregate_scaffold` implements the same
two formulas independently (uniform delta average + `cohort/num_clients`
scaled control-variate update). `tests/baseline/test_python_cpp_compat.py::ScaffoldParityTests`
runs both implementations against the same client deltas/control-variate
deltas (real PyTorch tensors, two different shapes) and asserts the
resulting model state and global control variate match to `1e-6`.

## Global control-variate checkpointing

`AggregatorCheckpoint.scaffold_control` (see
[checkpoint-format.md](checkpoint-format.md)) persists the global control
variate `c` as a `TensorCollection`, round-tripped through
`AggregatorCheckpointStore`. This is checkpointed independently of
`OptimizerState` (which SCAFFOLD does not otherwise use — SCAFFOLD is not
a FedOpt variant and has no first/second moment).

## What is explicitly deferred (not implemented in Milestone 2)

* **Per-client control-variate storage (`c_i`)** — the C++ core receives
  each client's `control_delta` (i.e. `c_i^+ - c_i`, already computed
  client-side, matching the legacy server's contract) and does not itself
  store or manage individual clients' `c_i` values. The design requirement
  ("must not require all client-local control variates to stay in
  coordinator memory") is satisfied by construction: nothing server-side
  holds per-client state at all in this milestone. A future coordinator
  (Milestone 3) would need a `ClientMetadata`-adjacent store (or an
  artifact/object-storage reference, per `plan.md`'s Phase 15 tensor
  formats) to persist `c_i` between a client's non-consecutive
  participations — that store does not exist yet.
* **Stale control-variate rejection** — the update validator checks that a
  control tensor is *present and valid* per manifest tensor when
  `require_control` is set, but does not currently check a control-variate
  "version" or staleness marker (there is no such field on `ClientUpdate`
  today). This is listed as a gap, not silently glossed over — see
  [known-limitations.md](known-limitations.md).

## Tests

* `cpp/core/tests/golden.cpp` / `smoke.cpp` — scalar SCAFFOLD delta and
  control-variate values, exact match asserted.
* `tests/baseline/test_python_cpp_compat.py::ScaffoldParityTests::test_scaffold_model_and_control_variate_update` —
  full PyTorch-tensor, cross-language round trip.
* `cpp/core/tests/validation.cpp` does not currently include a SCAFFOLD-specific
  rejection case (e.g. missing control tensor); the shared cohort-validation
  rejection tests (duplicate ids, non-finite values, etc.) apply to
  SCAFFOLD too since they run through the same `UpdateValidator`.
