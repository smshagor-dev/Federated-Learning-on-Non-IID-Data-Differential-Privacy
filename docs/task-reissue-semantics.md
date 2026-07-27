# Task Reissue Semantics

**Status: implemented (built on pre-existing `TaskDispatcher` behavior,
unchanged), live-validated.**

## The existing domain model this builds on

`TaskDispatcher` (C++, unchanged this slice) already had exactly the
right shape for reissue: `DispatchedTask::task_id` is assigned once,
at `enqueue()` time, and never changes; `lease_id` is reassigned fresh
(`"lease-" + std::to_string(++lease_sequence_)`) every time `acquire()`
hands the task to a worker; `attempt` is incremented
(`++task.attempt`) on every `acquire()` call for that task, including
reissues after `sweep_expired_leases()` or
`cancel_lease_for_worker()` requeue it.

This slice's contribution is binding that existing model into the
signed-task contract: `SignedCoordinatorTask.task_id`/`lease_id`/
`attempt` are populated directly from `DispatchedTask`'s fields (see
[signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s `AcquireTask`
wiring), and the worker-side accepted-task journal (see
[accepted-task-journal.md](accepted-task-journal.md)) uses exactly
this `(task_id, attempt)` pair to distinguish a legitimate reissue from
a duplicate/replayed execution attempt.

## What "reissue" means concretely

1. A worker acquires a task (`task_id="task-1"`, `lease_id="lease-1"`,
   `attempt=1`), receives a `SignedCoordinatorTask` binding exactly
   those values plus a fresh `nonce`/`sequence_number`, and records
   `ACCEPTED` in its journal.
2. The worker never submits a result before the lease expires (a
   crash, a hung training step, a network partition — the coordinator
   cannot distinguish these cases and does not try to).
3. `TaskDispatcher::sweep_expired_leases` (triggered lazily via
   `RunInstance::advance`, called at the top of every `AcquireTask`)
   requeues the task: `task_id` stays `"task-1"`, `lease_id` and
   `worker_id` are cleared, the task returns to the pending queue.
4. The **same or a different** worker's next `AcquireTask` call
   receives `task_id="task-1"` again, but with `lease_id="lease-2"`,
   `attempt=2`, and a fresh `nonce`/`sequence_number` — a structurally
   new `SignedCoordinatorTask`, signed independently.
5. The worker's journal sees an existing entry for `"task-1"` at a
   *lower* attempt with no `RESULT_SUBMITTED`/`COMPLETED` status (or no
   entry at all, if a different worker got the reissue) — the new
   acceptance is allowed.
6. If a stale, already-submitted attempt is somehow redelivered (e.g. a
   captured/replayed old response), the journal's
   `attempt`-vs-recorded-`attempt` comparison rejects it as a duplicate
   execution — see [accepted-task-journal.md](accepted-task-journal.md).

## Why `task_id` stays stable rather than minting a new logical ID

The specification's suggested alternative ("prefer an explicit logical
task ID / attempt ID / lease ID when practical") is already satisfied
by the pre-existing domain model without inventing a new field:
`task_id` *is* the stable logical identifier, `lease_id` *is* the
per-attempt identifier, and `attempt` is an explicit integer counter —
restructuring `TaskDispatcher`'s ID scheme to invent a fourth identifier
would be scope creep against a working, already-tested mechanism with
no demonstrated defect.

## Live validation

A real Docker-built coordinator (`task_lease_seconds=3`) issued a real
signed task, the lease was allowed to expire without submission, and a
second live `AcquireTask` call over the same real mTLS connection
returned the *same* `task_id` with `attempt=2` and a structurally
distinct signature/nonce/sequence_number — confirmed by direct
inspection of both live responses, not simulated. See
[signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s "Live
Docker validation" section.

## What is deferred

* An explicit coordinator-side "reissue reason" surfaced to the worker
  (the worker currently cannot distinguish "reissued because the prior
  lease expired" from any other reissue cause — it only sees a new
  `attempt`).
* Coordinator-initiated task **cancellation** notifications tied to
  reissue (out of scope for this slice; unrelated to signing).
