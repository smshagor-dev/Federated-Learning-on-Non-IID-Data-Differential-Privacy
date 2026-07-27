# Coordinator Signing Identity

**Status: implemented and live-validated.**

## What this is

A persistent Ed25519 keypair the C++ coordinator generates once and
reuses across restarts, kept deliberately separate from its TLS server
credential:

| | Purpose | File | Env var |
|---|---|---|---|
| TLS server key | authenticates the transport connection (mTLS) | operator-provided cert/key pair | `FL_COORDINATOR_SERVER_CERT` / `FL_COORDINATOR_SERVER_KEY` |
| Signing identity | authenticates individual signed tasks, independent of which connection carried them | `FL_COORDINATOR_SIGNING_KEY_PATH` (default `coordinator_signing_key.pem`) | `FL_COORDINATOR_SIGNING_KEY_PATH` |

Same separation-of-concerns argument as
[worker-identity.md](worker-identity.md) makes for worker signing
keys. A worker rotating/losing its TLS certificate does not need to
re-trust the coordinator; a coordinator rotating its TLS certificate
does not invalidate any worker's trust in its signing key.

## Implementation

`cpp/coordinator/include/fl_coordinator/coordinator_signing_identity.hpp`
/ `.cpp` (gRPC-gated build only, real OpenSSL `EVP_PKEY_ED25519`):

* `generate_coordinator_signing_identity()` — real keygen
  (`EVP_PKEY_CTX_new_id` / `EVP_PKEY_keygen`), never deterministic or
  hand-rolled.
* `load_or_create_coordinator_signing_identity(path)` — generates on
  first run and persists the raw 32-byte private seed to `path`
  (best-effort `chmod 0600` on POSIX; Windows has no equivalent, same
  honest caveat as `signing_identity.py`'s `save_signing_identity`).
  Throws `CoordinatorSigningIdentityError` on a malformed *existing*
  file rather than silently regenerating over it — a silent
  regeneration would change which key every already-deployed worker
  trusts, without anyone deciding that on purpose.
* `sign_with_coordinator_identity(identity, message)` — real
  `EVP_DigestSign`, hex-encoded 64-byte signature.
* `coordinator_key_id_for(public_key_hex)` — first 8 raw bytes of the
  public key, hex-encoded (16 hex chars). Matches
  `WorkerSigningIdentity._key_id_for`'s (Python) identical convention
  byte-for-byte.

This is the first place the C++ coordinator *signs* rather than only
*verifies* — every prior authenticity slice
([signed-capabilities.md](signed-capabilities.md),
[signed-worker-envelopes.md](signed-worker-envelopes.md)) only ever
checked a signature the coordinator did not produce.

## Lifecycle

* `main.cpp` calls `load_or_create_coordinator_signing_identity` once
  at startup and registers the resulting key_id/public_key into
  `CoordinatorSigningKeyRegistry` (idempotent — see
  [coordinator-signing-key-management.md](coordinator-signing-key-management.md)).
  A pre-existing, malformed key file fails the process at startup
  (structured error, non-zero exit), matching every other persistence
  class's fail-closed convention in this codebase.
* `AcquireTask` attaches a `SignedCoordinatorTask` to every response
  only when this identity is configured — see
  [signed-coordinator-tasks.md](signed-coordinator-tasks.md).

## Live validation

Verified inside a real Docker build (`mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`,
real `libgrpc++-dev`/`protobuf-compiler-grpc`):

* `fl_coordinator_task_signing_tests` (standalone gRPC-gated unit
  test): real keygen produces distinct keys across two calls; a fresh
  identity persists to disk and reloads with the same public key/key_id;
  a truncated (wrong-length) key file throws rather than silently
  regenerating; signing the same message twice is deterministic
  (Ed25519 is a deterministic signature scheme); a genuine signature
  verifies against the signer's own public key and fails against a
  different identity's key.
* A live coordinator process (real mTLS, `FL_COORDINATOR_SIGNING_KEY_PATH`
  set) started, generated a real identity file on first boot, and
  attached real `SignedCoordinatorTask` envelopes to live `AcquireTask`
  responses over a real gRPC connection to a real
  `GrpcCoordinatorClient` — see
  [signed-coordinator-tasks.md](signed-coordinator-tasks.md)'s "Live
  Docker validation" section for the full scenario list.

## Deferred

* Coordinator signing-key **rotation** as an operational flow (no gRPC
  rotation RPC, no live-validated rotation scenario) — see
  [coordinator-signing-key-management.md](coordinator-signing-key-management.md)'s
  scope note. `CoordinatorSigningKeyRegistry::commit_rotation` is
  implemented and unit-tested, just not wired to an RPC or exercised
  live.
* Hardware-backed key storage (HSM/TPM) — the private key is a plain
  file on disk, same as every worker signing key in this codebase.
