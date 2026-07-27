# Signed Coordinator Tasks and Worker-Side Replay Protection

**Status: core pipeline implemented, cross-language-parity-tested (real
golden fixture, two real cross-language bugs found and fixed), and
live-validated end to end in a real Docker build.** See
[known-limitations.md](known-limitations.md) for the authoritative
deferred-items list.

## Why this exists

Every prior authenticity slice ([signed-capabilities.md](signed-capabilities.md),
[signed-worker-envelopes.md](signed-worker-envelopes.md),
[signed-client-results.md](signed-client-results.md),
[signed-privacy-records.md](signed-privacy-records.md),
[signing-key-management.md](signing-key-management.md)) verifies messages
flowing **worker → coordinator**. Nothing previously verified the other
direction: `AcquireTask`'s response (`ClientTrainingTask`) was completely
unauthenticated. A network-positioned attacker (or a compromised
load-balancer/proxy) could hand a worker an arbitrary task — different
hyperparameters, a different dataset reference, a different privacy
config, or a stale/replayed task — and the worker had no way to detect
it. This slice closes that gap.

## Design

### Coordinator signing identity

See [coordinator-signing-identity.md](coordinator-signing-identity.md).
A single persistent Ed25519 keypair, deliberately separate from the TLS
server credential. First place the C++ coordinator *signs* rather than
only *verifies*.

### Coordinator signing-key registry

See [coordinator-signing-key-management.md](coordinator-signing-key-management.md).
Mirrors `SigningKeyRegistry`'s design, keyed by `signing_key_id` alone.
Rotation is implemented and unit-tested but not wired to a live RPC or
operational flow this pass.

### `SignedCoordinatorTask`

Rather than duplicating `ClientTrainingTask`'s ~15 existing fields
inside a new wrapper message, `SignedCoordinatorTask` is a small
sibling message carrying only signing metadata, attached to the
existing `ClientTrainingTask` response via a new optional field
(`signed_task`) — the same additive pattern already used for
`RegisterWorkerRequest.signed_capability` and
`SubmitClientResultRequest.envelope`. `task_payload_hash` binds it to
`ClientTrainingTask`'s sibling fields, so nothing needs to be
duplicated to be covered by the signature. Domain-separation prefix:
`FL_PLATFORM_COORDINATOR_TASK_V1\x00`.

Fields: `schema_version`, `coordinator_signing_key_id`, `worker_id`,
`task_id`, `lease_id`, `attempt`, `issued_at`, `expires_at`, `nonce`,
`sequence_number`, five configuration hashes (see
[task-configuration-hashes.md](task-configuration-hashes.md)),
`task_payload_hash`, `signature`.

### Five configuration hashes and the task payload hash

See [task-configuration-hashes.md](task-configuration-hashes.md) —
including the two real cross-language bugs (a float-formatting
threshold mismatch and a JSON key-ordering bug) found and fixed only
once both sides were actually run against the same fixed input inside
a real Docker build.

### Coordinator task sequence store

`CoordinatorTaskSequenceStore` (C++): a plain persisted monotonic
counter per `(coordinator_signing_key_id, worker_id)` — mirrors
Python's `SequenceStateStore` (a "what's the next value I should hand
out" generator), not `ReplayProtectionStore` (a "validate someone
else's claimed value" store), since the coordinator is the one
*issuing* this stream.

### `AcquireTask` wiring

Two pre-existing gaps were fixed as a direct, narrowly-scoped
prerequisite for meaningful signing (not a broader rewrite):
`response.lease_expires_at` and a new `response.attempt` are now
actually populated from `DispatchedTask::lease_expires_at_unix_s`/
`attempt` — previously left at their zero default, since nothing
consumed them before, so nothing signed them either. When
`coordinator_signing_identity_ != nullptr` (optional, same
backward-compatible pattern as every other enforcement point this
session), the five config hashes and the payload hash are computed
from the now-fully-populated response, a real OS-CSPRNG nonce
(`fl::core::OsEntropySecureRandomProvider`) and sequence number are
issued, and the result is signed and attached as `response.signed_task`.
A coordinator configured with a signing identity but no ACTIVE
registry key fails the request (`FAILED_PRECONDITION`) rather than
silently issuing an unsigned task.

### Trusted coordinator key bundle

See [coordinator-signing-key-management.md](coordinator-signing-key-management.md)'s
"Trusted coordinator key bundle" section. Workers load this file
directly from disk, never via RPC.

### Python verification pipeline

`GrpcCoordinatorClient.acquire_task` recomputes all six hashes from the
received wire fields (using `fl_platform.security.coordinator_task_signing`,
ported byte-for-byte from the C++ implementation, proven via a real
golden fixture), verifies the Ed25519 signature (PyNaCl) against the
resolved trusted key, checks expiry/future-issuance/worker-binding via
`fl_platform.security.coordinator_task_verifier.verify_coordinator_task`,
then checks nonce/sequence replay via `CoordinatorTaskReplayStore` (see
[coordinator-task-replay-protection.md](coordinator-task-replay-protection.md)),
then records `ACCEPTED` in the `AcceptedTaskJournal` (see
[accepted-task-journal.md](accepted-task-journal.md)) — catching
`DuplicateTaskExecutionError` and folding it into the same structured
rejection surface. Any failure raises `CoordinatorTaskRejectedError`
with one of 16 structured `CoordinatorTaskRejectionReason` values
**before** the caller (`WorkerService.run`) ever builds a model,
touches the dataset, or initializes Opacus/CUDA — verification happens
entirely inside `acquire_task`, before it returns a task to the caller
at all. A coordinator that sends a `signed_task` to a worker with no
`trusted_coordinator_keys_path` configured is treated as a hard
misconfiguration (rejected), not silently accepted unsigned.

### 16 structured rejection reasons (Work Package Q)

`UNKNOWN_SIGNING_KEY`, `REVOKED_SIGNING_KEY`, `EXPIRED_SIGNING_KEY`,
`INVALID_SIGNATURE`, `PAYLOAD_HASH_MISMATCH`,
`TRAINING_CONFIG_HASH_MISMATCH`, `MODEL_CONFIG_HASH_MISMATCH`,
`DATASET_PARTITION_HASH_MISMATCH`, `PRIVACY_CONFIG_HASH_MISMATCH`,
`PERSONALIZATION_CONFIG_HASH_MISMATCH`, `TASK_EXPIRED`,
`TASK_ISSUED_IN_FUTURE`, `WRONG_WORKER`, `DUPLICATE_NONCE`,
`DUPLICATE_OR_LOWER_SEQUENCE`, `DUPLICATE_TASK_EXECUTION`.

### Accepted-task journal, crash recovery, and reissue semantics

See [accepted-task-journal.md](accepted-task-journal.md) and
[task-reissue-semantics.md](task-reissue-semantics.md).

## Formal tests

* **C++** (`fl_coordinator_tests`, builds/runs on Windows/MSVC without
  gRPC): `coordinator_signing_key_registry_test.cpp`,
  `coordinator_task_sequence_store_test.cpp`.
* **C++** (`fl_coordinator_task_signing_tests`, gRPC-gated, Docker/CI
  only): real Ed25519 keygen/persist/reload/sign, all six hashes'
  determinism and tamper detection, the golden fixture, full sign/verify
  round trip (valid, tampered, wrong key).
* **C++** (`fl_coordinator_grpc_tests`): unchanged, still passes —
  confirms the optional/backward-compatible wiring did not disturb any
  existing coordinator-service behavior.
* **Python**: `test_coordinator_task_signing.py` (33 tests: six hash
  functions' determinism/tamper-detection/NaN-rejection, the golden
  fixture, four signature round-trip tests),
  `test_coordinator_task_replay.py` (8 tests), `test_task_journal.py`
  (11 tests), `test_coordinator_trust_bundle.py` (5 tests),
  `test_coordinator_task_verifier.py` (17 tests: the full pipeline,
  every one of the 16 rejection reasons individually triggered with a
  real Ed25519-signed fixture).

## Cross-language golden fixtures

See [task-configuration-hashes.md](task-configuration-hashes.md)'s "How
cross-language parity was actually proven" section.

## Live Docker validation

Real build: `mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`,
`apt-get install protobuf-compiler protobuf-compiler-grpc
libprotobuf-dev libgrpc++-dev`, `bash scripts/generate_protos.sh
generated` (real protoc + `grpc_cpp_plugin`, not a stub), full
`cmake --build` of every target including `fl_coordinator_grpc_server`,
`fl_coordinator_grpc_tests`, and the new
`fl_coordinator_task_signing_tests`. `ctest` result: **12/12 test
suites pass**, zero regressions.

Live scenarios exercised against a real coordinator process (real
mTLS, `FL_COORDINATOR_SIGNING_KEY_PATH` set) and a real
`GrpcCoordinatorClient`:

1. The coordinator generated a real signing identity on first boot and
   wrote a real trusted-key-bundle file; the worker loaded that exact
   file (never an RPC) and confirmed exactly one ACTIVE key.
2. A real `AcquireTask` call returned a genuinely signed task; the
   worker's full verification pipeline (hash recomputation, signature
   check, expiry check, replay-store commit) accepted it, and the
   accepted-task journal recorded `attempt=1`.
3. The lease was allowed to expire without submission
   (`task_lease_seconds=3`); a second live `AcquireTask` call returned
   the *same* `task_id` at `attempt=2` with a structurally distinct
   signature/nonce/sequence_number — a real reissue, not simulated.
4. A `CoordinatorTaskReplayCandidate` reusing the already-committed
   sequence number for that signing key was independently confirmed
   rejected by the replay store.
5. Marking the attempt-2 journal entry `RESULT_SUBMITTED`/`COMPLETED`
   and then calling `record_accepted` again at the same attempt raised
   `DuplicateTaskExecutionError` for real.
6. A third live task's journal entry was transitioned to `TRAINING`,
   then a **genuinely separate** `AcceptedTaskJournal` Python object was
   constructed against the same on-disk file (simulating a crash) —
   its `recover_on_startup` correctly reported that task recovered
   (`FAILED`).
7. A fabricated `SignedCoordinatorTask` with an all-zero signature was
   confirmed rejected by `verify_coordinator_task_signature` against
   the real trusted public key.
8. `GetCoordinatorSigningKeys` (admin RPC), called with a real
   `go-api` service certificate over live mTLS, returned the
   coordinator's real signing key (status `active`, correct
   fingerprint).
9. The same RPC, called with a real **worker** certificate instead,
   was rejected with `PERMISSION_DENIED` — confirming
   `reject_if_not_go_api_service_identity` actually gates this RPC
   over a live connection, not just in a unit test.

Result: **10/10** checks passed in the primary end-to-end script, plus
2/2 in a follow-up admin-RPC/access-control script — 12/12 total, zero
failures. The coordinator's own structured log independently confirms
the reissue: three `TASK_ASSIGNED` events for the identical `task_id`
(`task-1`), four seconds apart, matching the real lease-expiry timing.

## Scope decision for this slice

Given the size of the full specification (26 lettered work packages),
this slice delivers the **complete, real, live-validated core
pipeline** end to end — coordinator signing identity through
worker-side replay protection and crash recovery — and explicitly
defers:

* Live-tested coordinator signing-key **rotation** as an operational
  flow (the registry's `rotate()`/`commit_rotation()` is implemented
  and unit-tested; no gRPC rotation RPC is wired, no live rotation
  scenario is Docker-validated) — see
  [coordinator-signing-key-management.md](coordinator-signing-key-management.md).
* Go HTTP security administration APIs, the web Security Center,
  full Prometheus security metrics, durable enterprise audit
  persistence — unchanged from every prior slice's deferral list.
* The full 39-scenario Docker Compose validation matrix — this slice
  used a real, direct `docker run`/`cmake --build`/live-mTLS validation
  covering the core signed-task lifecycle instead (see above), matching
  the established convention for security-focused slices in this
  project (`docs/docker-runtime.md`'s "Secure Transport and Worker
  Identity Hardening slice" section used the identical approach).
* Everything already permanently out of scope for this engineering
  category: secure aggregation protocol execution, pairwise masking,
  threshold secret sharing, dropout recovery, homomorphic encryption,
  Byzantine robustness, remote attestation/TEE/TPM.

Not attempted: rewriting mutual TLS, worker key lifecycle, signed
capabilities, signed heartbeats, signed results, or signed privacy
records — none had a demonstrated defect.

## Do not claim

Secure aggregation is not complete and was not touched by this slice.
No custom threshold secret sharing was implemented.
