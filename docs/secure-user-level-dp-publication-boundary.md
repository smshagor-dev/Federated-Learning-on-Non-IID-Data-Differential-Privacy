# Secure User-Level DP Publication Boundary (Work Area R)

The "private publication boundary" is the sequence of state transitions
a single secure user-level-DP round moves through, from a complete
masked cohort to a durably persisted, privately-averaged model update.
Documented here from direct reading of the real implementation
(`SecureAggregationSessionManager::finalize()`,
`RunInstance::apply_secure_aggregate_and_advance`, both in
`cpp/coordinator/src/`), not from the task specification's own
suggested ordering — the real code's actual sequence differs from a
naive reading of the task's suggested step names in one place (noise
generation and application are a single, inseparable step inside
`finalize()`, not two independently observable ones), and checkpoint
persistence happens *after* the accounting commit, not before. Both
disclosed below rather than silently reordered to match the task's
literal wording.

## The sequence, as the real code executes it

1. **Contribution-collection-complete.** `SubmitMaskedClientUpdate`
   (`coordinator_service.cpp`) observes
   `status.masked_contribution_count() == status.cohort_size()` — every
   participant in the frozen cohort has submitted a signed, attested
   masked update. This is the trigger for everything that follows; it
   is not itself privacy-relevant (masked contributions are
   individually meaningless without every peer's mask).
2. **Aggregate-reconstructed + noise-generated + noise-applied (one
   inseparable step).** `SecureAggregationSessionManager::finalize()`
   decodes the masked ring sum per tensor into cleartext FP64 values,
   then — while still holding the sum, before its own existing
   divide-by-weight-sum step — adds one Gaussian noise draw per element
   (`noise_provider->gaussian_sample(noise_std_dev)`) when a
   `noise_provider` was supplied (always true for a `kUserLevelDp`
   session). **This is the moment private output first exists** — the
   noised, not-yet-divided sum, still entirely inside `finalize()`'s
   stack, never persisted or exposed as an intermediate. `finalize()`
   then divides by the weight sum and returns the noised **average**.
   Calling `finalize()` a second time on the same session is
   structurally refused: it throws unless
   `record.state_machine.state() == CohortState::kMaskedUpdateCollection`,
   and `finalize()` transitions the session's state machine away from
   that state before returning — **noise cannot be regenerated for an
   already-finalized session**, enforced by the session's own state
   machine, not by caller discipline.
3. **Model-update-prepared.** `finalize()`'s returned
   `fl::core::AggregationResult` (the noised average) is passed to
   `RunInstance::apply_secure_aggregate_and_advance`.
4. **Model-version-published.** Inside
   `apply_secure_aggregate_and_advance`, still under the same
   round-progression idempotency guard that makes this method safe
   against a retried RPC (`round_id != current_round_id_ || state !=
   kWaitingForClients` → early `return false`, no side effects at
   all), the noised average is applied onto `global_model_` and
   `model_version_` is bumped.
5. **Accountant-committed — the irreversible point.** Only now, and
   only for `privacy_mode == kUserLevelDp`, `user_level_accountant_->step(1)`
   runs and a `UserLevelLedgerEntry` (epsilon, delta, noise_multiplier,
   clipping_bound, num_clients, `committed_at_unix_s`) is appended to
   `user_level_ledger_`. **This is the moment privacy spend becomes
   irreversible** — once this line executes, the epsilon has been
   spent regardless of what happens to the process afterward (even a
   crash before the checkpoint write below does not "un-spend" it,
   since a restart resumes from whatever the ledger's in-memory state
   was — see the reconciliation gap below).
6. **Checkpoint-persisted.** Still inside
   `apply_secure_aggregate_and_advance`, the run transitions to
   `kCheckpointing` and then `kCompleted`/`kRunning`, and
   `save_checkpoint()` durably writes `model_version`, `current_round`,
   and the full `user_level_ledger_` (including the new entry's
   `committed_at_unix_s`) to disk. **This is the "requires restart
   reconciliation" boundary is measured against** — see below.
7. **Session-completed.** The coordinator emits
   `SECURE_AGGREGATION_SESSION_COMPLETED` and (for `kUserLevelDp`)
   `SECURE_USER_LEVEL_DP_ACCOUNTING_COMMITTED` +
   `SECURE_USER_LEVEL_DP_ROUND_COMPLETED` (see
   `docs/secure-user-level-operations-audit.md`'s event vocabulary).
   The secure aggregation session's own state was already set to
   `kCompleted` back in step 2 — this step is observability only, not a
   further state transition of the mechanism itself.

## What "private output exists" means

Private output — the noised, averaged model delta — exists starting at
step 2 and never earlier. Nothing before step 2 (masked contributions,
the decoded-but-not-yet-noised ring sum inside `finalize()`'s own
stack) is ever persisted, logged, or returned to a caller; the decoded-
but-unnoised sum is a local variable inside `finalize()`, never a
separate observable value.

## What makes spend irreversible

Step 5 (accountant-committed), and only step 5. The non-mutating budget
pre-check at session-creation time
(`project_user_level_epsilon_after_one_more_step()`, `AcquireTask`'s
gate) is explicitly *not* a reservation in the sense of a separately
persisted, releasable record — see
`docs/secure-user-level-dp-semantics.md` section 12. There is therefore
no "reservation release" step in this design: a session that never
reaches step 5 (aborted before completion, e.g. dropout) simply never
spends anything: the pre-check's projection is discarded, not
committed, and the next session's own pre-check recomputes fresh
against the still-unchanged ledger.

## The known restart-reconciliation gap

Steps 2–4 (finalize + apply model update) and step 5 (accountant
commit) + step 6 (checkpoint) are **not** a single atomic transaction —
a coordinator crash between step 4 and step 6 leaves the secure
aggregation session already reporting `COMPLETED` (set in step 2) while
the run's model version may or may not have advanced and the ledger
entry may or may not have been committed/persisted, depending on
exactly where the crash landed. This is the same disclosed residual-
inconsistency window `RunInstance::apply_secure_aggregate_and_advance`'s
own header comment and `docs/known-limitations.md` already describe for
the non-privacy-specific case, restated here specifically for the
privacy-accounting dimension:

- A crash strictly before step 5: on restart, the ledger has no entry
  for that round; the secure session (if its own state was persisted
  as `COMPLETED` before the crash — `SecureAggregationSessionManager`
  state is currently **in-memory only**, not itself checkpointed, so in
  practice a crash here loses the session's `COMPLETED` marker too) is
  simply gone. **No privacy spend occurred** — the round would need to
  be re-driven from scratch, which the no-dropout cohort-freeze
  mechanism handles the same way it handles any other never-completed
  session.
- A crash strictly between step 5 and step 6: the ledger entry existed
  in memory (spend is real, by the "accountant-committed" definition
  above) but was never durably persisted. On restart, that ledger entry
  is **gone** (checkpoints are the only persistence mechanism) — the
  in-memory epsilon spend is lost, but no corresponding round of actual
  model progress survived either (`model_version_`'s bump also depends
  on `save_checkpoint` in step 6 to survive a restart). This is a
  disclosed **fail-safe-shaped, not fail-secure-shaped** gap: the
  privacy ledger and the model state can only ever be inconsistent with
  each other by *both being rolled back together*, never one without
  the other, because the same `save_checkpoint()` call persists both.
  There is currently no automated detection that this happened — an
  operator reading `GetSecureUserLevelPrivacyHealth`'s
  `reconciliation_required` field will always see `false` today (no
  cross-check against, e.g., a separately durable "round N was
  attempted" marker exists yet). This is disclosed as a **real,
  bounded gap**, not silently reported as solved.

## Failure-injection tests covering these boundaries

`cpp/coordinator/tests/user_level_dp_test.cpp`'s "restart-after-
publication" and "corrupted budget/ledger checkpoint state" blocks
(added this slice) exercise:

- A full save/restore round-trip across steps 5→6 (real checkpoint
  file written, a fresh `RunManager`/`RunInstance` constructed exactly
  as a real coordinator restart would, `restore_from_checkpoint()`
  called) — confirms the ledger entry, including its
  `committed_at_unix_s`, survives byte-for-byte.
- A tampered checkpoint (`user_level_ledger_count` mismatched against
  the actual entry count present) — confirms `restore_from_checkpoint()`
  fails closed with a loud exception, never silently produces a
  truncated ledger a subsequent round could build on top of.
- The pre-existing "idempotent retry" test (same block, `user_level_dp_test.cpp`'s
  secure-path section) already covers the step-4/step-5 boundary's own
  guarantee: a duplicated `apply_secure_aggregate_and_advance` call for
  an already-applied `round_id` is refused before it can reach step 5 a
  second time.

Not covered by an automated test (disclosed, not silently skipped): an
actual process-kill injected between step 5 and step 6 in a live
multi-container Docker run (the in-process checkpoint test above proves
the *file format's* fail-closed behavior, not a real OS-level crash
mid-`save_checkpoint()`) — genuine crash-injection testing at that
granularity is out of proportion to this slice, consistent with
`docs/secure-user-level-operations-audit.md`'s scope statement.
