# Signing-Key Management

**Status: Implemented and Validated end-to-end (16/16 live scenario
checks plus a separately-verified legacy migration), including real
key rotation, grace-period acceptance and expiry, and revocation,
against a live containerized coordinator over genuine mTLS.** See
`cpp/coordinator/include/fl_coordinator/signing_key_registry.hpp`/`.cpp`,
`coordinator_service.cpp`'s `resolve_signing_key`/`signing_key_status_permits`,
and `python/src/fl_platform/security/signing_key_rotation.py`.

## Repository audit (Work Package A), before this slice

Confirmed by direct inspection, not assumed: `WorkerIdentityRecord`
(`worker_identity_registry.hpp`) stored exactly one
`signing_public_key`/`signing_key_id` pair per worker, with no status
machine of its own beyond the whole worker's own
`PENDING`/`ACTIVE`/`SUSPENDED`/`REVOKED`/`EXPIRED` states.
`coordinator_service.cpp`'s `RegisterWorker` handler rejected any
presented signing key that differed from the one already on record,
with a comment stating plainly: "signing-key rotation is not yet
implemented." Heartbeat, `SubmitClientResult`, and the privacy-record
pipeline each did a single hard-equality check
(`envelope.signing_key_id() != identity_record->signing_key_id`)
against that same single cached key. `ReplayProtectionStore`'s track
key was already `(worker_id, signing_key_id, message_stream)` --
independent per signing key by construction, requiring zero changes to
support multiple co-existing keys. `WorkerSigningIdentity`
(`signing_identity.py`) and its persistence
(`save_signing_identity`/`load_signing_identity`) supported exactly one
private key file per worker_id, with no key-id-keyed storage. No
signing-key expiry, grace period, or revocation existed anywhere.

## The registry

`SigningKeyRegistry` -- a new, persistent, protobuf-free class
mirroring `WorkerIdentityRegistry`/`ReplayProtectionStore`'s exact
persistence pattern (atomic temp-file+rename, FNV-1a checksum trailer,
throws rather than silently starting empty on corruption) -- is kept
**separate** from `WorkerIdentityRegistry`: a worker's identity
(certificate binding, suspend/activate/revoke status) and a worker's
signing-key history (which keys have ever existed, each one's own
ACTIVE/GRACE_PERIOD/REVOKED/EXPIRED status) are different concerns with
different lifecycles -- one worker identity, potentially many signing
keys over its lifetime.

`WorkerIdentityRegistry` itself is **unchanged in schema** (no new
fields, no version bump) -- it still caches a single "preferred"
signing_key_id/signing_public_key pair, kept in sync on every
successful rotation via its existing idempotent-refresh
`register_identity` call, so every pre-existing consumer that only
reads that single cached pair (e.g. `WorkerIdentitySummary`) continues
to see the current key without needing to become
`SigningKeyRegistry`-aware itself.

### Record fields

`SigningKeyRecord`: `schema_version`, `worker_id`, `signing_key_id`,
`public_key_hex`, `public_key_fingerprint` (a SHA-256 hex digest,
computed by the caller via `signed_envelope_verifier.cpp`'s
`public_key_fingerprint_hex` -- kept out of the registry itself so it
stays OpenSSL-free, like `WorkerIdentityRegistry`), `status`,
`created_at_unix_s`, `activated_at_unix_s`, `expires_at_unix_s`,
`grace_period_start_unix_s`, `grace_period_end_unix_s`,
`rotated_from_key_id`, `rotated_to_key_id`, `revoked_at_unix_s`,
`revocation_reason`, `registration_source`
(`"migration"`/`"registration"`/`"rotation"`).

### Statuses

`PENDING` (declared for the state machine's completeness; no code path
in this pass ever assigns it -- both a fresh initial registration and a
freshly-rotated key become `ACTIVE` immediately, matching the
trust-on-first-use convention already established for signed capability
statements), `ACTIVE`, `GRACE_PERIOD`, `REVOKED`, `EXPIRED`.

### Lazy expiry evaluation (Work Package I)

`find()`/`find_active()`/`has_any_valid_key()`/`list_for_worker()` all
evaluate expiry **relative to the `now_unix_s` passed in**, without
requiring a maintenance sweep to have run first -- an `ACTIVE` key past
its `expires_at_unix_s`, or a `GRACE_PERIOD` key past its
`grace_period_end_unix_s`, is reported as `EXPIRED` by these read paths
immediately, live-validated (see "Live validation" below). A separate
`sweep_expired(now)` exists to **persist** that transition (so
`list_for_worker`/any administration surface reflects a durable status
rather than only a transiently computed one), but is not required for
correct verification.

## Signing-key policy (Work Package D)

* At most one `ACTIVE` key and at most one `GRACE_PERIOD` key per
  worker at any time -- enforced by `validate_rotation`'s
  `kMaxActiveKeysExceeded` check and by construction (a rotation always
  demotes exactly the current `ACTIVE` key to `GRACE_PERIOD` while
  creating exactly one new `ACTIVE` key).
* Maximum grace period: `kMaxGracePeriodSeconds` (24 hours). A
  requested grace period beyond this is rejected outright
  (`kExcessiveGracePeriod`), never silently clamped.
* No minimum key lifetime or default rotation interval is enforced --
  a worker may rotate as often as it chooses; nothing in this pass
  rate-limits rotation requests.
* No task assignment when the worker has no valid (`ACTIVE` or
  `GRACE_PERIOD`) signing key at all -- enforced in `AcquireTask`, live-
  validated.
* Preferred key: whichever key is currently `ACTIVE`.
  `WorkerIdentityRegistry`'s cached `signing_key_id` is kept pointed at
  it on every successful rotation.

## Enforcement across signed messages (Work Package J)

| Message | Accepted key statuses |
|---|---|
| Capability statement (registration/refresh) | `ACTIVE` only |
| Key-rotation request | `ACTIVE` only (a `GRACE_PERIOD` or unknown key cannot authorize a rotation) |
| Heartbeat | `ACTIVE` or `GRACE_PERIOD` |
| Client result | `ACTIVE` or `GRACE_PERIOD` |
| Sample privacy record | `ACTIVE` or `GRACE_PERIOD`, **and must equal the outer client result's signing_key_id** (Work Package K) |

A single shared helper, `resolve_signing_key` (`coordinator_service.cpp`),
is now the one enforcement point every signed-message verification path
goes through: it resolves the actual public-key bytes to verify a
signature against (critically, **not** always
`WorkerIdentityRegistry`'s single cached "preferred" key -- a message
signed by a still-valid `GRACE_PERIOD` key must be verified against
*that* key's own bytes, which `SigningKeyRegistry` retains
independently of whatever the "preferred" cache currently points at)
and enforces the status table above via `signing_key_status_permits`.
When `signing_key_registry_` is not configured (`nullptr`, the default),
every one of these call sites falls back to the pre-existing
single-key comparison, preserving every test written before this
slice unchanged.

## Result-to-privacy key consistency (Work Package K)

A signed client result and its independently signed privacy record
must be signed by the **same** signing key -- checked explicitly
(`privacy_envelope.signing_key_id() != request->envelope().signing_key_id()`)
before either signature is even verified, rejected as
`privacy_record_key_mismatch`. This closes the case of a worker mixing
keys across the two independent signatures within one submission
(e.g. signing the result with the new key but the privacy record with
the old one, or vice versa).

## Live validation

A real Python script driving the **actual production
`GrpcCoordinatorClient` class** against a live containerized
coordinator, real mTLS. 16/16 checks passed:

1. `worker-1` registers with a real signed capability statement --
   `GetWorkerSigningKeys` confirms exactly one `ACTIVE` key with
   `registration_source = "registration"`.
2. A real signed client result is accepted.
3. `rotate_signing_key()` submits a real signed rotation request
   (signed by the current key) -- accepted; the new key is `ACTIVE`
   immediately; the previous key enters `GRACE_PERIOD`.
4. A client result signed with the **new** key is accepted.
5. A client result signed with the **old** (still-`GRACE_PERIOD`) key
   is **also** accepted.
6. After the grace period passes (a real 5-second wait), a client
   result signed with the now-expired old key is rejected, with a
   message naming the exact expired status.
7. An admin `RevokeWorkerSigningKey` call on the worker's sole
   remaining valid key succeeds, and the response correctly reports
   `worker_suspended = true`.
8. The now-`SUSPENDED`, keyless worker's subsequent `AcquireTask` call
   is rejected outright.

**Separately verified**: killing the coordinator, deleting only
`signing_key_registry.dat` (leaving `worker_identity_registry.dat`
intact), and restarting produced a real
`event=SIGNING_KEY_MIGRATED worker_id=worker-1 signing_key_id=...`
structured log line at startup, and a subsequent `GetWorkerSigningKeys`
call confirmed a real, persisted `ACTIVE` entry with
`registration_source = "migration"` -- see
[signing-key-migration.md](signing-key-migration.md) for the full
account, including an honestly-disclosed caveat about what this
specific test run's migration could and could not preserve.

## What is deferred

* No signing-key-specific security events/metrics/audit records beyond
  structured stderr logging (`event=WORKER_KEY_ROTATION_ACCEPTED`,
  `event=WORKER_KEY_REVOKED`, `event=SIGNING_KEY_MIGRATED`) -- no
  Prometheus counters, no durable audit-record store.
* No RPC exposes `SigningKeyRegistry::sweep_expired()` for
  administration-triggered maintenance; it exists but is only ever
  called (if at all) by future operational tooling, not this pass.
* No default rotation interval or minimum key lifetime is enforced --
  purely a policy choice this pass declined to add, not a missing
  capability of the registry itself.
* Coordinator-signed tasks (a persistent coordinator Ed25519 signing
  identity, `SignedCoordinatorTask`, Python-side task verification, a
  worker-side task replay store) are **entirely unimplemented** --
  a separate, equally large feature explicitly deferred to a future
  pass. See [known-limitations.md](known-limitations.md).
