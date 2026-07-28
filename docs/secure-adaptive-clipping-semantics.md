# Secure Adaptive Clipping with Private Indicator Aggregation — Formal Mechanism Specification

Written before implementation (Work Area B), building directly on
`secure-adaptive-clipping-runtime-audit.md`'s findings and reusing
`secure-user-level-dp-semantics.md`'s already-established adjacency
model rather than re-deriving one.

**Mandatory Privacy Trust Statement (verbatim, in force, extended):**
this mechanism may claim user-level differential privacy — for both
the model contribution and the clipping-indicator statistic — only
under the explicit assumption that participating workers compute their
local norm, indicator, and clipping correctly. The coordinator cannot
cryptographically verify any of the three. This implementation is:
**honest-client-dependent adaptive clipping**, **central differential
privacy over a securely aggregated clipped sum and a securely
aggregated indicator count**, **an experimental research
implementation**. It is not: malicious-client-secure,
verifiably-clipped, verifiably-indicated, attestation-backed,
hardware-enforced, or range-proof-enforced.

## 1. Privacy unit

Identical to the model mechanism's: one participating worker/user per
secure round. The indicator mechanism does **not** introduce a second
privacy unit — it protects a statistic (how many users' updates
exceeded the bound) computed over the same frozen cohort, using the
same per-user granularity (Work Area's "represent exactly one logical
indicator per selected user").

## 2. Adjacency model — add/remove-one-user (reused unchanged)

Identical to `secure-user-level-dp-semantics.md` section 2: two
frozen-cohort user sets are adjacent if one is obtained from the other
by adding or removing exactly one user's participation. Not
replace-one. See that document for the full reasoning — restated here
because the indicator mechanism protects the *same* frozen cohort under
the *same* adjacency question, not a re-derived one.

## 3. Indicator definition — reused from the existing cleartext mechanism, not the task's suggestion

```
indicator_i = 1[ r_i > C_t ]     (over-threshold: 1 means EXCEEDS the bound)
```

where `r_i` is worker `i`'s deterministic global L2 norm
(`compute_global_l2_norm`, unmodified) of its whole-user update, and
`C_t` is the current round's signed clip bound. This is the audit doc's
central finding: the existing, tested `AdaptiveClipController` consumes
`over_threshold_count`, the complement of the task's suggested
`b_i=1[r_i<=C_t]`. Reusing the existing definition (rather than the
task's) avoids running two different, undocumented-relationship
formulas for the same statistic — see the audit doc's "Existing
indicator definition" section for the full derivation showing both
conventions agree on update *direction* despite disagreeing on which
fraction is tracked.

Indicator value is exactly `{0, 1}` — never a probability, never a
margin, never the raw norm or a function of it beyond this one
comparison.

## 4. Indicator count sensitivity

**1**, under add/remove-one-user adjacency (section 2): removing one
user changes `sum_i indicator_i` by at most 1, since `indicator_i`
itself is bounded in `{0,1}`. This is the same value the existing
cleartext mechanism already uses (`privacy.cpp:276-278`) and is **not**
recalibrated using the model's clip norm `C` — the task's explicit
"do not use model clip norm as the indicator-count sensitivity"
requirement is satisfied by construction (the indicator noise
calibration below never references `C` at all).

## 5. Indicator noise

```
indicator_noise_stddev = count_noise_multiplier
```

(sensitivity 1, standard Gaussian-mechanism calibration — matches
`AdaptiveClippingConfig.count_noise_multiplier`'s existing meaning
exactly, unchanged). Drawn from the coordinator's existing vetted
`NoiseProvider` (the same interface — but a logically distinct call,
never the same draw — as the model mechanism's central noise; see
section 11).

```
noisy_count_t     = sum_i indicator_i + Normal(0, indicator_noise_stddev^2)
noisy_fraction_t  = clamp(noisy_count_t / cohort_size, 0, 1)
```

## 6. Clip-bound update equation — the existing multiplicative form, reused unchanged

```
error_t        = noisy_fraction_t - target_quantile
scale_t        = max(1 + learning_rate * error_t, 1e-6)
C_{t+1}         = clamp(C_t * scale_t, C_min, C_max)
```

Exactly `AdaptiveClipController::step`'s existing formula
(`privacy.cpp:281-292`) — not the task's suggested log-space/exponential
form (`C_{t+1} = C_t * exp(lr * (target_quantile - noisy_fraction))`).
Both share the same direction (section headed "Existing indicator
definition" in the audit doc derives this explicitly): too many users
over threshold raises the bound, too few lowers it. Only one formula is
implemented, per the task's own "do not combine incompatible formulas
silently" instruction.

## 7. Current-round immutability

`C_t` (the bound used to clip *this* round's updates and to compute
*this* round's indicators) is fixed the moment the coordinator signs
this round's tasks — read once from `adaptive_clip_controller_->clip_value()`
at task-issuance time, bound into the signed task's
`secure_adaptive_clipping_configuration_hash` (section 12), and never
mutated afterward for this round. `AdaptiveClipController::step()` is
called exactly once per round, at finalize time, strictly *after* every
task for that round has already been signed and every worker has
already submitted against `C_t` — the existing controller's own
single-threaded, single-call-per-round design already enforces this
(there is no setter for `clip_value_` other than `step()` and
`restore()`, and `restore()` is restart-only, called on a controller
with `steps()==0`). No new mutability guard is required; this section
documents the existing guarantee rather than introducing a new one.

## 8. Sampling assumption — reused, unchanged

`q = 1` (no privacy amplification), identical to user-level DP's own
stance (`secure-user-level-dp-semantics.md`). The indicator mechanism
observes the same frozen, coordinator-selected cohort every round — no
random subsampling exists for either mechanism, so no amplification
term applies to either.

## 9. Fixed user weight

Adaptive clipping is only reachable when `USER_LEVEL_DP`/`HYBRID_DP`'s
own uniform-weighting requirement already holds (the `AcquireTask` gate
checks this before it ever reaches the adaptive-specific checks — see
section 13). The indicator contribution is exactly one `{0,1}` value
per user, secured with the identical fixed-weight-1 masking convention
`masked_weight` already uses (section 14) — never scaled, never
weighted by sample count.

## 10. Two mechanisms, two epsilons under one privacy unit — never combined into a third

Per round, per run, the system reports (see the audit doc's "Accountant
composition" section for why these stay separate rather than composing
through a shared accountant instance):

- `user_epsilon` (model mechanism) — from `user_level_accountant_`,
  unchanged from the existing mechanism.
- `clipping_epsilon` (indicator mechanism) — from
  `adaptive_clip_controller_`'s own accountant instance, unchanged from
  the existing cleartext mechanism.

**Forbidden, structurally, not just by convention**: there is no
combined field anywhere summing these two (or, in `HYBRID_DP`, all
three including `sample_epsilon`). `PrivacyMetricsSnapshot` already
models this correctly today (`has_user_level`/`user_epsilon` and
`has_clipping`/`clipping_epsilon` as separate optional sections) — this
slice's job is to keep populating both sections independently under
secure aggregation, not to add a new combined one.

## 11. Independent noise sources

The model mechanism's central Gaussian noise
(`add_central_gaussian_noise`, applied to the decoded aggregate sum
inside `SecureAggregationSessionManager::finalize()`) and the indicator
mechanism's Gaussian noise (`gaussian_sample(count_noise_multiplier)`,
applied to the decoded indicator count) are two **separate calls**
against the same `NoiseProvider` interface — same vetted OS-CSPRNG-backed
implementation, independently drawn, never the same random value reused
for both, never derived from each other. This mirrors the hybrid
slice's "independent noise sources" section exactly, extended from two
mechanisms to two mechanisms plus this one.

## 12. Configuration binding — one new task-signing hash, following the existing pattern exactly

A new `secure_adaptive_clipping_configuration_hash(task)` function,
structurally identical to the existing
`secure_user_level_dp_configuration_hash` (`coordinator_task_signing.cpp:437-467`):
an `{"secure_adaptive_clipping_active":false}` stub when inactive (for
hash stability across non-adaptive runs), or an alphabetical canonical
JSON over the active configuration's fields when active
(`current_clip_bound`, `min_clip`, `max_clip`, `target_quantile`,
`learning_rate`, `indicator_count_sensitivity` — always `1`, included
for explicitness not because it varies —, `indicator_noise_multiplier`,
`clip_state_step_count`). Bound into `coordinator_task_signing_bytes`
alongside the other six hash fields, covered by the same one Ed25519
signature over the whole task. No separate "hybrid-style" combined
message is introduced — this hash is independently, cryptographically
bound the identical way the user-level and sample-level hashes already
are, which the hybrid slice already established is sufficient binding
without a third redundant construct.

`clip_state_step_count` is `adaptive_clip_controller_->steps()` at
task-signing time — this **is** the "clip-state version" Work Area C/F
asks for; no new versioning scheme is introduced (see the audit doc's
"Existing component inventory" table).

## 13. `AcquireTask` compatibility gate — placement in the existing ladder

Currently, `adaptive_clipping_enabled()` is checked **before** the
privacy-mode branches, rejecting unconditionally
(`coordinator_service.cpp:1488-1500`). This slice changes that check to:

1. If `adaptive_clipping_enabled()` and privacy mode is **neither**
   `USER_LEVEL_DP` nor `HYBRID_DP`: reject
   `SECURE_ADAPTIVE_CLIPPING_UNSUPPORTED_PRIVACY_MODE` (adaptive
   clipping requires a user-level clipping layer to adapt).
2. If `adaptive_clipping_enabled()` and mode is `USER_LEVEL_DP`/`HYBRID_DP`:
   fall through into the existing shared user-level validation ladder
   (weighting/clip-config/quantization/budget), **then** run adaptive-
   specific validation (bound ordering, quantile range, learning rate,
   noise multiplier, clip-state version freshness — Work Area D's full
   list) using `SECURE_ADAPTIVE_CLIPPING_*`-prefixed reason codes.
3. If `adaptive_clipping_enabled()` is false: existing fixed-clip
   behavior, entirely unchanged.

This mirrors the hybrid slice's own "extend the shared ladder, add a
prefix-specific extra check up front" pattern
(`secure-hybrid-dp-runtime-audit.md`'s `AcquireTask` section) rather
than a parallel, duplicated validation path.

## 14. Secure indicator representation and masking

The indicator is encoded as a single ring value, identically to the
fixed weight (`masked_weight`): `0` or `1` cast directly into the
`uint64` ring (no fixed-point scaling needed — a `{0,1}` value has no
fractional component to quantize), masked with
`derive_weight_mask`/`mask_encoded_value` under a **third** HKDF
purpose label:

```
HKDF_PURPOSE_CLIPPING_INDICATOR_MASK_STREAM = "clipping_indicator_mask_stream"
kHkdfPurposeClippingIndicatorMaskStream      = "clipping_indicator_mask_stream"
```

domain-separated from `tensor_mask_stream`/`weight_mask_stream` by
`derive_purpose_key`'s existing `info = purpose_label || 0x00 || context`
construction (`secure_aggregation_crypto.cpp:207-218`) — a different
purpose label with the identical (`tensor_name=""`) canonical context
already produces an independent key, exactly the same way the weight
mask is already separated from tensor masks today. No new masking
primitive is introduced; `derive_weight_mask`/`mask_encoded_value`/
`sum_masked_values` are called unchanged with the new purpose label.

Maximum unmasked aggregate: `cohort_size` (every indicator is at most
`1`). This is well inside the existing fixed-point ring's proven bound
(`max_input_magnitude`/`max_cohort_size` in `FixedPointEncodingProfile`,
already validated for the model tensors and fixed weight at session
creation) — no new ring-bound validation is required beyond checking
`0 <= decoded_count <= cohort_size` after reconstruction, which is a
plain integer range check, not a quantization concern.

## 15. Adaptive binding — a new, bounded, self-contained signed message

A new `SignedAdaptiveClippingBinding` message, structurally identical
to `SignedUserLevelPrivacyAttestation` (self-contained
signing_key_id/payload_hash/signature, verified against the SAME
resolved key as the outer envelope — no second key lookup), kept
**separate** rather than merged into the user-level attestation, for
the same reason `SignedSamplePrivacyRecord` and
`SignedUserLevelPrivacyAttestation` are kept separate: independent
verifiability of independent evidence. Fields: `schema_version`,
`worker_id`, `client_id`, `run_id`, `round_id`, `task_id`, `session_id`,
`model_version`, `adaptive_configuration_hash` (= section 12's hash,
re-asserted by the worker), `clip_state_step_count`,
`current_clip_bound` (the signed `C_t` this worker actually used —
public, coordinator-already-knows-it information, evidence of
configuration consistency, not a new information leak), `provider`,
`operation_completed`, `issued_at`, `expires_at`,
`signing_key_id`/`payload_hash`/`signature`.

**Deliberately excludes** (per the task's explicit prohibition): the
clear indicator value, the unclipped norm, the clipped norm, the
clipping factor, whether clipping occurred for this specific worker.
This binding proves *configuration* consistency and message integrity
— never that the indicator is truthful (Mandatory Privacy Trust
Statement, restated at the top of this document).

## 16. Coordinator-side masked-indicator validation — inserted into the existing ladder

Per the audit's exact `SubmitMaskedClientUpdate` line-by-line map: a
new verification sub-block inserted immediately after the existing
user-level attestation block (`coordinator_service.cpp:~4211`) and
before the hybrid sample-record block, following the identical staged-
then-committed discipline every other check in this function already
uses (validate inside the `try` block against `manager_->get(...)`;
stage replay/monotonicity candidates; commit only after
`secure_aggregation_manager_->submit_masked_update()` itself durably
succeeds). Checks, in order: adaptive clipping active for this run? →
binding present? → binding's worker/client/run/round/task/session/model_version
== outer envelope's? → binding's `signing_key_id` == outer envelope's?
→ `verify_signed_adaptive_clipping_binding(...)` (Ed25519, expiry) →
`adaptive_configuration_hash` == the run's current hash? →
`clip_state_step_count` == the run's current step count (stale-state
rejection — a worker holding a task signed against an older clip state
than the one now current is rejected, not silently accepted against a
mismatched bound)? → masked indicator present, encoded width exactly
one ring value, checksum valid?

## 17. Complete-cohort indicator reconstruction

Reuses the exact same "every frozen participant, exactly once, sum in
the ring, decode only the aggregate" discipline
`SecureAggregationSessionManager::finalize()` already applies to
tensors and the fixed weight — extended to also sum and decode the
masked indicator contributions, gated behind the identical
complete-cohort precondition (`masked_contribution_count() ==
cohort_size()`). Decoded count is range-validated
(`0 <= count <= cohort_size`) with the same "reject, never clamp
silently" discipline `finalize()`'s `expected_weight_sum` check already
uses. No individual indicator is ever decoded or exposed — only the
final aggregate integer.

## 18. Publication boundary — one atomic transaction, not two linked ones

**Model publication and clip-state publication are one atomic
transaction in this implementation** — both happen inside the same
`apply_secure_aggregate_and_advance` call, under the same
round-progression idempotency guard, in the same way the model
mechanism's own `UserLevelAccountant::step()` and checkpoint write
already are one transaction today (see
`secure-user-level-dp-publication-boundary.md`'s sequence). Concretely,
extending that document's existing sequence:

1. Contribution-collection-complete (unchanged).
2. Aggregate-reconstructed + noise-generated + noise-applied — **now
   also**: indicator-aggregate-reconstructed + indicator-noise-generated
   + indicator-noise-applied, inside the same `finalize()`-adjacent
   step, before either output is returned.
3. `AdaptiveClipController::step(indicator_count, cohort_size)` is
   called — advancing `C_{t+1}` — **immediately alongside** the model
   noise application, mirroring exactly where the cleartext path already
   calls it relative to the model mechanism's own accounting
   (`run_manager.cpp:952-993`: model noise/accounting first, adaptive
   step immediately after, same function, same call).
4. Model-version-published (unchanged).
5. **Both** accountants commit — `user_level_accountant_->step(1)` and
   `adaptive_clip_controller_->step(...)`'s internal accountant step —
   inside the same `apply_secure_aggregate_and_advance` call, both
   gated by the identical idempotency guard. This is the moment **both**
   privacy spends become irreversible, together, not independently
   staggered.
6. Checkpoint-persisted — writes model version, both ledgers
   (`user_level_ledger_`, `adaptive_clipping_ledger_`), and the new
   clip bound/step count, in the same `save_checkpoint()` call that
   already persists the model mechanism's state.
7. Session-completed (observability only).

This is a deliberate, disclosed design choice, not an oversight: making
these two linked-but-separate transactions would require a new
two-phase commit protocol this codebase has no precedent for, to
protect against a crash window (between committing one and the other)
that — per section "Restart reconciliation" in the audit doc — the
model mechanism alone already tolerates today via the identical
fail-safe-shaped gap (a crash rolls both back together, never one
without the other, because one `save_checkpoint()` call persists both).
Extending that same tolerated gap to include a third piece of state
(the clip bound) does not introduce a new failure mode; splitting it
into two independently-committing transactions would introduce a new
one (model committed, indicator not, or vice versa) that does not exist
today for any other pair of mechanisms in this codebase.

## 19. Dropout behavior

Identical to the existing no-dropout-cohort rule, extended to cover the
indicator mechanism explicitly: any frozen participant's failure to
submit aborts the secure session before `finalize()` is ever called —
no model aggregate, no indicator aggregate, no model noise, no
indicator noise, no clip-state update, no model publication. Since both
mechanisms commit inside the same call (section 18), "abort before
finalize" structurally guarantees neither commits — there is no
separate "release the indicator reservation" step because, per the
audit doc's "Budget lifecycle" section, no persisted reservation exists
for either mechanism to release. `HYBRID_DP`'s sample-level spend is
reconciled per its own existing, unchanged release boundary
(`secure-hybrid-dp-semantics.md` section 7) — this slice does not
change sample-level dropout handling.

## 20. HYBRID interaction

For `HYBRID_DP` with adaptive clipping enabled: sample-level private
training (Opacus) happens first, exactly as the hybrid slice already
established, producing the whole-user delta the adaptive mechanism then
operates on identically to `USER_LEVEL_DP`'s case — compute norm →
compute indicator → clip using `C_t` → encode/mask model tensors → mask
fixed weight → mask indicator → submit all three bindings (sample
privacy record, user-level attestation, adaptive clipping binding).
Sample-level epsilon remains entirely separate, exactly as
`secure-hybrid-dp-semantics.md` section 4 already mandates — this slice
adds a third independent epsilon (clipping) alongside the existing two
(sample, user-level-model), never combining any pair of the three.

## 21. Trust assumptions (restated, extended)

Everything `secure-user-level-dp-semantics.md`'s Mandatory Privacy
Trust Statement already establishes for worker-side norm computation
and clipping applies identically to indicator computation: a malicious
worker can submit an indicator inconsistent with its actual update (a
`1` when the true norm is below the bound, or vice versa), or an
inconsistent pairing of indicator and clipped update. The coordinator
verifies structural and cryptographic bindings (signatures, hashes,
replay, monotonicity, cohort completeness) but cannot and does not
claim to verify semantic correctness of the indicator's *value*. This
is disclosed, not hidden, in every observability surface this slice
touches.
