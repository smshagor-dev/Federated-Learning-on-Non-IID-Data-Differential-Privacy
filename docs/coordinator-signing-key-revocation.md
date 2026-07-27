# Coordinator Signing-Key Revocation

**Status: implemented and live-validated** (both via the live
`RevokeCoordinatorSigningKey` RPC and via the recovery CLI's `revoke`
subcommand, exercised independently).

## Design

`RevokeCoordinatorSigningKey` (`ADMIN_CONTROL`, gated identically to
`GetCoordinatorSigningKeys`/`RotateCoordinatorSigningKey`) calls
`CoordinatorSigningKeyRegistry::revoke_key` — the same registry method
built (and unit-tested) in the Coordinator-Signed Tasks slice, now
wired to a live RPC for the first time.

### Request fields

`signing_key_id`, `reason`, `request_id`, `trace_id`, `idempotency_key`,
`expected_status` (compare-and-set: if non-empty and the key's current
status does not match, the request is rejected with
`FAILED_PRECONDITION` rather than silently revoking a key an operator
did not expect to be in that state).

### Rules implemented

* **Revocation is immediate** — `revoke_key` transitions the record to
  `REVOKED` synchronously; there is no pending/scheduled revocation.
* **A revoked key cannot sign new tasks** — moot for the coordinator's
  own key specifically (only the currently-`ACTIVE` key is ever used
  for signing; a revoked key was never the active signer, or, if it
  was, revoking it removes it from `active_key()`'s result, which
  `AcquireTask` checks before signing every task — see below).
* **Workers reject tasks signed by a revoked key** — enforced entirely
  client-side: the trusted-key bundle omits any `REVOKED` key
  (`trusted_public_keys()` only returns `ACTIVE`/`GRACE_PERIOD`), so a
  worker that reloads the bundle after a revocation simply has no
  trusted key to verify a revoked signer's tasks against, and
  `verify_coordinator_task` rejects with `UNKNOWN_SIGNING_KEY`.
* **Historical task records retain key metadata** — `SignedCoordinatorTask.coordinator_signing_key_id`
  is never rewritten after the fact; `CoordinatorSigningKeyRegistry::list()`
  and the recovery CLI's `show` subcommand both continue to display a
  revoked key's full history (`revoked_at_unix_s`, `revocation_reason`)
  indefinitely.
* **The trusted-key bundle is regenerated** after every revocation
  (`regenerate_trusted_key_bundle`, same atomic/versioned writer as
  rotation — see [trusted-coordinator-key-bundle.md](trusted-coordinator-key-bundle.md)).
* **Revoking a grace-period key is allowed** — `revoke_key` has no
  status precondition beyond "not already revoked" (idempotent).
* **Revoking the only ACTIVE key forces secure task issuance to
  stop** — `AcquireTask` already checked (from the Coordinator-Signed
  Tasks slice) `coordinator_signing_key_registry_->active_key(now).has_value()`
  before signing; revoking the sole ACTIVE key makes that check fail,
  and `AcquireTask` returns `FAILED_PRECONDITION` for every subsequent
  call until a new key is rotated in. `RevokeCoordinatorSigningKeyResponse.production_task_issuance_stopped`
  tells the caller this happened without them needing to separately
  query `GetCoordinatorSigningKeys`.
* **No automatic replacement key** — revocation never calls
  `generate_coordinator_signing_identity()` or touches the registry's
  ACTIVE slot beyond marking the target key `REVOKED`. An operator who
  wants a replacement must call `RotateCoordinatorSigningKey` (or the
  recovery CLI's `rotate` subcommand) explicitly and separately.

### Idempotency

Identical to rotation's: a retried request with the same
`idempotency_key` returns the persisted outcome (`changed`,
`production_task_issuance_stopped`, and the key's current summary)
without re-running `revoke_key` — safe because `revoke_key` is already
naturally idempotent at the domain level (first reason wins), so a
retry cannot corrupt state even without the idempotency store, but the
store still avoids re-doing bundle regeneration and re-logging the
event on every retry.

## Live validation

Two independent paths, both real:

1. **Live RPC**: a real coordinator process, a real go-api mTLS
   identity calling `RevokeCoordinatorSigningKey` over the wire.
2. **Recovery CLI**: `fl_coordinator_key_admin_cli revoke --key-id ...`
   against the same on-disk registry file with no server running at
   all — confirmed to produce the identical registry state
   (REVOKED status, retained metadata, empty trusted-key bundle
   when the sole ACTIVE key is revoked) as the RPC path.

See [security-administration-report.md](security-administration-report.md)
for the full scenario list.

## What is deferred

* No automated alert/notification when `production_task_issuance_stopped`
  becomes true — an operator must actively check.
* No Go/web surface calls this RPC yet (see
  [known-limitations.md](known-limitations.md)).
