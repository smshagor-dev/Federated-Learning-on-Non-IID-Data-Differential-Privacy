# Secure User-Level Differential Privacy — Formal Mechanism Specification

Written before implementation (Work Area B), per the task's own
"Do not begin implementation until these decisions are explicit"
instruction. See `secure-user-level-dp-runtime-audit.md` for the
code-grounded audit these decisions are built on top of.

**Mandatory Privacy Trust Statement (verbatim, in force):** this
mechanism may claim user-level differential privacy only under the
explicit assumption that participating workers execute the signed
clipping configuration correctly. The coordinator cannot
cryptographically verify that a masked individual update was clipped
correctly. This implementation is: **honest-client-dependent
user-level DP**, **central differential privacy over a securely
aggregated clipped sum**, **an experimental research implementation**.
It is not: malicious-client-secure, verifiably-clipped,
attestation-backed, hardware-enforced, or range-proof-enforced.

## 1. Privacy unit

One participating worker/user per secure round. Exactly one logical
contribution per selected, frozen-cohort user — never a sub-user or
per-sample unit (that is sample-level DP, a separate, already-existing
mechanism, untouched by this slice).

## 2. Adjacency model — **add/remove-one-user**

Two users' training-set collections are adjacent if one is obtained
from the other by adding or removing exactly one user's entire
participation in the round. **This slice selects add/remove-one, not
replace-one.** Reasons: (a) it is the model the task's own worked
formula (`sensitivity ≤ C`) directly supports without an additional
factor-of-two proof obligation; (b) the frozen cohort's participant
set for one round is fixed by construction (the roster is signed and
closed before training begins) — "removing" a user from that frozen
set is the operationally meaningful adjacency question for this
mechanism (what does the aggregate look like with vs. without this
user's masked contribution), not "replacing" one user's data with
another's within a fixed-size cohort. Replace-one adjacency (requiring
`2C` sensitivity, per the task's own caveat) is **not** implemented or
claimed this slice — doing so without an explicit, separately reviewed
proof would violate the task's own "do not calibrate using `C` while
documenting replace-one adjacency without a valid proof" instruction.

## 3. Sensitivity

For global L2 clip norm `C` under add/remove-one adjacency, the L2
sensitivity of the **clipped sum** (before division) is bounded by
`C`: removing one user's clipped, at-most-`C`-norm contribution from
the sum changes the sum's L2 norm by at most `C`. This is the standard
result for a sum of independently clipped vectors under add/remove
adjacency and requires no additional proof beyond the clipping
guarantee itself (each term contributes at most `C`; removing one term
changes the sum by exactly that term, whose norm is bounded by `C`).

## 4. Clipping norm and domain

**Global L2 norm over the complete model delta** (not per-tensor, not
per-layer) — matches the existing cleartext mechanism's own norm
definition (`compute_shared_norm`), computed fresh, worker-side, over
the raw (pre-fixed-point) `dict[str, torch.Tensor]` delta FedAvg
produces. Since secure aggregation is FedAvg-only this slice (adaptive
clipping, Ditto, Per-FedAvg, and every other algorithm remain
unsupported under secure aggregation, per the existing gate and this
slice's own explicit-out-of-scope list), **there are no personalized/
local-only parameters to exclude** — every tensor in a FedAvg delta is
shared. Work Area F's "exclude personalized local-only parameters"
requirement is satisfied vacuously and documented as such, not
silently ignored.

## 5. Weight — fixed at exactly 1

Per the Initial Weighting Restriction: every participating user
contributes with weight exactly `1`, enforced both worker-side
(`validate_client_weight`'s existing bound, additionally checked
`== 1` for this mode) and coordinator-side (rejecting any submission
whose decoded/claimed weight differs). A configuration or task
requesting variable/sample-count weighting under secure user-level DP
is rejected with `SECURE_USER_LEVEL_DP_VARIABLE_WEIGHT_UNSUPPORTED`
before a session is ever created.

## 6. Cohort

The complete frozen cohort, exactly as the existing no-dropout secure
aggregation runtime already requires — no partial-cohort finalization,
no dropout recovery (unchanged, out of scope, per the Threshold
Secret-Sharing Restriction).

## 7. Sampling assumption — conservative, `q = 1`, `NO_AMPLIFICATION`

Per the audit's finding #2: the existing cleartext mechanism already
assumes `target_clients_per_round / total_clients` is a valid Poisson
sampling ratio for RDP amplification, and this project has never
independently validated that its client-selection mechanism satisfies
the accountant's mathematical sampling assumptions. This slice does
**not** inherit that assumption. The secure path's accountant is fed
`sample_rate = 1.0` unconditionally (`NO_AMPLIFICATION`) — the
accounting is computed as if every eligible user always participates,
which is the conservative (epsilon-overstating, never epsilon-
understating) choice. A future slice may introduce a validated random-
sampling mechanism with its own audit, tests, and accountant-
compatibility proof; this slice deliberately does not attempt it, and
`SamplingAssumption.RANDOM_SUBSAMPLING` (or similar) is not wired to
anything real yet — a request for amplification is rejected with
`SECURE_USER_LEVEL_DP_AMPLIFICATION_UNSUPPORTED`.

## 8. Privacy-accounting mechanism

Reuses `fl::core::UserLevelAccountant` (C++, wraps the existing
`MomentsAccountant`/RDP machinery) **unchanged as a type** — same
class, same `.step(1)`/`.get_epsilon()`/`.project_epsilon(n)` API — fed
by the secure path instead of the cleartext path, with `sample_rate`
fixed at `1.0` (item 7) rather than the cleartext path's ratio. One
accountant instance per `RunInstance`, already constructed whenever
`config_.privacy_mode == kUserLevelDp` regardless of secure-aggregation
status (audit finding #4) — no new accountant type is introduced.

## 9. Noise distribution and placement

Gaussian, added **once**, to the aggregate **sum** (not the average,
not per-participant, not per-tensor-independently-scaled beyond the
uniform per-element standard deviation below), **after** complete-
cohort validation and **before** the existing divide-by-cohort-size
step inside `SecureAggregationSessionManager::finalize()`:

```
private_average = (Σ clipped_user_updates + N(0, σ²·I)) / cohort_size
```

`σ = noise_multiplier × effective_sensitivity` (item 11) — every
tensor element receives an independent draw with this same standard
deviation, matching `add_central_gaussian_noise`'s existing per-
element-independent convention. This is calibrated for the **sum**,
which is why it is not divided by cohort size before being added
(contrast with the existing cleartext formula, which divides because
it adds noise *after* FedAvg has already averaged — see the audit's
finding #3 for the explicit side-by-side).

## 10. Output

The noised average, applied through the existing FedAvg model-update
path (`apply_secure_aggregate_and_advance` adds the noised, divided
delta onto `global_model_` exactly as it already adds any other
decoded secure aggregate — no new model-application code path).

## 11. Quantization margin and effective sensitivity

Fixed-point encoding (`encode_value`) already bounds its own per-
element quantization error at `0.5 / scale_factor` (half the smallest
representable step, from round-half-away-from-zero rounding) — an
existing, already-proven bound, not re-derived here. For one user's
clipped update spread across `N` total elements (summed over every
tensor in the manifest), the worst-case **L2 norm** of that user's
full quantization-error vector is bounded by treating every element as
simultaneously at its per-element worst case:

```
quantization_margin = sqrt(N) × (0.5 / scale_factor)

effective_sensitivity = clip_norm (C) + quantization_margin
```

This is the exact "derive from scale factor, element count, tensor
shapes, rounding rule" construction the task requires: `N` comes from
the run's tensor manifest (shapes), `scale_factor` and the `0.5`
half-step constant come from the fixed-point profile and its
already-fixed rounding rule. Noise is calibrated against
`effective_sensitivity`, never the optimistic unquantized `C`
(item 9's `σ` formula uses `effective_sensitivity`, not `C`). A
profile is rejected at configuration-validation time
(`SECURE_USER_LEVEL_DP_UNSAFE_QUANTIZATION_MARGIN`) if
`effective_sensitivity` would push the existing
`prove_domain_bounds` worst-case-aggregate chain
(`secure-aggregation-masked-runtime-audit.md`'s own citation of that
formula) past its `INT64_MAX` safety boundary — the margin is folded
into the **same** existing overflow-proof chain, not checked
independently. Both C++ and Python compute this identical formula
(cross-language fixture in Work Area AC asserts byte-for-byte
equality of the resulting configuration hash, which includes
`effective_sensitivity` as a hashed field).

## 12. Budget reservation and commit behavior

Per the audit's finding #5, this slice implements a **narrower, real,
disclosed** design rather than a fully separate persisted
reservation-ID entity:

- **Reserve** = a non-mutating pre-check at secure-session-creation
  time, inside `AcquireTask`'s session-creation block:
  `user_level_accountant_->project_epsilon(1)` (an existing,
  already-tested, non-mutating method) compared against
  `config_.user_level_privacy.epsilon_budget`. If the projected
  epsilon would meet or exceed the budget, the session is not created
  at all (mirrors the existing `STOP_BEFORE_EXCEEDING` cleartext
  check, moved to session-creation time instead of finalize time — a
  strictly earlier, strictly more conservative point to refuse).
- **Commit** = the accountant's **real**, mutating `.step(1)` call,
  which happens at exactly one place:
  `apply_secure_aggregate_and_advance`, and only after the noised
  aggregate has already been successfully applied to `global_model_`.
  This call site is already protected by `RunInstance`'s existing
  round-progression guard (`round_id != current_round_id_ ||
  state != kWaitingForClients` → safe no-op) — a retried
  `SubmitMaskedClientUpdate` RPC for an already-applied round cannot
  double-commit, because the guard that already makes model
  application idempotent makes the accountant step idempotent too
  (same call, same guard).
- **Release** = implicit: if the round aborts (dropout, deadline,
  restart) before `apply_secure_aggregate_and_advance` runs, the
  accountant was never mutated — there is nothing to "release," by
  construction, not by an explicit compensating action.

This is disclosed as a deliberate simplification of the task's
suggested `RESERVED`/`COMMITTED`/`RELEASED`/`EXPIRED`/`FAILED`
five-state model: it satisfies every *functional* requirement in Work
Areas N/R/U/V (no double-spend, no spend on no-output abort, commit
exactly once, restart-safe) using infrastructure that already exists
and is already tested, rather than introducing a new persisted entity
whose own restart-safety would need to be independently proven.

## 13. Trust assumptions (restated, load-bearing for every claim in this document)

The coordinator trusts each worker to: apply the signed clipping
configuration's exact clip norm before masking; use exactly the fixed
weight `1`; not submit an unclipped or arbitrarily-clipped masked
update. The coordinator does **not** trust: any worker-reported
epsilon, budget, sampling rate, or accountant state (Work Area M) — it
remains the sole authority for all of those, computed entirely from
its own `UserLevelAccountant` instance and the fixed configuration it
itself signed into the task. The signed `SignedUserLevelPrivacyAttestation`
(Work Area I) is evidence that a worker *received and acknowledged*
the exact clipping configuration used, not cryptographic proof that
clipping was executed correctly — restated explicitly here because it
is the single most important caveat this whole mechanism depends on.
