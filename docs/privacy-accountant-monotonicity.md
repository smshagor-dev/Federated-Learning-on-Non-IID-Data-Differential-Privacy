# Accountant Monotonicity Store

**Status: Implemented and Validated, including live rejection of a
non-increasing accountant step and a lower epsilon at a higher step
against a real containerized coordinator.** See
`cpp/coordinator/include/fl_coordinator/accountant_monotonicity_store.hpp`,
`cpp/coordinator/src/accountant_monotonicity_store.cpp`. Unit-tested
locally via MSVC (`run_accountant_monotonicity_store_tests`,
`cpp/coordinator/tests/accountant_monotonicity_store_test.cpp`).

## What this is

A restart-safe store, mirroring `WorkerIdentityRegistry`/
`ReplayProtectionStore`'s exact persistence pattern (atomic temp-file+
rename writes, FNV-1a checksum trailer, throws rather than silently
starting empty on corruption), that answers: "does this signed sample
privacy record's accountant step look like a real continuation of this
(run, client, worker, accountant type) track's own prior history?"

One track per `(run_id, client_id, worker_id, accountant_type)` —
deliberately **not** keyed by `configuration_hash` (a changed
configuration_hash for an otherwise-identical track must be *rejected*,
not silently treated as the start of a new, independent track — see
below).

## What it rejects

| Candidate vs. the track's last-accepted state | Rejection reason |
|---|---|
| `step` not strictly greater than last accepted | `kStepNotIncreasing` |
| `epsilon` lower than last accepted | `kEpsilonDecreased` |
| `delta` different from the track's established value | `kDeltaChanged` |
| `configuration_hash` different from the track's established value | `kConfigurationHashChanged` |

A brand-new track (first time this exact `(run_id, client_id,
worker_id, accountant_type)` combination is seen) always accepts —
there is nothing to be lower than yet, matching
`ReplayProtectionStore`'s identical "a new track's first message is
still checked, but has nothing to violate" convention.

## `validate()`/`commit()` split

Same transaction-ordering rule as `ReplayProtectionStore`: `validate()`
is read-only; `commit()` is called by `coordinator_service.cpp` only
*after* `RunInstance::submit_client_result` has actually accepted the
result. A privacy-record rejection at any earlier stage (signature,
binding, monotonicity, budget-decision-consistency) never advances the
track's state.

## Deliberately unbounded (unlike `ReplayProtectionStore`)

`ReplayProtectionStore` bounds its track/nonce counts because an
attacker who can reach it (a certificate-authenticated but potentially
malicious worker) could otherwise grow it unboundedly with fresh
signing keys/streams. `AccountantMonotonicityStore` has no equivalent
requirement stated in its own specification and the natural cardinality
here — one track per `(run, client, worker, accountant)` combination
that has ever actually submitted a private result — is already bounded
by real run/client counts tracked elsewhere in this coordinator
(`WorkerRegistry`, `RunManager`), not by anything an unauthenticated
caller controls (every candidate reaching this store has already
passed signature verification).

## Explicit reset, not implemented as an RPC

`reset(TrackKey, reason, now)` exists at the store level (clears a
track back to "brand new") — this is the specification's "explicit
accountant reset process" requirement, deliberately the *only* way a
track's `last_accepted_step`/`last_epsilon` can move backward. **No RPC
exposes this yet** — doing so would need a new `ADMIN_CONTROL` RPC,
out of scope for this pass. Store-level only, unit-tested, not yet
operator-reachable.

## What this does not prove

Matches [signed-privacy-records.md](signed-privacy-records.md)'s
identical disclaimer: this store does not independently recompute
Opacus accounting. It detects a *replayed*, *rolled-back*, or
*configuration-drifted* submission relative to a worker's own prior
signed history — it cannot detect an honestly-computed but
mathematically wrong epsilon value.

## Simplification versus the parent specification

The specification additionally asked for: tracking `accountant_state_hash`
reuse-with-conflicting-step as its own distinct rejection rule, and a
"lower accountant step" vs. "duplicate accountant step with conflicting
data" distinction. Both are folded into the single `kStepNotIncreasing`
rule (`step <= last_accepted_step` rejected unconditionally, regardless
of whether the resubmitted data happens to match): any resubmission at
a non-increasing step is already rejected by strict step monotonicity,
so a separate hash-reuse-conflict code path would add complexity
without adding coverage beyond what step monotonicity already
guarantees. Stated as a deliberate simplification, not silently
narrowed.

## Live validation

Two real rejection scenarios were exercised against a live
containerized coordinator (see
[signed-privacy-records.md](signed-privacy-records.md)'s "Live, real,
end-to-end validation" section for the full scenario list):

* A resubmission at the same `accountant_step` as the last accepted one
  (2, after 2 was already committed) was rejected with
  `"accountant_step 2 does not exceed the last accepted step 2"`.
* A resubmission at a higher step (5) but with a lower epsilon (0.1)
  than the last accepted epsilon (0.6) was rejected with
  `"epsilon 0.100000 is lower than the last accepted epsilon 0.600000"`.

## What is deferred

* No RPC exposes `reset()` — see above.
* `configuration_hash` changes are rejected but the *correct* value for
  a given task/round is never independently verified against the
  coordinator's own assigned `SampleLevelDPConfig` — see
  [signed-privacy-records.md](signed-privacy-records.md)'s "Deviations"
  section.
* No security event, metric, or audit record is emitted specifically
  for a monotonicity violation beyond the structured gRPC error message
  and rejection reason string.
