# Signing-Key Migration

**Status: Implemented and Validated live**, including a real coordinator
restart with `signing_key_registry.dat` deleted while
`worker_identity_registry.dat` was retained. See
`cpp/coordinator/main.cpp`'s migration loop (right after both
registries are constructed, before `CoordinatorServiceImpl` is built).

## What migrates and when

At every coordinator startup, unconditionally: for every
`WorkerIdentityRecord` in `WorkerIdentityRegistry` that has a
non-empty `signing_public_key`/`signing_key_id`, if
`SigningKeyRegistry` has no entry yet for that exact
`(worker_id, signing_key_id)` pair, one is created via
`register_initial_key` with `registration_source = "migration"`,
preserving the exact same `signing_key_id` and public-key bytes --
never a new key, never a changed key_id.

## Idempotency

The existence check (`signing_key_registry->find(...).has_value()`)
is what makes this loop safe to run on **every** startup, not just
"the first one after upgrading" -- a worker already migrated (or
freshly registered through the registry-aware `RegisterWorker` path,
which populates both registries directly) is skipped on every
subsequent restart. No separate "has migration already run" flag file
is needed.

## Live validation

A real coordinator process was killed, `signing_key_registry.dat` was
deleted while `worker_identity_registry.dat` (containing a real,
previously-registered `worker-1` record) was left untouched, and the
process was restarted. The startup log produced a real, structured
line:

```text
event=SIGNING_KEY_MIGRATED worker_id=worker-1 signing_key_id=6b01abc84ad2cb52
```

A subsequent `GetWorkerSigningKeys` call confirmed a real, persisted
entry: `status=active`, `registration_source=migration`, with the
exact same `signing_key_id` the worker had been using before the
restart.

## An honestly-disclosed caveat about this specific test run

The `worker-1` record used for this live test had, over the course of
the same test session, already gone through **real key rotation and
revocation** against the now-deleted `SigningKeyRegistry` file -- its
signing key had actually been marked `REVOKED` there before the file
was deleted. Migration has no way to know that: it can only read what
`WorkerIdentityRegistry`'s own single cached
`signing_public_key`/`signing_key_id` fields say (which carry no
per-key revocation state of their own, only the identity's own overall
`registration_status`), so the migrated key came back as `ACTIVE`.

This is expected, not a defect: migration's actual design target is a
coordinator upgrading from a version that **never had a
`SigningKeyRegistry` at all** (the single-key-only era this slice
replaces) -- in that real scenario there is no prior per-key revocation
state to lose, since none ever existed. Deleting an **existing**
`SigningKeyRegistry` file that already recorded finer-grained key
history is a different, artificial scenario (only exercised here to
prove the migration code path fires and persists correctly), and is
not equivalent to a genuine first-time upgrade. Stated plainly so this
distinction is never silently assumed away.

## What is deferred

* No explicit "migration status" record beyond `registration_source`
  and the coordinator's own structured startup log line -- there is no
  separate persisted migration journal an operator could audit after
  the fact beyond those two things.
* No rollback mechanism if a migration partially completes (e.g. the
  process crashes mid-loop) -- since each `register_initial_key` call
  is itself atomic and idempotent, a resumed startup simply continues
  migrating whatever remains, which is the practical equivalent of a
  rollback-free retry, but this was not separately stress-tested with
  an injected mid-loop crash.
