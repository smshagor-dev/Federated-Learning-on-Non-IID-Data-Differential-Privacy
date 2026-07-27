# Accepted-Task Execution Journal

**Status: implemented, unit-tested, live-validated (crash recovery and
duplicate-execution rejection both proven with real process/instance
restarts).**

## Design

`fl_platform.worker.task_journal.AcceptedTaskJournal` tracks every task
a worker accepts through a fixed state machine:

```
ACCEPTED -> PREPARING -> TRAINING -> RESULT_READY -> RESULT_SUBMITTED -> COMPLETED
                                                    (or FAILED / CANCELED at any point)
```

Persistent, atomic writes (temp-file + `Path.replace()`), same
convention as `SequenceStateStore`. Keyed by `task_id` (the *logical*
task identity, stable across reissue — see
[task-reissue-semantics.md](task-reissue-semantics.md)); each entry
also stores `lease_id`, `attempt`, `worker_id`,
`coordinator_signing_key_id`, `status`, `updated_at`.

## Two jobs

### 1. Crash recovery (Work Package O)

On process startup, `recover_on_startup(now)` marks any entry left in
`PREPARING` or `TRAINING` (the process died mid-execution, since a
clean run always transitions onward from those states) as `FAILED`
with reason `worker_restarted_during_execution`. This codebase has no
training-state checkpointing to safely resume a partially-completed
local training step from, so the deliberately conservative policy is:
**never silently resume; require the coordinator to reissue.** A fresh
`AcquireTask` call naturally gets a new `lease_id` and an incremented
`attempt` for the same logical `task_id` — see
[task-reissue-semantics.md](task-reissue-semantics.md).

`WorkerService.run()` calls `recover_on_startup` once, immediately
after successful registration and before the main acquire/train/submit
loop starts, and logs each recovered `task_id` at `WARNING`.

### 2. Duplicate-execution prevention (Work Package P)

`TaskDispatcher` (C++) keeps `task_id` stable and increments `attempt`
on every reissue (`sweep_expired_leases`/`cancel_lease_for_worker`).
`record_accepted()` — called from inside
`GrpcCoordinatorClient.acquire_task`, immediately after cryptographic
verification succeeds and before any task is returned to the caller —
raises `DuplicateTaskExecutionError` if this exact `task_id` already
has an entry at `RESULT_SUBMITTED`/`COMPLETED` with an `attempt` that
is not strictly lower than the one being accepted now. This is what
makes a replayed or duplicated task-acquisition response unable to
trigger a second real execution, even if it independently passed every
other check (signature, hashes, expiry, sequence/nonce).

An acceptance at the *same* attempt that never reached
`RESULT_SUBMITTED`/`COMPLETED` (e.g. a retried acquire before any
training happened) is **not** treated as a duplicate — it never
actually executed.

`GrpcCoordinatorClient.acquire_task` catches `DuplicateTaskExecutionError`
and re-raises it as
`CoordinatorTaskRejectedError(CoordinatorTaskRejectionReason.DUPLICATE_TASK_EXECUTION)`,
folding it into the same structured-rejection surface as every
cryptographic check — see
[signed-coordinator-tasks.md](signed-coordinator-tasks.md).

## Integration with `WorkerService`

`WorkerService` reads the journal via `client.accepted_task_journal`
(a public property, `None` unless the client was constructed with
`trusted_coordinator_keys_path` set — duck-typed via `getattr`, since
`CliBridgeCoordinatorClient` has no such attribute at all) and drives
`PREPARING`/`TRAINING`/`RESULT_READY`/`RESULT_SUBMITTED`/`COMPLETED`/
`FAILED`/`CANCELED` transitions at the corresponding points in
`run()` — the exact same journal instance `acquire_task` used for
`ACCEPTED`/duplicate-execution detection, so a fresh instance
constructed against the same file (simulating a crash) sees the true
in-flight state.

## Formal tests

`python/tests/test_task_journal.py`: `record_accepted` creates an
entry, full lifecycle transitions, transitioning an unknown task
raises, a reissue at a higher attempt is allowed, duplicate execution
at the same-or-lower attempt is rejected, an accepted-but-never-executed
entry does not block re-acceptance, `recover_on_startup` marks
in-flight tasks `FAILED` (via a genuinely separate
`AcceptedTaskJournal` instance against the same file, simulating a
real crash) and leaves completed tasks alone, corruption detection.

## Live validation

Real Docker build: a live coordinator issued a real signed task; the
worker's journal recorded `ACCEPTED` at `attempt=1`; after a real
lease-expiry reissue, the same `task_id` was recorded at `attempt=2`;
marking that entry `RESULT_SUBMITTED`/`COMPLETED` and then attempting
`record_accepted` again at `attempt=2` raised
`DuplicateTaskExecutionError` for real; a third real task's journal
entry was transitioned to `TRAINING`, then a **genuinely separate**
`AcceptedTaskJournal` Python object was constructed against the same
on-disk file (simulating a fresh process after a crash) and its
`recover_on_startup` call correctly reported that task as recovered
(`FAILED`). See
[signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s "Live
Docker validation" section.

## What is deferred

* Journal entry retention/cleanup policy (entries accumulate
  indefinitely; no TTL or size-based eviction implemented this pass).
* Persisting `failure_reason` in a queryable/reportable form beyond the
  raw JSON field written by `recover_on_startup`.
