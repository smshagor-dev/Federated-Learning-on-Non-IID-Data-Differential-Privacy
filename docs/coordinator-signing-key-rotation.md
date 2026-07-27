# Coordinator Signing-Key Rotation (Live Operational Flow)

**Status: implemented and live-validated.** This closes the gap
explicitly deferred in
[coordinator-signing-key-management.md](coordinator-signing-key-management.md)'s
"Scope" section: `CoordinatorSigningKeyRegistry::validate_rotation`/
`commit_rotation` were implemented and unit-tested previously but had
no RPC and no live-validated operational flow. Both now exist.

## Design (implementation design note, written before code)

### What already existed (not rewritten)

`CoordinatorSigningKeyRegistry` (ACTIVE/GRACE_PERIOD/REVOKED/EXPIRED,
lazy expiry evaluation, validate/commit transaction split, max grace
period) and the trusted-key-bundle-write logic in `main.cpp` were built
in the Coordinator-Signed Tasks slice and are reused, not rewritten —
per the standing instruction not to rewrite stable logic without a
demonstrated defect.

### What is new

1. **Keyed coordinator private-key storage.** The coordinator's
   *first-ever* ("genesis") identity still loads from the single fixed
   `FL_COORDINATOR_SIGNING_KEY_PATH` file, unchanged. A key created by
   rotation is persisted to
   `{FL_COORDINATOR_SIGNING_KEY_DIR}/coordinator.{key_id}.signing-key.pem`
   — the coordinator-side mirror of `signing_key_rotation.py`'s
   `{worker_id}.{key_id}.signing-key.pem` convention for workers
   (`coordinator_signing_identity.hpp`'s `save_keyed_coordinator_signing_identity`/
   `load_keyed_coordinator_signing_identity`).
2. **A thread-safe mutable active-identity holder**
   (`CoordinatorActiveIdentityStore`): `AcquireTask` previously read a
   `const CoordinatorSigningIdentity*` set once at construction. Real
   rotation needs to swap which identity is used for signing while the
   server keeps handling concurrent requests — `current()` returns an
   immutable snapshot (`shared_ptr<const CoordinatorSigningIdentity>`),
   `set()` atomically replaces it under a mutex. No signing call ever
   observes a partially-updated identity.
3. **`RotateCoordinatorSigningKey` / `RevokeCoordinatorSigningKey` RPCs**
   (`ADMIN_CONTROL`, gated identically to `GetCoordinatorSigningKeys`).
4. **An `IdempotencyStore`** (protobuf-free, atomic-file-persisted,
   same convention as every other store in this codebase): keyed by
   `(rpc_name, idempotency_key)`, records the outcome of a mutation the
   first time it completes; a retry with the same key returns the
   recorded outcome directly, without re-running the mutation (so a
   retried rotation never mints a second new key).
5. **A reusable, atomic trusted-bundle writer**
   (`trusted_key_bundle.hpp`/`.cpp`, Work Package E) — see
   [trusted-coordinator-key-bundle.md](trusted-coordinator-key-bundle.md).
   Called at coordinator startup and after every rotation/revocation.

### Rotation flow

```
Go administration request (deferred — not built this slice; the RPC
itself is the real, live-tested surface a future Go client will call)
  -> RotateCoordinatorSigningKey (ADMIN_CONTROL, go-api identity required)
  -> idempotency check (return cached result if idempotency_key seen before)
  -> registry.validate_rotation(expected_current_active_key_id, ...)
  -> generate a fresh Ed25519 identity (real OpenSSL keygen)
  -> registry.commit_rotation(...)  [new key ACTIVE, old key GRACE_PERIOD]
  -> persist the new identity's private key (keyed file)
  -> regenerate the trusted-key bundle atomically
  -> swap CoordinatorActiveIdentityStore to the new identity
  -> record the idempotency outcome
  -> new AcquireTask calls sign with the new key
  -> already-issued tasks signed by the old key remain verifiable
     (GRACE_PERIOD) until their own expiry or the grace period ends,
     whichever comes first
```

If bundle regeneration fails, the rotation is **not** committed to the
registry and the active identity is **not** swapped — see "Rollback on
bundle-generation failure" below.

### Required fields (per the specification)

`RotateCoordinatorSigningKeyRequest`: `request_id`, `trace_id`,
`reason`, `expected_current_signing_key_id`, `new_key_expires_at_unix_s`,
`requested_grace_period_seconds`, `idempotency_key`.
`RevokeCoordinatorSigningKeyRequest`: `signing_key_id`, `reason`,
`request_id`, `trace_id`, `idempotency_key`, `expected_status`.

No private key ever appears in a request, a response, an event, or an
audit record — both RPCs' responses carry only
`CoordinatorSigningKeyRecordSummary` (public metadata).

### Safety rules implemented

* **No current ACTIVE key**: `validate_rotation` already rejects
  (`kUnknownCurrentKey`/`kCurrentKeyNotActive`) — reused unchanged.
* **More than one ACTIVE key**: cannot occur by construction — the
  registry only ever creates a second ACTIVE key by transitioning the
  first to GRACE_PERIOD/EXPIRED in the same `commit_rotation` call
  (unit-tested previously; re-confirmed here).
* **Maximum grace period**: `kMaxGracePeriodSeconds` (24h), reused
  unchanged.
* **Maximum coordinator-key lifetime**: enforced by requiring
  `new_key_expires_at_unix_s` to be a real time strictly after `now`
  (existing `kInvalidExpiry` check) — this slice adds an explicit
  *maximum* lifetime cap (`kMaxCoordinatorKeyLifetimeSeconds`, 90 days)
  rejected with a new `kExcessiveKeyLifetime` reason, since the prior
  slice's registry only checked for a *minimum* validity, not a
  maximum.
* **Rollback on bundle-generation failure**: the RPC handler generates
  the new identity and calls `commit_rotation` only after confirming
  the bundle *can* be written (a dry-run existence/permission check on
  the target directory); if the atomic bundle write itself still fails
  after `commit_rotation`, the handler logs a `CRITICAL`-severity error
  and returns `INTERNAL` — the registry state is *not* rolled back in
  that narrow post-commit-write-failure case (rolling back a
  already-persisted, already-atomically-written registry file would
  itself need a second transaction with its own failure modes; instead
  the documented recovery path is to retry bundle regeneration via the
  recovery CLI tool — see
  [coordinator-key-recovery.md](coordinator-key-recovery.md)). This
  honest limitation is stated here rather than silently implied to be
  fully transactional.
* **Restart persistence**: the registry file and the keyed private-key
  file are both real, atomically-written files; a restart re-derives
  the correct active identity (see "Keyed coordinator private-key
  storage" above).
* **Idempotent retry**: see `IdempotencyStore` above.

## Live validation

See [security-administration-report.md](security-administration-report.md)
for the full scenario list — real Docker build, real mTLS, a real
rotation performed against a live coordinator process, confirmed via a
second `AcquireTask` call signed with the new key while a task signed
by the old (now GRACE_PERIOD) key remained independently verifiable.

## What is deferred

* The Go administration client and HTTP endpoint that would call this
  RPC in production (`POST /api/v1/security/coordinator/signing-keys/rotate`)
  — the RPC itself is real and live-tested directly; no Go code exists
  yet to call it. See [known-limitations.md](known-limitations.md).
* A background scheduler that rotates automatically on a timer.
