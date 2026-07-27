# Adaptive Clipping

**Status: implemented & tested in both C++ and Python (kept behaviorally
identical, cross-checked).** Source: `fl::core::AdaptiveClipController`
(`cpp/core/include/fl_core/privacy.hpp`/`.cpp`),
`fl_platform.privacy.adaptive_clipping.AdaptiveClipController`
(`python/src/fl_platform/privacy/adaptive_clipping.py`). Tests:
`cpp/core/tests/privacy_test.cpp`'s adaptive-clipping group,
`cpp/coordinator/tests/adaptive_clipping_test.cpp`,
`python/tests/test_privacy_foundations.py`,
`python/tests/test_privacy_statistical_validation.py`'s convergence
test.

## What it is

A quantile-based dynamic clip bound (Andrew et al., 2021): instead of a
fixed clip bound chosen once, the bound moves each round toward whatever
value would make a target fraction (`target_quantile`) of clients'
updates land exactly at the boundary. This matters because a clip bound
that's too low clips (and thus distorts) most updates, while one that's
too high lets outliers dominate the noised aggregate — the "right" bound
depends on the actual update-norm distribution, which shifts over
training and isn't known in advance.

## Why this is its own DP mechanism, not a free optimization

The bound is computed from a **count** — how many of this round's
clients exceeded the current bound — and that count is itself
privacy-sensitive (it leaks information about the update-norm
distribution, which is derived from client data). Adaptive clipping
privatizes it before it ever influences the bound:

1. Count how many of the cohort's clients had a raw (unclipped) delta
   norm exceeding the current bound (`compute_shared_norm`, the same
   global multi-tensor L2 norm user-level DP's clipping step uses — see
   [user-level-dp.md](user-level-dp.md)). The raw count is never stored,
   logged, or returned by anything past this point.
2. Add Gaussian noise: `noisy_count = count + N(0, count_noise_multiplier²)`
   — sensitivity 1 (one client changes the count by at most 1), so no
   subsampling amplification term applies (every already-selected cohort
   member contributes exactly one bit).
3. `noisy_fraction = clamp(noisy_count / cohort_size, 0, 1)`.
4. `error = noisy_fraction - target_quantile`;
   `scale = max(1 + clip_learning_rate · error, 1e-6)`;
   `clip_value = clamp(clip_value · scale, min_clip, max_clip)`. Too many
   clients over threshold (`error > 0`) *raises* the bound — the
   opposite of Andrew et al.'s own "fraction below the clip" convention,
   since this implementation tracks the fraction *over* the clip
   instead. (A sign-flip bug here — lowering the bound when too many
   clients were being clipped — was caught by a real direction-checking
   test during development, not by inspection; see
   `test_clip_moves_toward_target_quantile` in
   `python/tests/test_privacy_foundations.py`.)

Step 2's noised count query is accounted by its own dedicated instance
of the Gaussian-mechanism accountant (`sample_rate=1.0` — see
[privacy-mathematics.md](privacy-mathematics.md) for why this reuses the
formula, not the epsilon, of user-level DP's accountant).

## Wiring into a round

`RunConfig.adaptive_clipping_enabled` (default `false`, unchanged
pre-existing behavior when off) gates whether `finalize_round` asks
`AdaptiveClipController::clip_value()` for this round's bound instead of
the fixed `initial_clipping_bound`. After aggregation, the controller's
`step(over_threshold_count, cohort_size)` both advances the bound for
*next* round and returns this round's `AdaptiveClippingLedgerEntry`
(clip value used, noisy fraction, epsilon, delta — never the raw count).

## Statistical validation

Beyond the direction/monotonicity checks, a dedicated convergence test
(`test_clip_bound_converges_near_the_target_quantile_norm`) draws client
norms from a distribution with a known true median, starts the
controller deliberately far from it, and confirms it converges to within
25% of the true value after 150 rounds — validating the *steady-state
behavior*, not just the per-step direction.
