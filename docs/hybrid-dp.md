# Hybrid DP

**Status: implemented & tested, including a full gRPC round-trip test
and (for the sample-level half) live Docker Compose validation.**
Source: `fl::core::PrivacyMode::kHybridDp` (checked alongside
`kUserLevelDp`/`kSampleLevelDp` throughout `run_manager.cpp` and
`coordinator_service.cpp` — there is no separate hybrid-specific code
path, only "is sample-level active" and "is user-level active" checks
that both happen to be true). Tests:
`cpp/coordinator/tests/hybrid_dp_test.cpp`,
`cpp/coordinator/tests/coordinator_service_test.cpp`'s hybrid-DP gRPC
block.

## What it is

Sample-level DP (Python, per-training-example, via Opacus) and
user-level DP (C++, per-client-round-contribution, central clip+noise)
active **simultaneously** on the same run. This is not a third
mechanism — it's the first two mechanisms' existing, independent code
paths both switched on for one run. The Critical Privacy Rule (see
[privacy-mathematics.md](privacy-mathematics.md)) applies in full:
sample-level and user-level epsilon are never combined into a single
number anywhere, including under hybrid mode.

## Wiring

`PrivacyMode::kHybridDp` is checked everywhere `kSampleLevelDp` or
`kUserLevelDp` individually would be:

* `config_from_request` maps *both* `sample_level` and `user_level` wire
  sub-configs into `RunConfig` when mode is `kHybridDp` (each validated
  independently — a hybrid run with an invalid sample-level config is
  rejected the same way a pure-sample-level run would be).
* `RunInstance::make_descriptor` marks a dispatched task
  `sample_level_dp_active = true` (with the sample-level config
  attached) whenever mode is `kSampleLevelDp` *or* `kHybridDp` — a
  worker cannot tell from the task alone whether user-level DP is also
  active for this run, and doesn't need to: it only ever applies the
  mechanism it owns.
* `RunInstance::finalize_round`'s clip/aggregate/noise pipeline
  (see [user-level-dp.md](user-level-dp.md)) runs whenever mode is
  `kUserLevelDp` *or* `kHybridDp`, unconditionally on whether any
  particular client's submission carried a sample-level ledger entry.
* `acquire_task`'s compatible-worker-only gate (see
  [worker-privacy-capabilities.md](worker-privacy-capabilities.md))
  applies whenever mode is `kSampleLevelDp` *or* `kHybridDp` — a worker
  that never advertised `supports_sample_level_dp` is refused a task
  from a hybrid run exactly as it would be from a pure-sample-level run.

## What a hybrid round actually produces

For one round with N participating clients: **N** `SampleLevelLedgerEntry`
records (one per client, each independently computed by that client's
own Opacus instance) and **one** `UserLevelLedgerEntry` record (the
round's central clip+noise step, computed once over the whole cohort's
already-locally-DP-trained deltas). These two ledgers are never zipped
together — see [privacy-ledger.md](privacy-ledger.md).

## Compatibility

Not every algorithm supports hybrid DP just because it supports both
mechanisms individually: `hybrid_status()` (both languages — see
[privacy-compatibility-matrix.md](privacy-compatibility-matrix.md))
takes the *worse* of the two mechanisms' individual statuses for a given
algorithm, since hybrid mode composes both and is only as usable as its
weakest link.

## Live validation

Sample-level DP's half of hybrid mode was live-validated end-to-end
through a real Python worker container running actual Opacus DP-SGD
training (see [user-level-dp.md](user-level-dp.md)'s live-validation
note and [docker-runtime.md](docker-runtime.md) for the full session).
User-level DP's half was already independently live-validated the same
session. A combined single-run hybrid live test (both mechanisms active
on the same run, in the same Docker Compose session) was not performed
this phase — the gRPC-level integration test
(`coordinator_service_test.cpp`'s hybrid block) exercises the identical
combined code path with real wire messages, just not inside live
containers; see known-limitations.md.
