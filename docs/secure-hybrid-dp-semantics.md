# Secure Hybrid Differential Privacy — Formal Semantics (Work Area B)

Defines the secure hybrid DP mechanism as **two layered mechanisms with
different privacy units**, composed but never merged. Everything in
[secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md)
(adjacency model, sensitivity, noise placement, quantization margin,
budget-reservation design) remains true for this mechanism's user-level
layer, unchanged — this document adds the sample-level layer and the
exact composition rule, and restates the mandatory separation.

## 1. Sample-level layer

- **Privacy unit**: one training sample inside a worker's local
  dataset.
- **Adjacency model**: add/remove-one-sample (Opacus's standard DP-SGD
  adjacency — unchanged from the existing plain `SAMPLE_LEVEL_DP` mode;
  this slice does not re-derive or re-review it).
- **Mechanism**: per-sample gradient clipping at `max_grad_norm`, then
  Gaussian noise scaled by `noise_multiplier * max_grad_norm / batch_size`
  added to the summed per-sample gradients, exactly as Opacus's
  `PrivacyEngine.make_private` implements it — unmodified, reused
  verbatim from `python/src/fl_platform/worker/task_runner.py`'s
  `run_private_local_training`.
- **Accountant**: `SampleLevelAccountant` (Python, wraps Opacus's own
  RDP/PRV/GDP accountant) — unmodified.
- **Budget**: `SampleBudgetEnforcer`, worker-side, unmodified.
- **Publication boundary**: see §7 below — new for this slice (the
  cleartext sample-level path's publication boundary is "submitted via
  `SubmitClientResult`"; the secure path's is "submitted via
  `SubmitMaskedClientUpdate`", a materially different transport with a
  materially different coordinator-side verification block).
- **Trust assumption**: honest-client-dependent. The coordinator never
  recomputes a worker's sample-level epsilon — it verifies a *signed
  claim* of what the worker's own Opacus accountant reported, exactly
  as the existing cleartext path already does. This slice makes no
  stronger claim than that existing, already-live mechanism does.

## 2. User-level layer

Unchanged from
[secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md)
in every respect: privacy unit (one participating worker/logical
user), adjacency model (add/remove-one-user), whole-update global L2
clipping at the worker, central Gaussian noise added to the securely
aggregated clipped sum before division, `UserLevelAccountant`, budget
reservation via a non-mutating epsilon projection at session-creation
time, publication boundary at "central noise applied and model version
published" (see
[secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md)).
**Not re-derived, not re-implemented, not modified by this slice.**

## 3. Combined execution — exact mechanism order

The worker-side order, as actually implemented (not merely specified):

```
1. Verify coordinator-signed task
   (task_payload_hash covers BOTH sample-level config's own
   privacy_configuration_hash AND user-level config's own
   secure_user_level_dp_configuration_hash independently — see §5).
2. Verify secure aggregation fields present and active.
3. Complete secure cohort handshake, verify frozen roster.
4. Run sample-level private local training
   (task_runner.run_private_local_training — UNMODIFIED).
5. Construct the whole-user model delta from the now sample-private
   local model state (personalized-parameter exclusion, finite-value
   validation — UNMODIFIED, same code as every other secure path).
6. Compute the whole-update global L2 norm over the sample-private
   delta and apply user-level clipping
   (user_level_clipping.clip_delta_to_l2_norm — UNMODIFIED).
7. Build the signed SignedSamplePrivacyRecord from the sample-level
   accountant's real output (reusing the exact construction the
   cleartext path already uses).
8. Build the signed SignedUserLevelPrivacyAttestation over the CLIPPED
   delta's clip_norm/effective_sensitivity (UNMODIFIED).
9. Fixed-point encode the clipped, sample-private delta.
10. Pairwise-mask tensors and the fixed weight (=1).
11. Submit: SubmitMaskedClientUpdateRequest carrying
    (a) the outer signed envelope + MaskedClientUpdate
        (sample_privacy_record_hash populated, user_level_attestation
        populated),
    (b) the sample privacy record's own independent envelope+payload.
12. Never submit a cleartext ClientResult.
```

Step 4 (sample-level noise, local) happens strictly before step 6
(user-level clipping, whole-update) — the clipped update the worker
transmits is therefore always the *sample-private* update, never the
raw one. Step 9-10 (encoding/masking) happen strictly after step 6 —
the coordinator never sees an intermediate, unclipped, or unmasked
value at any point.

**Coordinator role**: unchanged from plain secure `USER_LEVEL_DP` for
the aggregate-reconstruction/noise/model-apply steps. The only new
coordinator behavior is verifying each worker's `SignedSamplePrivacyRecord`
*individually*, per submission (not as part of the aggregate) — the
tensor payload stays masked and hidden exactly as before; only the
already-existing, already-privacy-preserving-by-design accounting
metadata (epsilon, delta, step count — never a gradient value) is
newly verified and recorded for the secure path.

## 4. Two epsilons, two deltas — never combined

Per round, per worker, the system reports:

- `sample_epsilon`, `sample_delta`, `sample_accountant_type` — from
  that worker's own `SignedSamplePrivacyRecord`.
- (once per round) `user_epsilon`, `user_delta`, `user_accountant_type`
  — from the run's single `UserLevelAccountant`.

**Forbidden, structurally, not just by convention**: there is no
`hybrid_epsilon` field anywhere in any proto message, Go type, or web
type this slice adds. `HybridRoundSummary` (the ledger record, §6) has
a `sample_level` sub-section and a `user_level` sub-section, never a
top-level combined scalar.

A prior draft of this document claimed a client-sent "combined epsilon"
request is rejected at configuration-validation time with a structured
`SECURE_HYBRID_DP_COMBINED_EPSILON_UNSUPPORTED` reason. That was
inaccurate and has been corrected: no such field exists anywhere in the
wire schema for a client to send in the first place (`POST
/api/v1/coordinator/runs`'s `privacy` object only ever decodes
`sample_level`/`user_level`/`adaptive_clipping` sub-objects — see
`go/internal/transport/httpapi/coordinator_handlers.go`), and Go's
`encoding/json` decode call used there (`decodeJSON`) does not set
`DisallowUnknownFields`, so an unrecognized key such as
`privacy.epsilon` is silently dropped, not rejected with an error. This
is *stronger* than a runtime rejection reason, not weaker: there is no
code path anywhere — client request, coordinator validation, ledger
write, or API response — capable of producing or accepting a combined
epsilon value, so there is nothing for a rejection reason to guard
against. No `SECURE_HYBRID_DP_COMBINED_EPSILON_UNSUPPORTED` enum value
exists in `SecureAggregationRejectionReason` (confirmed: grepped the
whole tree — zero matches outside this corrected paragraph), and none
is added, because adding a rejection code for an input shape the wire
format cannot represent would be dead code asserting a guarantee the
schema already provides for free.

## 5. Configuration binding — no new combined-config message

As established in the audit doc: `privacy_configuration_hash(task)`
(sample-level) and `secure_user_level_dp_configuration_hash(task)`
(user-level) are **each independently already part of**
`coordinator_task_signing_bytes()`. For a `kHybridDp` secure task, both
functions produce real (non-`"sample_level_dp_active":false`,
non-`"secure_aggregation_active":false`) hashes, and both are covered
by the one Ed25519 signature over the whole task. This is the complete,
sufficient configuration-binding property hybrid needs: a worker cannot
forge either sub-configuration without invalidating the signature, and
the two sub-configurations are cryptographically inseparable from each
other (both signed by the same key, over the same task, at the same
time) without needing a third, purpose-built "hybrid configuration
hash" to additionally prove that. `docs/secure-hybrid-dp-runtime-audit.md`'s
"Why no new HybridPrivacyConfiguration/HybridPrivacyBinding message"
section is the authoritative reasoning; this section is the pointer to
it from the semantics side.

## 6. Hybrid round ledger

A new, additive `HybridRoundSummary` C++ struct/proto records, per
completed hybrid round: `run_id`, `round_id`, `model_version_before`,
`model_version_after`, `cohort_size`, `completion status`, plus **two
nested sections, never merged**:

- `sample_level`: accountant type, number of accepted sample-level
  records this round, the *last-recorded* `sample_epsilon`/`sample_delta`
  observed this round (per-worker values remain in the existing
  `sample_level_ledger_`, not duplicated here — this summary is an
  aggregate pointer, not a second source of truth).
- `user_level`: adjacency model, clip norm, effective sensitivity,
  noise multiplier, `user_epsilon`/`user_delta` (the single
  `UserLevelAccountant`'s post-round value), budget commit status.

## 7. Sample-level publication boundary (secure path) — new for this slice

The cleartext sample-level path's publication boundary
("`SubmitClientResult` durably accepted") does not directly apply to
the secure path, whose transport is `SubmitMaskedClientUpdateRequest`.
Adopted rule, conservative, mirroring the cleartext path's own
philosophy:

**A worker's sample-level privacy spend for a hybrid secure round is
committed when the coordinator durably accepts the
`SubmitMaskedClientUpdateRequest` carrying that worker's signed sample
privacy record** — i.e., the exact same gRPC call, and the exact same
verification block (signature, structural binding, replay,
monotonicity, budget-decision-contradiction), that already governs
`sample_privacy_record_hash`'s binding into the outer masked update.
`AccountantMonotonicityStore`'s existing per-(run,client,worker,
accountant_type) monotonicity guarantee is what makes this commit
point safe against a retried RPC — a duplicate submission with the
same accountant step is rejected by the store before a second ledger
entry could ever be appended, mirroring exactly how the cleartext
path's own commit-once property already works. **A masked submission
that reaches this point commits sample-level spend regardless of
whether the round later completes** (i.e., regardless of whether the
cohort ever reaches complete-cohort finalization) — this is the
"dropout does not erase an already-accepted worker's sample-level
spend" rule the task specification requires, and it falls out for free
from the existing accept-then-finalize-separately architecture: masked
contributions are recorded (and their sample-level records committed)
*before* the cohort is known to be complete, exactly like every other
per-worker step in this codebase.

An RPC failure *before* the coordinator's accept response is returned
(network failure, timeout) is **not** distinguished from "never sent"
by this slice — the worker's own existing retry logic
(`_submit_masked_with_retry`) already handles this by retrying, and
`AccountantMonotonicityStore`'s replay-safe design means a retried,
already-accepted submission is safely re-acknowledged, not
double-committed. A literal `SAMPLE_RECONCILIATION_REQUIRED` persisted
state for the narrow "transport outcome is unprovable" window is not
implemented this slice (see the audit's scope statement) — the
existing retry-is-safe property is the real mitigation in place today.

## 8. User-level publication boundary (secure path)

Unchanged: see
[secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md).
Central noise is generated and applied exactly once, inside
`SecureAggregationSessionManager::finalize()`, strictly after complete-
cohort validation; the `UserLevelAccountant` commits exactly once,
gated by `RunInstance`'s existing round-progression idempotency guard.
Nothing about the hybrid mode changes this — the aggregate-
reconstruction/noise/model-apply/accountant-commit sequence for a
hybrid round is byte-for-byte the same code path plain secure
`USER_LEVEL_DP` already uses.

## 9. Dropout semantics

When a frozen participant fails before the cohort completes:

- The secure session aborts (`SecureAggregationAbortReason::kDropout`
  or deadline-exceeded, unchanged existing mechanism).
- No aggregate is reconstructed, no central noise is generated, no
  model update is applied, no user-level accountant commit occurs, the
  user-level budget reservation is released (unchanged existing
  behavior — `AcquireTask`'s pre-check was non-mutating, so "release"
  is simply "the projection is never turned into a commit").
- **Sample-level spend already committed for workers whose masked
  submission was already durably accepted (§7) remains committed** —
  their `SampleLevelLedgerEntry` rows are not retracted. This is a
  direct consequence of §7's design: sample-level commit happens at
  per-worker submission-accept time, independent of and strictly
  earlier than cohort-completion, so an abort that happens *after* some
  workers already submitted cannot retroactively un-commit their
  already-recorded sample-level spend without an active, separate
  rollback mechanism this slice does not build (and which would itself
  be privacy-incorrect — the sample-level noise was already applied
  inside that worker's own local training process, a fact independent
  of what the coordinator later does with the aggregate).
- Workers whose masked submission was never sent, or never accepted,
  never had a sample-level ledger entry created in the first place —
  nothing to release.

## 10. Independent noise sources

Sample-level noise (worker-side, Opacus `PrivacyEngine`, keyed off
whatever secure-random provider the worker's own privacy config
requires) and user-level noise (coordinator-side,
`CryptoSecureNoiseProvider`, OS-CSPRNG-backed) are **already
structurally independent processes** — one runs inside the worker's
Python process during local training, the other runs inside the
coordinator's C++ process during finalization, minutes apart, with no
shared state, no shared seed, no shared code path. This slice
introduces no new correlation risk because it introduces no new
sampling code — both noise mechanisms are reused completely unmodified.
The bounded statistical smoke test in
`cpp/core/tests/secure_random_test.cpp` (added in the prior slice)
already demonstrates the production user-level noise provider is
non-deterministic and uncorrelated across independent instances; a
parallel worker-side smoke test for Opacus's own noise generator is not
added this slice (Opacus's own noise implementation is third-party,
already exercised by the existing `test_private_training.py` suite, and
re-validating a third-party library's own RNG independence is outside
this slice's scope).

## 11. Mandatory trust boundary (restated)

Permitted claims: sample-level DP according to the validated local
training configuration and signed worker record; honest-client-
dependent user-level DP; central Gaussian noise over a securely
aggregated clipped sum; no-dropout complete-cohort execution; separate
sample-level and user-level accounting; experimental layered privacy.

Forbidden claims: coordinator recomputation of worker sample-level
epsilon; cryptographically verified sample-level training;
cryptographically verified whole-update clipping; malicious-client-
secure hybrid privacy; verifiable local sample counts; verifiable user
weight; Byzantine robustness; dropout resilience; production privacy
readiness; a formal end-to-end privacy proof; a single combined
epsilon across the two privacy units.
