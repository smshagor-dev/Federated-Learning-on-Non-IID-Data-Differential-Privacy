# Worker Activation

**Status: Implemented and Validated live.** `ActivateWorker` RPC over
`WorkerIdentityRegistry::activate` (persistent, restart-safe, idempotent
— from the prior slice).

## What is actually checked

`WorkerIdentityRegistry::activate` (prior slice, unchanged) transitions
`PENDING`/`SUSPENDED` → `ACTIVE`, and throws `WorkerIdentityRegistryError`
if the record is `REVOKED` (revocation is terminal — activation is
rejected, not silently ignored; `ActivateWorker`'s handler propagates
this as `FAILED_PRECONDITION`).

**Requirements from the parent specification not separately re-checked
by `ActivateWorker` itself** (a real, honest gap, not silently assumed
satisfied):

* "Certificate is not expired" — not re-validated at activation time;
  the certificate's expiry is a property of the mTLS handshake that
  authenticated the `ActivateWorker` *caller* (go-api), not the worker
  being activated, so there is nothing to re-check against for the
  target worker at this RPC.
* "Certificate fingerprint is not revoked" / "at least one valid active
  signing key exists" / "capability statement remains valid or must be
  refreshed" — none of these are separately re-verified by
  `ActivateWorker`; only the `registration_status != REVOKED` check
  (already enforced by `WorkerIdentityRegistry::activate` itself) gates
  the call. A worker whose capability statement has since expired would
  still be marked `ACTIVE` by this call, and would only be caught the
  next time it actually tries to `RegisterWorker`/`Heartbeat`/submit a
  result (each of those independently verifies whatever they need at
  that time).

## `ActivateWorker` RPC

```text
ActivateWorkerRequest{ worker_id, reason, request_id, trace_id }
  -> WorkerLifecycleResponse{ identity, changed, leases_canceled=0 }
```

Requires the go-api service identity, same as `SuspendWorker`/
`RevokeWorker`. **Never activates a worker automatically from a
heartbeat** — `Heartbeat`'s handler contains no call to `activate()`
anywhere; the only way a worker's status changes is this explicit RPC
or the prior slice's `RegisterWorker` (which never activates either —
a fresh `worker_id` starts `PENDING`, never `ACTIVE`). A structured
`event=WORKER_ACTIVATED` line is written to stderr on every call.
Idempotent: activating an already-active worker succeeds, `changed=false`.

## Live validation

See [signed-client-results.md](signed-client-results.md): a suspended
worker was activated via this RPC (`registration_status="active"`),
and its subsequent `AcquireTask` call succeeded, proving the status
transition actually took effect on the enforcement path, not just in
the registry record.
