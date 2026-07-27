# User-Level Differential Privacy

**Status: implemented & tested, including a live Docker Compose
validation.** Source: `cpp/core/include/fl_core/privacy.hpp`/`.cpp`
(clipping, noise, accountant), `cpp/coordinator/src/run_manager.cpp`'s
`RunInstance::finalize_round` (wiring), `cpp/coordinator/src/coordinator_service.cpp`'s
`config_from_request` (CreateRun wire mapping). Tests:
`cpp/core/tests/privacy_test.cpp`, `cpp/coordinator/tests/user_level_dp_test.cpp`,
`cpp/coordinator/tests/coordinator_service_test.cpp`'s hybrid-DP block.
See [privacy-mathematics.md](privacy-mathematics.md) for the accounting
formulas this mechanism uses.

## What it protects

One client's **complete round contribution** — the entire effect of that
client's local dataset on one round's aggregate — as a single
indistinguishable unit. This is coarser-grained than sample-level DP (one
training example) and computed entirely by the C++ coordinator, not the
worker: a client submits its raw local update, and the coordinator
clips, aggregates, and noises it centrally.

## The three-step pipeline (`RunInstance::finalize_round`)

1. **Clip.** Every participating client's submitted delta is scaled so
   its global (multi-tensor, FP64) L2 norm across only the
   *shared/aggregatable* tensors (per the run's `AggregationManifest`)
   never exceeds the current clip bound `C_t`:
   `scale_i = min(1, C_t / (‖δ_i‖₂ + ε))`. Personalized/frozen tensors
   pass through completely unmodified ("local-head exclusion"). The
   clip bound is either fixed (`user_level_privacy.initial_clipping_bound`)
   or, if adaptive clipping is enabled, whatever
   `AdaptiveClipController::clip_value()` computed at the end of the
   previous round — see [adaptive-clipping.md](adaptive-clipping.md).
2. **Aggregate.** The already-clipped deltas are combined by the run's
   configured algorithm (FedAvg/FedProx/etc.) exactly as a non-private
   run would — clipping happens *before* aggregation, not as a separate
   mechanism bolted on after.
3. **Noise.** Independent `N(0, σ_noise²)` is added once to every
   element of the aggregated result (never per-client — see
   `add_central_gaussian_noise`'s doc comment for why reusing a
   per-client noise mechanism here would be a different, unintended
   guarantee). `σ_noise = noise_multiplier · C_t / target_clients_per_round`
   — exact for `uniform` weighting, a documented approximation for
   `capped_sample_count`/`normalized_bounded` (see
   known-limitations.md's Privacy Engineering Phase section for why the
   true bound is config-dependent and wasn't fully derived this phase).

The accountant steps once per round regardless of how many clients
actually participated; `sample_rate` for the RDP formula is
`target_clients_per_round / total_clients`, fixed at run creation.

## Privacy-safe weighting is enforced, not optional

Unrestricted `sample_count` weighting gives unbounded per-client
sensitivity in the weighted sum, breaking the clip bound's guarantee.
`config_from_request` rejects it outright for user-level/hybrid DP
(`kSampleCount` weighting → `CreateRun` fails validation); only
`uniform`, `capped_sample_count`, and `normalized_bounded` are accepted,
matching Python's `PRIVACY_SAFE_WEIGHTING_STRATEGIES`.

## Noise generation

`NoiseProvider` is an abstraction over two implementations:
`DeterministicNoiseProvider` (seeded, tests only — deliberately a
separate seed field, `privacy_noise_seed`, from `client_selection_seed`
so privacy noise and ordinary experiment randomness never share a seed)
and `SecureNoiseProvider` (the runtime default, `std::random_device`-seeded
`std::mt19937_64`, mutex-protected for concurrent element-wise draws).
Neither is a CSPRNG — see
[privacy-engineering-security-audit.md](privacy-engineering-security-audit.md).

## Checkpoint/recovery

The accountant's step count, the current clip bound (if adaptive), and
all three privacy ledgers are part of the coordinator's checkpoint body
(same FNV1a-checksummed format as every other field) and are restored on
`restore_from_checkpoint()` — a coordinator restart mid-run does not
reset accumulated epsilon back to zero. See
`cpp/coordinator/tests/privacy_recovery_test.cpp` for the regression
test that specifically checks this (epsilon and clip bound both continue
their trajectory across a simulated restart, not restart from scratch).

## Live validation

A real 2-round user-level-DP run was driven through the full stack (Go
API → C++ coordinator → real Python worker) via Docker Compose; the
reported epsilon (5.302585092994046 after round 1, 7.837641821656742
after round 2, σ=1.0, clip=5.0, δ=1e-5, single client) was independently
hand-verified against the RDP formula above (minimizing over integer
orders by hand gives the same values to full precision). See
[docker-runtime.md](docker-runtime.md) for the full session log,
including two real bugs this validation pass caught and fixed (a
missing `opacus`/`prometheus_client` dependency in the worker's Docker
image, and a dropped `entry_id` field in the wire encoding).
