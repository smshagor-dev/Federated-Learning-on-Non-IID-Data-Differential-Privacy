# Secure User-Level Differential Privacy Runtime — Audit (Work Area A)

Written before implementation, per this project's established working
method. Covers the required audit of the existing (cleartext)
user-level DP flow, classifies each behavior for reuse under secure
aggregation, records code-to-documentation discrepancies found while
gathering it, and (because the literal task specification — Work Areas
A through AK, a 62-scenario Docker validation matrix, full Go/web
observability, formal statistical noise certification, and full
performance benchmarking — is again far larger than any single slice
this project has scoped before) states explicitly what gets full
depth, what gets real-but-bounded depth, and what is deliberately
deferred with a disclosed reason. This mirrors the precedent set by
`secure-aggregation-masked-runtime-audit.md`'s own scope statement for
the immediately prior slice.

## Code-to-documentation discrepancies found

The task's "Required Working Method" step 3 lists 18 docs to read.
Several named docs do not exist under those names — the closest real
equivalents are used instead:

| Requested doc | Actual doc |
|---|---|
| `docs/fixed-point-secure-encoding.md` | Covered inside `secure-aggregation-masked-runtime-audit.md` (the "Verified starting state" section) and `secure-aggregation-protocol-foundation.md` |
| `docs/secure-aggregation-privacy-compatibility.md` | `docs/privacy-compatibility-matrix.md` (cleartext-only today) plus the `AcquireTask` gating logic in `coordinator_service.cpp` (no doc dedicated to the secure-specific gate yet — this slice adds `docs/secure-user-level-dp-semantics.md` to cover it) |
| `docs/user-level-differential-privacy.md` | `docs/user-level-dp.md` |
| `docs/privacy-accounting.md` | `docs/privacy-mathematics.md` |
| `docs/privacy-budget-policy.md` | `docs/privacy-budget-policies.md` |

**A real staleness bug found while reading `docs/user-level-dp.md`**:
its "Noise generation" section states `SecureNoiseProvider` (a plain
`std::mt19937_64` seeded once from `std::random_device`, explicitly
**not** a CSPRNG) is "the runtime default" for user-level DP noise.
Direct code reading (`run_manager.cpp`'s constructor) shows this is no
longer true — `noise_provider_` is constructed as
`fl::core::CryptoSecureNoiseProvider` (real OS-CSPRNG-backed, via
`OsEntropySecureRandomProvider`/`BCryptGenRandom`/`/dev/urandom`),
matching the Privacy Engineering phase's own upgrade. `user-level-dp.md`
was never updated after that upgrade landed. Not fixed in this slice
(out of scope — this slice's own docs correctly describe the current
state; fixing a stale sibling doc is a separate, small cleanup this
report flags rather than silently leaves undiscovered).

## Verified starting state, re-confirmed by direct code reading

Gathered via a dedicated read-only research pass over the actual
source (not the task's own summary). Every claim below cites a real
file/function.

### 1. Existing user-level DP clipping is coordinator-side, on cleartext deltas — confirmed structurally incompatible

`fl::core::clip_client_delta`/`compute_shared_norm` (`cpp/core/include/fl_core/privacy.hpp`)
compute one global FP64 L2 norm across every shared tensor, scale by
`min(1, C/(‖δ‖₂+ε))`. The call site is `RunInstance::finalize_round`
(`cpp/coordinator/src/run_manager.cpp`, ~line 883-925) — **after** the
coordinator has already collected every client's full plaintext
`ClientUpdate.delta` into `round_results_`, one client at a time,
**before** `aggregator->aggregate(...)`. This is exactly the pattern
Secure Aggregation's whole design exists to prevent, and it is already
the documented reason `AcquireTask`'s session-creation gate rejects
`kUserLevelDp` for secure sessions today (`SECURE_AGGREGATION_USER_LEVEL_DP_UNSUPPORTED`,
`coordinator_service.cpp`). No worker-side clipping code exists
anywhere (`python/src/fl_platform/worker/task_runner.py` has only
`run_local_training` and `run_private_local_training` — sample-level
Opacus, not whole-update L2 clipping).

**Classification: Incompatible with secure aggregation, unchanged.**
The existing coordinator-side clip/aggregate/noise pipeline in
`finalize_round` is left completely untouched by this slice — it
continues to serve non-secure `USER_LEVEL_DP` runs exactly as before.
A **new**, parallel, worker-side clip path is built for the secure
case; the two never share a code path (deliberately — sharing would
risk silently regressing the already-validated cleartext behavior for
a change this slice doesn't need to make).

### 2. Sampling rate: existing cleartext path already claims amplification — the new secure path must NOT reuse this assumption

`docs/user-level-dp.md`'s own description (confirmed by
`finalize_round`'s accountant `.step()` call site): `sample_rate =
target_clients_per_round / total_clients`, i.e. the existing cleartext
mechanism already treats the scheduler's cohort selection as if it
were genuine Poisson/random subsampling, feeding that ratio directly
into the RDP accountant as an amplification factor. **This project's
own Client Selection design has never been independently audited for
whether it actually satisfies the accountant's mathematical sampling
assumptions** (no such audit exists in `docs/`). This is exactly the
kind of unvalidated-amplification claim this slice's own Mandatory
instructions warn against repeating. **Classification: Unsafe to
reuse as-is.** The new secure path uses the conservative mandated
default (`q = 1`, `NO_AMPLIFICATION`) unconditionally — it does not
inherit or reuse the cleartext path's `target_clients_per_round /
total_clients` ratio. This is a deliberate divergence from the
existing cleartext mechanism's assumption, not an oversight; both are
documented side by side in `secure-user-level-dp-semantics.md`.

### 3. Noise formula: existing cleartext path adds noise to the already-averaged delta; the new mechanism adds noise to the pre-division sum

Existing: `noise_std = noise_multiplier * clip_bound / effective_cohort_size`,
added to `result.model_delta` **after** `aggregator->aggregate(...)`
has already produced a weighted **average** (FedAvg divides during
aggregation) — dividing `noise_std` by cohort size here is the correct
way to calibrate noise for an already-averaged quantity.

New (secure) mechanism, per this task's own mandated formula:
`private_average = (sum_of_clipped_user_updates + gaussian_noise) /
cohort_size` — noise is added to the **raw sum** (what
`SecureAggregationSessionManager::finalize()` decodes internally,
**before** its existing divide-by-weight-sum step), then the whole
noised sum is divided once. This means `noise_std` for the secure path
is `noise_multiplier * effective_sensitivity` — **not** divided by
cohort size, because the division happens once, afterward, to the
already-noised sum. **Classification: Reusable with modification** —
same `NoiseProvider`/`UserLevelAccountant`/`UserLevelDPConfig` C++
types, structurally different injection point and a different
noise-scale formula (documented explicitly, not silently reused).

### 4. `finalize()` and `apply_secure_aggregate_and_advance` have zero privacy logic today — confirmed by direct reading

`SecureAggregationSessionManager::finalize()`
(`cpp/coordinator/src/secure_aggregation_session_manager.cpp`,
~609-721): decodes the masked-ring sum per tensor into cleartext FP64
values, then **immediately** divides every element by the decoded
weight sum (lines ~695-701) and returns. No clip, no noise, no
accountant, no ledger. `RunInstance::apply_secure_aggregate_and_advance`
(`cpp/coordinator/src/run_manager.cpp`, ~1125-1172): adds the decoded
delta onto `global_model_`, advances `model_version_`, checkpoints —
also zero privacy logic. **This is the exact, minimal injection
surface this slice extends** — noise goes inside `finalize()`, before
its existing divide loop (an optional, backward-compatible parameter);
accountant-step + ledger-write goes inside
`apply_secure_aggregate_and_advance`, after the model delta is
successfully applied (both already have everything else needed —
`RunInstance` already owns `user_level_accountant_`, `noise_provider_`
(a real `CryptoSecureNoiseProvider`), and `user_level_ledger_` as
existing members, constructed whenever `config_.privacy_mode ==
kUserLevelDp` regardless of secure-aggregation status). **Classification:
Reusable unchanged (the accountant/noise-provider/ledger types and
instances) + net-new (the injection wiring itself).**

### 5. No true reserve/commit budget lifecycle exists anywhere

All existing accounting is immediate-apply: `.step()` runs
unconditionally inside `finalize_round`/Python training, gated only by
a **non-mutating** pre-check (`project_epsilon`/`project_next_epsilon`
on a throwaway clone) for `STOP_BEFORE_EXCEEDING`. There is no
persisted reservation ID/state machine anywhere in this codebase.
**Classification: Deferred in the literal sense the task's own
suggested field list implies (`RESERVED`/`COMMITTED`/`RELEASED`/
`EXPIRED`/`FAILED` as a fully separate persisted entity with its own
ID) — real but bounded in this slice.** The design adopted (see the
semantics doc) reuses `RunInstance`'s existing round-progression
idempotency guard (`current_round_id_`/`state_machine_` — already
proven to make `apply_secure_aggregate_and_advance` a safe no-op on
retry) as the *sole* commit gate: the accountant is mutated at exactly
one call site, guarded by an invariant that already exists and is
already tested. "Reservation" becomes a non-mutating pre-check at
session-creation time (`project_epsilon(1)` against
`epsilon_budget`), not a separately persisted record. This is a
deliberate, documented simplification — not silently narrower than
what the task describes without disclosure.

### 6. `AccountantMonotonicityStore` — reusable, narrower purpose than budget reservation

`cpp/coordinator/include/fl_coordinator/accountant_monotonicity_store.hpp`:
enforces non-decreasing `step`/`epsilon`, unchanged `delta`/
`configuration_hash` per `(run_id, client_id, worker_id,
accountant_type)` track, via the same validate-then-commit split as
`ReplayProtectionStore`. **Classification: Not reused this slice.**
Its purpose (detect replay/rollback of an *individual client's*
sample-level accounting record) doesn't map onto a single
coordinator-authoritative user-level accountant instance per run — the
new mechanism's "commit exactly once" guarantee comes from
`RunInstance`'s round-progression invariant instead (item 5), which is
the correct level for a per-run (not per-client) accountant. Recorded
here so a future slice doesn't assume it was silently repurposed.

### 7. No per-mechanism configuration hash exists for user-level DP

`privacy_configuration_hash` (`coordinator_task_signing.{hpp,cpp}` /
`.py`) only ever hashed `SampleLevelPrivacyFields`. No
`user_level_dp_configuration_hash`/`adaptive_clipping_configuration_hash`
exists. **Classification: Net new**, added as a sibling hash function
(matching the existing `secure_aggregation_configuration_hash`
precedent — a new, independently-computed hash folded into the signed
task bytes, never merged into the unrelated existing one).

### 8. `SecureAggregationTaskBinding`/`SecureAggregationSessionConfig` already at fields 1-15 / 1-28

Both additive, gated at field-number-stability by
`scripts/verify_proto_contracts.py`. This slice appends new fields
starting at 16 / 29 respectively — never renumbers or removes an
existing field.

### 9. `PrivacyMode` is an orthogonal axis to `secure_aggregation_active` — no new enum value needed

`fl::core::PrivacyMode::kUserLevelDp` already exists and is already
threaded through `RunConfig`/`AcquireTask`. The **only** thing gating
secure user-level DP today is one `if` branch in `AcquireTask`'s
session-creation block that unconditionally treats `kUserLevelDp` as
incompatible. **Classification: Reusable with modification** — this
slice adds the *conditions* under which that branch instead accepts
`kUserLevelDp` (uniform weight already enforced elsewhere, no adaptive
clipping, algorithm is `fedavg`, and the run's `user_level_privacy`
config passes the new validation in Work Area D) rather than
introducing a new `PrivacyMode` value or a parallel enum. This keeps
the wire contract's existing orthogonal-axes design (`privacy_mode` +
`secure_aggregation_active` as independent dimensions) intact.

### 10. Go and Web have zero secure-aggregation wiring today

`SecurityOverviewResponse.SecureAggregationAvailable` is a **hardcoded
`false`** in `go/internal/transport/httpapi/security_overview.go` — Go
never calls any of the six existing secure-aggregation RPCs. No
`web/app/security/**` subpage exists for secure aggregation at all.
**Classification: Deferred, disclosed** — matches the immediately
prior slice's own AG/AH deferral (Go read-only APIs and web
observability), for the same reason: this is a large, independent unit
of work (new Go RPC client methods + HTTP routes + permissions +
telemetry + a new web page) that would roughly double this slice's
size for a read-only observability surface, not the core privacy
mechanism itself. Recorded honestly, not silently skipped.

### 11. Sessions never removed from `sessions_` after finalize

`contributions_by_worker` is `.clear()`'d only on deadline-abort, never
after a successful `finalize()` — completed sessions accumulate in the
coordinator's in-memory `sessions_` map for the process lifetime.
**Classification: Unclear / pre-existing, not addressed by this
slice.** Not a correctness issue for the noise/accounting mechanism
this slice adds (masked values are individually meaningless without
every peer's mask, and this behavior predates this slice by two
slices) — flagged for a future memory-bounding pass, not fixed here to
avoid rewriting stable behavior without a demonstrated defect (the
Required Working Method's own instruction).

## Full behavior classification table

| Behavior | Classification |
|---|---|
| Coordinator-side cleartext clipping (`finalize_round`) | Incompatible with secure aggregation — left unchanged, not reused |
| `UserLevelAccountant`/`UserLevelDPConfig` (C++ types) | Reusable unchanged |
| `CryptoSecureNoiseProvider` (OS-CSPRNG) | Reusable unchanged |
| Existing noise-scale formula (`C/cohort_size`, post-average) | Reusable with modification (different injection point → different formula, both documented) |
| Existing sampling-rate assumption (`target_clients_per_round/total_clients`) | Unsafe to reuse as-is — new path forces `q=1` |
| `SecureAggregationSessionManager::finalize()` | Reusable with modification (new optional noise params) |
| `RunInstance::apply_secure_aggregate_and_advance` | Reusable with modification (new accountant/ledger commit) |
| `AccountantMonotonicityStore` | Not reused — different level of the system |
| Budget reserve/commit as a separately-persisted entity | Deferred — replaced by a narrower, real, disclosed design (item 5) |
| `AcquireTask`'s privacy-mode gate | Reusable with modification (conditions added, not restructured) |
| `privacy_configuration_hash` | Not extended — new sibling hash added instead |
| `SecureAggregationTaskBinding`/`SessionConfig` proto | Reusable, additive extension |
| Go/Web secure-aggregation observability | Deferred, disclosed |
| In-memory session accumulation after finalize | Unclear, pre-existing, not addressed |

## Scope statement

Consistent with — not a retreat from — the precedent set by every
oversized slice in this project so far:

**Full depth**: adjacency/sensitivity/sampling/weighting semantics
(Work Areas B, the "Mandatory Mathematical Decisions" section);
configuration contracts + validation (C, D) as a bounded but real
field set (see the semantics doc for exactly which of the task's
suggested 20 fields are load-bearing this slice vs. deferred); secure
task privacy binding (E); deterministic worker-side global L2 norm and
clipping (F, G); quantization-aware effective sensitivity (H); signed
user-level privacy attestation and its binding into
`MaskedClientUpdate` (I, J); worker masked-submission flow (K);
coordinator masked-update validation extensions (L); the authoritative
coordinator accountant and its reservation-as-pre-check design (M, N);
`q=1` sampling semantics (O); the vetted central Gaussian noise
provider wired into the secure path (P); aggregate noise application
(Q); finalization idempotency reusing the existing round-progression
guard (R); the privacy ledger entry (S); the compatibility matrix
update (T); dropout/restart behavior reusing the existing no-dropout
abort machinery (U, V); a representative security-event set (W); C++
and Python tests for all of the above (AA, AB); cross-language config/
attestation-hash fixtures (AC); a focused set of privacy-property
tests (AD); a bounded runtime-validation harness group (AE); real
multi-worker Docker validation proving clipping and noise actually
engage, not just wiring (AF, bounded scenario count — see below);
documentation (AK, the highest-value subset).

**Real but bounded**: metrics (X) — a representative counter set, not
every listed metric; CI gates (AI) — new tests land in the existing
`cpp-grpc`/`python` jobs' already-broad target/suite lists (matching
the immediately prior slice's own finding that no new CI *job*
structure was actually needed), plus explicit confirmation new
artifact-sanitation categories are covered; artifact sanitation (AJ) —
extended only where a genuinely new sensitive-data category exists
(noise tensors, attestation private-key material) that the existing
sanitation script doesn't already cover generically.

**Deferred, disclosed with reasons, never reported as done**: Go
read-only APIs and web observability (Y, Z) — matches the immediately
prior slice's own AG/AH deferral, for the same proportionality reason;
formal statistical noise certification as an independent Work Area
(AG) — folded into a real but small bounded-sample smoke assertion
inside the existing noise test suite rather than a separate multi-page
report, since a rigorous independent randomness certification is out
of proportion to one slice and was never implied to be achievable
here; performance benchmarking (AH) — the existing benchmark harness
is not extended this slice (a real gap, disclosed, not fabricated with
invented numbers); the full 62-scenario Docker validation matrix (AF)
— real, substantial live validation is performed (proving the
mechanism, not just RPC wiring), but not 62 hand-enumerated scripted
scenarios; every item in "Explicitly Out of Scope" (hybrid DP,
adaptive clipping under secure aggregation, threshold secret sharing,
dropout recovery, variable weighting, malicious-client clipping
verification, attestation-as-cryptographic-proof, TEE/TPM,
Byzantine-robust aggregation, and the rest) — none of it is
implemented, matching the task's own explicit prohibition.
