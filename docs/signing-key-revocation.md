# Signing-Key Revocation

**Status: Implemented and Validated live**, including real
worker-suspension-on-sole-key-revocation and real subsequent
task-acquisition blocking. See `coordinator_service.cpp`'s
`RevokeWorkerSigningKey`.

## The operation

`RevokeWorkerSigningKey(RevokeWorkerSigningKeyRequest) returns (RevokeWorkerSigningKeyResponse)`
-- an `ADMIN_CONTROL` RPC, gated identically to the five worker-lifecycle
RPCs from the prior slice (`reject_if_not_go_api_service_identity`: a
worker certificate calling this is rejected outright; only the
authenticated `spiffe://federated-platform/service/go-api` identity
may call it). Request: `worker_id`, `signing_key_id`, `reason`,
`request_id`, `trace_id`. Immediate, unconditional, idempotent (a
second revocation of an already-revoked key returns the first
revocation's reason unchanged, matching `WorkerIdentityRegistry::revoke`'s
identical convention) -- revocation is meant to be usable as an
unconditional emergency action, never itself rejectable.

## Effects

* The key's status becomes `REVOKED`, persisted immediately.
* Every subsequent signed message presenting that `signing_key_id` is
  rejected (`signing_key_status_permits` returns `false` for `REVOKED`
  against every message kind, including capability refresh and
  rotation).
* Existing replay/sequence state for that key is left in place (not
  purged) -- unlike full worker revocation, which does purge all of a
  worker's replay state via `ReplayProtectionStore::purge_worker`
  (there is no equivalent narrower purge for a single revoked key in
  this pass).
* Historical records that key already signed remain exactly as
  verifiable as before.

## When the revoked key was the worker's only valid key

If, after revocation, `SigningKeyRegistry::has_any_valid_key` reports
`false` for that worker (no remaining `ACTIVE`/`GRACE_PERIOD` key at
all), the handler automatically transitions the worker's
`WorkerIdentityRegistry` status to `SUSPENDED` (unless it is already
`SUSPENDED` or `REVOKED`) -- a controlled, **reversible** state an
operator can later `ActivateWorker` out of once a new key is
registered through a real recovery flow, rather than leaving the
worker's identity silently `ACTIVE` with no usable key. The response's
`worker_suspended` field reports whether this actually happened.

A revoked key can never authorize its own replacement -- `resolve_signing_key`'s
`ACTIVE`-only requirement for `SignedMessageKind::kKeyRotation` already
guarantees this without any additional special-casing.

## Live validation

See [signing-key-management.md](signing-key-management.md)'s "Live
validation" section, scenarios 7-8: revoking `worker-1`'s sole
remaining `ACTIVE` key returned `key.status == "revoked"` and
`worker_suspended == true`; the worker's immediately-following
`AcquireTask` call was rejected outright (no task returned), proving
both the revocation itself and the automatic suspension it triggered
are real and enforced, not just recorded.

## What is deferred

* No "controlled identity recovery flow" beyond the pre-existing
  `ActivateWorker` RPC (from the prior slice) plus a fresh
  `RegisterWorker`/rotation to establish a new valid key -- there is no
  dedicated, purpose-built "recover this worker's identity after a key
  compromise" RPC or workflow beyond composing those two existing
  operations.
* No security event, metric, or audit record beyond the structured
  `event=WORKER_KEY_REVOKED` stderr log line.
* No narrower `ReplayProtectionStore` purge scoped to just the revoked
  key (only whole-worker purges exist, from the prior slice, and are
  not triggered by this operation).
