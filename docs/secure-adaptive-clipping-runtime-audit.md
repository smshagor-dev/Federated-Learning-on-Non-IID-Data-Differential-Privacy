# Secure Adaptive Clipping with Private Indicator Aggregation — Audit (Work Area A)

Written before implementation, per this project's established working
method (see the equivalent section in
`docs/secure-hybrid-dp-runtime-audit.md` for the precedent this mirrors).
Covers the required audit of the existing **cleartext** adaptive
clipping mechanism, classifies each component for reuse under secure
aggregation, records code-to-documentation discrepancies, and states
what gets full depth, bounded depth, or disclosed deferral.

Historical note: this file is intentionally the pre-implementation
audit. Secure adaptive clipping under secure aggregation has since been
implemented and live-validated; see
`docs/secure-adaptive-clipping-runtime-report.md` for the fresh runtime
evidence and current status. Any wording below that says the secure
adaptive-clipping path "does not exist" should be read as historical
audit context, not the current state of the tree.

## Critical framing: a mature cleartext mechanism already exists

Unlike the hybrid slice (where the target mechanism was net-new), a
**complete, tested, cross-language-parity-verified** cleartext adaptive
clipping mechanism already exists and is wired into
`RunInstance::finalize_round` for `USER_LEVEL_DP`/`HYBRID_DP` runs
(`docs/adaptive-clipping.md`, `cpp/core/include/fl_core/privacy.hpp:276-361`,
`cpp/core/src/privacy.cpp:253-306`,
`python/src/fl_platform/privacy/adaptive_clipping.py`). This is the
"existing non-secure adaptive clipping behavior" the task instructs me
to audit and reuse — not a description to be re-derived from the
task's own suggested formulas.

**What does not exist**: adaptive clipping under **secure aggregation**.
`AcquireTask` unconditionally rejects it today:

```cpp
// cpp/coordinator/src/coordinator_service.cpp:1488-1500
} else if (run.adaptive_clipping_enabled()) {
    secure_config.set_privacy_mode_compatible(false);
    secure_config.set_privacy_incompatibility_reason(
        "SECURE_AGGREGATION_ADAPTIVE_CLIPPING_UNSUPPORTED: clipping indicators are "
        "not themselves securely aggregated yet");
}
```

This slice's job is to lift that rejection by giving the worker a way
to compute its own local indicator and securely aggregate it — feeding
the *existing, unmodified* `AdaptiveClipController` a securely-decoded
count instead of a coordinator-computed cleartext one. It is **not** a
green-field mechanism design.

## Existing indicator definition — differs from the task's suggestion, reused as-is

The task's own suggested definition is `b_i = 1[r_i <= C_t]` (indicator
= 1 when the update is **at or below** the bound). The existing,
tested, cross-language-parity-verified implementation uses the
**complement**:

```
indicator_i = 1[r_i > C_t]     (over-threshold indicator)
```

`over_threshold_count = sum_i indicator_i` is what
`AdaptiveClipController::step(over_threshold_count, cohort_size)`
consumes (`cpp/coordinator/src/run_manager.cpp:923-930`,
`cpp/core/src/privacy.cpp:267-301`). Per the task's own explicit
instruction ("Reuse its semantics when mathematically sound... document
the definition and derive the update direction explicitly... do not
combine incompatible formulas silently"), **this slice reuses the
existing over-threshold definition unchanged, not the task's suggested
at-or-below definition.** Two independent formulas for the same
mechanism would be a real correctness hazard — this codebase's own
history already contains a caught sign-flip bug here (see
`docs/adaptive-clipping.md`'s note on `test_clip_moves_toward_target_quantile`),
which is exactly the kind of error silently mixing two indicator
conventions would reintroduce.

Direction, derived explicitly (both conventions agree on direction,
disagree on which fraction and which sign carries the meaning):

- Existing: `error = noisy_over_threshold_fraction - target_quantile`;
  `scale = max(1 + learning_rate * error, 1e-6)`;
  `clip *= scale`, clamped to `[min_clip, max_clip]`. Too many clients
  *over* threshold (`error > 0`) → bound too low → **raise** it.
- Task's suggested log-space form (`b_i=1[r<=C]`,
  `C_{t+1} = C_t * exp(lr * (target_quantile - noisy_fraction))`):
  too few clients *at-or-below* the bound (fraction low, meaning many
  are over) → `target_quantile - noisy_fraction` positive → **raise**
  it. Same direction, complementary variable, different (linear-scale
  vs. log-space/exponential) mathematical form.

Both directionally agree; only the existing, tested, linear
multiplicative form (Andrew et al. 2021, as actually implemented here)
is used. The task's log-space formula is **not** implemented — using
it would require either re-deriving and re-testing the whole controller
from scratch (duplicating a mechanism that already has convergence
tests) or running two different formulas for the same statistic
depending on secure-vs-cleartext mode, which the task explicitly
forbids ("do not combine incompatible formulas silently").

## Indicator count sensitivity

Confirmed 1: "one client can change [`over_threshold_count`] by at
most 1" (`cpp/core/src/privacy.cpp:276-278`), for the add/remove-style
per-user adjacency this codebase already uses for user-level DP
(`docs/secure-user-level-dp-semantics.md`'s adjacency section — the
same adjacency model applies here since the indicator mechanism
protects the same per-user privacy unit). The existing noise
calibration already uses sensitivity 1 directly
(`noise_std = count_noise_multiplier`, no separate sensitivity
multiplier) — this slice does not change that calibration, only its
input source (secure-decoded sum instead of a coordinator-computed
cleartext sum).

## Existing component inventory

| Component | Location | Classification |
|---|---|---|
| `AdaptiveClippingConfig` | `cpp/core/include/fl_core/privacy.hpp:295-306` | Reusable unchanged — already has every numeric field the task's suggested `SecureAdaptiveClippingConfiguration` wants (initial/min/max clip, target_quantile, learning_rate, count_noise_multiplier, target_delta, epsilon_budget) |
| `AdaptiveClipController` | `privacy.hpp:315-361`, `privacy.cpp:253-306` | Reusable unchanged — `step(over_threshold_count, cohort_size)`, `clip_value()`, `epsilon()`, `projected_epsilon_after_one_more_round()`, `restore(clip_value, steps)` is exactly the API the secure path needs too |
| `AdaptiveClippingLedgerEntry` | `run_manager.hpp:148-157` | Reusable unchanged — already the per-round accounting record (run_id, round_id, epsilon, delta, clip_value, noisy_over_threshold_fraction) |
| `PrivacyMetricsSnapshot.{has_clipping,clipping_epsilon,clipping_delta,current_clip_value}` | `run_manager.hpp:180-183` | Reusable unchanged — already models clipping as its own, separate mechanism section |
| Checkpoint persistence (`adaptive_clip_value`, `adaptive_clip_accountant_steps`, `adaptive_clipping_ledger_entry`) | `run_manager.cpp:1507-1522`, `1727-1798` | Reusable unchanged — clip bound and accountant step count already survive a restart; this **is** the "clip-state persistence" Work Area E asks for, already atomic (whole-checkpoint write), already checksummed (existing checkpoint format), already schema-validated on restore |
| Budget check (`check_reactive_budget("clipping", ...)`) | `run_manager.cpp:1096-1098`, `847-850` | Reusable unchanged — a non-mutating epsilon-budget gate identical in shape to user-level DP's own (no persisted reservation entity for either mechanism today — see below) |
| `RunConfig.adaptive_clipping_enabled` / `.adaptive_clipping` | `run_manager.hpp:104-105` | Reusable unchanged — the config surface this slice binds into signed tasks |
| Worker-side norm computation | `python/src/fl_platform/secure_aggregation/user_level_clipping.py::compute_global_l2_norm` | Reusable unchanged — the worker already computes this exact deterministic norm before clipping; the indicator is one comparison against the signed clip bound, computed immediately after |
| `SignedUserLevelPrivacyAttestation` | `proto/worker/worker.proto:552-581` | Reusable as a template, not extended in place — a new, separate self-contained signed message follows its exact shape (see Work Area H below) |
| HKDF purpose-label domain separation | `secure_aggregation_crypto.hpp:109-110`, `crypto.py:42-43` | Reusable pattern, additive — a third sibling label, `clipping_indicator_mask_stream` |
| `derive_weight_mask`/weight-masking pipeline | `secure_aggregation_tensor_mask.{hpp,cpp}`, `tensor_mask.py:72-73` | Reusable unchanged — `derive_weight_mask` is already `derive_tensor_mask_stream(..., 1).front()`; the indicator mask is the identical shape (one scalar), so the identical function is called with a different purpose label, not a new code path |
| `MaskedClientUpdate` proto message | `proto/worker/worker.proto:489-533` | Requires additive extension — next free field number 26 |
| `SubmitMaskedClientUpdate` verification ladder | `coordinator_service.cpp:3952-4644` | Requires additive extension — a new verification sub-block after the user-level attestation block (line ~4211), staged/committed under the exact same two-phase discipline already used for the sample-record and outer replay checks |
| `SecureAggregationSessionManager::finalize()` | `secure_aggregation_session_manager.hpp:189-192` | Requires additive extension or a parallel indicator-sum decode path — see Work Area M |
| `coordinator_task_signing.cpp`'s hash functions | `:300-467` | Requires additive extension — a new `secure_adaptive_clipping_configuration_hash` sibling to `secure_user_level_dp_configuration_hash` |
| `UserLevelAccountant` (single-mechanism RDP) | `privacy.hpp:250-274` | **Does not support composing two different-noise-multiplier Gaussian mechanisms into one epsilon** — see "Accountant composition" below |
| Budget reservation store | *(does not exist as a persisted entity — confirmed absent)* | N/A — see "Budget lifecycle" below |
| Restart reconciliation for privacy budgets | *(does not exist as code — `reconciliation_required` is hardcoded `false`)* | Disclosed gap, inherited unchanged — see "Restart reconciliation" below |
| Threshold secret sharing / dropout recovery | N/A | Incompatible/unsafe — unchanged, out of scope |

## Accountant composition: not built, deliberately not built here either

The task allows composition "when the accountant supports the two
Gaussian mechanisms... implemented through the accountant rather than
manually adding reported epsilon values" — conditional language, not a
mandate. `UserLevelAccountant` is a **single-mechanism** RDP accountant
(one `noise_multiplier`, one `sample_rate`, one RDP curve accumulated
per instance — `privacy.hpp:250-274`). `AdaptiveClipController` already
owns its **own separate instance** of it
(`privacy.cpp:258`), not a shared one with the model mechanism — this
is the codebase's own pre-existing "Critical Privacy Rule"
(`privacy.hpp:286-292`): *"the count query's epsilon/delta protect a
different neighboring relation... this mechanism's accountant is
entirely separate from `UserLevelAccountant`'s own instance/state, even
though both happen to reuse the identical Gaussian-mechanism RDP math."*

Building real cross-mechanism RDP composition (summing per-order RDP
curves across two differently-parameterized Gaussian mechanisms before
converting to one epsilon) would be a legitimate but substantial,
separately-reviewable privacy-math change — not something to bolt onto
an already large slice without dedicated scrutiny. **This slice keeps
the model mechanism and the indicator mechanism separately accounted,
exactly as the cleartext mechanism already does today, secure or not.**
Both remain under the user-level privacy unit (same adjacency, same
per-round cadence), reported side by side, never summed into one
number — consistent with the hybrid slice's "two epsilons, never
combined" precedent, extended here to "three epsilons" (sample-level,
user-level-model, user-level-indicator), still never combined.

## Budget lifecycle: no persisted reservation exists today, for either mechanism

`docs/secure-user-level-dp-publication-boundary.md` confirms explicitly:
the "reserve" step is a **non-mutating** projection
(`project_epsilon_after_one_more_round`) at session-creation time, not
a separately persisted, releasable record — there is no
`BudgetReservation` class or store anywhere in `cpp/`. The single
irreversible event is the accountant's `step()` call at finalize time.
This slice mirrors that **exact same lightweight pattern** for the
indicator mechanism (a non-mutating projection check at `AcquireTask`,
a single `adaptive_clip_controller_->step()` call at finalize) rather
than inventing a new persisted `RESERVED/COMMITTED/RELEASED` state
machine the model mechanism itself doesn't have. Building one for the
indicator mechanism alone, while the model mechanism it shares a
privacy unit with still lacks one, would be a false, misleading
sophistication asymmetry.

## Restart reconciliation: inherits the existing, disclosed gap

`GetSecureUserLevelPrivacyHealth` hardcodes `reconciliation_required =
false` today (`coordinator_service.cpp:4809`) — disclosed explicitly in
`docs/secure-user-level-dp-publication-boundary.md`'s "known
restart-reconciliation gap" section as a real, bounded, fail-safe-shaped
(not fail-secure-shaped) limitation: model state and the privacy ledger
can only ever roll back *together* (the same `save_checkpoint()` call
persists both), never one without the other. The indicator mechanism's
ledger entry is appended by the *same* `save_checkpoint()` call
(`adaptive_clipping_ledger_` is already in the checkpoint body) — so
it inherits the identical guarantee and the identical gap, unchanged.
This slice does not attempt to build automated reconciliation detection
that doesn't exist for the model mechanism either.

`SecureAggregationSessionStore::reconcile_after_restart` (a real,
tested, idempotent mechanism —
`secure_aggregation_session_store.cpp:214+`, called from `main.cpp:417`
before any RPC is served) already aborts any non-terminal secure
session on coordinator restart. Since the indicator mechanism's
per-round commit happens inside the same `apply_secure_aggregate_and_advance`
call as the model mechanism's commit (see Work Area M/Q below), a
coordinator restart mid-round aborts the whole secure session — neither
mechanism partially commits, matching the "Neither output published →
release both reservations" requirement structurally, not via new code.

## Documentation discrepancies found

| Requested doc | Actual doc |
|---|---|
| `docs/secure-user-level-runtime-report.md` | `docs/secure-user-level-dp-runtime-report.md` |
| `docs/secure-user-level-publication-boundary.md` | `docs/secure-user-level-dp-publication-boundary.md` |
| `docs/privacy-accounting.md` | `docs/privacy-mathematics.md` |
| `docs/privacy-budget-policy.md` | `docs/privacy-budget-policies.md` |
| `docs/security-runtime-scenario-registry.md` | `scripts/security-validation/registry.py` (no doc; self-documenting) |

All others in the task's reading list exist under their literal names
(`docs/secure-hybrid-dp-*`, `docs/secure-user-level-dp-semantics.md`,
`docs/secure-user-level-operations-report.md`,
`docs/secure-aggregation-masked-runtime-report.md`,
`docs/secure-aggregation-privacy-compatibility.md`,
`docs/security-events.md`, `docs/security-metrics.md`,
`docs/security-runtime-validation.md`, `docs/security-ci.md`,
`docs/known-limitations.md`, `docs/docker-runtime.md`).

## Environment note: pre-existing full regression baseline confirmed fresh

Before any code change, a fresh regression run was executed and matches
the task's stated starting evidence exactly: C++ local 7/7, C++ Docker
gRPC combined 15/15 (8/8 gRPC-gated within that), Python 493 passed/1
skipped, Go build/vet/test clean, Web lint/typecheck/build clean with
46/46 Vitest, terminology check passing, proto contract check passing.
Git status was clean at the start of this slice (the prior slice's work
was committed as `c5f6f08` between sessions).

One environment-level blocker was found, not a code defect: attempting
to bring up this project's Docker Compose stack to run the existing
`secure-aggregation-user-level-dp`/`secure-aggregation-hybrid-dp`
runtime-validation harness groups failed with `port is already
allocated` on `6379` — an unrelated, already-running container
(`bloodbridge-redis-1`, a different project on this machine) is
squatting on the host port this project's `docker-compose.dev.yml`
also publishes redis on. This blocks *any* live Docker validation for
this session, not just the pre-implementation confirmation run,
deferred to the point this slice actually needs live validation (Work
Area AI) rather than resolved speculatively now.

## Scope statement

This task's literal specification (Work Areas A through beyond AI,
truncated in the source specification itself past 50,000 characters) is
again far larger than any single slice can cover at uniform, maximal
depth. Following this project's established precedent (five prior
oversized slices, each with a disclosed Full/Bounded/Deferred split),
and specifically continuing the hybrid slice's own precedent of
reusing existing mechanisms rather than inventing parallel ones:

**Full depth** (real, working, tested, live-validated code):
- Work Areas A (this audit), B (semantics doc).
- Work Area D: an `AcquireTask` compatibility gate lifting the current
  blanket rejection when privacy mode is `USER_LEVEL_DP`/`HYBRID_DP`
  and the adaptive configuration is valid.
- Work Area F: a `secure_adaptive_clipping_configuration_hash` sibling
  to the existing `secure_user_level_dp_configuration_hash`, bound into
  the signed task the identical way.
- Work Areas G/I/J: worker-side indicator creation (one comparison
  against the already-computed norm), ring representation (reuse the
  existing fixed-point/ring infrastructure — the indicator is `{0,1}`,
  trivially representable), and pairwise masking (a third HKDF purpose
  label, reusing `derive_weight_mask`'s exact code path unchanged).
- Work Area H: a new, bounded, self-contained signed binding message
  (not a full extension of `SignedUserLevelPrivacyAttestation`, to keep
  the two concerns — clipping-configuration evidence vs.
  adaptive-indicator evidence — independently verifiable, matching how
  `SignedSamplePrivacyRecord` and `SignedUserLevelPrivacyAttestation`
  are already kept separate rather than merged).
- Work Area K/L: `MaskedClientUpdate` additively extended, coordinator
  verification block reusing the exact staged-then-committed discipline
  every other check in `SubmitMaskedClientUpdate` already uses.
- Work Areas M/N/O: complete-cohort indicator reconstruction, noise
  generation reusing the existing noise-provider infrastructure with a
  distinct configuration, and the state update calling the *existing*
  unmodified `AdaptiveClipController::step()`.
- Work Areas P/Q (bounded, per "Budget lifecycle" above): the
  already-existing single-commit-point pattern wired correctly for the
  indicator mechanism, documented precisely rather than wrapped in a
  new, asymmetric state machine.
- Work Area T/U: `USER_LEVEL`/`HYBRID` runtime integration end to end.
- Work Area V: dropout aborts the whole session (model + indicator,
  structurally, since both commit inside the same
  `apply_secure_aggregate_and_advance` call).
- Work Area X (bounded): a representative event vocabulary wired at
  real call sites, not the full ~21 suggested names.
- Work Areas AD/AE/AF/AG: real C++ and Python tests, including
  cross-language golden fixtures for the new hash/binding.
- Work Area AH/AI (bounded): a real runtime-validation harness group
  and a real multi-worker live Docker validation.

**Deferred, disclosed with reasons, never reported as done** (mirroring
the hybrid slice's own precedent of zero net-new Go routes/Web pages,
instead fixing/extending existing ones cheaply):
- Work Area C: no new `SecureAdaptiveClippingConfiguration` mega-type —
  the existing `AdaptiveClippingConfig`/`AdaptiveClipController` already
  serve this role; only the thin task-signing/wire-binding layer around
  them is new.
- Work Area E: no new `SecureAdaptiveClipState` type — the existing
  checkpoint fields (`adaptive_clip_value`,
  `adaptive_clip_accountant_steps`, `adaptive_clipping_ledger`) already
  are this state, already atomic/persisted/restart-safe.
- Work Areas R/S: accountant composition and a new persisted budget
  reservation state machine are explicitly not built — see the two
  dedicated sections above.
- Work Areas Z/AA/AB/AC: no new Go route family, no new Web page, no
  new Playwright specs this slice. The cheapest honest equivalent —
  surfacing indicator-mechanism epsilon/clip-value through the
  *existing* `/api/v1/secure-aggregation/privacy/budget` and `/rounds`
  responses, kept strictly separate from the model mechanism's own
  fields — is evaluated for low cost/high value inclusion; a dedicated
  `/adaptive-clipping/*` route family and page are deferred to a
  follow-up "Operations, Observability, and Release Evidence" slice,
  mirroring the exact precedent already set twice in this project.
- Work Area Y: metrics reuse the existing `fl_secure_user_dp_*` series
  where the indicator mechanism's data naturally fits (e.g. clip-value
  gauges), rather than a full ~21-series-name buildout.
- Automated restart reconciliation (Work Area W) — inherits the
  existing, disclosed gap; not newly built.
- Every item under "Explicitly Out of Scope" in the task (variable user
  weights, per-layer/per-tensor clipping, median/histogram protocols,
  ZK proofs, attestation, TEE/TPM, Byzantine robustness, threshold
  secret sharing, dropout recovery, and the rest) — none of it is
  touched, matching the task's own explicit prohibition.

See `docs/secure-adaptive-clipping-semantics.md` for the full formal
specification this scope statement implements.
