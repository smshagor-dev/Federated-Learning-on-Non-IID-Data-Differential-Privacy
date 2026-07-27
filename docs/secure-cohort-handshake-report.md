# Secure Cohort Handshake and Signed Roster Runtime — Completion Report

**Status: the handshake is real, live, and validated end to end through
`READY_FOR_MASKED_TRAINING` — a real coordinator-to-worker protocol run
over mTLS with real Ed25519 signing, verified against a real three-worker
Docker Compose stack. Masked model-update submission and secure
aggregate finalization remain explicitly out of scope and unimplemented,
per this slice's own mandatory constraint.** See
[secure-cohort-handshake-foundation.md](secure-cohort-handshake-foundation.md)
for the design decisions this report evaluates against, and
[known-limitations.md](known-limitations.md)'s "Secure Cohort Handshake
and Signed Roster Runtime slice" section for the full itemized gap and
bug list this report summarizes.

Every one of the 20 in-scope work items is evaluated below as
Implemented/Validated, Bounded, or explicitly out of scope — none is
silently skipped.

## Items 1–3: Coordinator ownership, safe persistence, session creation before task issuance — Implemented and validated

`SecureAggregationSessionManager` is constructed once in `main.cpp` and
injected into `CoordinatorServiceImpl`. A new `SecureAggregationSessionStore`
(`secure_aggregation_session_store.{hpp,cpp}`) persists only
`session_id, run_id, round_id, state, created_at, completed_at,
abort_reason, failure_reason` — never key material, never masked
values — using the exact tab-separated/`record_count=`/FNV-1a-`checksum=`/
atomic-write pattern `WorkerIdentityRegistry` already established.
Session creation was originally planned inside `RunInstance::begin_round()`
but **moved to `CoordinatorServiceImpl::AcquireTask`** after discovering
mid-implementation that `SecureAggregationSessionManager` requires real
generated protobuf types and is therefore gRPC-gated, while
`run_manager.hpp`/`.cpp` must stay buildable without any protobuf
headers (a genuine architectural conflict, caught before writing the
code that would have caused it — see the design doc's "Mid-implementation
correction" section). The corrected design needed **zero changes** to
`run_manager.hpp`/`.cpp`/`RunConfig`: `AcquireTask` creates a session
lazily, once per `(run_id, round_id)`, using
`RunInstance::round_snapshot(round_id).selected_clients` as the real
cohort, gated on a new coordinator-wide `FL_SECURE_AGGREGATION_ENABLED`
env var (not a per-run wire-configurable flag — a deliberate, disclosed
scope-narrowing decision to avoid touching `CreateRunRequest`/
`experiment.proto`).

## Item 4: Secure task binding — Implemented and validated

New `SecureAggregationTaskBinding` message, new
`ClientTrainingTask.secure_aggregation` field (19, additive), new
`SignedCoordinatorTask.secure_aggregation_configuration_hash` field (18,
additive). Populated in `AcquireTask` via a new manager query,
`find_binding_for_participant(run_id, round_id, worker_id)`, which only
returns a binding while the session is still forming (never for an
already-frozen/completed/aborted session). Folded into the real signed
bytes (`coordinator_task_signing_bytes`) as a sibling hash, matching
`personalization_configuration_hash`'s existing "never folded into
`task_payload_hash`" precedent — locked in by a new
`test_tampered_secure_aggregation_hash_invalidates_signature` test.

## Item 5: Python secure-task verification — Implemented and validated

New check in `verify_coordinator_task` (`coordinator_task_verifier.py`):
recomputes `secure_aggregation_configuration_hash` from the received
task fields and compares against the signed value, raising
`CoordinatorTaskRejectedError(SECURE_AGGREGATION_BINDING_MISMATCH)` on
mismatch — same position/pattern as every other configuration-hash
check in that pipeline. New `SecureAggregationConfigurationHashTests`
class (5 tests: deterministic-when-inactive, active-binding-changes-hash,
tamper-detection, does-not-affect-`task_payload_hash`,
rejects-non-finite-deadline) and a dedicated
`test_secure_aggregation_binding_mismatch_rejected` verifier test.

## Items 6–7: Fresh worker key generation and signed key-advertisement construction — Implemented and validated

New `python/src/fl_platform/secure_aggregation/key_advertisement.py`:
`generate_ephemeral_keypair()` reuses the prior slice's real, tested
`generate_x25519_keypair()` directly (no new key-generation code).
`build_signed_key_advertisement()` builds a `SecureAggregationKeyAdvertisement`,
computes its payload hash via a new
`secure_aggregation_key_advertisement_payload_hash_input()` function
(byte-for-byte mirror of the C++ side), and signs it with the worker's
real `WorkerSigningIdentity`, wrapped in a `SignedWorkerEnvelope`
(`MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT`,
`MESSAGE_STREAM_SECURE_AGGREGATION`). 9 tests in
`test_secure_cohort_handshake_key_advertisement.py` plus a
`KeyAdvertisementHashTests` class in `test_signed_envelope.py`.

## Item 8: Live `AdvertiseSecureAggregationKey` RPC — Implemented and validated

Full SIGNED_WORKER_MESSAGE pipeline, replicated from the
`Heartbeat`/`RotateWorkerSigningKey` precedent: mTLS peer check →
signed envelope required → `WorkerIdentityRegistry` lookup/status →
`resolve_signing_key` (new `SignedMessageKind::kSecureAggregationKeyAdvertisement`,
ACTIVE/GRACE_PERIOD permitted) → payload-hash recompute → Ed25519
verify → `ReplayCandidate` on `MessageStream::kSecureAggregation` →
domain call (`manager->advertise_key`) → replay commit only after
domain success → `SecurityEventType::kSecureAggregationKeyAdvertisementAccepted`/
`kSecureAggregationKeyAdvertisementRejected` emission. Live-validated:
all three workers in the Docker run had their advertisement accepted.

## Item 9: Complete-cohort freeze — Implemented and validated

Automatic, inside item 8's handler: the moment an accepted
advertisement brings `key_advertisement_count` to `cohort_size`, the
handler immediately calls `freeze_cohort()`, wrapped in try/catch so a
freeze failure never un-accepts an already-accepted advertisement.
Live-validated: the Docker run's coordinator froze the complete
three-worker cohort.

## Item 10: Coordinator-signed frozen roster — Implemented and validated

`freeze_cohort()` gained an optional `const CoordinatorSigningIdentity*`
parameter; when provided (always, from the live RPC handler using
`coordinator_active_identity_->current()`), computes real canonical
roster bytes via a new domain-separated
`compute_frozen_cohort_roster_signing_bytes()` helper, a real
`sha256_hex` payload hash, and a real Ed25519 signature via
`sign_with_coordinator_identity`. Tested: 128-hex signature, 64-hex
payload hash, correct key_id.

## Item 11: Live `GetFrozenCohortRoster` RPC — Implemented and validated

mTLS check, requires a configured manager, returns
`available=false` (not an error) before freeze or for an unknown
session — the roster is never exposed before freeze. Checks the
requesting `worker_id` is among the roster's participants.
Live-validated: all three workers successfully retrieved the roster.

## Item 12: Python frozen-roster verification — Implemented and validated

`verify_frozen_cohort_roster()` in `key_advertisement.py`: session/run/
round/model_version binding check, unsigned-roster rejection,
independent Ed25519 signature verification (PyNaCl) against the
trusted coordinator key bundle, own-participant-entry match (worker_id,
client_id, and — critically — that the public key on the roster
matches what this worker actually advertised), duplicate-worker-id
detection across all participants, invalid-length/all-zero peer-public-key
detection. Duck-typed via a `FrozenCohortRosterLike` structural
`Protocol` (not `object` — refined during this slice's own mypy-strict
pass) so this module has no import-time dependency on generated
grpc/protobuf bindings. Live-validated: all three workers independently
verified the real coordinator-signed roster and accepted it.

## Items 13–14: Read-only session RPCs and administrative abort — Implemented and validated

`GetSecureAggregationSession`/`ListSecureAggregationSessions` are thin
wrappers over `manager->find()`/`manager->list()` (the latter supports
`run_id`/`state_filter`, pagination fields accepted but not yet
implemented — documented, not silently dropped). `AbortSecureAggregationSession`
is ADMIN_CONTROL-gated via the same `reject_if_not_go_api_service_identity`/
`emit_permission_denied_event` pattern as `SuspendWorker`/`RevokeWorker`.

## Item 15: Deadlines — Implemented and validated

Already enforced inside `advertise_key()`/`submit_masked_update()`
(prior slice). This slice adds `sweep_expired_advertisement_deadlines()`,
called unconditionally at the top of every `AcquireTask` whenever a
manager is configured — not from `RunInstance::advance()` (the
originally-planned hook, superseded by the same architectural
correction as items 1–3) — aborting any session past its
`key_advertisement_deadline_unix_s` with an incomplete cohort. Tested:
no-op-before-deadline, aborts-with-`DEADLINE_EXCEEDED`-after,
never-sweeps-an-already-frozen session.

## Item 16: Restart abort behavior — Implemented and validated

`SecureAggregationSessionStore::reconcile_after_restart(now)`: a
log-level operation on the persisted store, not a live
`CohortStateMachine` transition — the in-memory manager starts empty on
every restart, so there is nothing live to abort against. Scans every
persisted record and rewrites any non-terminal one directly to
`ABORTED`/`kCoordinatorRestart`. Called once from `main.cpp` at
startup, emitting `SecurityEventType::kSecureAggregationRestartAborted`
per reconciled session. Tested: marks non-terminal records, leaves
terminal ones untouched, idempotent, persists durably, survives a
simulated restart (fresh store instance over the same file).

## Item 17: Minimal events and metrics — Implemented and validated

Six new `SecurityEventType` values
(`kSecureAggregationSessionCreated/CohortFrozen/
KeyAdvertisementAccepted/KeyAdvertisementRejected/SessionAborted/
RestartAborted`) plus a new `SecuritySubjectType::kSecureAggregationSession`,
emitted from every real call site listed above. No new Prometheus
metric this pass — Go/web are explicitly out of scope, and a
C++-native metrics endpoint remains the same disallowed new dependency
documented in every prior slice; the events themselves (journaled,
queryable via the existing `ListSecurityEvents` RPC) are this item's
"minimal" observability surface, as scoped in the design doc.

## Item 18: Three-worker Docker handshake validation — Implemented and validated (live, real evidence)

`infra/compose/docker-compose.secure-cohort-handshake.yml` (a new
override, layered on `docker-compose.dev.yml` +
`docker-compose.security.yml`) parameterizes the worker topology to
three real `python-worker`-family services (`python-worker`/worker-1,
`worker-2`, `worker-3`), each with its own issued mTLS certificate
(`scripts/pki/issue-worker-cert.sh worker-2`/`worker-3`) and persistent
signing identity, plus a shared `coordinator-trust-bundle` named volume
(a real, previously-undisclosed gap this slice closes — see below) and
`FL_SECURE_AGGREGATION_ENABLED=true` on the coordinator.
`scripts/validate_secure_cohort_handshake.py` drives the whole flow:
brings up postgres/redis/coordinator/api, creates and starts a real
3-client `fedavg` run via the Go HTTP API, brings up all three workers,
then polls each worker's own container logs for the real
`_perform_secure_cohort_handshake` completion/failure marker — the same
unmodified production code path, not a test-only hook.

**Result: 7/7 checks passed.** All three workers independently
generated fresh ephemeral X25519 keys, had their signed advertisements
accepted, observed the coordinator freeze the complete three-worker
cohort, retrieved and independently verified the coordinator-signed
frozen roster, and logged reaching `READY_FOR_MASKED_TRAINING`. Full
log evidence (all three workers' `secure cohort handshake complete`
lines, the coordinator's `RUN_CREATED`/`RUN_VALIDATED`/`RUN_STARTED`
events) captured during this session.

Getting to a real, passing run surfaced four genuine, previously-latent
bugs unrelated to secure aggregation's own logic — all fixed, all
documented in known-limitations.md's new section:

1. `scripts/generate_protos.sh` (the repo's only tracked shell script)
   checked out with CRLF line endings on this Windows machine's default
   `core.autocrlf=true` git config, breaking `bash scripts/generate_protos.sh`
   inside every Docker build that runs it. Fixed with a new
   `.gitattributes` (`*.sh text eol=lf`).
2. The `cpp-grpc` CI job's `ctest` step, as previously written, would
   fail in real GitHub Actions CI regardless of this slice — it ran
   against every registered test in the build tree, not just the
   handful actually built by that job's `--target` list. Reproduced
   live in a throwaway Docker container before fixing it.
3. No compose file shared the coordinator's signed public-key bundle
   with any worker container (each worker needs it on disk to verify a
   signed task or frozen roster) — closed with a shared named volume.
4. `docker compose up -d --build <services>` rebuilds and silently
   recreates the *entire dependency graph* of the named services
   (including already-running, stateful dependencies like
   `coordinator`), not just the named services — discovered when a
   just-created run vanished after bringing workers up in a second
   `--build up` call. Worked around by building every image exactly
   once, up front.

One additional real gap was found and **deliberately left unfixed**,
disclosed rather than silently patched: `WorkerService.run()`'s
`acquire_task()` call site does not catch the general
`CoordinatorRejectedError` (only the two more specific subclasses),
so an `AcquireTask` rejection like "unknown run_id" crashes the worker
process instead of retrying. Fixing this touches this project's
established worker main-loop robustness contract, which is outside
this slice's 20-item scope — worked around at the validation-script
level (sequencing: infra → create/start run → workers) instead.

## Item 19: CI coverage — Implemented and validated

`.github/workflows/ci.yml`'s `cpp-grpc` job now builds and tests
`fl_secure_aggregation_crypto_tests`, `fl_secure_aggregation_tensor_mask_tests`,
and `fl_secure_aggregation_session_manager_tests` (the first two existed
since the prior slice but were never in this job's target list — also
closed here), plus the `-R` test-filter fix described above. Verified
by reproducing the exact CI job commands in a throwaway Docker
container mirroring `ubuntu-latest`: 8/8 gRPC-gated test suites pass,
0 unrelated "Not Run" failures.

## Item 20: Documentation — Implemented

This report, the corrected
[secure-cohort-handshake-foundation.md](secure-cohort-handshake-foundation.md)
(two stale scope-decision entries fixed to match the mid-implementation
correction), a new section in
[known-limitations.md](known-limitations.md), and a `plan.md` status
update.

## Mandatory Security Boundary — Unchanged, honestly restated

Provider remains `SECAGG_NO_DROPOUT_EXPERIMENTAL`. Still Experimental /
Synchronous / Fixed-cohort / No-dropout / Honest-client-dependent /
Research-oriented. Still NOT Dropout-resilient / Malicious-client-secure
/ Byzantine-robust / Verifiable-clipping / Production-secure-aggregation
/ Complete-Bonawitz / Complete-SecAgg+ / Threshold-recovery-capable.
Nothing in this slice changes any of these facts — the handshake being
real and live does not make the underlying protocol's security
properties any stronger than the prior slice's threat model already
states.

## Threshold Secret-Sharing Restriction — Unchanged

No vetted threshold secret-sharing dependency was selected or used.
No custom Shamir secret sharing, finite-field share interpolation,
secret-share distribution, recovery-share exchange, or private-mask
reconstruction was implemented anywhere in this slice. A participant
lost after cohort freeze still cannot be recovered from — the only
Tier-1 behavior this slice adds here is what already existed
(deadline/abort), never a reconstruction path.

## Regression evidence (fresh, this session)

```
python scripts/check_project_terminology.py        # pass
python scripts/verify_proto_contracts.py            # pass (proto changes additive only)
python -m pytest python/tests -q
  # 349 passed, 6 skipped (up from 338 before this slice's own test additions;
  # excludes test_worker_entrypoint_wiring.py/test_worker_transport.py/
  # test_grpc_coordinator_client.py, which require grpc/protobuf packages
  # this local venv does not install -- Docker/CI-only, pre-existing
  # project constraint, unrelated to this slice)
ruff check <every touched Python file>               # all clean
mypy <every touched Python source file>               # clean (pre-existing,
  # unrelated federated.*/scipy-stubs errors in transitively-imported
  # legacy modules excluded, confirmed unrelated to this slice's changes)
docker build + cmake --build (gRPC-gated target list incl. this slice's
  new fl_secure_aggregation_session_manager_tests) && ctest -R '<8 targets>'
  # 8/8 suites pass, 0 warnings (after fixing 2 nodiscard warnings in
  # this slice's own test additions)
docker compose (3-worker validation) -- 7/7 checks pass, real evidence
  captured (see item 18 above)
```

**Nothing committed, pushed, tagged, or opened as a pull request**, per
this session's ongoing instruction. `certs/dev/workers/worker-3/` was
newly issued (private key git-ignored, matching worker-1/worker-2's
existing precedent).

## Recommendations

- **Recommended next slice**: masked model-update submission end to
  end — the natural continuation now that the handshake reliably
  reaches `READY_FOR_MASKED_TRAINING`. Requires, at minimum: production
  worker tensor/weight masking (reusing the prior slice's tested
  `mask_tensor`/`derive_weight_mask`), a real `SubmitMaskedClientUpdate`
  handler (mirroring this slice's `AdvertiseSecureAggregationKey`
  verification-pipeline pattern), and secure aggregate finalization
  wired into a live round's model-version advance.
- **Recommended before that**: fix the disclosed
  `CoordinatorRejectedError` worker-crash gap in `WorkerService.run()`
  — a production worker restarting into a not-yet-created run should
  not need this session's script-level sequencing workaround to avoid
  crash-looping.
- **Not recommended yet**: dropout recovery or threshold secret
  sharing — unchanged from the prior slice's own recommendation, still
  blocked on a vetted dependency selection.
