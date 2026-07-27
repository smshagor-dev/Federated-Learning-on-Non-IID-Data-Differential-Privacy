# Federated Learning Super System — Master Plan

**Repository:** `smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy`  
**Document:** `plan.md`  
**Last updated:** July 26, 2026  
**Current category:** Secure Aggregation and Cryptographic Protocols  
**Repository state:** Large uncommitted working tree; nothing should be committed or pushed without explicit approval.

---

## 1. Executive Summary

এই projectটি একটি Python-only federated learning research prototype থেকে ধাপে ধাপে multi-language, production-shaped federated learning platform-এ রূপান্তরিত হয়েছে।

বর্তমান architecture:

```text
Next.js Web Dashboard
        |
        | REST + SSE
        v
Go Control Plane
        |
        | gRPC + mTLS
        v
C++ Federated Coordinator
        |
        | gRPC + mTLS
        v
Python PyTorch Workers
```

বর্তমানে systemটি research-grade distributed federated learning, privacy experimentation, personalization, live monitoring, authenticated transport এবং signed worker capability verification করতে সক্ষম।

তবে systemটি এখনো production-ready নয়, কারণ complete secure aggregation protocol, message-wide authenticity enforcement, replay protection, key lifecycle, durable enterprise persistence, large-scale distributed execution, high availability এবং independent security/privacy review এখনো বাকি।

---

## 2. Canonical Engineering Categories

Project-wide naming সব জায়গায় responsibility-based হবে। Numbered roadmap terminology ব্যবহার করা হবে না।

Canonical categories:

1. Foundation
2. Aggregation Core
3. Coordinator Runtime
4. Algorithm Expansion
5. Privacy Engineering
6. Secure Aggregation and Cryptographic Protocols
7. Distributed Execution
8. Enterprise Platform
9. Observability and Operations
10. Production Hardening

---

## 3. Overall Status

| Engineering Category | Status | Summary |
|---|---|---|
| Foundation | Complete | Monorepo, legacy preservation, contracts, tooling, build foundations |
| Aggregation Core | Complete | C++ aggregation, FedOpt, validation, checkpointing, Python parity |
| Coordinator Runtime | Complete | Live C++ coordinator, Python worker, Go integration, events, Docker runtime |
| Algorithm Expansion | Complete | FedSAM, Ditto, Per-FedAvg, personalization, registries, fairness analytics |
| Privacy Engineering | Complete | Opacus, user-level DP, adaptive clipping, hybrid mode, privacy ledger |
| Secure Aggregation and Cryptographic Protocols | In progress | mTLS (now extended to the Python worker too), secure randomness, PKI, identity binding, signed capabilities, signed heartbeat/replay protection, signed client results (now with local worker-side accepted/rejected event emission), worker lifecycle enforcement, signed sample-level privacy records with accountant monotonicity, full signing-key lifecycle, signed coordinator tasks with worker-side replay protection/crash recovery, live coordinator signing-key rotation/revocation, a real Go security HTTP API (13 endpoints, now with CORS support for real browser access) with a `security.*` permission model, a shared cross-language security-event schema with durable event/audit journals, a full Web Security Center (6 routes, browser-tested), real worker-security-event centralization end to end, a modular 94-scenario live runtime-validation harness, a Grafana dashboard, CI runtime-validation gates, a release-evidence bundle generator, and now a real, live-validated secure cohort handshake (coordinator-signed frozen roster, worker key advertisement/verification, three-worker Docker validation, 7/7 checks passing), masked model-update submission and no-dropout secure aggregate finalization (15/15 checks passing), secure user-level DP under secure aggregation with full operations/observability (22/22 then 12/12 checks passing), and now secure hybrid DP under secure aggregation composing both sample-level and user-level DP with separately-accounted epsilons (38/38 checks passing) all complete; a small set of deliberately-deferred live adversarial/tampering scenarios (unit-tested only), threshold secret sharing, and dropout recovery remain |
| Distributed Execution | Not started | Ray/Flower, multi-node scheduling, straggler handling, async modes |
| Enterprise Platform | Not started | PostgreSQL, Redis, MinIO/S3, durable services, enterprise auth |
| Observability and Operations | Partial foundation | Prometheus and structured logs exist; full operations stack remains |
| Production Hardening | Not started | HA, disaster recovery, penetration test, cryptographic review, release process |

---

# 4. Completed Engineering Work

## 4.1 Foundation — Complete

### Architecture and repository

Completed:

- Multi-language monorepo structure
- C++ workspace with CMake
- Python package with `pyproject.toml`
- Go module and control-plane skeleton
- Next.js App Router dashboard
- Protobuf contracts
- Docker Compose foundation
- Kubernetes baseline manifests
- Legacy Python research application preserved
- Repository terminology policy
- Automated terminology checker
- Documentation naming converted to category-based format

### Legacy preservation

The original research system remains available, including:

- FedAvg
- FedProx
- SCAFFOLD
- CIFAR-10
- MNIST
- Dirichlet partitioning
- Pathological partitioning
- Legacy client-level clipping and noise
- RDP accountant
- CSV logging
- Plot generation
- Tkinter research dashboard

### Tooling and validation

Completed:

- pytest
- Ruff
- mypy
- Go test and vet
- Next.js lint, typecheck, test and build
- C++ Debug and Release builds
- CTest
- Protobuf generation scripts
- Protobuf freshness checking
- CI foundation
- Docker Compose validation

---

## 4.2 Aggregation Core — Complete

### C++ aggregation architecture

Implemented:

- `Aggregator`
- `AggregatorRegistry`
- `ServerOptimizer`
- `WeightingStrategy`
- `UpdateValidator`
- `TensorCollection`
- `ModelManifest`
- `ModelSnapshot`
- `ClientUpdate`
- `AggregatedUpdate`
- `OptimizerState`
- `AggregationContext`

### Supported aggregation algorithms

Implemented and tested:

- FedAvg
- FedProx server aggregation
- SCAFFOLD
- FedAdagrad
- FedAdam
- FedYogi

### Weighting strategies

Implemented:

- Uniform weighting
- Sample-count weighting
- Capped weighting
- Normalized bounded weighting

### Validation

The C++ core rejects:

- Empty cohorts
- Duplicate client IDs
- Duplicate update IDs
- Duplicate nonces
- Stale model versions
- Future model versions
- Wrong run or round
- Missing tensors
- Unexpected tensors
- Shape mismatch
- Dtype mismatch
- Invalid byte lengths
- Invalid checksums
- NaN
- Infinity
- Zero or negative sample counts
- Invalid contribution caps

### Checkpointing

Implemented:

- Aggregator state persistence
- FedOpt first moments
- FedOpt second moments
- Optimizer steps
- SCAFFOLD global control variate
- Weighting metadata
- Schema versioning
- Checksums
- Atomic write
- Corruption detection
- Truncation rejection

### Python-to-C++ parity

Validated:

- FedAvg equal and unequal sample weights
- Uniform weighting
- Capped weighting
- FedProx
- SCAFFOLD
- FedAdagrad
- FedAdam
- FedYogi
- Invalid-update rejection

---

## 4.3 Coordinator Runtime — Complete

### Live distributed runtime

Implemented:

- C++ gRPC coordinator
- Python PyTorch worker
- Go coordinator client
- Go HTTP control APIs
- SSE event forwarding
- Next.js live run view
- Worker registration
- Worker heartbeat
- Pull-based task acquisition
- Task leases
- Task retries
- Worker disconnect handling
- Synchronous communication rounds
- Model-version progression
- Duplicate-result rejection
- Stale-result rejection
- Run pause
- Run resume
- Run cancel
- Coordinator checkpoint and recovery

### Live algorithms

Validated through the distributed path:

- FedAvg
- FedProx
- SCAFFOLD

### SCAFFOLD state

Implemented:

- Global control-variate state
- Per-client control-variate persistence
- Filesystem-backed client algorithm state
- Version checks
- Checksum checks
- Corruption rejection
- Restart persistence

### Events and metrics

Implemented:

- Coordinator events
- SSE forwarding
- Structured logs
- Prometheus `/metrics`
- Run status metrics
- Worker metrics
- Task metrics
- Aggregation metrics

### Docker runtime

Validated:

- C++ coordinator
- Python worker
- Go API
- Next.js web
- Prometheus
- Supporting services
- Full run lifecycle
- Clean teardown

---

## 4.4 Algorithm Expansion — Complete

### FedSAM

Implemented:

- Two-pass SAM local training
- Gradient norm calculation
- Parameter perturbation
- Second forward/backward pass
- Parameter restoration
- SGD base optimizer
- Momentum
- Weight decay
- Configurable `rho`
- Adaptive SAM option
- Non-finite handling
- Cancellation
- Deadline handling

### Ditto

Implemented:

- Global training model
- Persistent personalized model
- Cold start
- Warm start
- Global update generation
- Personalized objective
- Regularization toward global model
- Separate global and personalized evaluation
- Personalized checkpoint persistence

### Per-FedAvg

Implemented:

- Deterministic support/query split
- Inner adaptation
- First-order meta update
- Post-adaptation evaluation
- Small-client fallback
- Adaptation metrics

### Personalization architecture

Implemented:

- Shared backbone
- Local personalization head
- Full personalized model
- Aggregation manifests
- Shared-parameter-only aggregation
- Local-head rejection
- Personalized checkpoint store
- Cache and retention controls

### Registries

Implemented:

- Model registry
- Dataset registry
- Dataset partition metadata
- IID partition metadata
- Dirichlet partition metadata
- Pathological partition metadata
- Model status lifecycle
- Dataset status lifecycle

### Evaluation and fairness

Implemented:

- Global accuracy
- Personalized mean accuracy
- Personalized median accuracy
- P10 and P90 accuracy
- Worst-client accuracy
- Best-client accuracy
- Fairness gap
- Standard deviation
- Coefficient of variation
- Jain fairness index
- Fraction of clients improved
- Per-client improvement

### Go and web

Implemented:

- Algorithm metadata APIs
- Model registry APIs
- Dataset registry APIs
- Personalization APIs
- Fairness APIs
- Experiment-builder algorithm configuration
- Model registry page
- Dataset registry page
- Personalized run dashboard
- Algorithm comparison views

---

## 4.5 Privacy Engineering — Complete

### Sample-level differential privacy

Implemented with Opacus:

- Real private training
- Per-sample gradients
- Gradient clipping
- Gaussian noise
- Configurable accountant selection
- RDP support
- PRV support where available
- Model validation
- Explicit module fixup
- Secure-random capability gating
- Worker-side privacy ledger
- Epsilon reporting
- Delta reporting
- Checkpoint and restore

### Sample privacy budget enforcement

Implemented policies:

- `WARN_ONLY`
- `STOP_BEFORE_EXCEEDING`
- `STOP_AFTER_CURRENT_TASK`
- `FAIL_TASK`

Implemented:

- Pre-check
- Post-step check
- Budget decision
- Structured task failure
- Accountant checkpoint
- Restore without double counting

### User-level differential privacy

Implemented in C++:

- Global multi-tensor update norm
- Client-update clipping
- Uniform weighting
- Capped weighting
- Bounded weighting
- Central Gaussian noise
- Dedicated user-level accountant
- Separate user epsilon and delta
- Checkpoint and recovery

### Adaptive clipping

Implemented:

- Dynamic clipping bound
- Target quantile
- Noisy clipped-count estimate
- Bound increase
- Bound decrease
- Minimum bound
- Maximum bound
- Maximum per-round change
- Separate clipping accountant
- Checkpoint and restore

### Hybrid privacy

Implemented:

```text
Sample-level DP inside Python worker
+
User-level DP inside C++ coordinator
```

Guarantees remain separate:

- Sample epsilon and delta
- User epsilon and delta
- Adaptive-clipping epsilon and delta

No combined epsilon is produced.

### Privacy ledger

Implemented:

- Sample-level entries
- User-level entries
- Adaptive-clipping entries
- Configuration hashes
- Accountant-state hashes
- Budget warnings
- Exhaustion states
- Go privacy APIs
- Web Privacy Center

### Privacy Center

Implemented:

- Sample privacy card
- User privacy card
- Adaptive-clipping card
- Separate privacy charts
- Budget warning
- Budget exhaustion
- Trusted-coordinator warning
- Secure-aggregation-unavailable warning

### Important remaining trust limitations

Even though Privacy Engineering is complete:

- User-level DP without secure aggregation assumes a trusted coordinator.
- Coordinator can see individual client updates before clip and noise.
- Worker-reported sample epsilon is authenticated only when signed-message integration is enabled.
- Signed worker claims do not prove honest training.
- Personalized client models require separate protection.
- Sample-level and user-level guarantees protect different neighboring relations.

---

# 5. Secure Aggregation and Cryptographic Protocols — Current Progress

This category is in progress.

## 5.1 Completed: Security and Cryptographic Design

Completed:

- Secure aggregation architecture decision record
- Secure aggregation threat model
- Cryptographic dependency assessment
- Mandatory trust statement
- Attack disposition table
- Security assumptions documentation
- Threshold secret-sharing dependency investigation

### Important blocker

No adequately vetted threshold secret-sharing dependency has been selected for the current C++ and Python stack.

Rules:

- Do not write custom threshold secret sharing.
- Do not implement custom Shamir reconstruction.
- Do not use floating-point secret-sharing arithmetic.
- Do not claim dropout recovery until a vetted implementation is selected and validated.

## 5.2 Completed: Secure Randomness

Implemented:

- C++ `SecureRandomProvider`
- OS-backed CSPRNG
- Windows `BCryptGenRandom`
- Linux `/dev/urandom`
- Hard failure on entropy-source failure
- Deterministic provider for tests
- Secure provider metadata
- Live `CryptoSecureNoiseProvider`
- User-level DP noise integration
- Adaptive-clipping noise integration
- Python secure-random capability detection
- Opacus `secure_mode=True` gating

Validated:

- Real C++ Debug and Release tests
- Real Python private training
- Secure-random unavailable rejection
- Runtime benchmarks

Current design note:

- Runtime noise currently uses buffered real entropy.
- A vetted stream-generator integration may still be needed for larger workloads.
- Random values are never logged.

## 5.3 Completed: Development PKI

Implemented and validated:

- Development CA generation
- Coordinator certificate issuance
- Go API certificate issuance
- Worker certificate issuance
- Bash scripts
- PowerShell scripts
- Certificate inspection
- Certificate revocation
- CRL regeneration
- URI SAN identities
- Temporary PKI verification
- Git ignore rules for private material

Identity convention:

```text
spiffe://federated-platform/service/coordinator
spiffe://federated-platform/service/go-api
spiffe://federated-platform/worker/{worker-id}
```

This is a SPIFFE-style identity convention, not full SPIFFE/SPIRE integration.

## 5.4 Completed: Mutual TLS

Implemented and validated:

- C++ coordinator mTLS runtime
- Go-to-C++ real mTLS communication
- Python-to-C++ real mTLS communication
- Go local mTLS handshake tests
- Python real secured gRPC Health RPC
- C++ runtime verification inside Docker
- Explicit insecure development mode
- Production mTLS configuration
- Coordinator certificate validation
- Worker certificate validation
- Go service certificate validation

## 5.5 Completed: Certificate Identity Binding

Implemented:

- Peer certificate extraction
- URI SAN extraction
- Certificate-to-service binding
- Certificate-to-worker binding
- Wrong-service rejection
- Wrong-worker rejection
- Cross-identity rejection
- Certificate fingerprint handling

Validated through real accept and reject scenarios.

## 5.6 Completed: Persistent Worker Identity Registry

Implemented:

- Filesystem-backed C++ registry
- Atomic persistence
- Worker ID uniqueness
- Certificate fingerprint uniqueness
- Signing-key binding
- Status machine
- Restart persistence
- Corruption checks

Worker states:

```text
PENDING
ACTIVE
SUSPENDED
REVOKED
EXPIRED
```

Current limitation (updated):

- Status persistence exists.
- Suspension and revocation are now enforced at `RegisterWorker`, `Heartbeat`,
  and `AcquireTask`, and revocation additionally cancels every active lease
  across every run (see 5.8 below).
- `SubmitClientResult` and `ReportTaskProgress` do not independently check
  worker status — a revoked worker's practical ability to submit is closed
  by lease cancellation rather than by a redundant status check in those two
  handlers specifically. This is a known, documented gap, not an oversight.

## 5.7 Completed: Signing Identity and Signed Capabilities

Implemented:

- Python Ed25519 worker signing identity
- PyNaCl integration
- Signing public key registration
- Signing key ID
- Canonical JSON serialization
- C++ canonical serialization
- Python/C++ byte parity
- C++ Ed25519 signature verification
- Live signed capabilities in `RegisterWorker`
- Expiry validation
- Worker ID validation
- Certificate identity binding
- Tamper detection
- Key mismatch detection
- Idempotent capability refresh
- Multiple worker registration

Validated through live mTLS and Docker.

Important limitation:

- Signed capabilities authenticate which worker made the claim.
- They do not prove that the worker actually runs the claimed code.
- Hardware or software attestation is not implemented.

## 5.8 Completed: Signed Client Results and Worker Lifecycle Enforcement

Implemented:

- `SignedWorkerEnvelope` extended to `SubmitClientResult` (`CLIENT_RESULT`
  message type and replay/sequence stream)
- Real per-tensor SHA-256 checksums (previously always empty on the wire)
- Canonical, cross-language-proven Client Result Hash
- `ReplayProtectionStore` reused unchanged for the `CLIENT_RESULT` stream
- Certificate identity binding added to `AcquireTask` and `SubmitClientResult`
  (neither had any before)
- Worker status enforcement at `AcquireTask` (`SUSPENDED`/`REVOKED` rejected)
- Five new `ADMIN_CONTROL` RPCs: `GetWorkerIdentity`, `ListWorkerIdentities`,
  `SuspendWorker`, `ActivateWorker`, `RevokeWorker` — gated on the go-api
  service certificate identity
- Cross-run active lease cancellation on revocation
- `GetRound`/`GetModelManifest` resolved to explicit `UNIMPLEMENTED`
- `ReportTaskProgress` given a real handler (previously declared but unimplemented)
- Python `GrpcCoordinatorClient` now signs real capability statements and
  client results when a signing identity is configured (previously the
  production client sent both unsigned)
- Fail-closed default for the live coordinator: unsigned client results are
  rejected unless `FL_ALLOW_UNSIGNED_CLIENT_RESULTS=true` is explicitly set

Validated through a 22-scenario live test against a real containerized
coordinator with real mTLS, using the actual production
`GrpcCoordinatorClient` class (not a hand-rolled RPC harness): dual-worker
registration, task acquisition, signed-result submission, real aggregation,
duplicate-resubmission rejection, wrong-signing-key rejection, and the full
suspend → blocked → activate → can-acquire → revoke → lease-canceled →
admin-RPCs-rejected lifecycle.

Current limitations (stated honestly; sample-level privacy record
signing is now closed — see 5.9 below):

- `SubmitClientResult`/`ReportTaskProgress` do not directly check
  `SUSPENDED`/`REVOKED` status (see 5.6 above).
- No signing-key rotation, no coordinator-signed tasks, no Go/web security
  administration surfaces, no Prometheus metrics for this slice's events
  (structured stderr logs only), no formal audit-record persistence.
- Full 30-scenario Docker Compose validation was not run; validation used
  direct `docker run` scenarios instead.

See [message-authenticity-report.md](docs/message-authenticity-report.md)
and [signed-client-results.md](docs/signed-client-results.md) for full detail.

## 5.9 Completed (privacy-record authenticity only): Privacy Record Authenticity, Signing-Key Lifecycle, and Coordinator-Signed Tasks

Despite the slice name (inherited from the parent specification), only
the privacy-record-authenticity portion was actually delivered this
pass — signing-key lifecycle and coordinator-signed tasks are explicitly
deferred, stated honestly below rather than claimed.

Implemented:

- Independently signed `SignedSamplePrivacyRecord` (`SAMPLE_PRIVACY_RECORD`
  message type, `PRIVACY_RECORD` stream) — reuses the existing
  `SignedWorkerEnvelope`/`ReplayProtectionStore` machinery rather than a
  second signature mechanism
- Canonical, cross-language-golden-fixture-proven Sample Privacy Record Hash
  (27 fields; a real, reviewed fixture independently generated on both
  sides, not a tautological self-check)
- A binding check tying the signed record to the plaintext
  `SampleLevelLedgerEntry` submitted alongside it (run/round/client/worker/
  task/epsilon/delta/noise_multiplier/sample_rate/accountant_step/
  accountant_type must all match)
- A second, independent binding: the outer Client Result Hash now also
  binds to the privacy record envelope's own `payload_hash`
- A new, persistent `AccountantMonotonicityStore` enforcing accountant-step
  and epsilon monotonicity per `(run, client, worker, accountant_type)`
  track, with an explicit (not yet RPC-exposed) reset method
- Budget-decision-consistency enforcement: a normal update alongside a
  `stopped_before_step`/`refused_before_training`/`failed_task` decision
  is rejected; `stopped_after_task` is correctly allowed
- Fail-closed default for the live coordinator: unsigned privacy records
  are rejected unless `FL_ALLOW_UNSIGNED_PRIVACY_RECORDS=true` is set
- Formal Python `pytest` coverage for client-result and privacy-record
  signing (`python/tests/test_signed_envelope.py`, 27 tests) — closes the
  prior slice's disclosed "no formal pytest test files" gap for these two
  message types

Validated through a 21-scenario live test against a real containerized
coordinator with real mTLS, using the actual production
`GrpcCoordinatorClient` class: a real signed privacy record accepted
alongside real (synthetic) training data with real aggregation;
monotonicity accept (higher step, non-decreasing epsilon) and reject
(non-increasing step; lower epsilon at a higher step), each with a
precise explanatory message; budget-decision contradiction reject and
policy-compliant accept; signed-record-vs-plaintext-ledger-entry binding
mismatch reject; missing-signed-privacy-record fail-closed reject.

Current limitations (updated — signing-key lifecycle is now closed,
see 5.10 below):

- Coordinator-signed tasks are entirely unimplemented — no coordinator
  signing identity, no signed-task contract, no worker-side verification
  or task replay store. `AcquireTask` still returns a fully unsigned task.
- `configuration_hash` is checked for consistency within a track's own
  history but not independently recomputed against the coordinator's own
  assigned privacy config for that round.
- `AcquireTask` does not consult budget-decision history to block future
  task assignment after a real `stopped_after_task` exhaustion.
- No RPC exposes `AccountantMonotonicityStore::reset()`.
- No security events/metrics/audit records for this slice's rejection
  paths beyond structured gRPC error messages.

See [message-authenticity-report.md](docs/message-authenticity-report.md),
[signed-privacy-records.md](docs/signed-privacy-records.md), and
[privacy-accountant-monotonicity.md](docs/privacy-accountant-monotonicity.md)
for full detail.

## 5.10 Completed: Signing-Key Lifecycle

Implemented:

- Persistent, multi-key-per-worker `SigningKeyRegistry`
  (`PENDING`/`ACTIVE`/`GRACE_PERIOD`/`REVOKED`/`EXPIRED`), separate from
  `WorkerIdentityRegistry`, restart-safe, corruption-detecting
- Idempotent legacy migration from `WorkerIdentityRegistry`'s existing
  single-key data, exercised via a real coordinator restart
- Signed `WorkerKeyRotationRequest` contract, reusing
  `SignedWorkerEnvelope`/`ReplayProtectionStore` via a new `KEY_MANAGEMENT`
  stream
- Real grace-period acceptance and real, elapsed-time expiry (not
  simulated)
- Immediate signing-key revocation with automatic worker suspension
  when the revoked key was the worker's only valid one
- A single shared enforcement point (`resolve_signing_key`) across
  capability statements, heartbeats, client results, and privacy
  records
- `AcquireTask` blocks a worker with no valid signing key at all
- Three new RPCs: `RotateWorkerSigningKey` (`SIGNED_WORKER_MESSAGE`),
  `GetWorkerSigningKeys`/`RevokeWorkerSigningKey` (`ADMIN_CONTROL`)
- Cross-language golden fixture for the rotation-request payload hash
- Python `signing_key_rotation.py` module: keyed-by-(worker,key)
  private-key persistence, local rotation-state dataclass, both
  unit-tested
- `GrpcCoordinatorClient.rotate_signing_key()` wired into the real
  production client class

Validated through a 16-scenario live test plus a separately-verified
legacy migration, against a real containerized coordinator with real
mTLS, using the actual production `GrpcCoordinatorClient` class:
registration populates the registry; a real signed rotation is
accepted (new key ACTIVE, old key GRACE_PERIOD); messages signed with
either key are accepted during the grace window; after the grace
period genuinely elapses, the old key is rejected; admin revocation of
a worker's sole valid key suspends it and blocks further task
acquisition; and — separately — killing the coordinator, deleting only
the signing-key registry file, and restarting produces a real
`SIGNING_KEY_MIGRATED` event and a persisted, migrated `ACTIVE` entry.

Current limitations (stated honestly):

- Coordinator-signed tasks remain entirely unimplemented (see above) —
  a separate, equally large feature.
- No signing-key-specific security events/metrics/audit records beyond
  structured stderr logging.
- `rotate_signing_key()` does not yet persist its own rotation state to
  disk across a worker process restart (tracked only in memory for the
  client object's lifetime).
- No default rotation interval, minimum key lifetime, or automated
  background expiry sweep (expiry is still correctly enforced lazily
  at verification time regardless).
- Old private-key file cleanup after grace-period expiry is not
  automated.
- Only direct `docker run` scenarios were live-validated, not the full
  33-scenario Docker Compose flow (which also requires coordinator-signed
  tasks and Go/web verification, neither of which exist).
- No performance benchmarking was performed.

See [signing-key-management.md](docs/signing-key-management.md),
[signing-key-migration.md](docs/signing-key-migration.md),
[key-rotation.md](docs/key-rotation.md),
[signing-key-grace-period.md](docs/signing-key-grace-period.md), and
[signing-key-revocation.md](docs/signing-key-revocation.md) for full detail.

## 5.11 Completed: Coordinator-Signed Tasks and Worker-Side Replay Protection

Implemented:

- A persistent coordinator Ed25519 signing identity
  (`FL_COORDINATOR_SIGNING_KEY_PATH`), deliberately separate from the
  TLS server credential — the first place the C++ coordinator *signs*
  rather than only *verifies*
- `CoordinatorSigningKeyRegistry`, mirroring `SigningKeyRegistry`'s
  design (`ACTIVE`/`GRACE_PERIOD`/`REVOKED`/`EXPIRED`), keyed by
  `signing_key_id` alone
- `SignedCoordinatorTask` contract, additively attached to the existing
  `ClientTrainingTask` response (`signed_task` field), binding
  `worker_id`/`task_id`/`lease_id`/`attempt`/`issued_at`/`expires_at`/
  `nonce`/`sequence_number` plus five configuration hashes and a task
  payload hash under one Ed25519 signature
- Five configuration hashes (Training/Model/Dataset Partition/Privacy/
  Personalization) each with their own domain-separation prefix, plus
  a task payload hash — scoped to fields `ClientTrainingTask` actually
  carries on the wire
- Two pre-existing wire-mapping gaps fixed as a direct prerequisite for
  signing: `AcquireTask` now actually populates `lease_expires_at` and
  a new `attempt` field (both were previously left at their zero
  default)
- A real OS-CSPRNG nonce (`fl::core::OsEntropySecureRandomProvider`)
  and a `CoordinatorTaskSequenceStore` per `(coordinator_signing_key_id,
  worker_id)` for every signed task
- A trusted-coordinator-key bundle file, written by the coordinator at
  startup/rotation, loaded by workers directly from disk — never via
  RPC
- Full Python-side verification (`fl_platform.security.coordinator_task_verifier`)
  with 16 structured rejection reasons, running entirely inside
  `GrpcCoordinatorClient.acquire_task` before any model/dataset access
- A worker-side `CoordinatorTaskReplayStore` (nonce + strictly-
  increasing sequence, persistent)
- An `AcceptedTaskJournal` (`ACCEPTED`→`PREPARING`→`TRAINING`→
  `RESULT_READY`→`RESULT_SUBMITTED`→`COMPLETED`/`FAILED`/`CANCELED`)
  with real crash recovery (mark in-flight `FAILED`, require reissue —
  no training-state checkpointing exists to safely resume from) and
  duplicate-execution rejection keyed on `(task_id, attempt)`
- `GetCoordinatorSigningKeys` admin RPC (operational visibility only —
  not how a worker bootstraps trust)
- A real cross-language golden fixture that caught two genuine bugs
  during Docker validation: a `std::to_chars` float-formatting
  threshold mismatch (`0.0001` rendered as `"1e-04"` in C++ vs.
  Python's `"0.0001"`) and a JSON key-ordering bug in the hand-written
  C++ privacy-configuration-hash encoder — both fixed and reverified

Validated through a real Docker build
(`mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`, real
`libgrpc++-dev`/`protobuf-compiler-grpc`, real `grpc_cpp_plugin`): all
12 `ctest` suites pass (zero regressions, including the unchanged
`fl_coordinator_grpc_tests`), and a live end-to-end script against a
real running coordinator (real mTLS, real `GrpcCoordinatorClient`)
confirmed 12/12 checks: a real trusted-key bundle with exactly one
active key; a real signed task accepted end to end; a real
lease-expiry-driven reissue keeping the same `task_id` at a higher
`attempt` (confirmed independently via the coordinator's own
`TASK_ASSIGNED` event log); a duplicate/lower sequence number rejected
by the replay store; a duplicate execution at the same attempt
rejected by the journal; a genuinely separate journal instance
recovering an in-flight task as `FAILED` after a simulated crash; a
fabricated signature rejected; the `GetCoordinatorSigningKeys` admin
RPC returning the real key over a real go-api identity; and the same
RPC rejecting a worker identity with `PERMISSION_DENIED`.

Current limitations (stated honestly):

- ~~No gRPC rotation RPC for the coordinator's own signing key, and no
  live-validated rotation scenario~~ — **superseded, see
  [5.12](#512-completed-coordinator-signing-key-rotation-revocation-and-trusted-bundle-lifecycle)**.
- No signed-coordinator-task-specific security events, metrics, or
  audit records beyond existing structured stderr logging.
- Journal entry retention/cleanup and replay-store time-based nonce
  expiry are not implemented (bounded only by a fixed per-track cap).
- `__main__.py`/`configuration.py` were not wired with new env vars for
  the trusted-key-bundle/replay-store/journal paths — the live
  validation constructed `GrpcCoordinatorClient` directly, the same
  scope boundary every prior mTLS/signing slice's `__main__.py` wiring
  has also left unaddressed.
- Only direct `docker run`/live-mTLS scenarios were validated, not the
  full 39-scenario Docker Compose flow.
- No performance benchmarking was performed.

See [signed-coordinator-tasks.md](docs/signed-coordinator-tasks.md),
[coordinator-signing-identity.md](docs/coordinator-signing-identity.md),
[coordinator-signing-key-management.md](docs/coordinator-signing-key-management.md),
[task-configuration-hashes.md](docs/task-configuration-hashes.md),
[coordinator-task-replay-protection.md](docs/coordinator-task-replay-protection.md),
[accepted-task-journal.md](docs/accepted-task-journal.md), and
[task-reissue-semantics.md](docs/task-reissue-semantics.md) for full detail.

## 5.12 Completed: Coordinator Signing-Key Rotation, Revocation, and Trusted-Bundle Lifecycle

Scope note: this slice was deliberately scoped to C++/Python only
(confirmed with the user) — no Go client, no Go HTTP surface, no Web
Security Center, no durable audit journal, no Prometheus metrics, and
no 58-scenario Docker Compose matrix. Each is itemized as deferred
below and in [known-limitations.md](docs/known-limitations.md).

Implemented:

- Live `RotateCoordinatorSigningKey` and `RevokeCoordinatorSigningKey`
  gRPC RPCs, both `ADMIN_CONTROL`-gated (go-api identity only, rejected
  for a worker identity), both idempotent via a new persisted
  `IdempotencyStore` keyed on `(rpc_name, idempotency_key)` — required
  because a retried rotation must return the *same* freshly-generated
  key, not mint a second one
- A real end-to-end rotation flow: real Ed25519 keygen → registry
  validation (including two new rejection reasons, `kInvalidExpiry` and
  `kExcessiveKeyLifetime`, and a new `kMaxCoordinatorKeyLifetimeSeconds`
  cap) → keyed private-key file persisted (`coordinator.{key_id}.signing-key.pem`,
  mirroring the worker-side convention) → atomic trusted-bundle
  regeneration → thread-safe in-memory active-identity swap
  (`CoordinatorActiveIdentityStore`, immutable `shared_ptr` snapshots —
  a task-signing call in flight is unaffected by a concurrent rotation)
- Coordinator restart correctly resumes from whichever key the registry
  says is ACTIVE, not always the genesis key (`main.cpp` now loads the
  real active identity from the keyed directory when it differs from
  genesis)
- A strengthened trusted-key-bundle format (schema version, bundle
  version monotonically incremented on every write, checksum) and a
  new Python-side `TrustedCoordinatorKeyBundleReloader`: rejects a
  corrupted candidate, rejects a rollback (lower `bundle_version`),
  rejects a bundle with more than one ACTIVE key, keeps the previous
  valid bundle on any rejection
- `GrpcCoordinatorClient.acquire_task` now reloads the trusted bundle
  immediately before verifying a task's signature ("reload before
  rejecting an unknown key"), so a coordinator-side rotation is picked
  up on the very next task acquisition without a separate explicit
  reload call
- `fl_coordinator_key_admin_cli`, a standalone, protobuf-free recovery
  tool operating directly on the persisted registry/keyed-key-directory/
  bundle files with no running server required (`show`, `rotate`,
  `revoke`, `regenerate-bundle`) — the specification's explicitly
  accepted alternative to a full recovery API. Deliberately asymmetric
  from the live RPC: the CLI's `rotate` falls back to registering a
  fresh initial key when no ACTIVE key exists (a real recovery
  mechanism for an operator with filesystem access); the RPC does not
  have this fallback and instead rejects, since an unauthenticated-
  precondition RPC silently minting a new trust root would be a much
  larger blast-radius mistake
- A handful of new structured stderr security events:
  `COORDINATOR_KEY_ROTATION_STARTED`, `COORDINATOR_KEY_ROTATION_COMPLETED`,
  `COORDINATOR_KEY_ROTATION_FAILED`, `COORDINATOR_KEY_REVOKED` (carries
  `production_task_issuance_stopped`), `TRUSTED_BUNDLE_GENERATED`,
  `TRUSTED_BUNDLE_GENERATION_FAILED`

Validated through a real Docker build: the full C++ suite (12/12 ctest
targets) and full Python suite (298 passed, 1 skipped) both green
throughout with zero regressions. A live 18-check end-to-end script
against a real running coordinator (real mTLS, real go-api and worker
identities) confirmed: genesis bundle starts at version 1; a real
rotation is accepted and not an idempotent replay; a retried rotation
with the same idempotency key IS a replay and returns the *same* new
key_id; the previous key becomes GRACE_PERIOD; `GetCoordinatorSigningKeys`
lists both; a real task is issued and signed with the new key after a
client-side reload; a post-grace-period reload is accepted (lazy expiry
confirmed with a real elapsed-time wait); a real revocation is applied
and reports `production_task_issuance_stopped=true`; `AcquireTask` then
fails closed (`FAILED_PRECONDITION`); a retried revocation is an
idempotent replay; rotating over the RPC with no ACTIVE key is
correctly rejected (confirming the CLI-only recovery-fallback design);
and an unauthorized worker identity is rejected from
`RotateCoordinatorSigningKey`. Separately, the recovery CLI was
exercised directly for every documented recovery scenario (bootstrap,
rotate-with-grace-period, real elapsed-time expiry, revoke-the-sole-
active-key, and recover-via-fresh-initial-key), each bundle version
independently re-loaded and checksum-verified by a fresh Python process
proving real (non-tautological) cross-language checksum agreement.

Current limitations (stated honestly):

- No Go coordinator security client and no Go security HTTP APIs — the
  two new RPCs (and every pre-existing admin RPC) have no HTTP-callable
  surface yet.
- No Web Security Center — no dashboard, no worker/coordinator-key
  admin UI, no rotation/revocation forms.
- No durable, queryable audit journal — the registry files themselves
  are the durable record; there is no separate append-only audit log.
- No Prometheus metrics for this slice.
- No formal, schema-versioned security-event type beyond the handful of
  new structured stderr log lines listed above — no event bus routing,
  no SSE stream.
- No 58-scenario Docker Compose validation matrix and no automated
  validation harness script — validated via direct `docker run` and a
  hand-written live-test script, consistent with every prior slice's
  Docker-validation scope in this project.
- No bundle self-signature (documented as an intentional scope boundary
  in [trusted-coordinator-key-bundle.md](docs/trusted-coordinator-key-bundle.md),
  not an oversight).
- The lost-active-private-key recovery path does not auto-revoke the
  old (now-unusable) registry entry — an operator must do so explicitly
  if they are certain the old key is gone for good.
- No old-signing-key-file cleanup (same disclosed gap as the worker-key
  rotation slice).
- No performance benchmarking.

See [coordinator-signing-key-rotation.md](docs/coordinator-signing-key-rotation.md),
[coordinator-signing-key-revocation.md](docs/coordinator-signing-key-revocation.md),
[trusted-coordinator-key-bundle.md](docs/trusted-coordinator-key-bundle.md),
[coordinator-key-recovery.md](docs/coordinator-key-recovery.md), and
[security-administration-report.md](docs/security-administration-report.md)
for full detail.

## 5.13 Completed: Security Operations and Administration (Go API + Permissions)

Scope note: this slice was scoped to "Go API + permissions only"
(confirmed with the user over "Go API + minimal Web Security Center").
The Web Security Center, a formal security-event schema, Prometheus
metrics, a durable security-specific audit journal, and security-focused
CI gates are all itemized as deferred below and in
[known-limitations.md](docs/known-limitations.md).

Implemented:

- Two new, real C++ `ADMIN_CONTROL` RPCs, `GetTransportSecurityStatus`
  and `GetSecurityTrustModel` — read-only, aggregate-only (no full
  key/worker listing), reporting the coordinator's own already-resolved
  transport mode and trust-model summary counts
- A full Go `SecurityClient` (embedded into `coordinator.Client`): 12
  typed methods, implemented for real by `GrpcClient` and
  deterministically by `MockClient`, with a dedicated
  `mapSecurityGrpcError` distinguishing `PermissionDenied`/`NotFound`/
  `FailedPrecondition` into their own Go error sentinels
- `go/internal/security`: 14 `security.*` permission constants and a
  real ADMIN/RESEARCHER/VIEWER/SERVICE matrix (`Allows(role, perm)`),
  replacing what would otherwise have been scattered inline role
  checks for this surface — SERVICE deliberately receives zero
  permissions by default (not automatically ADMIN), with the
  documented, honest limitation that no per-user explicit-scope
  plumbing exists yet to grant it anything beyond that default
- 13 real HTTP endpoints under `/api/v1/security/...`
  (transport/trust-model/workers list+detail/suspend/activate/revoke/
  worker signing-keys list+revoke/coordinator signing-keys
  list+rotate+revoke/audit) plus one honest `501` for `/events` (no
  event schema/stream exists yet — a real, permission-checked endpoint
  that says so rather than faking an empty list)
- Role-aware response redaction for worker-identity views (VIEWER gets
  `{worker_id, registration_status}` only) and audit records
  (`security.audit.read_detailed`, ADMIN-only, gates the full actor
  email + free-form details map)
- An `Idempotency-Key`-based mutation-safety mechanism at the HTTP
  layer (in-memory cache, one mutex serializing cached mutations —
  a disclosed correctness-over-throughput trade-off) — coordinator
  signing-key rotation specifically requires an idempotency key (`400`
  without one), since a rotation mints a genuinely fresh key every time
  it executes
- Real audit logging of every security mutation into the *existing*,
  general-purpose Go `AuditRepository` (not a new store), surfaced via
  `GET /api/v1/security/audit`
- The first-ever Docker Compose PKI wiring in this project:
  `infra/compose/docker-compose.security.yml`, an override file that
  mounts real dev-PKI certificates into `coordinator`+`api` and
  switches both to real mTLS — neither compose file had ever mounted
  any PKI material before this slice (confirmed by direct inspection)

Validated through a real Docker Compose mTLS run (`coordinator`+`api`+
`postgres`+`redis`, real dev-PKI certs, `FL_TRANSPORT_MODE=mtls` on
both sides): the full C++ suite (12/12 `ctest` targets, including the
two new RPCs) and the full Go suite (all packages, including 11 new
security-handler tests and additional security-client/permission
tests) both green. A 22-check live walkthrough confirmed: real mTLS
handshake both directions; transport status and trust model over real
mTLS; coordinator signing-key listing/rotation (real Ed25519 keygen,
real grace-period transition)/revocation
(`production_task_issuance_stopped=true`) over real mTLS; a real
Ed25519-signed `RegisterWorker` call (via a scratch script using
`fl_platform.security.signing_identity`/`capability_statement`)
followed by the new worker-admin endpoints seeing and suspending that
real worker; VIEWER redaction and RESEARCHER/VIEWER permission denial
(`403`) confirmed live; `404`/`401` confirmed for an unknown worker and
no bearer token; HTTP-layer idempotent replay confirmed byte-identical
for a retried rotation; a real, correctly-redacted audit trail across
every mutation performed; the events endpoint's honest `501`; and,
independently via a direct gRPC call bypassing Go, a worker identity
rejected with `PERMISSION_DENIED` from the new
`GetTransportSecurityStatus` RPC, confirming the gRPC-layer
`ADMIN_CONTROL` gate still holds beneath the new HTTP permission layer.

A real bug was found and fixed live during this validation:
`docs/mtls.md`'s example `FL_COORDINATOR_SERVER_NAME` value (a SPIFFE
URI) does not work against Go's standard-library `crypto/tls` hostname
verification, which only checks DNS/IP SANs, never URI SANs — the
correct value is a DNS name actually on the cert (`coordinator`). This
had never been caught before because no Compose file had ever
attempted a real mTLS handshake in this project prior to this slice.

Current limitations (stated honestly):

- No Web Security Center (deferred per the confirmed scope decision).
- No formal, schema-versioned security-event type or event stream —
  `/events` is real but returns `501`.
- No Prometheus metrics for this HTTP surface.
- No durable, security-specific audit journal — the existing
  general-purpose Go audit repository is reused, not replaced, and
  only captures Go-mediated mutations.
- SERVICE-role explicit per-user scope grants have no plumbing
  (`HasScope` exists, nothing feeds it from a live request).
- Redaction covers only worker-identity views and audit records —
  worker/coordinator signing-key listings are all-or-nothing.
- The HTTP idempotency cache is in-memory only, lost on restart.
- `python-worker`/`web` are excluded from the new Compose mTLS
  override (python-worker's own TLS env-var wiring gap, disclosed in
  a prior slice, is unaddressed).
- No security-focused CI gates.
- Worker activation/revocation/signing-key-revocation were live-tested
  only for the permission-denial case this pass (their underlying RPCs
  were already live-validated in 5.8; this pass focused Docker time on
  the new Go/HTTP surface).

See [security-api.md](docs/security-api.md),
[security-permission-model.md](docs/security-permission-model.md),
[security-capability-inventory.md](docs/security-capability-inventory.md),
and [security-operations-report.md](docs/security-operations-report.md)
for full detail.

## 5.14 Completed: Security Events, Metrics, and Durable Audit Journal

Implemented:

- A shared, versioned security-event schema (`schema_version = 1`),
  field-for-field mirrored in C++ (`security_event.hpp`/`.cpp`), Python
  (`security/security_event.py`), and Go
  (`internal/observability/security_event.go`) — 55 event types across
  transport/identity/worker-key/signed-message/coordinator-task/
  administration categories, 4 severities, 6 outcomes, 5 actor types, 14
  subject types, all bounded (`safe_details` ≤ 10 keys/256 chars,
  `reason_code` ≤ 128 chars), validated before persistence in all three
  languages
- A real, independently-generated cross-language golden fixture (not a
  tautological self-check) proving byte-identical canonical-JSON
  encoding and FNV-1a checksum agreement across C++, Python, and Go for
  a fixed test event
- Durable, JSON-Lines, rotating (10 MiB default, 5 retained
  generations), corruption-recovering (skip-and-count a malformed line,
  never fail startup) event journals in all three languages, and a
  second, security-specific, paginated/filterable durable audit journal
  (`SecurityAuditJournal`) in C++ and Go — deliberately additive to the
  pre-existing general-purpose Go `AuditRepository`, not a replacement
  for it
- A new coordinator RPC, `ListSecurityEvents` (`ADMIN_CONTROL`, cursor +
  filters), and a matching Go `SecurityClient` method (real + mock)
- `GET /api/v1/security/events` replaced with a real implementation
  (merges Go-local and coordinator-relayed events, role-redacted,
  paginated) — no longer `501`
- `GET /api/v1/security/audit` switched to read from the new
  `SecurityAuditJournal` with real cursor pagination and actor/action/
  resource_type/outcome/time-range filtering
- A new `SECURITY_AUDIT_ACCESSED` meta-audit event, emitted whenever a
  detailed (ADMIN) audit read actually returns records
- Low-cardinality Prometheus counters: `fl_security_events_total` (Go,
  hand-rolled exposition, same pattern as the pre-existing privacy
  metrics) and `fl_worker_security_events_total` (Python, real
  `prometheus_client`), both labeled by a coarse category (not the raw
  ~55-value event type)
- Event/audit emission wired at a representative subset of call sites:
  C++ worker lifecycle (suspend/activate/revoke + lease cancellation),
  worker/coordinator signing-key revocation, coordinator signing-key
  rotation, every `ADMIN_CONTROL` permission denial, transport startup,
  and `Heartbeat`; Python's coordinator-task verification pipeline (16
  rejection reasons); Go's every security mutation handler plus
  centralized permission-denial and detailed-audit-access emission
- The project's first committed, reusable Docker Compose
  security-validation script
  (`scripts/validate_security_observability.py`) — every prior "N/N
  checks" number in this project came from one-off scratch scripts

Validated: C++ Docker build (`mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`,
real gRPC) — 12/12 `ctest` targets passing, including the new
`security_event`/`security_event_journal`/`security_audit_journal`
suites; two real GCC-specific standard-conformance bugs found and fixed
during this build (a missing `<cstdint>` include, and a nested-class
default-constructor-argument pattern MSVC silently accepted but GCC
correctly rejects); Python 287 passed/1 skipped (was 264); Go 189
passed/0 failed (was 161); `scripts/validate_security_observability.py`
run live against a real `postgres`+`redis`+`coordinator`+`api` Docker
Compose stack with real mTLS — 12/12 checks: real (non-501) events
endpoint, a real permission-denial producing a real observable event,
the new paginated/filterable audit journal, role-based redaction on
both endpoints confirmed live, a real Prometheus counter, and event/
audit persistence across an `api` container restart. Full detail in
[security-events.md](docs/security-events.md),
[security-metrics.md](docs/security-metrics.md),
[security-audit-journal.md](docs/security-audit-journal.md), and
[security-runtime-validation.md](docs/security-runtime-validation.md).

Current limitations (stated honestly):

- Event/audit emission covers a representative subset of operations,
  not every operation in the 55-event registry — see
  [security-observability-inventory.md](docs/security-observability-inventory.md)
  for the exact per-operation status, updated in place by this slice.
- Python-worker-originated events are persisted locally and exposed via
  Prometheus but are not shipped to the coordinator/Go — centralizing
  them would require a new signed wire message type, out of scope here.
- No native C++ Prometheus `/metrics` endpoint — C++-owned security-
  event counts are not yet relayed into Go's `fl_security_events_total`
  by a background poller (that counter currently reflects Go-originated
  events only).
- The C++ coordinator's own `SecurityAuditJournal` has no dedicated
  gRPC read RPC (file-only); it is not merged with Go's own audit
  journal into one queryable view.
- The Docker Compose validation script restarts only the `api`
  container, not `coordinator`, and does not cover `python-worker`/
  `web` (excluded from the mTLS override for the same pre-existing,
  disclosed reason every prior slice's Compose validation excluded
  them).
- No Web Security Center (out of scope for this slice, as directed).
- No security-focused CI gates.

See [security-events.md](docs/security-events.md),
[security-metrics.md](docs/security-metrics.md),
[security-audit-journal.md](docs/security-audit-journal.md),
[security-runtime-validation.md](docs/security-runtime-validation.md),
and [security-observability-inventory.md](docs/security-observability-inventory.md)
for full detail.

---

## 5.15 Completed (partially): Web Security Center, Event Centralization, and Security CI

Implemented:

- A full Web Security Center: `/security` (aggregate overview: transport,
  worker identities/signing keys, coordinator signing keys, signed-
  message/privacy-record/task-authenticity tallies, journal health,
  event-source health, an explicit secure-aggregation-not-implemented
  disclosure banner), `/security/workers` (list, role-aware columns),
  `/security/workers/[workerId]` (identity/signing-keys/recent-activity
  detail plus admin suspend/activate/revoke/revoke-key actions),
  `/security/coordinator-keys` (rotate/revoke), `/security/events` and
  `/security/audit` (filterable, live-polled, bounded-buffer explorers)
- A new `SubmitWorkerSecurityEvents` gRPC RPC and wire contract
  (`SignedWorkerSecurityEventBatch`, `MESSAGE_TYPE_SECURITY_EVENT_BATCH`,
  `MESSAGE_STREAM_SECURITY_EVENTS`) relaying Python-worker-originated
  security events to the coordinator's own journal for real — reusing
  the exact `SignedWorkerEnvelope` verification pipeline
  (signature/replay/worker-binding), not new crypto — closing the
  "Python-worker-originated events are not shipped to the coordinator"
  gap disclosed in 5.14 above
- A Python worker-side persistent event queue
  (`worker/security_event_queue.py`), built on the existing
  `SecurityEventJournal` as its storage engine (not a second store),
  with at-least-once delivery and a restart-safe cursor sidecar file
- `GetSecurityEventSourceHealth` gRPC RPC (added: C++ implementation +
  Go `/api/v1/security/events/sources` endpoint) and a matching typed
  web API function, reporting per-source (`go-api`/`coordinator`/
  `python-worker`) record counts, batch accept/reject counts, distinct-
  workers-seen, and lag
- `GET /api/v1/security/overview` (new Go endpoint), aggregating
  existing coordinator RPCs plus a bounded tally over the event
  journals — no new counters duplicating what the journals already
  record
- A typed web client layer (`lib/security-api.ts`) with `AbortSignal`
  and `Idempotency-Key` support (new capabilities `lib/api.ts` does not
  have), and two new shared components (`ConfirmDialog` — reason +
  consequence explanation + acknowledgment + a per-open-session
  idempotency key reused across retries; `SecurityStatusPill`)
- New low-cardinality Prometheus gauges in Go
  (`fl_security_event_source_records`, `_batches`,
  `_distinct_workers`, `_lag_seconds`), fed on every event-source-health
  poll — closing part of the "C++-owned security-event counts are not
  relayed" gap disclosed in 5.14 (the *aggregate* health is now
  relayed; per-event C++ counts still are not, and still deliberately
  so — see 10.2/known-limitations.md)
- Two new CI gates: `cpp-grpc` (builds and `ctest`s the real gRPC-gated
  coordinator — closing the "no CI job builds the gRPC-gated
  coordinator" gap that existed since the Coordinator Runtime phase)
  and `secret-scan` (tracked-file scan for private-key/credential
  markers, broader than the pre-existing PKI-fixture-only check in
  `pki-verify`)

Validated: a live Docker gRPC build
(`mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`) — 12/12 `ctest`
suites passing, including new `SubmitWorkerSecurityEvents` integration
coverage (signature verification, replay rejection, unknown-worker
rejection, oversized-batch rejection, per-event skip-not-fatal
validation, source-health aggregation) and a cross-language golden-
fixture hash test; Go `go test ./...` all packages passing (6 new
tests, `go vet`/`go build` clean); Python `pytest tests python/tests`
336 passed / 1 skipped (was 321; 15 new tests), `ruff check`/`ruff
format --check` clean; web `npm run test` 46 passed (was 26; 20 new
tests), `npm run lint`/`npm run typecheck`/`npm run build` clean. Full
detail in [security-event-centralization.md](docs/security-event-centralization.md),
[web-security-center.md](docs/web-security-center.md), and
[security-ui-report.md](docs/security-ui-report.md).

Current limitations (stated honestly — this is a **partial** close of
the parent specification's full scope, by deliberate tiering, not by
oversight):

- Critical event coverage was not exhaustively wired across the full
  ~55-type registry this slice either — two new event types
  (`WORKER_SECURITY_EVENT_BATCH_ACCEPTED`/`_REJECTED`) were added for
  the new RPC, but a full audit-and-wire pass across every remaining
  unwired call site in C++/Python/Go was not attempted.
- No Grafana dashboard was built — `infra/grafana/` still provisions
  only a datasource. The new metrics are real and scrapeable; only the
  dashboard JSON/provisioning is missing.
- `scripts/security-validation/` was not modularized or expanded into
  a full enumerated 62-scenario matrix; the existing 12/12-check script
  from 5.14 was not re-run or extended with new
  `SubmitWorkerSecurityEvents`-specific live-Compose scenarios.
- No browser end-to-end automation (no Playwright/Cypress in this
  repository) — the Web Security Center is verified via Vitest
  component/API-layer tests plus a real production build, never a
  scripted or manual click-through of the running dev server.
- `SubmitWorkerSecurityEvents` was not exercised live over a real
  Docker Compose stack with a real Python worker process and real
  mTLS end-to-end — validated instead via a live Docker `ctest` build
  exercising `CoordinatorServiceImpl` directly, plus isolated Python
  unit tests of the queue/signing logic (both halves share the
  identical, cross-language-golden-fixture-verified canonicalization
  logic, but an actual live process-to-process call was not made).
  `docker-compose.security.yml`'s mTLS override still does not extend
  to the `python-worker` service (a pre-existing gap from 5.14).
- No per-user `HasScope` plumbing for the `SERVICE` role (pre-existing
  gap, unchanged — see `go/internal/security/permissions.go`'s own
  doc comment).

See [security-event-centralization.md](docs/security-event-centralization.md),
[web-security-center.md](docs/web-security-center.md), and
[security-ui-report.md](docs/security-ui-report.md) for full detail.

## 5.16 Completed: Security Runtime Completion and Release Evidence

Closes most of 5.15's disclosed gaps by actually exercising the whole
stack live over Docker Compose, not just unit/mock coverage. This slice's
single biggest finding: **live validation caught real defects no unit
test could have caught**, because unit tests run against a host
environment/mock stub that was never wrong the same way the real
Docker images and real network topology were.

Implemented:

- Real mTLS + a real persistent Ed25519 signing identity + real
  security-event centralization wired into the actual Python worker
  entrypoint (`worker/__main__.py`) for the first time — previously
  `GrpcCoordinatorClient` supported all of this but the deployed
  container never passed any of the real parameters.
  `docker-compose.security.yml` now extends mTLS to `python-worker`
  too (closing 5.15's last-listed gap).
- A worker now registers with the coordinator on startup even with no
  `run_id` configured (the default container mode) — previously only
  the training-loop path called `RegisterWorker` at all.
- `CLIENT_RESULT_ACCEPTED`/`REJECTED` and
  `PRIVACY_RECORD_ACCEPTED`/`REJECTED` local worker-side event
  emission for `SubmitClientResult` — previously the one signed-
  message RPC with no local event at all.
- The modular `scripts/security-validation/` runtime-validation
  harness (14 groups, a versioned scenario registry, JSON+Markdown
  reports, mechanical "zero assertions = FAIL" anti-fraud enforcement)
  replacing 5.14's flat 12-check script — 94 registered scenarios (37
  real/live, 57 honestly DEFERRED with a stated reason, 0 BLOCKED).
- A real Playwright browser-test suite (5 spec files, Work Packages
  G–M) for every Web Security Center route, run against the live
  Compose stack with real HTTP APIs — no mocked mutations. Closes
  5.15's "no browser end-to-end automation" gap.
- Event-source staleness detection: a fixed 120s threshold, a new
  `stale` field on `GET /api/v1/security/events/sources`, surfaced in
  the web overview's status pill.
- A Grafana Security Operations dashboard (6 panels) — the first
  dashboard this repository ships — with real provisioning wiring.
  Closes 5.15's "no Grafana dashboard" gap.
- Two new CI workflows: `security-runtime-pr` (a required, fast subset
  of the live harness on every push/PR) and the scheduled
  `security-runtime-full.yml` (every group, including the browser
  suite). A CI/local artifact-sanitation check
  (`check_artifact_sanitation.py`) and a reproducible sanitized
  release-evidence bundle generator (`generate_release_evidence.py` →
  `artifacts/security-release-evidence/`).
- `docs/security-capability-inventory.md` rewritten to the
  Capability/Unit/Cross-lang/Integration/Docker/Browser/CI/Restart/
  Failure/Status/Evidence/Limitation column set, `docs/security-event-
  source-health.md` and `docs/security-dashboard.md` added.

**Real defects found and fixed by live validation, not by any unit test:**

1. The `python-worker` Docker image never installed the `security`
   extra (`pynacl`/`cryptography`) — the container crashed on import
   before it could start at all. Unit tests never caught this because
   they run against a host Python environment that already has these
   packages installed.
2. The health-poll-only worker path (the default container mode)
   never called `RegisterWorker` — fixed by registering on startup
   regardless of mode.
3. `docker-compose.security.yml` configured `FL_WORKER_WORKER_ID=
   python-worker-1` (the Dockerfile default) while the mounted mTLS
   certificate was issued for `worker-1` — every signed RPC was
   rejected with `PERMISSION_DENIED` on a worker_id/certificate
   identity mismatch.
4. The harness's own `Context.http()` didn't catch a bare
   `TimeoutError` from a slow-but-eventually-arriving response body
   read (only `urllib.error.URLError` was caught) — a real retry-loop
   scenario (coordinator outage/recovery) crashed instead of treating
   one slow poll as "not ready yet."
5. Vitest's default test-file glob also collected the new Playwright
   `e2e/*.spec.ts` files, which use Playwright's own `test`/`describe`
   — `vitest.config.ts` now excludes `e2e/**`.
6. **No CORS handling existed anywhere in the Go API.** The web app
   (`http://localhost:3000`) and the API (`http://localhost:8080`) are
   different origins; every client-side fetch from `lib/api.ts`/
   `lib/security-api.ts` is cross-origin and was silently blocked by
   the browser's same-origin policy — invisible to curl, Go's own
   `httptest`-based tests, and the Python harness (none enforce CORS
   the way a real browser does). This means the Web Security Center,
   and likely other client-side-fetching pages, had never actually
   been confirmed working against the real Dockerized API in an actual
   browser before this slice's Playwright work — only via component
   tests, an API-layer test, and a production build. Fixed with a
   `withCORS` middleware (reflects `Origin`, no
   `Access-Control-Allow-Credentials` since auth is Bearer-token only,
   never cookies) plus 3 new Go tests.

**A final pass, once the browser suite could actually run cleanly,
found two more real application defects the same way:**

7. Coordinator signing-key rotation form defaults (365-day expiry,
   7-day grace period) exceeded the server's real enforced maximums
   (90 days, 1 day) — a real admin using the form's own untouched
   defaults always got a real 409. Fixed: defaults changed to 90/1
   days, with matching `max` input attributes.
8. `event_id` is unique only within its own source's sequence, not
   globally across the merged Go-local + coordinator-relayed
   `GET /api/v1/security/events` response (confirmed live: 32 fetched
   events, only 20 unique IDs) — caused a React key collision
   rendering the wrong event's content in a table row. Fixed at the
   rendering layer (composite `source_service:event_id` keys); the
   underlying non-uniqueness is disclosed, not changed at the wire
   level, to avoid a larger, riskier change to pagination semantics
   for a rendering-only defect.

Validated (fresh counts, final pass): C++ debug `ctest` 7/7 passing; Go
`go build`/`go vet`/`go test ./...` all packages passing (gofmt-clean
for every file touched this slice; `-race` not run locally --
`CGO_ENABLED=0` by default on this Windows shell, preserved in CI);
Python `pytest tests python/tests` 358 passed / 1 skipped, `ruff
check`/`ruff format --check` clean, `mypy` clean (76 source files); web
`npm run test` 46 passed, `npm run lint`/`typecheck`/`build` clean;
standalone Playwright (5 specs, real Chromium browser): **20/20
passed**; the full runtime-validation harness live against Docker, one
invocation, all 14 groups including the browser suite: **37 PASS, 0
FAIL, 0 BLOCKED, 57 DEFERRED, 0 SKIPPED**; `security-ui` group alone,
harness-managed: **5 PASS, 0 FAIL**; artifact sanitation and the
regenerated release-evidence bundle both clean.

**Security readiness classification: RESEARCH_SECURITY_READY.** Not
higher: independent cryptographic review, privacy review, penetration
testing, operational security review, and disaster-recovery validation
all remain outside this and every prior slice's scope, by explicit
instruction.

Current limitations (stated honestly):

- Adversarial/tampering live scenarios (tampered batch, invalid
  signature, wrong-worker-identity, replay, oversized batch, and the
  equivalent for signed messages/tasks) remain DEFERRED — real, but
  unit-level only. Live adversarial injection against a running mTLS
  stack was judged disproportionate effort for this slice; the
  reasoning is stated per-scenario in the registry, not asserted
  blanket.
- Worker signing-key rotation/revocation and worker revocation are not
  exercised live — destructive to the shared harness stack every other
  scenario in the same run depends on.
- `event_id` global (cross-source) uniqueness and its effect on
  `after_event_id` cursor semantics — disclosed, not fixed at the wire
  level (item 8 above).
- No dedicated RESEARCHER-role browser spec (covered live at the HTTP
  layer by the non-browser harness instead).
- No per-user `HasScope` plumbing for the `SERVICE` role (pre-existing
  gap, unchanged).
- Python worker's own `/metrics` endpoint is not wired into any
  Compose override (`metrics_port` defaults to 0/disabled) — unit-
  tested only.

See [security-capability-inventory.md](docs/security-capability-inventory.md),
[security-runtime-validation.md](docs/security-runtime-validation.md),
[security-runtime-completion-report.md](docs/security-runtime-completion-report.md),
[security-ci.md](docs/security-ci.md),
[security-event-source-health.md](docs/security-event-source-health.md),
and [security-dashboard.md](docs/security-dashboard.md) for full detail.

---

# 6. Immediate Remaining Work

The prior immediate slice, Message Authenticity Enforcement and Identity
Lifecycle, and the following slice, Signed Client Results and Worker
Lifecycle Enforcement, are both now complete (5.7, 5.8 above). The
subsections below are kept as the original scope record for the whole
"message authenticity and worker lifecycle" arc; each is now marked with
its real status rather than rewritten from scratch, so gaps remain visible
rather than silently narrowed.

This must be completed before pairwise masking or secure aggregation protocol execution begins.

### 6.1 RPC security policy — Done

See [rpc-security-policy.md](docs/rpc-security-policy.md): every RPC is
inventoried and classified into the categories below; signed-envelope,
replay, and authorization requirements are documented per RPC and reflect
real, live-validated behavior, not aspiration.

Original scope, for reference:

- Inventory every RPC
- Classify transport and identity requirements
- Define signed-envelope requirements
- Define replay policy
- Define sequence stream
- Define authorization and audit behavior

RPC categories:

```text
PUBLIC_HEALTH
AUTHENTICATED_SERVICE
AUTHENTICATED_WORKER
ADMIN_CONTROL
SIGNED_WORKER_MESSAGE
```

### 6.2 Signed worker envelopes — Partial

Implemented and live-validated: Heartbeat, Client result, Sample
privacy record, Signing-key rotation (each as its own message
type/stream, reusing this same envelope — see
[signed-privacy-records.md](docs/signed-privacy-records.md) and
[key-rotation.md](docs/key-rotation.md)).

Still unimplemented (no producer or consumer): Task acquisition, Task
acceptance, Task progress, Task failure, Personalization metrics,
Worker drain, Worker shutdown.

Envelope fields:

- Schema version
- Message type
- Worker ID
- Run ID
- Round ID
- Task ID
- Client ID
- Model version
- Message stream
- Sequence number
- Issue time
- Expiry time
- Nonce
- Payload hash
- Signing-key ID
- Signature

### 6.3 Signed coordinator tasks — Done

Coordinator signing identity and task signatures implemented and
live-validated — see [5.11](#511-completed-coordinator-signed-tasks-and-worker-side-replay-protection)
and [signed-coordinator-tasks.md](docs/signed-coordinator-tasks.md).

Workers verify (all implemented):

- Task signature
- Task expiry
- Task nonce (replay protection)
- Worker binding (task addressed to this worker)
- Model manifest hash (part of Model Configuration Hash)
- Dataset partition hash
- Training config hash
- Privacy config hash
- Personalization config hash
- Task payload hash (binds run/round/client/model version/lease/attempt)
- Sequence number (replay protection)
- Duplicate-execution detection (accepted-task journal)

Coordinator signing-key rotation as a live operational flow is now
implemented and live-validated — see
[5.12](#512-completed-coordinator-signing-key-rotation-revocation-and-trusted-bundle-lifecycle).

### 6.4 Payload hashing — Partial

Implemented and live-validated: Heartbeats, Client results (which subsumes
Tensor manifests, Tensor checksums, Training results, and nested
Personalization-metrics fields as part of the one hash), Privacy
records as their own independently-hashed and signed structure (27
fields, cross-language golden fixture — see
[payload-hashing.md](docs/payload-hashing.md)), and now Task manifests
(five configuration hashes plus a task payload hash — see
[task-configuration-hashes.md](docs/task-configuration-hashes.md)).
Not implemented: independently-hashed Personalization metrics as their
own signed message type.

Raw tensor values should not be duplicated merely for signing. (Honored:
the C++ verifier binds to `TensorManifest.checksum`, recomputed and
verified against real values, never to the raw float array a second time.)

### 6.5 Persistent replay protection — Partial

Implemented for the `HEARTBEAT`, `CLIENT_RESULT`, `PRIVACY_RECORD`, and
`KEY_MANAGEMENT` streams (same coordinator-side store, proven
stream-agnostic across all four). Not yet exercised for
`TASK_LIFECYCLE` or `PERSONALIZATION` — see
[message-sequences.md](docs/message-sequences.md). A separate,
worker-side store (`CoordinatorTaskReplayStore`, tracking the
coordinator's own issued sequence rather than a worker's) now protects
the coordinator→worker direction — see
[coordinator-task-replay-protection.md](docs/coordinator-task-replay-protection.md).

Implemented mechanism (all rules below apply uniformly regardless of which
stream uses them):

- Nonce hashing
- Bounded nonce retention
- Per-stream sequence tracking
- Restart persistence
- Corruption detection
- Expiry cleanup
- Duplicate nonce rejection
- Duplicate sequence rejection
- Lower sequence rejection
- Sequence-gap policy

Sequence streams:

```text
CONTROL
HEARTBEAT
TASK_LIFECYCLE
CLIENT_RESULT
PRIVACY_RECORD
PERSONALIZATION
KEY_MANAGEMENT
```

### 6.6 Signing-key lifecycle — Done

Implemented and live-validated, real coordinator restart included:

- Persistent public signing-key records (`SigningKeyRegistry`, separate
  from `WorkerIdentityRegistry`, multi-key-per-worker)
- Active status
- Grace-period status (real acceptance during the window, real
  elapsed-time expiry)
- Revoked status (immediate, with automatic worker suspension when it
  was the worker's sole valid key)
- Expired status (evaluated lazily at verification time, not only by a
  background sweep)
- Signed key rotation (`RotateWorkerSigningKey`, current-key-only
  authorization)
- Grace-period expiry
- Immediate key revocation (`RevokeWorkerSigningKey`)
- Restart persistence
- Legacy migration from the prior single-key model, exercised via a
  real restart

Not implemented: audit events beyond structured stderr logging;
security metrics; a default rotation interval or automated background
expiry sweep (expiry is still correctly enforced lazily regardless);
automated old-private-key-file cleanup. See
[signing-key-management.md](docs/signing-key-management.md) for the
full accounting.

### 6.7 Worker status enforcement — Partial

Done, live-validated: Suspension, Activation, Revocation (via the five new
admin RPCs), Active lease cancellation (cross-run, on revoke), Scheduler
exclusion (`AcquireTask` rejects `SUSPENDED`/`REVOKED`), Heartbeat
restrictions (from the prior slice).

Not done: Result restrictions (`SubmitClientResult` does not itself check
status — see 5.8's limitations), Privacy-record restrictions (no
independent privacy-record message exists yet — see 6.2), Capability-refresh
restrictions, Key-rotation restrictions (no rotation exists — see 6.6).

Recommended policies (unchanged from original scope; suspended-worker
policy is now implemented as stated, revoked-worker policy is implemented
except for the "no result"/"no privacy record" lines noted above):

```text
Suspended worker:
- No new tasks
- Existing task result may be accepted until lease expiry
- Signed heartbeat may report suspended state
- Admin may reactivate

Revoked worker:
- Active lease canceled
- No heartbeat
- No task acquisition
- No result
- No privacy record
- No capability refresh
- No key rotation
- Cannot reactivate
```

### 6.8 Certificate fingerprint revocation — Partial

Certificate-to-worker identity binding exists and is enforced on
`RegisterWorker`, `Heartbeat`, `AcquireTask`, and `SubmitClientResult`
(previously only the first two). A dedicated, independent
fingerprint-revocation list distinct from `WorkerIdentityRegistry` status
(`REVOKED` worker status is enforced, but not a separate
certificate-fingerprint CRL check per RPC) is not implemented.

Validate:

- Certificate fingerprint
- Worker identity
- URI SAN
- Worker status
- Certificate expiry
- Fingerprint revocation state

### 6.9 Go security APIs — Done

Implemented and live-validated (see 5.13, and 5.14 for the events/audit
rows below, both now closed): a full Go `SecurityClient` (13 methods —
`ListSecurityEvents` added in 5.14), `go/internal/security`'s
permission model, and 14 real HTTP endpoints under
`/api/v1/security/...`.

- Transport status — done
- Trust model — done
- Worker list — done
- Worker details — done
- Suspend worker — done
- Activate worker — done
- Revoke worker — done
- Signing-key list — done
- Revoke signing key — done
- Coordinator signing-key list — done
- Rotate coordinator signing key — done
- Revoke coordinator signing key — done
- Security events — done (5.14): real, merged (Go-local + coordinator-relayed), role-redacted, paginated — no longer 501
- Security audit — done (5.14): backed by the new, security-specific, paginated/filterable `SecurityAuditJournal` — the general Go audit repository is still written to additively, not replaced

### 6.10 Web security administration — Not started

Blocked on nothing technically now (6.9 is done), but deliberately not
attempted this slice — the user chose "Go API + permissions only" over
"Go API + minimal Web Security Center" when asked to scope 5.13.
Implement:

- Transport status panel
- Worker identity table
- Worker detail page
- Certificate status
- Signing-key status
- Capability expiry
- Suspension
- Activation
- Revocation
- Signing-key revocation
- Replay alerts
- Signature alerts
- Privacy-record rejection alerts

### 6.11 Security events, metrics and audit — Partial (substantially closed, see 5.14)

A formal, schema-versioned, cross-language security-event type now
exists (C++/Python/Go, real cross-language golden-fixture checksum
parity), with durable, rotating, corruption-recovering event journals
per service, a new security-specific durable audit journal (additive to
the pre-existing general-purpose Go `AuditRepository`, not a
replacement), and low-cardinality Prometheus counters in Go and Python
— see 5.14 for full detail.

The pre-existing `CoordinatorEventType`/`EventBus` mechanism (per-run,
in-memory, SSE-facing) is **unchanged** — the new journals are separate,
global, persistent stores, not an extension of it. Structured stderr
logging continues alongside the new journals at every wired call site
(additive, not replaced).

Still not implemented (see 5.14's "Current limitations" for the full
list): event/audit emission at every operation in the registry (a
representative, documented subset is wired — see
[security-observability-inventory.md](docs/security-observability-inventory.md));
shipping Python-worker events to the coordinator/Go; a native C++
Prometheus endpoint or a background poller relaying C++/Python event
counts into Go's counters; a gRPC read RPC for the C++ coordinator's
own audit journal.

### 6.12 Full Docker validation — Partial

Validated live via direct `docker run` (not Docker Compose) for: signed
capability accepted, unsigned heartbeat rejected, signed heartbeat
accepted, signed result accepted, tampered result rejected (via checksum
mismatch), replay rejected, worker suspension, worker activation, worker
revocation, revoked-worker lease cancellation, admin RPCs rejected for
non-go-api identity, worker signing-key rotation/grace-period/expiry/
revocation (see 5.10), a real signed-task issued/verified/
reissued/replay-rejected/duplicate-execution-rejected/crash-recovered
end to end, the `GetCoordinatorSigningKeys` admin RPC accepted for
a go-api identity and rejected for a worker identity (see 5.11), and —
this slice — a real coordinator signing-key rotation over live mTLS
(idempotent retry returning the same key, not a fresh one), the
previous key correctly entering GRACE_PERIOD, a real lazy-evaluated
expiry after an actual elapsed-time wait, a real revocation reporting
`production_task_issuance_stopped=true`, `AcquireTask` failing closed
once no ACTIVE coordinator key remains, an RPC-level rotation attempt
with no ACTIVE key correctly rejected (vs. the CLI's recovery
fallback), a worker identity rejected from `RotateCoordinatorSigningKey`,
and the recovery CLI independently exercised for bootstrap/rotate/
expire/revoke/recover with every resulting bundle version cross-checked
by a fresh Python process (see 5.12); and — this slice, and for the
first time via **real Docker Compose** rather than direct `docker run`
— real mTLS between the `coordinator` and `api` containers, transport
status/trust model/coordinator-signing-key listing over that real
mTLS connection, a real Ed25519-signed `RegisterWorker` call followed
by the new worker-admin HTTP endpoints seeing and suspending that real
worker, role-based redaction and permission denial (403) confirmed
live for VIEWER/RESEARCHER, 404/401 confirmed, HTTP-layer idempotent
replay confirmed byte-identical for a repeated rotation request, a
real correctly-redacted audit trail, the events endpoint's honest 501,
and a worker identity independently rejected with `PERMISSION_DENIED`
from the two new C++ RPCs (see 5.13) — over 80 checks total across
this project's live security test scripts.

Not validated: signed/replayed privacy-record scenarios as an
independent message beyond what 5.9 already covered, web security
verification (no web surface exists), Prometheus scraping, and the
full Docker Compose orchestration matrix (this slice's Compose run was
scoped to `postgres`+`redis`+`coordinator`+`api` only, deliberately
excluding `python-worker`/`web` — see 5.13's limitations).

Required scenarios (original scope, for reference):

- Signed capability accepted
- Unsigned heartbeat rejected
- Signed heartbeat accepted
- Signed result accepted
- Tampered result rejected
- Replay rejected
- Signed privacy record accepted
- Replayed privacy record rejected
- Coordinator-signed task accepted
- Tampered task rejected
- Key rotation
- Grace-period acceptance
- Old-key rejection
- Worker suspension
- Worker activation
- Worker revocation
- Revoked worker lease cancellation
- Security API verification
- Web security verification
- Prometheus scraping
- Clean teardown

---

# 7. Secure Aggregation Protocol — Remaining

This work starts only after Message Authenticity and Identity Lifecycle is complete.

**Status update — Secure Aggregation Protocol Foundation and No-Dropout
Masked-Sum Core slice:** the cryptographic and mathematical **core**
subset of this section — §7.3 (fixed-point encoding), §7.4 (ephemeral
key exchange, primitives only), and §7.5 (pairwise masking) — is now
real, tested, cross-language-verified code (C++ + Python, Docker-
validated OpenSSL crypto, golden fixtures, a capstone
cancellation/dropout-breaks-cancellation proof). See
[secure-aggregation-protocol-foundation.md](secure-aggregation-protocol-foundation.md)
for the Tier 1/Tier 2 scope split and
[known-limitations.md](known-limitations.md)'s "Secure Aggregation
Protocol Foundation and No-Dropout Masked-Sum Core slice" section for
exactly what is and is not covered. **Not started**: §7.1's full
protocol state machine (this slice implements a narrower 6-state
no-dropout machine, not the 11-state Bonawitz/SecAgg+-style machine
listed below — see the Mandatory Security Boundary in that foundation
doc), any live RPC/wire wiring for any of §7.1–§7.5, and all of §7.6
(secret sharing) and §7.7 (dropout recovery), which remain explicitly
out of scope pending a vetted threshold secret-sharing dependency (see
[cryptographic-primitives.md](cryptographic-primitives.md) §4 —
unresolved). The provider name implemented is
`SECAGG_NO_DROPOUT_EXPERIMENTAL`, not `SECAGG_PLUS_NATIVE` (§7.2) — the
name itself communicates that this is not the full protocol described
below.

**Status update — Secure Aggregation Wire Protocol and Live No-Dropout
Execution slice:** real, versioned protobuf wire contracts now exist
for §7.1's session/roster/masked-update messages (a narrower 6-state
session lifecycle, not the 11-state machine below — same scope
boundary as the prior slice) and §7.2's provider enum
(`SECAGG_NO_DROPOUT_EXPERIMENTAL` only). A real, tested, in-memory C++
`SecureAggregationSessionManager` orchestrates the full
create→advertise→freeze→submit→finalize lifecycle end to end, proven
by a capstone test using real X25519/HKDF/ChaCha20 across a real
3-participant cohort. **Still not live**: this manager is not wired
into `CoordinatorServiceImpl`; the six new RPCs
(`AdvertiseSecureAggregationKey`, `GetFrozenCohortRoster`,
`SubmitMaskedClientUpdate`, `GetSecureAggregationSession`,
`ListSecureAggregationSessions`, `AbortSecureAggregationSession`) are
declared and return explicit `UNIMPLEMENTED`, matching the pre-existing
`GetRound` precedent. No Python worker integration, no live signature/
replay/sequence verification, no FedAvg-registry integration, no
events/metrics/Go/web surface, no multi-worker Docker validation. See
[secure-aggregation-wire-protocol-audit.md](secure-aggregation-wire-protocol-audit.md)
and
[secure-aggregation-wire-protocol-foundation.md](secure-aggregation-wire-protocol-foundation.md)
for the full audit and Tier 1/Tier 2 scope split, and
[known-limitations.md](known-limitations.md)'s corresponding section
for the itemized gap list.

**Status update — Secure Cohort Handshake and Signed Roster Runtime
slice:** the handshake portion of §7.1's lifecycle — cohort forming,
key advertisement, cohort freeze, signed roster distribution, workers
reaching a verified `READY_FOR_MASKED_TRAINING` state — is now real,
gRPC-reachable, and live-validated. `SecureAggregationSessionManager`
is wired into `CoordinatorServiceImpl`: `AdvertiseSecureAggregationKey`,
`GetFrozenCohortRoster`, `GetSecureAggregationSession`,
`ListSecureAggregationSessions`, and `AbortSecureAggregationSession`
are live (no longer `UNIMPLEMENTED`); sessions are created automatically
from a round's real cohort (`FL_SECURE_AGGREGATION_ENABLED`
coordinator-wide opt-in); the coordinator signs the frozen roster with
its real Ed25519 identity; Python workers generate fresh ephemeral
X25519 keys, advertise them, and independently verify the signed
roster. A real three-worker Docker Compose stack (real mTLS, real
per-worker signing identities) validated the complete handshake live —
7/7 automated checks passed, see
[secure-cohort-handshake-report.md](secure-cohort-handshake-report.md).
**`SubmitMaskedClientUpdate` remains `UNIMPLEMENTED`, by explicit
instruction** — masked model-update submission, tensor/weight masking
in the production worker, and secure aggregate finalization are the
next slice, not this one. See
[secure-cohort-handshake-foundation.md](secure-cohort-handshake-foundation.md)
and [known-limitations.md](known-limitations.md)'s corresponding
section for the full scope split and the real (mostly
infrastructure/environment, not protocol-logic) bugs this slice's own
live validation found and fixed.

**Status update — Masked Update Runtime and No-Dropout Secure FedAvg
Finalization slice:** the portion of §7.1's lifecycle the prior slice
stopped short of — `MASKED_UPDATE_COLLECTION` through
`COMPLETED` — is now real, gRPC-reachable, and live-validated.
`SubmitMaskedClientUpdate` is no longer `UNIMPLEMENTED`: it verifies a
real signed `MaskedClientUpdate` (mTLS, identity/status, signing-key
resolution, Ed25519 signature, an independent
`kSecureAggregationMaskedUpdate` replay track), persists accepted
contributions, and — once the complete frozen cohort has submitted —
finalizes the session and advances the live round's `model_version`
through a new `RunInstance::apply_secure_aggregate_and_advance` bridge.
Python workers locally train, fixed-point encode, pairwise-mask, sign,
and submit real masked updates (`fl_platform.secure_aggregation.masked_update`),
structurally never falling back to cleartext submission for a secure-
bound task; the coordinator additionally enforces this server-side on
`SubmitClientResult`. Secure aggregation is gated to `fedavg` with no
privacy mode or sample-level DP (`USER_LEVEL`/`HYBRID` DP and adaptive
clipping are rejected with an immediate, observable session abort). A
real three-worker Docker Compose stack drove a complete single-round
FedAvg run through the masked path end to end — `model_version`
genuinely advanced `v0 → v1`, 15/15 automated checks passed. Two real
bugs (a deadline-sweep race against an already-complete cohort, and a
replay-track collision between key advertisement and masked-update
sequence numbers) were found and fixed by this slice's own tests and
live validation, not discovered later. See
[secure-aggregation-masked-runtime-audit.md](secure-aggregation-masked-runtime-audit.md)
and
[secure-aggregation-masked-runtime-report.md](secure-aggregation-masked-runtime-report.md)
for the full audit and completion report, and
[known-limitations.md](known-limitations.md)'s corresponding section
for the itemized gap list. **Still not built**: threshold secret
sharing, dropout recovery, or any of §7.6/§7.7 below (unresolved, no
vetted dependency selected); Go/web secure-aggregation observability;
native Prometheus metrics for this protocol.

**Status update — Secure User-Level Differential Privacy Runtime
slice:** `USER_LEVEL_DP` is now usable under secure aggregation for
the first time — the prior slice's `SECURE_AGGREGATION_USER_LEVEL_DP_UNSUPPORTED`
gate is lifted whenever weighting is uniform, adaptive clipping is
disabled, and the privacy configuration is valid and safely
quantizable. Worker-side deterministic global L2 clipping
(`user_level_clipping.py`) replaces the coordinator-side clipping the
existing cleartext mechanism used — that cleartext mechanism (§7's own
`finalize_round` pipeline) is completely unchanged and continues to
serve non-secure `USER_LEVEL_DP` runs exactly as before; this is a new,
parallel, worker-side path, not a rewrite. Central Gaussian noise (the
run's existing OS-CSPRNG-backed `CryptoSecureNoiseProvider`) is added
once, inside `SecureAggregationSessionManager::finalize()`, to the
decoded masked-ring sum before the existing divide-by-weight-sum step
— calibrated against a quantization-aware effective sensitivity
(`clip_norm + sqrt(N)*(0.5/scale_factor)`), never the optimistic
unquantized clip norm. A self-contained signed
`SignedUserLevelPrivacyAttestation` (worker evidence of configured
clipping behavior, never cryptographic proof of correct execution —
see the Mandatory Privacy Trust Statement) is bound into every
`MaskedClientUpdate` and verified against the same signing key as the
outer envelope. The coordinator's existing `UserLevelAccountant`
commits exactly once per round, gated by the same round-progression
idempotency guard that already made the secure-aggregate-apply bridge
safe against retried RPCs; a non-mutating projection refuses session
creation outright if the round would exceed the configured epsilon
budget. A real three-worker Docker Compose stack drove a complete
single-round FedAvg run with a deliberately tiny clip norm through the
full path — real clipping engaged on real training gradients, all
three signed attestations were accepted, `model_version` genuinely
advanced. 22/22 automated checks passed. Two real cross-language
attestation-hash bugs (a JSON key-ordering mistake in the hand-written
C++ canonicalization, and a wrong enum-value guess in the regression
test meant to catch exactly that class of bug) were found and fixed by
this slice's own live validation and cross-language golden-fixture
test, not discovered later. See
[secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md)
and
[secure-user-level-dp-runtime-report.md](secure-user-level-dp-runtime-report.md)
for the full mechanism specification and completion report, and
[known-limitations.md](known-limitations.md)'s corresponding section
for the itemized gap list. **Still not built**: secure hybrid DP,
secure adaptive clipping, variable user weights under secure
aggregation, replace-one adjacency, random-subsampling amplification
(the last two are reserved wire enum values, never produced); Go/web
observability for user-level-DP session state; new Prometheus metrics;
threshold secret sharing and dropout recovery remain unresolved,
unchanged from every prior secure-aggregation slice.

**Status update — Secure User-Level DP Operations, Observability, and
Release Evidence slice:** closes the Go/web-observability and metrics
gaps the previous slice explicitly deferred, without changing the
approved mechanism. A bounded, representative `SECURE_USER_LEVEL_DP_*`
event vocabulary (12 types) is now wired at real C++/Python call sites;
Go-side `fl_secure_user_dp_*` Prometheus metrics are fed by a new
`GetSecureUserLevelPrivacyHealth` coordinator read RPC (no native C++
metrics endpoint, preserving the established re-export architecture);
5 new `GET /api/v1/secure-aggregation/privacy/*` routes exist with
responsibility-named permissions, a real ADMIN/RESEARCHER/VIEWER/
SERVICE access matrix (SERVICE has no implicit access anywhere), and
explicit per-role response types; a new Web page
(`/security/secure-aggregation/privacy`) shows capability, runtime
health, a budget lookup, and a paginated Privacy Round Explorer, with
all 10 mandated trust-limitation warnings always visible; a real
Playwright browser spec covers admin/viewer/service-role behavior
against the live backend; a new bounded statistical smoke test
validates the real `CryptoSecureNoiseProvider` (20,000 draws, real
observed mean/variance against tolerance); the publication-boundary
state machine is now documented with restart/corrupted-checkpoint
failure-injection tests; the live runtime-validation harness (including
a full Playwright browser suite covering both the 5 pre-existing Web
Security Center specs and the new privacy page) passed 12/12. **Three
real bugs were found and fixed by this slice's own testing** (a
checkpoint field silently never persisted, a cross-service event-flush
timing race in the validation script, and a list-endpoint returning 404
instead of an empty page for an unknown run_id — full detail in the
completion report §2), each caught only by actually running the code
live, not by inspection. See
[secure-user-level-operations-audit.md](secure-user-level-operations-audit.md)
for the scope statement,
[secure-user-level-dp-publication-boundary.md](secure-user-level-dp-publication-boundary.md)
for the state machine, and
[secure-user-level-operations-report.md](secure-user-level-operations-report.md)
for the full completion report. **Still not built**: the remaining
~17 of ~29 named event types and ~17 of ~31 named metrics (finer-
grained sub-steps); automated restart-reconciliation detection (the
health RPC's `reconciliation_required` field always reports `false`
today — no cross-check between the ledger and model-version state
exists yet); performance benchmarking; secure hybrid DP, secure
adaptive clipping, threshold secret sharing, and dropout recovery
remain unchanged out of scope.

**Status update — Secure Hybrid Differential Privacy Runtime slice:**
`HYBRID_DP` is now usable under secure aggregation for the first time
— `AcquireTask`'s prior unconditional `kHybridDp` rejection is lifted
by composing the two already-built, already-live-validated mechanisms
(sample-level Opacus DP-SGD, worker-side; secure user-level DP,
worker-side clipping + central aggregate noise, from the immediately
prior slice) rather than building either from scratch. The worker-side
execution order is fixed and enforced by construction: sample-level
private training produces the whole-user delta first, worker-side
global L2 user-level clipping runs on that already-DP-SGD-trained
delta second, then fixed-point encoding and pairwise masking as usual
— never the reverse. No new combined-configuration message was added:
`privacy_configuration_hash(task)` and
`secure_user_level_dp_configuration_hash(task)` were already
independently, cryptographically bound into the one signed task before
this slice, which is sufficient hybrid binding on its own (see
`secure-hybrid-dp-runtime-audit.md`'s dedicated reasoning). This slice
also closed a real, pre-existing gap affecting `SAMPLE_LEVEL_DP` alone
under secure aggregation, not just hybrid:
`MaskedClientUpdate.sample_privacy_record_hash` existed on the wire and
was already covered by the outer envelope's signature, but the worker
hardcoded it to `""` and the coordinator never verified it —
`SubmitMaskedClientUpdateRequest` now carries the real signed
`SignedSamplePrivacyRecord` envelope/payload, verified via the exact
signature/binding/replay/monotonicity/budget-contradiction logic the
cleartext path already used. Sample-level and user-level epsilon/delta/
accountant state are reported and accounted completely separately
throughout — no `hybrid_epsilon` field exists anywhere, and no
combined-epsilon request is even representable in the wire schema. A
real three-worker Docker Compose stack drove a complete single-round
FedAvg run with a deliberately tight sample-level `max_grad_norm=0.5`
and a deliberately tiny user-level `initial_clipping_bound=0.01`
through the full hybrid path — both real clipping mechanisms genuinely
engaged, in the correct order, on real training output; all three
workers' dual signed records were accepted; `model_version` genuinely
advanced `v0 → v1`; a real positive user-level `epsilon_spent=5.303`
was reported. 38/38 automated checks passed. **Four real bugs were
found and fixed by this slice's own testing**: two silent-mode-mismatch
bugs in the finalize/commit path (the accountant-commit gate and the
central-noise computation both checked `privacy_mode == kUserLevelDp`
only, meaning every hybrid round would have silently skipped its
user-level accountant commit **and** received zero central user-level
noise while still reporting `HYBRID_DP` as active — both found by
direct code re-reading, both proven fixed by a dedicated new C++ test
before live validation ran) and two issues in the live validation
script itself (a `UnicodeDecodeError` crashing `docker compose build`'s
output capture under Windows' default codepage, and a wrong test
assertion checking the coordinator's stdout log for an event that —
like its `SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED` sibling — is
only ever written to the durable security-event journal, found live
when that one assertion failed 38/39 while the correct journal-API
check for the identical event already passed). See
[secure-hybrid-dp-semantics.md](secure-hybrid-dp-semantics.md) and
[secure-hybrid-dp-runtime-report.md](secure-hybrid-dp-runtime-report.md)
for the full mechanism specification and completion report, and
[known-limitations.md](known-limitations.md)'s corresponding section
for the itemized gap list. **Still not built**: secure adaptive
clipping, secure aggregation of clipping indicators, variable user
weights, sample-count-weighted hybrid privacy, a single combined
epsilon, formal cross-unit privacy composition; Go read-only hybrid-
specific API routes and a dedicated Web hybrid observability page (the
existing `/security/secure-aggregation/privacy` page already correctly
reports the user-level layer for any run including a hybrid one, now
that this slice's `GetSecureUserLevelPrivacyHealth`/
`GetSecureUserLevelPrivacyBudget` fixes are in place); new hybrid-
specific Prometheus metrics; performance benchmarking; threshold secret
sharing and dropout recovery remain unresolved, unchanged from every
prior secure-aggregation slice.

## 7.1 Protocol requirements

Implement a published Bonawitz-style or SecAgg+-style cohort protocol.

Protocol states:

```text
COHORT_FORMING
IDENTITY_VERIFICATION
KEY_ADVERTISEMENT
ENCRYPTED_SHARE_DISTRIBUTION
MASKED_UPDATE_COLLECTION
DROPOUT_RESOLUTION
UNMASKING
AGGREGATE_VALIDATION
COMPLETED
ABORTED
FAILED
```

## 7.2 Secure aggregation provider

Implement:

```text
NONE
SECAGG_PLUS_NATIVE
```

Optional reference backend:

```text
FLOWER_SECAGG_PLUS_REFERENCE
```

The reference backend must not replace the C++ coordinator as system authority.

## 7.3 Fixed-point secure encoding

Required:

- Finite integer domain
- Signed fixed-point representation
- Configurable scale factor
- Deterministic rounding
- Overflow detection
- Safe cohort-size bounds
- Safe clipping-bound bounds
- Quantization-error reporting
- Cross-language parity

Do not mask raw IEEE floating-point bytes.

## 7.4 Ephemeral key exchange

Implement:

- Fresh X25519 key per secure session
- Session-bound derivation
- Run-bound context
- Round-bound context
- Model-version-bound context
- No key reuse
- Cleanup after completion or abort

## 7.5 Pairwise masking

Implement:

- Pairwise shared secrets
- Domain-separated HKDF labels
- Tensor-bound mask streams
- Parameter-bound mask streams
- Chunk-bound mask streams
- Pairwise sign rules
- Private masks
- Cancellation in aggregate
- No reuse across tensors or rounds

## 7.6 Secret sharing

Required for dropout recovery.

Before implementation:

- Select a vetted dependency
- Validate license
- Validate field arithmetic
- Validate test vectors
- Validate C++ and Python interoperability

Do not implement custom threshold cryptography.

## 7.7 Dropout recovery

Handle:

```text
DROPPED_BEFORE_MASKED_UPDATE
DROPPED_AFTER_MASKED_UPDATE
DROPPED_DURING_UNMASKING
```

Required:

- Minimum survivor threshold
- Maximum tolerated dropout
- Encrypted shares
- Authenticated shares
- Wrong-session rejection
- Wrong-recipient rejection
- Duplicate-share rejection
- Excessive-dropout abort

## 7.8 Transcript integrity

Maintain a transcript hash covering:

- Session config
- Cohort
- Protocol version
- Cryptographic suite
- Public keys
- Share messages
- Masked update hashes
- Dropout declarations
- Recovery shares
- Final aggregate hash
- Final status

## 7.9 Secure privacy integration

### Secure aggregation without user DP

```text
Client update
→ fixed-point encoding
→ masking
→ secure sum
→ decode aggregate
→ server optimizer
```

### Secure user-level DP

```text
Worker clips complete update locally
→ worker masks update
→ coordinator recovers aggregate only
→ coordinator adds central noise
→ user accountant advances
```

Trust limitation:

- Worker clipping is trusted.
- Verifiable clipping is not implemented.
- Malicious-client user-level DP is not claimed.

### Secure hybrid DP

```text
Worker performs sample-level DP
→ worker performs local user clipping
→ worker masks update
→ secure aggregate
→ coordinator adds central user noise
→ separate accountants advance
```

### Secure adaptive clipping

Workers securely aggregate only clipping indicators.

The coordinator must not see individual client update norms or individual indicators.

---

# 8. Distributed Execution — Remaining

After secure aggregation protocol validation:

## 8.1 Local multiprocessing

Implement:

- Worker process pool
- CPU allocation
- GPU allocation
- Memory-aware scheduling
- Deterministic seeds
- Worker restart
- Task cancellation
- Isolated failures
- Bounded queues

## 8.2 Ray and Flower

Implement:

- Ray simulation backend
- Flower simulation adapter
- Resource declarations
- Placement strategy
- Multi-GPU support
- Multi-node support
- Large simulated client population
- Worker autoscaling

## 8.3 Scheduling modes

Implement:

```text
SYNCHRONOUS
DEADLINE_BASED_SEMI_SYNCHRONOUS
BUFFERED_ASYNCHRONOUS
STALENESS_AWARE_ASYNCHRONOUS
```

## 8.4 Straggler handling

Implement:

- Round deadlines
- Minimum valid clients
- Target clients
- Late-result policy
- Task carryover policy
- Staleness weighting
- Maximum staleness
- Retry limits
- Backpressure

## 8.5 Scale validation

Test:

- 100 clients
- 500 clients
- 1,000 clients where practical
- Multi-GPU
- Multiple nodes
- Worker churn
- Network delay
- Partial outages
- Secure aggregation cohorts at scale

---

# 9. Enterprise Platform — Remaining

## 9.1 PostgreSQL

Replace file-backed or in-memory metadata with durable repositories for:

- Users
- Projects
- Experiments
- Runs
- Rounds
- Workers
- Worker identities
- Signing keys
- Models
- Datasets
- Partitions
- Artifacts
- Privacy ledger
- Security audit
- Events

Required:

- Versioned migrations
- Transaction boundaries
- Indexes
- Optimistic locking
- Backup
- Restore
- Point-in-time recovery

## 9.2 Redis

Use for:

- Distributed locks
- Task coordination
- Short-lived leases
- Event fan-out
- Rate limiting
- Idempotency keys
- Replay cache acceleration

Redis must not be the only durable source for identities, ledgers or audit events.

## 9.3 MinIO or S3

Use for:

- Models
- Checkpoints
- Personalized models
- Dataset manifests
- Result artifacts
- Reports
- Benchmark artifacts

Required:

- Versioning
- Encryption at rest
- Retention
- Checksums
- Signed references
- Lifecycle rules

## 9.4 Enterprise authentication

Implement:

- Password hashing
- Access tokens
- Refresh tokens
- Token rotation
- Session invalidation
- API keys
- Service accounts
- OIDC-ready design
- Project-level RBAC
- Fine-grained security permissions

## 9.5 Production API behavior

Implement:

- Pagination
- Filtering
- Stable error model
- Idempotency
- Rate limits
- Request limits
- API versioning
- OpenAPI
- Audit coverage

---

# 10. Observability and Operations — Remaining

## 10.1 Metrics

Complete Prometheus metrics for:

- API
- Coordinator
- Workers
- Security
- Privacy
- Secure aggregation
- Scheduling
- Storage
- Checkpointing
- GPU
- CPU
- Memory
- Network

Avoid high-cardinality labels.

## 10.2 Grafana

Create dashboards for:

- Platform overview
- Runs
- Workers
- Client fleet
- Privacy
- Secure aggregation
- Security
- API
- Storage
- GPU/CPU
- Alerts

## 10.3 OpenTelemetry

Implement:

- Distributed tracing
- Trace propagation
- Run ID
- Round ID
- Worker ID
- Task ID
- Model version
- Security event correlation

## 10.4 Centralized logs

Implement:

- Structured JSON logs
- Sensitive-field redaction
- Retention
- Search
- Run correlation
- Security correlation
- Privacy correlation

## 10.5 Alerting

Implement alerts for:

- Coordinator down
- Worker loss
- Run stuck
- Privacy budget warning
- Privacy exhaustion
- Certificate expiry
- Invalid signature
- Replay attack
- Secure aggregation abort
- Storage full
- Database failure
- Redis failure
- GPU OOM
- High API latency

## 10.6 Operational objectives

Define:

- SLOs
- Error budgets
- Availability targets
- Latency targets
- Recovery time objective
- Recovery point objective

---

# 11. Production Hardening — Remaining

## 11.1 High availability

Implement:

- Coordinator leader election
- Failover
- Graceful shutdown
- Rolling restart
- Run ownership
- Duplicate aggregation prevention
- Safe session recovery

## 11.2 Disaster recovery

Implement:

- Backup policies
- Restore procedures
- Restore drills
- Database recovery
- Object-storage recovery
- Key recovery
- Certificate recovery
- Checkpoint validation

## 11.3 Failure handling

Test:

- Database outage
- Redis outage
- MinIO outage
- Network partition
- Disk full
- Memory pressure
- GPU OOM
- Coordinator crash
- Worker crash
- Clock skew
- Certificate expiry
- Replay-store corruption
- Checkpoint corruption

## 11.4 Security hardening

Implement:

- Secrets manager
- Certificate automation
- Key rotation automation
- Image signing
- SBOM
- Dependency scanning
- Container scanning
- Secret scanning
- Artifact provenance
- Security headers
- CORS
- CSRF
- Rate limiting
- Least privilege
- Network policies

## 11.5 Independent review

Required:

- Cryptographic review
- Privacy review
- Penetration test
- Threat-model review
- Architecture review
- Dependency review
- License review

## 11.6 Release engineering

Implement:

- Protected branches
- Required checks
- Versioned releases
- Semantic versioning
- Changelog
- Signed artifacts
- Staging
- Approval flow
- Rollback
- Release notes
- Upgrade guides

---

# 12. Readiness Levels

## 12.1 Research-ready

Requirements:

- Message authenticity complete
- Replay protection complete
- Key lifecycle complete
- Worker revocation complete
- Full authenticated Docker regression
- Stable experiment presets
- Stable documentation
- Versioned research release

Trusted coordinator is acceptable when clearly documented.

## 12.2 Internal pilot-ready

Requirements:

- Research-ready requirements
- Secure aggregation or explicit trusted-coordinator approval
- PostgreSQL
- Redis
- MinIO/S3
- Backup and restore
- Production mTLS
- Monitoring and alerts
- Security administration
- Staging deployment
- Small external worker fleet test

## 12.3 Production-ready

Requirements:

- Internal pilot requirements
- Fully validated secure aggregation
- Distributed execution
- High availability
- Disaster recovery
- Production Kubernetes
- Independent cryptographic review
- Penetration test
- Privacy review
- Load testing
- Soak testing
- Chaos testing
- Incident response
- Signed releases
- SBOM
- Production secrets management
- Formal launch checklist

---

# 13. Immediate Execution Order

The recommended implementation order is:

```text
Message Authenticity Enforcement and Identity Lifecycle
→ Secure Aggregation Protocol
→ Distributed Execution
→ Enterprise Platform
→ Observability and Operations
→ Production Hardening
→ Independent Security and Privacy Review
→ Research or Production Release
```

Immediate priority:

1. RPC security policy
2. Signed worker envelopes
3. Signed coordinator tasks
4. Payload hashing
5. Replay protection
6. Sequence validation
7. Signing-key rotation
8. Signing-key revocation
9. Worker suspension
10. Worker activation
11. Worker revocation
12. Certificate fingerprint revocation
13. Go security APIs
14. Web security administration
15. Security events and metrics
16. Full authenticated Docker validation

---

# 14. Critical Blockers

## 14.1 Threshold secret sharing

Status: Blocked pending vetted dependency selection.

Impact:

- Secure dropout recovery cannot be safely completed.
- Complete secure aggregation cannot be claimed.
- Custom cryptography must not be introduced as a shortcut.

## 14.2 Worker honesty

Current authenticated worker identity and signed claims do not prove:

- The expected software is running
- DP training was performed honestly
- Clipping was applied correctly
- The dataset was used correctly
- The worker is not malicious

Remote attestation or verifiable computation is deferred.

## 14.3 Secure user clipping

When secure aggregation is enabled:

- Coordinator cannot clip individual updates.
- Workers must clip locally.
- Without verifiable clipping, honest-client behavior is assumed.
- Malicious-client user-level DP is not claimed.

## 14.4 Large uncommitted working tree

Current repository contains substantial uncommitted changes.

Before any release:

- Run `git status`
- Review all tracked changes
- Review all untracked files
- Remove temporary artifacts
- Run all tests
- Create a review branch
- Make clear category-based commits
- Open a reviewable pull request
- Tag only after validation

---

# 15. Cross-Cutting Validation Requirements

Every category must preserve:

- C++ Debug build
- C++ Release build
- CTest
- Python pytest
- Ruff
- mypy
- Go fmt
- Go vet
- Go tests
- Go race tests where supported
- Next.js lint
- Next.js typecheck
- Web tests
- Next.js production build
- Protobuf generation
- Protobuf compatibility
- Terminology check
- Docker Compose config
- Docker builds
- Docker runtime
- Clean teardown

Security-related work must additionally preserve:

- mTLS tests
- Certificate identity tests
- Signature tests
- Tamper tests
- Expiry tests
- Replay tests
- Rotation tests
- Revocation tests
- Secret scanning

Privacy-related work must additionally preserve:

- Sample accountant tests
- User accountant tests
- Adaptive clipping tests
- Hybrid privacy tests
- Privacy checkpoint recovery
- Separate ledger validation
- No combined epsilon

---

# 16. Documentation Policy

Documentation must use category-based names.

Preferred prefixes:

```text
foundation-
aggregation-core-
coordinator-runtime-
algorithm-expansion-
privacy-engineering-
secure-aggregation-
distributed-execution-
enterprise-platform-
observability-operations-
production-hardening-
```

Documentation must distinguish:

- Implemented
- Validated
- Experimental
- Blocked
- Deferred

No feature may be described as complete unless a real build or passing test supports the claim.

---

# 17. Security and Privacy Rules That Must Not Be Broken

1. Never log raw client updates.
2. Never log privacy noise.
3. Never expose private keys.
4. Never commit generated private keys.
5. Never expose raw replay nonces.
6. Never expose pairwise secrets.
7. Never expose secret shares.
8. Never route large model tensors through Go JSON APIs.
9. Never combine sample and user epsilon into one total.
10. Never silently fall back from private to non-private training.
11. Never silently fall back from secure to insecure transport.
12. Never silently fall back from unknown algorithms to FedAvg.
13. Never implement custom cryptographic primitives without review.
14. Never claim signed capabilities are attestation.
15. Never claim mTLS is secure aggregation.
16. Never claim trusted-coordinator user-level DP hides updates from the coordinator.
17. Never claim malicious-client clipping security without verification.
18. Never claim production readiness without full operational and security validation.

---

# 18. Final Current-State Verdict

The project is currently:

```text
Advanced distributed federated-learning research platform
with real C++ coordination, Python training, Go control APIs,
web monitoring, personalization, differential privacy,
secure randomness, mutual TLS, certificate identity binding,
persistent worker identities, and signed worker capabilities.
```

The project is not yet:

```text
A fully secure, horizontally scalable, highly available,
production-ready federated learning platform.
```

The most important remaining work is:

```text
Signed message authenticity
→ Replay protection
→ Identity and key lifecycle
→ Secure aggregation
→ Distributed scale
→ Durable enterprise infrastructure
→ Production operations and hardening
```

This document is the authoritative project plan until replaced by a newer reviewed version.
