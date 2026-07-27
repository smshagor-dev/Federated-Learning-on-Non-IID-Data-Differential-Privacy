# Secure Hybrid Differential Privacy Runtime — Completion Report

See [secure-hybrid-dp-runtime-audit.md](secure-hybrid-dp-runtime-audit.md)
for the pre-implementation audit and scope statement, and
[secure-hybrid-dp-semantics.md](secure-hybrid-dp-semantics.md) for the
full mechanism specification (execution order, dual-budget/publication-
boundary semantics, the "two epsilons, never combined" rule, the
mandatory trust boundary). `SECAGG_NO_DROPOUT_EXPERIMENTAL` remains the
provider throughout — nothing in this slice claims coordinator
recomputation of sample-level epsilon, cryptographic verification of
Opacus execution or whole-update clipping, malicious-client security,
dropout resilience, a formal end-to-end privacy proof, or a single
combined epsilon across the two privacy units. **This is layered,
honest-client-dependent, experimental hybrid privacy: a sample-level
guarantee and a user-level guarantee, reported and accounted
separately.**

## What this slice makes real

Two mechanisms already existed and were already independently
live-validated: sample-level DP (Opacus, worker-side, any secure-
aggregation setting) and secure user-level DP (worker-side clipping +
central aggregate noise, built and validated in the immediately prior
slice). A **cleartext** hybrid mode already composed sample-level DP
with the *non-secure-aggregation* flavor of user-level DP
(`PrivacyMode::kHybridDp`, `docs/hybrid-dp.md`) — untouched by this
slice. What did not exist, and is what this slice builds, is hybrid
composed with the **secure-aggregation** flavor of user-level DP:
`AcquireTask` unconditionally rejected `kHybridDp` under secure
aggregation before this slice.

- **`AcquireTask`'s hybrid compatibility gate**
  (`cpp/coordinator/src/coordinator_service.cpp`): a new `kHybridDp`
  branch runs an up-front sample-level configuration validation
  (`SECURE_HYBRID_DP_INVALID_SAMPLE_CONFIGURATION`), then the exact
  same user-level validation ladder plain `USER_LEVEL_DP` already uses
  (weighting/clip-norm/quantization-margin/budget), with
  `SECURE_HYBRID_DP_*`-prefixed rejection reasons.
- **No new combined-configuration message.** `privacy_configuration_hash(task)`
  (sample-level) and `secure_user_level_dp_configuration_hash(task)`
  (user-level) were *already* independently part of
  `coordinator_task_signing_bytes()` before this slice — both
  sub-configurations are already cryptographically bound into the one
  signed task. A third, purpose-built "hybrid configuration hash" would
  either duplicate already-authoritative information or require an
  unreviewed cross-unit composition rule this task's own instructions
  forbid inventing — see the audit doc's dedicated section.
- **Worker-side hybrid execution order**
  (`python/src/fl_platform/worker/service.py`): sample-level private
  training (Opacus, unchanged) → whole-user delta construction →
  worker-side global L2 user-level clipping (unchanged code path,
  `handshake.secure_user_level_dp_active`) → fixed-point encode →
  pairwise mask → signed submission carrying **both** privacy records.
  Every step reuses an existing, already-tested function; nothing new
  was built for the training/clipping/encoding/masking math itself.
- **Closed a real, pre-existing gap**: `MaskedClientUpdate.sample_privacy_record_hash`
  existed on the wire, was already covered by the outer envelope's own
  signature, and was already documented — but the worker hardcoded it
  to `""` and the coordinator never verified it, for *any* secure-
  aggregation-bound submission with sample-level DP active, not just
  hybrid. `SubmitMaskedClientUpdateRequest` gained
  `sample_privacy_record_envelope`/`sample_privacy_record_payload`
  fields; `SubmitMaskedClientUpdate` gained a verification block
  reusing the cleartext path's exact signature/structural-binding/
  replay/monotonicity/budget-decision-contradiction logic, staged and
  committed only after `submit_masked_update` itself durably succeeds
  (this codebase's established deferred-commit discipline). Three new
  rejection reasons: `SECURE_AGGREGATION_REJECTION_REASON_SAMPLE_RECORD_MISSING`,
  `_SAMPLE_RECORD_INVALID_SIGNATURE`, `_SAMPLE_RECORD_BINDING_MISMATCH`.
- **`_build_signed_sample_privacy_record_payload` now reports
  `privacy_mode=PRIVACY_MODE_HYBRID_DP` (4)** when both mechanisms are
  active for a task, versus `PRIVACY_MODE_SAMPLE_LEVEL_DP` (2) when
  sample-level DP alone is active under secure aggregation — closing a
  second gap where a worker could not previously distinguish the two
  from its own vantage point.
- **Two epsilons, never combined.** No `hybrid_epsilon` field exists
  anywhere in any proto message, Go type, or web type this slice adds.
  Sample-level epsilon/delta/accountant and user-level epsilon/delta/
  accountant are reported, accounted, and ledgered completely
  separately — see the semantics doc §4 for the full reasoning,
  including why no `SECURE_HYBRID_DP_COMBINED_EPSILON_UNSUPPORTED`
  rejection code was added (the wire schema has no field capable of
  representing a combined-epsilon request in the first place, which is
  a stronger guarantee than a runtime rejection check).
- **Compatibility matrix** (`docs/secure-aggregation-privacy-compatibility.md`):
  `HYBRID_DP` is now accepted under secure aggregation under the union
  of both mechanisms' existing conditions.

## Four real bugs found and fixed by this slice's own testing

Two mechanism bugs, both found by direct re-reading of the
finalize/commit path while wiring it — not by live validation this
time (unlike the prior two slices, whose bugs were only caught live) —
but both are exactly the class of silent-mode-mismatch bug this
project's own precedent warns about, and both were confirmed fixed by
a dedicated new C++ test block before any live validation ran. Two
more issues surfaced by the live validation script itself (below,
"Live validation"), both in the script rather than the mechanism.

1. **`apply_secure_aggregate_and_advance`'s accountant-commit gate
   checked `privacy_mode == kUserLevelDp` only.** Every hybrid round's
   user-level accountant step and ledger append were silently skipped
   — `kHybridDp != kUserLevelDp`, so the condition was always false for
   a hybrid run. Fixed by extending the condition to accept both modes.
2. **`SubmitMaskedClientUpdate`'s finalize block computed central noise
   under the identical `privacy_mode == kUserLevelDp`-only condition.**
   A hybrid round's aggregate would have finalized with
   `noise_provider = nullptr, noise_std_dev = 0.0` — meaning **hybrid
   rounds would never have received any central user-level noise at
   all**, silently degrading to no user-level privacy while still
   reporting `HYBRID_DP` as the active mode. Fixed by extending the
   same condition.

Both are proven fixed by a new hybrid test block appended to
`cpp/coordinator/tests/user_level_dp_test.cpp`, which builds a real
`kHybridDp` `RunConfig`, calls `apply_secure_aggregate_and_advance`, and
asserts both the user-level accountant/ledger commit **and** that a
separately-appended sample-level ledger entry survives independently —
this test would have failed loudly against the pre-fix code for both
bugs.

Two more issues were found live while running the validation script
itself, both in the script, neither in the mechanism:

3. **`UnicodeDecodeError` crashing `docker compose build`'s output
   capture** under Windows' default `cp1252` console codepage. Fixed
   by making `scripts/validate_secure_hybrid_dp.py`'s subprocess helper
   decode as UTF-8 with `errors="replace"`.
4. **A wrong test assertion**, not a product bug: the script's first
   version checked the coordinator's own stdout log text for
   `event_type=SECURE_HYBRID_DP_CONFIGURATION_ACCEPTED`, by analogy
   with the core round-lifecycle markers (`AGGREGATION_COMPLETED`
   etc.) checked in the same script. That event — like its
   `SECURE_USER_LEVEL_DP_CONFIGURATION_ACCEPTED` sibling, confirmed by
   reading `coordinator_service.cpp`'s shared emission block — is
   written only to the durable `SecurityEventJournal`, never mirrored
   to stdout. The live run failed that one assertion (38 passed, 1
   failed) while the correct, already-passing journal-API check for
   the identical event proved the mechanism itself was fine. Fixed by
   removing the incorrect stdout assertion rather than adding a
   spurious stdout log call to the C++ side purely to satisfy a wrong
   test.

## Live validation

`scripts/validate_secure_hybrid_dp.py` +
`infra/compose/docker-compose.secure-hybrid-dp.yml` (a thin
`FL_WORKER_RUN_ID` override on the existing secure-cohort-handshake
compose stack — same real mTLS, real Ed25519 signing, real containers).
Creates a single-round 3-client FedAvg run with `privacy.mode=hybrid_dp`:
a deliberately tight sample-level `max_grad_norm=0.5` (so per-sample
DP-SGD clipping engages on ordinary real training) and a deliberately
tiny user-level `initial_clipping_bound=0.01` (real whole-user deltas
virtually always exceed it, so user-level clipping engages too, on
real training output, not a synthetic injected value).

**38/38 checks passed** (the first run caught bug #4 above and failed
1/39; the second run, after removing the incorrect stdout assertion,
passed clean — one fewer total check, not a loosened one, since the
underlying property that assertion meant to test was already proven by
the still-present journal-API check):

- all three workers reach `READY_FOR_MASKED_TRAINING`,
- all three workers apply real worker-side global L2 **user-level**
  clipping, logged as happening *after* sample-level private training
  produced the whole-user delta — the mandatory ordering, not merely
  configured but genuinely exercised on real, already-DP-SGD-trained
  gradients,
- all three workers submit a masked update carrying both a signed
  sample-level privacy record and a signed user-level attestation,
  accepted by the coordinator,
- the run reaches `COMPLETED`, `model_version` genuinely advances
  `v0 → v1` after exactly one round,
- no worker ever falls back to the cleartext `ClientResult` path,
- the coordinator's structured log confirms the ordinary
  `AGGREGATION_COMPLETED → MODEL_VERSION_UPDATED → CHECKPOINT_COMPLETED
  → RUN_COMPLETED` sequence,
- the security-event journal contains real
  `SECURE_HYBRID_DP_CONFIGURATION_ACCEPTED`, `_USER_BUDGET_RESERVED`,
  `_SAMPLE_RECORD_ACCEPTED`, `_BINDING_ACCEPTED`, and `_ROUND_COMPLETED`
  events, with no spurious `_CONFIGURATION_REJECTED` or `_ROUND_ABORTED`,
- the two `GetSecureUserLevelPrivacyHealth`/`GetSecureUserLevelPrivacyBudget`
  fixes are proven live: both now return `200` for this real hybrid
  run instead of the pre-fix `412`, `health` counts it as an active
  user-level-DP-layer run, and `budget` reports a real positive
  `epsilon_spent=5.303` for exactly 1 committed round,
- `status.provider` remains the unchanged
  `SECAGG_NO_DROPOUT_EXPERIMENTAL`,
- teardown leaves zero project containers running.

This does not and cannot prove the exact numeric noise value added by
either mechanism — both use real, non-deterministic noise sources
(Opacus's own RNG for sample-level, the OS-CSPRNG-backed
`CryptoSecureNoiseProvider` for user-level). What it proves is that the
real, live, multi-container pipeline is wired correctly end to end for
the hybrid mode specifically.

## Full regression, final numbers

- **C++, protobuf-free (local Windows/MSVC)**: `ctest --test-dir
  build/cpp-debug -C Debug` — **7/7** suites passed, including the new
  hybrid test block in `user_level_dp_test.cpp`.
- **C++, gRPC-gated (Docker, mirroring the CI `cpp-grpc` job)**: **8/8**
  test executables passed, including the new cross-language golden
  fixture for the hybrid-mode (`privacy_mode=4`) sample-record
  canonical hash in `signed_envelope_verifier_test.cpp`. All 15 C++
  test executables (7 protobuf-free + 8 gRPC-gated) also verified
  together in one full Docker build: **15/15**.
- **Python**: `python -m pytest tests python/tests` — **493 passed, 1
  skipped** (up from the starting-evidence baseline; 5 of the new tests
  are hybrid-specific: 4 in `test_grpc_coordinator_client.py`'s new
  `GrpcCoordinatorClientSubmitMaskedUpdateTests` and 1 hybrid-mode
  cross-language golden fixture in `test_signed_envelope.py`). One
  transient failure (`SecurityEventFlushThreadTests::test_flush_loop_survives_an_exception_and_keeps_retrying`)
  was observed on a single combined-suite run under load and passed
  cleanly both in isolation and on an immediate rerun of the full
  suite — a pre-existing, timing-sensitive thread test unrelated to
  this slice (untouched file, not part of this slice's changes), not a
  regression.
- **Go**: `go build ./...`, `go vet ./...`, `go test ./...` — clean, no
  failures (unchanged by this slice — no Go files were modified).
- **Web**: `npm run lint`, `npm run typecheck`, `npm test` (**46/46**),
  `npm run build` — all clean (unchanged by this slice — no web files
  were modified).
- **Terminology check** (`scripts/check_project_terminology.py`):
  passing. **Proto contract verification**
  (`scripts/verify_proto_contracts.py`): passing.
- **Runtime-validation harness**: the new `secure-aggregation-hybrid-dp`
  group (`scripts/security-validation/groups/secure_hybrid_dp.py`, 3
  scenarios) registers cleanly with no scenario-ID collisions across
  the full registry (104 total scenarios, up from 101).

## What remains bounded or deferred (honest, per the audit doc's own scope statement)

Consistent with — not a retreat from — the audit doc's pre-declared
scope split:

- **Bounded, not exhaustive**: a representative `SECURE_HYBRID_DP_*`
  event vocabulary (8 event types wired at real call sites — session
  config accept/reject, user budget reserved, sample record accept/
  reject, binding accepted, round completed/aborted), not the full
  ~22-name suggestion; the runtime-validation harness group covers 3
  scenarios exercising the two observability fixes plus config
  acceptance, not a 24-scenario/71-item checklist; the formal 8-state
  `HYBRID_*` worker state machine is documented in prose in the
  semantics doc rather than implemented as an unused parallel enum
  (the real states are ordinary Python control flow with real
  exception handling).
- **Deferred, disclosed** (mirroring the first secure-user-level-DP
  slice's own precedent of a dedicated follow-up "Operations,
  Observability, and Release Evidence" slice, which then genuinely
  happened): new Prometheus metrics specific to hybrid; Go read-only
  hybrid-specific APIs and a dedicated Web hybrid observability page —
  the *existing* `/security/secure-aggregation/privacy` page and its Go
  API already correctly report the user-level layer for any run
  including a hybrid one, once `GetSecureUserLevelPrivacyHealth`/
  `GetSecureUserLevelPrivacyBudget` were fixed this slice to recognize
  `kHybridDp` (two cheap, high-value fixes made without new Go/web
  code); performance benchmarking; new CI *job* structure (new tests
  land in the existing broad `cpp-grpc`/`python`/`go`/`web` jobs).
- **Explicitly out of scope, unchanged from every prior secure-
  aggregation slice**: secure adaptive clipping, secure aggregation of
  clipping indicators, variable user weights, sample-count-weighted
  hybrid privacy, a single combined epsilon, formal cross-unit privacy
  composition, cryptographic verification of Opacus execution or
  whole-update clipping, range proofs, ZK proofs, worker/remote
  attestation, TEE/TPM, Byzantine-robust aggregation, threshold secret
  sharing, dropout recovery, partial-cohort finalization, homomorphic
  encryption, independent privacy/cryptographic/penetration review.

See [known-limitations.md](known-limitations.md)'s "Secure Hybrid
Differential Privacy Runtime slice" section for the itemized gap list
in that document's standard format, and [plan.md](plan.md) for the
corresponding status-update entry.
