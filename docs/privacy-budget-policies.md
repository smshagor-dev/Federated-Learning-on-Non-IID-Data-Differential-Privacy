# Privacy Budget Policies

**Status: implemented & tested.** Source: `fl::core::PrivacyBudgetPolicy`
(`cpp/core/include/fl_core/privacy.hpp`), enforcement in
`RunInstance::finalize_round` (`cpp/coordinator/src/run_manager.cpp`,
both the pre-check block and the `check_reactive_budget` closure), wire
enum `fl.privacy.v1.PrivacyBudgetPolicy` (proto). Tests: the
budget-policy groups in `cpp/coordinator/tests/user_level_dp_test.cpp`
and `cpp/coordinator/tests/adaptive_clipping_test.cpp`.

## What a budget is

Each of user-level DP and adaptive clipping (sample-level DP's budget is
tracked but enforced worker-side, not by the coordinator — see below)
has its own `epsilon_budget` field, independent per mechanism per the
Critical Privacy Rule ([privacy-mathematics.md](privacy-mathematics.md)).
`epsilon_budget <= 0.0` means "unset" — no enforcement beyond
`target_delta` itself, and `budget_remaining` is reported as `+infinity`
rather than `0`, so "no budget configured" is never confused with
"budget exhausted."

## The four policies

| Policy | Check timing | Behavior once epsilon meets/exceeds budget |
|---|---|---|
| `kWarnOnly` (default) | reactive | Emits `kPrivacyBudgetWarning`/`kPrivacyBudgetExceeded` events only; never blocks anything. A caller who sets a budget without picking a policy gets visibility, not a surprise run stoppage. |
| `kStopBeforeExceeding` | **preventive** | Checked *before* the round's mechanism is applied, using the *projected* epsilon it would produce. If applying it would meet-or-exceed budget, that round's private release is refused entirely (never partially applied) and the run ends (`kCompleted`) without releasing the round. The only policy that guarantees the budget is never actually crossed. |
| `kStopAfterCurrentRound` | reactive | The round is applied and released normally (may cross the budget by up to one round's worth); if the resulting epsilon meets/exceeds budget, no further round starts (`kCompleted`). |
| `kFailRun` | reactive | Same reactive check as `kStopAfterCurrentRound`, but treats crossing the budget as a run failure (`kFailed`) rather than a graceful stop. |

## Preventive vs. reactive, concretely

`kStopBeforeExceeding` is checked at the very top of `finalize_round`,
using `project_epsilon(1)`/`projected_epsilon_after_one_more_round()` —
"what would epsilon become if this round's mechanism ran" — *before*
clip/aggregate/noise happens. If the projection meets or exceeds budget
for user-level DP or adaptive clipping, a `kPrivacyBudgetExceeded` event
fires with a message explaining the round was refused, and no noise is
added, no client contribution is incorporated, nothing is released.

The other three reactive policies (`kWarnOnly` also runs this check, but
only to decide whether to warn — never to block) run *after* the round's
real epsilon is known, via a shared `check_reactive_budget` closure
applied independently to each active mechanism:

1. If `warning_threshold_fraction` is configured and current epsilon has
   crossed that fraction of budget but not the full budget yet, emit
   `kPrivacyBudgetWarning` and return (no stop/fail).
2. Otherwise, if epsilon has met/exceeded the full budget, emit
   `kPrivacyBudgetExceeded` and apply the mechanism's `switch` on policy
   (`kWarnOnly` → nothing further; `kStopAfterCurrentRound` → stop after
   this round; `kFailRun` → fail the run). `kStopBeforeExceeding` also has
   a branch here as defense-in-depth only — the pre-check above should
   already have prevented reaching this point under that policy.

## Applied independently per mechanism

`check_reactive_budget` is called once for user-level DP's accountant and
once (separately) for adaptive clipping's accountant, each against its
own `epsilon_budget` and its own current epsilon. A budget configured for
one mechanism never triggers a stop/fail/warning for another — hybrid
mode with both mechanisms active and different budgets on each can, for
example, stop user-level DP's contribution while adaptive clipping keeps
running, or vice versa (the policy on each is evaluated and acted on
independently; there is no run-wide "any mechanism exceeded" flag).

## Sample-level DP's budget

**Status: enforced worker-side as of the Secure Aggregation and
Cryptographic Protocols category's closure-gate work.** Sample-level
DP's accounting happens entirely in the Python worker (see
[privacy-mathematics.md](privacy-mathematics.md)); the coordinator still
only stores and relays each `SampleLevelLedgerEntry`, never computing a
running total itself — but the worker now enforces
`SampleLevelDPConfig.epsilon_budget` against its own real Opacus
accountant before signing off on a result, via
`fl_platform.privacy.budget_enforcement.SampleBudgetEnforcer`. See
[secure-aggregation-architecture.md](secure-aggregation-architecture.md)'s
closure-gate section for the full design (a separate, per-task policy
enum — `SamplePrivacyBudgetPolicy`: WARN_ONLY/STOP_BEFORE_EXCEEDING/
STOP_AFTER_CURRENT_TASK/FAIL_TASK — distinct from the four
`PrivacyBudgetPolicy` values above, since sample-level enforcement is
worker-side and per-task, not coordinator-side and per-round).
`epsilon_budget` still flows through to `PrivacyLedger.project()`'s
`budget_remaining` field for operator visibility, in addition to now
being actively enforced, not merely displayed.

**Updated (Privacy Record Authenticity, Signing-Key Lifecycle, and
Coordinator-Signed Tasks slice)**: the coordinator now also enforces
*consistency* between a signed privacy record's `budget_decision` and
whether the accompanying submission looks like a normal completed
update — a `stopped_before_step`/`refused_before_training`/`failed_task`
decision alongside a normal result is rejected as a contradiction;
`stopped_after_task` is correctly allowed (the triggering task's result
is still submittable per policy). This is consistency enforcement, not
independent recomputation — see
[signed-privacy-records.md](signed-privacy-records.md)'s "Budget-decision
consistency" section for the full rule table.

**What is still not covered by this pass**: the wire contract
(`CreateRun` → `ClientTrainingTask.sample_level_privacy`) does not yet
carry `sample_budget_policy` end-to-end — the proto field exists
(`SampleBudgetPolicy` in `privacy.proto`) but `coordinator_client.py`'s
wire decode does not populate it yet, since doing so needs regenerated
Python bindings this environment cannot produce without Docker/CI (see
known-limitations.md). Enforcement also does not yet persist across a
worker *process* restart or hand off between different worker processes
serving the same client across rounds — the enforcer is scoped to one
worker process's in-memory lifetime for a given client_id. Both are
documented gaps, not silent omissions.
