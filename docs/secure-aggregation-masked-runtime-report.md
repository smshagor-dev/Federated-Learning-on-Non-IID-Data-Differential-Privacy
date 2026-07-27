# Masked Update Runtime and No-Dropout Secure FedAvg Finalization — Completion Report

See [secure-aggregation-masked-runtime-audit.md](secure-aggregation-masked-runtime-audit.md)
for the pre-implementation audit, the RunManager/gRPC-gating
architectural decision, the corrected weighting order, and the full
Work Area A → AR scope statement this report evaluates against.
`SECAGG_NO_DROPOUT_EXPERIMENTAL` is the provider throughout — nothing
in this slice implies complete, dropout-resilient, malicious-client-
secure, or production-ready secure aggregation. The Mandatory Security
Boundary and Threshold Secret-Sharing Restriction from the audit doc
remain unchanged and fully in force.

## What this slice makes real

The prior slice (Secure Cohort Handshake and Signed Roster Runtime)
proved the handshake alone — a session forms, workers advertise keys,
the coordinator freezes and signs a roster, workers verify it and
reach `READY_FOR_MASKED_TRAINING`. None of that ever touched the
model. This slice is the one that does:

- **Worker-side masked update construction**
  (`python/src/fl_platform/secure_aggregation/masked_update.py`):
  client-weight and local-delta validation, weighted fixed-point
  encoding (corrected order — see the audit doc's "Critical
  mathematical correction" section), canonical mask-context derivation,
  pairwise tensor/weight masking via the existing X25519/HKDF/ChaCha20
  primitives, and signed `MaskedClientUpdate` construction — wired into
  `WorkerService.run()` so a secure-bound task takes this path
  *structurally*, never the cleartext one (Work Area P).
- **Live `SubmitMaskedClientUpdate` RPC**
  (`coordinator_service.cpp`): the full signed-message pipeline
  (mTLS identity, envelope required, identity/status, signing-key
  resolution, payload-hash recompute, Ed25519 verify, replay/sequence
  validation, domain call, replay commit only after domain success,
  security-event emission) mirroring `AdvertiseSecureAggregationKey`'s
  already-proven shape exactly. On the cohort's last contribution, it
  synchronously calls `SecureAggregationSessionManager::finalize()` and
  bridges the decoded `fl::core::AggregationResult` into the live round
  via a new, narrow, protobuf-free-safe `RunInstance::apply_secure_aggregate_and_advance`
  method (mirrors only `finalize_round()`'s model-advance + checkpoint
  tail, not a refactor of that function).
- **Masked-update deadline sweep** (Work Area S):
  `sweep_expired_masked_update_deadlines`, the collection-phase
  analogue of the prior slice's `sweep_expired_advertisement_deadlines`,
  wired into the same `AcquireTask` sweep point. Correctly skips a
  session whose complete cohort has already submitted and is merely
  awaiting `finalize()` (a real bug caught by this slice's own new
  test — see below).
- **Cleartext prohibition, coordinator-enforced** (Work Area P):
  `SubmitClientResult` now rejects any cleartext submission for a
  run/round bound to a secure aggregation session, via a new
  `SecureAggregationSessionManager::find_status_for_run_round`
  accessor — with the one deliberate, disclosed exception of a session
  aborted for `PRIVACY_MODE_INCOMPATIBLE` (Work Areas Z/AB), which is
  the coordinator's own decision to fall back to ordinary unmasked
  training, not a worker bypassing masking.
- **Algorithm/privacy-mode compatibility gating** (Work Areas Z/AB):
  decided once per session at creation time in `AcquireTask`, stored on
  `SecureAggregationSessionConfig`/`SecureAggregationTaskBinding`
  (`privacy_mode_compatible`, `privacy_incompatibility_reason`).
  Only `fedavg` with no privacy mode or `SAMPLE_LEVEL` DP is
  compatible; `USER_LEVEL`/`HYBRID` DP and adaptive clipping are
  rejected with a structured reason and an immediate session abort
  (`SECURE_AGGREGATION_ABORT_REASON_PRIVACY_MODE_INCOMPATIBLE`),
  observable via a `kSecureAggregationSessionAborted` security event.
- **Five new `SecurityEventType` values**
  (`kSecureAggregationMaskedUpdateAccepted/Rejected`,
  `kSecureAggregationCompleteCohortReceived`,
  `kSecureAggregationSessionCompleted`,
  `kSecureAggregationAggregateValidationFailed`), on top of the six the
  prior slice added — a representative, not exhaustive, event surface
  per this project's established scope-narrowing convention. Round-
  level observability (`AGGREGATION_COMPLETED`/`MODEL_VERSION_UPDATED`/
  `CHECKPOINT_COMPLETED`/`RUN_COMPLETED`) is inherited for free from
  the existing `CoordinatorEventType` vocabulary via
  `apply_secure_aggregate_and_advance` — no new event types were needed
  there. Go/web observability and native Prometheus metrics remain
  deferred, matching the audit doc's own AG/AH deferral and the
  Privacy Engineering phase's established "no native C++ Prometheus
  endpoint" precedent.

## Two real bugs found and fixed by this slice's own testing

Both were caught by tests/validation this slice wrote, not discovered
later — the working method this project has followed throughout.

1. **`sweep_expired_masked_update_deadlines` could abort a session
   whose complete cohort had already submitted**, just because the
   sweep ran before `finalize()` did. Caught by a new C++ test
   (`secure_aggregation_session_manager_test.cpp`) asserting a complete-
   but-not-yet-finalized session is never swept. Fixed by skipping any
   session where `contributions_by_worker.size() >= cohort_size()`.
2. **Masked-update submissions were rejected as replays of the key
   advertisement.** `AdvertiseSecureAggregationKey` and
   `SubmitMaskedClientUpdate` both validated their `ReplayCandidate`
   against the single shared `MessageStream::kSecureAggregation` track,
   while the worker's own `SequenceStateStore` already tracks them as
   two independent local counters
   (`"secure_aggregation_key_advertisement"` vs
   `"secure_aggregation_masked_update"`) — so a worker's first-ever
   masked-update submission (local sequence 1) collided with its
   already-committed key advertisement (also sequence 1 on the shared
   coordinator-side track) and was rejected with
   `sequence_number equals the last accepted sequence for this track`.
   Found live, by the first real run of the 3-worker Docker validation
   below — not by any unit test, since no unit test previously drove
   both message types against one shared `ReplayProtectionStore`
   instance. Fixed by adding a new, independent
   `MessageStream::kSecureAggregationMaskedUpdate` track (the
   `MessageStream` enum's own doc comment, written by the *prior*
   slice, already called for "key advertisements and masked-update
   submissions get their own independent sequence track" — this slice
   is what finally acts on that). A new regression test in
   `replay_protection_store_test.cpp` pins the exact scenario: sequence
   1 on `kSecureAggregation` and sequence 1 on
   `kSecureAggregationMaskedUpdate`, for the same worker/key, are both
   independently accepted.

## Live validation

`scripts/validate_masked_update_runtime.py` +
`infra/compose/docker-compose.masked-update-runtime.yml` (a thin
`FL_WORKER_RUN_ID` override on top of the prior slice's
`docker-compose.secure-cohort-handshake.yml`, same mTLS/signing/trust-
bundle setup, real containers, real PyTorch training). Brings up a
real coordinator + Go API + 3 python-worker containers, creates and
starts a single-round 3-client FedAvg run with
`FL_SECURE_AGGREGATION_ENABLED=true`, and asserts the round completes
through the masked path end to end — not the handshake alone.

**15/15 checks passed** on the run that incorporates both bugfixes
above (the first live run, pre-fix, correctly caught bug #2 and failed
5/15; a validation-script field-name mistake — `status` vs the real
`state` field on `GET .../runs/{runId}` — caused one further spurious
failure on the second run, fixed in the script, third run clean):

- all three workers independently reach `READY_FOR_MASKED_TRAINING`
  (re-confirming the prior slice's already-proven handshake as a
  precondition, not re-proving it),
- all three workers locally train, encode, mask, sign, and submit a
  `MaskedClientUpdate` the coordinator accepts,
- the run reaches `COMPLETED` and `model_version` genuinely advances
  `v0 → v1` after exactly one round — confirmed via the same REST
  polling this project has used for every prior live FedAvg validation,
  not by trusting worker log claims alone,
- the coordinator's own structured event log independently confirms
  the same story: `AGGREGATION_COMPLETED → MODEL_VERSION_UPDATED (v1)
  → CHECKPOINT_COMPLETED → RUN_COMPLETED`, all emitted from
  `apply_secure_aggregate_and_advance`'s bridge into the ordinary round
  lifecycle,
- no worker ever fell back to the cleartext `ClientResult` path
  (checked as independent evidence on top of the structural guarantee
  in `WorkerService.run()`'s branching).

## Full regression, final numbers

- **C++, protobuf-free (local Windows/MSVC, no Docker needed)**:
  `ctest --test-dir build/cpp-debug -C Debug` — 7/7 suites passed
  (`fl_core_smoke`, `fl_aggregator_golden`, `fl_validation_tests`,
  `fl_checkpoint_tests`, `fl_privacy_tests`, `fl_secure_random_tests`,
  `fl_coordinator_tests` — the last including the new
  `apply_secure_aggregate_and_advance` tests in `run_manager_test.cpp`
  and the new replay-track-collision regression test).
- **C++, gRPC-gated (Docker, mirroring the CI `cpp-grpc` job exactly)**:
  8/8 test executables passed (`fl_coordinator_grpc_tests`,
  `fl_signed_envelope_verifier_tests`,
  `fl_capability_statement_verifier_tests`,
  `fl_coordinator_task_signing_tests`, `fl_peer_identity_tests`,
  `fl_secure_aggregation_crypto_tests`,
  `fl_secure_aggregation_tensor_mask_tests`,
  `fl_secure_aggregation_session_manager_tests` — the last including
  the new masked-update-deadline-sweep and `find_status_for_run_round`
  tests). No CI workflow changes were needed — both new-test-bearing
  files land in targets the `cpp-debug`/`cpp-release`/`cpp-grpc` jobs
  already build and run.
- **Python**: `python -m pytest python/tests` — 413 passed, 1 skipped
  (unchanged skip count from before this slice), run against
  Python protobuf/gRPC bindings regenerated locally via
  `python -m grpc_tools.protoc` (bundles its own protoc — no system
  `protoc`/Docker needed for this step) to pick up every proto field
  this slice and its predecessor added.
- **Live 3-worker Docker validation**: 15/15, described above.

## What remains bounded or deferred (honest, per the audit doc's own scope statement)

Consistent with — not a retreat from — the audit doc's pre-declared
scope split:

- **Bounded, not exhaustive**: event/metric coverage (5 new types, not
  every conceivable masked-update event); C++ test coverage targets
  the new session-manager/replay-store/run-manager logic directly, not
  a full second live-RPC gRPC test harness for
  `SubmitMaskedClientUpdate` itself (matching the precedent the prior
  slice already set — `AdvertiseSecureAggregationKey` has no dedicated
  `coordinator_service_test.cpp` coverage either; RPC-level correctness
  is proven live, by Docker validation, for both).
- **Deferred, disclosed**: Go read-only APIs and web secure-aggregation
  observability (Work Areas AG/AH — no HTTP/UI surface for session
  state exists, matching this slice's own audit-doc deferral);
  performance benchmarking (Work Area AO); the 62-scenario validation
  matrix (this report's 15 live checks plus the C++/Python unit and
  integration suites are the real, load-bearing evidence instead);
  artifact-sanitation additions beyond what already exists.
- **Explicitly out of scope, unchanged from every prior secure-
  aggregation slice**: threshold secret sharing, dropout recovery,
  partial-cohort finalization, Byzantine-robust aggregation, ZK proofs,
  verifiable clipping, attestation, TEE/TPM, homomorphic encryption,
  Ray/Flower, production Kubernetes, independent cryptographic review,
  independent penetration testing.

See [known-limitations.md](known-limitations.md)'s "Masked Update
Runtime and No-Dropout Secure FedAvg Finalization slice" section for
the itemized gap list in that document's standard format, and
[plan.md](plan.md) for the corresponding status-update entry against
§7.1's protocol requirements.
