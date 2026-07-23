# FedOpt Server Optimizers

## Status: implemented and tested (FedAdagrad, FedAdam, FedYogi), golden-parity verified

Reference: Reddi et al., *Adaptive Federated Optimization*, 2020
(arXiv:2003.00295).

## Architecture

Per the explicit Milestone 2 requirement, each optimizer is its own class
rather than a branch inside one shared function:

```cpp
class ServerOptimizer {
public:
    virtual OptimizerState apply(
        const ModelManifest& manifest,
        const TensorCollection& aggregated_delta,
        const OptimizerState& previous_state,
        const AggregationOptions& options,
        TensorCollection& out_model_delta
    ) const = 0;
    virtual std::string name() const = 0;
};

class FedAdagradOptimizer final : public ServerOptimizer { ... };
class FedAdamOptimizer final : public ServerOptimizer { ... };
class FedYogiOptimizer final : public ServerOptimizer { ... };

std::unique_ptr<ServerOptimizer> make_server_optimizer(AggregationAlgorithm);
```

`FedOptAggregator` (in `cpp/core/src/aggregation.cpp`) owns cohort
validation and weighting (shared across all three variants), then
delegates the moment update and model-delta formula entirely to the
selected `ServerOptimizer`. This was a refactor during this milestone: the
original implementation had all three formulas in one function with
`if (algorithm_ == ...)` branches; it was split apart specifically because
the requirement says not to combine the formulas into one large
conditional. The refactor was verified to be behavior-preserving — the
same golden test values and CTest suite pass before and after (see
[testing.md](testing.md)).

## Shared setup (`prepare_fedopt_step`)

Every variant, on each call:

1. Initializes `first_moment`/`second_moment` to zero-filled tensors
   matching the manifest if this is the first step (`previous_state` was
   empty) — this is the "first server step is correct when moments are
   initially zero" requirement.
2. Increments `step` by 1.
3. Computes `aggregated_delta` from client updates using the configured
   `WeightingStrategy` (shared with FedAvg/FedProx — see
   [cpp-aggregation-architecture.md](cpp-aggregation-architecture.md)).

## Per-tensor formulas

Let `Δ` be the aggregated client delta for one tensor, `m`/`v` the first/second
moment, `t` the step number (1-indexed after increment), `β1`/`β2` the
moment decay rates, `τ` the numerical-stability epsilon, `η` the server
learning rate.

**FedAdagrad** (no bias correction — matches the original FedOpt paper's
Adagrad variant):

```text
m_t = β1 * m_{t-1} + (1 - β1) * Δ
v_t = v_{t-1} + Δ²
model_delta = η * m_t / (sqrt(v_t) + τ)
```

**FedAdam** (bias-corrected, standard Adam second moment):

```text
m_t = β1 * m_{t-1} + (1 - β1) * Δ
v_t = β2 * v_{t-1} + (1 - β2) * Δ²
m_hat = m_t / (1 - β1^t)
v_hat = v_t / (1 - β2^t)
model_delta = η * m_hat / (sqrt(v_hat) + τ)
```

**FedYogi** (bias-corrected first moment identical to Adam; second moment
moves toward `Δ²` by a *signed* step rather than a convex combination,
which is what makes Yogi more robust to heavy-tailed gradients than Adam):

```text
m_t = β1 * m_{t-1} + (1 - β1) * Δ
v_t = v_{t-1} - (1 - β2) * sign(v_{t-1} - Δ²) * Δ²
m_hat = m_t / (1 - β1^t)
v_hat = v_t / (1 - β2^t)
model_delta = η * m_hat / (sqrt(v_hat) + τ)
```

## Numerical stability

* `server_lr` must be `> 0` and `tau >= 0`, checked before any tensor math
  (`FedOptAggregator::aggregate`).
* Division is always `x / (sqrt(v) + τ)`, never a bare `sqrt(v)` — `τ`
  exists precisely so this never divides by exactly zero even when `v` is
  legitimately zero (e.g. the very first FedAdagrad step with a zero
  delta).
* `v` for FedYogi can in principle be pushed slightly negative by the
  signed update if `Δ²` overshoots; `sqrt` of a negative value would
  produce NaN, which then fails `TensorBuffer::validate()` in the
  resulting `AggregatedUpdate` (via `TensorCollection::assign`) — the
  aggregator does **not** silently clamp/zero it out. This is a known
  sharp edge, not a bug fix already applied; see
  [known-limitations.md](known-limitations.md).
* Values are accumulated in `double` regardless of the declared tensor
  dtype (see [tensor-format.md](tensor-format.md)), reducing (but not
  eliminating) floating-point drift across many rounds.

## Tests

* `cpp/core/tests/golden.cpp` / `fl_aggregator_golden` — prints round-1 and
  round-2 outputs for all three algorithms; independently re-derived in
  Python in `tests/baseline/test_cpp_golden_parity.py` and asserted equal
  to 6 decimal places.
* `tests/baseline/test_python_cpp_compat.py::FedOptParityTests` — same
  three algorithms, but through the full Python → CLI → C++ round trip
  with real multi-shape PyTorch tensors (not scalars), across two
  sequential rounds each (so moment persistence across rounds is
  exercised, not just a single first step).
