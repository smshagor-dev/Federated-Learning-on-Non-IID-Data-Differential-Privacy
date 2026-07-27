# Coordinator Signing-Key Management

**Status: registry implemented and unit-tested. Rotation is a
persisted, tested *capability*, not a live-validated operational
flow this slice — see "Scope" below.**

## Design

`CoordinatorSigningKeyRegistry`
(`cpp/coordinator/include/fl_coordinator/coordinator_signing_key_registry.hpp`)
mirrors [`SigningKeyRegistry`](signing-key-management.md)'s proven
design (lazy expiry evaluation, atomic temp-file+rename persistence,
validate/commit transaction split for rotation) but tracks the
coordinator's *own* key(s), keyed by `signing_key_id` alone — there is
exactly one coordinator, not one row per `(worker_id, signing_key_id)`.

Statuses: `ACTIVE`, `GRACE_PERIOD`, `REVOKED`, `EXPIRED` (no
`PENDING` — the coordinator's own identity is ACTIVE from the moment
it is registered, unlike a worker's key, which can be registered ahead
of becoming preferred).

Interface (matches the suggested shape):

* `register_initial_key(request)` — the coordinator's first-ever key,
  ACTIVE immediately. Idempotent refresh on restart (same key_id + same
  public key is a no-op). Throws if a *different* public key is
  presented under an already-registered key_id, or if the coordinator
  already has an ACTIVE key (use rotation instead).
* `validate_rotation(request)` / `commit_rotation(request)` — read-only
  validate, then commit; rejection reasons:
  `kUnknownCurrentKey`, `kCurrentKeyNotActive`, `kDuplicateNewKeyId`,
  `kDuplicatePublicKey`, `kInvalidKeyLength`, `kExcessiveGracePeriod`
  (max 24h, matching `SigningKeyRegistry::kMaxGracePeriodSeconds`).
* `revoke_key(signing_key_id, reason, now)` — idempotent (first reason
  wins).
* `find(signing_key_id, now)`, `active_key(now)`,
  `trusted_public_keys(now)` (ACTIVE + GRACE_PERIOD — the set written
  to the trusted-key bundle file), `list(now)`, `update_expired_keys(now)`
  — all lazily expiry-evaluated, same convention as `SigningKeyRegistry`.

Protobuf-free and gRPC-free (fingerprint computed externally via
`signed_envelope_verifier.cpp`'s `public_key_fingerprint_hex` and
passed in, same convention as `SigningKeyRegistry`) — builds and is
unit-testable on Windows/MSVC without a local gRPC toolchain.

## Lifecycle wiring

`main.cpp`: loads/creates the coordinator's signing identity (see
[coordinator-signing-identity.md](coordinator-signing-identity.md)),
constructs `CoordinatorSigningKeyRegistry` at
`FL_COORDINATOR_SIGNING_KEY_REGISTRY_PATH` (default
`coordinator_signing_key_registry.dat`), and registers the identity's
key idempotently on every startup (a restart with the same identity
file is a no-op refresh, matching every other "register on startup"
loop in this file).

## Trusted coordinator key bundle

Workers must not learn to trust a coordinator key from the very task
whose authenticity is in question. `main.cpp` writes
`trusted_public_keys(now)` to a JSON bundle file at
`FL_COORDINATOR_SIGNING_KEY_BUNDLE_PATH` on every startup — delivered
to workers out of band (mounted volume / same channel as the CA cert),
never fetched over the connection being authenticated. Format:

```json
{"keys":[{"signing_key_id":"...","public_key_hex":"...","status":"active"}]}
```

`fl_platform.security.coordinator_trust_bundle.load_trusted_coordinator_keys`
(Python) is the only sanctioned worker-side loader — reads the file
directly from local disk, never via RPC. A separate admin RPC
(`GetCoordinatorSigningKeys`, gated identically to
`GetWorkerSigningKeys`) exists for operational visibility only — not
for worker trust bootstrap; its proto comment says so explicitly.

## Formal tests

`cpp/coordinator/tests/coordinator_signing_key_registry_test.cpp` (part
of `fl_coordinator_tests`, builds and passes on Windows/MSVC without
gRPC): initial-key registration + idempotent refresh, key-swap
rejection, second-initial-key-while-ACTIVE rejection, full rotation
validate/commit, every rotation rejection reason, lazy expiry of a
grace-period key, `trusted_public_keys` before/after expiry,
revocation + idempotency + unknown-key-throws, restart persistence,
corruption detection.

## Live validation

Real Docker build: a live coordinator process generated its identity,
registered it into the registry, wrote a real trusted-key bundle file,
and a real `GrpcCoordinatorClient` loaded that exact file and verified
live signed tasks against it — see
[signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s "Live
Docker validation" section.

## Scope: what is deferred

* **No gRPC rotation RPC.** The registry's `validate_rotation`/
  `commit_rotation` are implemented and unit-tested in isolation, but
  nothing in `coordinator_service.cpp` calls them — there is no RPC a
  coordinator operator could call to rotate the live signing key.
* **No live-validated rotation scenario.** The worker-key-rotation
  slice's live test (old key grace-period acceptance, post-grace-period
  expiry rejection) was not repeated for the coordinator's own key.
* A manual CLI/administration script for coordinator key rotation (the
  spec's suggested minimal alternative to a full RPC) was not built
  this pass either — left for a follow-on slice, alongside full Go/web
  key administration.
