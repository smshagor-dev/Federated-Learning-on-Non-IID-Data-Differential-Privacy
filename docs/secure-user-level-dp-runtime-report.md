# Secure User-Level Differential Privacy Runtime — Completion Report

See [secure-user-level-dp-runtime-audit.md](secure-user-level-dp-runtime-audit.md)
for the pre-implementation audit and
[secure-user-level-dp-semantics.md](secure-user-level-dp-semantics.md)
for the full mechanism specification (adjacency model, sensitivity,
noise placement/scale, quantization margin, budget reservation design)
this report evaluates against. `SECAGG_NO_DROPOUT_EXPERIMENTAL`
remains the provider throughout — nothing in this slice claims
verifiable clipping, malicious-client security, dropout resilience, or
production readiness. The Mandatory Privacy Trust Statement stands
unchanged: this is **honest-client-dependent user-level DP**, **central
differential privacy over a securely aggregated clipped sum**, an
**experimental research implementation**.

## What this slice makes real

The prior slice (Masked Update Runtime and No-Dropout Secure FedAvg
Finalization) proved a securely aggregated FedAvg round for
`NONE`/`SAMPLE_LEVEL_DP` privacy modes. `USER_LEVEL_DP` was
structurally rejected under secure aggregation
(`SECURE_AGGREGATION_USER_LEVEL_DP_UNSUPPORTED`) because the only
existing mechanism clipped and noised cleartext per-client deltas
coordinator-side — incompatible with secure aggregation's "coordinator
never sees an individual update" guarantee. This slice builds the
alternative: **worker-side clipping + central aggregate noise**.

- **Worker-side deterministic global L2 clipping**
  (`python/src/fl_platform/secure_aggregation/user_level_clipping.py`):
  canonical-order, FP64-accumulated norm computation; clipping applied
  before fixed-point encoding, never after; fixed weight exactly `1`
  per the Initial Weighting Restriction (`validate_client_weight` is
  never called on this path).
- **Quantization-aware effective sensitivity**
  (`compute_quantization_margin`/`compute_effective_sensitivity`, both
  languages): `effective_sensitivity = clip_norm + sqrt(N) *
  (0.5/scale_factor)` — noise is calibrated against this, never the
  optimistic unquantized clip norm.
- **Signed `SignedUserLevelPrivacyAttestation`**
  (`python/src/fl_platform/secure_aggregation/user_level_attestation.py`,
  `cpp/coordinator/src/signed_envelope_verifier.cpp`): self-contained
  (own `signing_key_id`/`payload_hash`/`signature`), bound into
  `MaskedClientUpdate` and verified against the SAME signing key
  already resolved for the outer envelope. Deliberately excludes the
  unclipped norm, clipped norm, and clipping factor — evidence of
  configured worker behavior, never proof of correct execution.
- **Central Gaussian noise inside `SecureAggregationSessionManager::finalize()`**:
  a new optional `(noise_provider, noise_std_dev, expected_weight_sum)`
  parameter set. Noise is added to the decoded ring **sum**, once,
  strictly after complete-cohort validation and strictly before the
  existing divide-by-weight-sum step — reusing the run's existing
  `CryptoSecureNoiseProvider` (OS-CSPRNG-backed), not a new noise
  mechanism. `expected_weight_sum` is a defense-in-depth integrity
  check (not a cryptographic guarantee) that the decoded weight sum
  matches the fixed-weight-1 cohort size.
- **Authoritative coordinator accounting, commit exactly once**:
  `apply_secure_aggregate_and_advance` now steps the run's existing
  `UserLevelAccountant` and appends a `UserLevelLedgerEntry` — gated by
  the same round-progression idempotency guard that already made this
  method safe against retried RPCs, so the accountant step inherits
  that guarantee for free. "Reserve" is a non-mutating
  `project_user_level_epsilon_after_one_more_step()` pre-check at
  session-creation time (`AcquireTask`), refusing the session outright
  if the projected epsilon would meet/exceed `epsilon_budget` — a
  disclosed, narrower alternative to a fully separate persisted
  reservation entity (see the semantics doc section 12 for the full
  reasoning).
- **Compatibility matrix** (`docs/secure-aggregation-privacy-compatibility.md`):
  `USER_LEVEL_DP` is now accepted under secure aggregation when
  weighting is uniform, adaptive clipping is disabled, the privacy
  config is valid and safely quantizable, and the projected budget
  check passes — structured rejection reasons
  (`SECURE_USER_LEVEL_DP_VARIABLE_WEIGHT_UNSUPPORTED`,
  `_INVALID_CONFIGURATION`, `_UNSAFE_QUANTIZATION_MARGIN`,
  `_BUDGET_EXHAUSTED`) otherwise. `HYBRID_DP` remains unsupported,
  unchanged.

## Two real bugs found and fixed by this slice's own testing — both in the attestation cross-language hash

Both were caught by the live Docker validation's first run (exit code
0 but 9/22 checks failed) — exactly the scenario live validation
exists to catch, since every prior unit test (Python-only round-trip,
C++-only round-trip) was self-consistent within its own language and
could not have detected either:

1. **`client_id`/`clip_norm` JSON key ordering.** Python's
   `json.dumps(sort_keys=True)` self-corrects any dict-literal
   insertion order to true alphabetical order at serialization time;
   the hand-written C++ canonicalization has no equivalent
   auto-sorting and simply emits fields in whatever order they were
   typed. The C++ source had `clip_norm` before `client_id` — wrong
   alphabetically (`'e' < 'p'` at the fourth character) — so the two
   languages produced byte-different canonical JSON for the identical
   field values, and every attestation the coordinator verified failed
   with `payload_hash_mismatch`. Fixed by reordering both
   `user_level_privacy_attestation_payload_hash_input` and
   `user_level_privacy_attestation_signing_bytes` in
   `signed_envelope_verifier.cpp`.
2. **The golden fixture's own `provider` value was wrong.** While
   adding a cross-language golden-fixture regression test (Work Area
   AC — exactly the check that should have caught bug #1 before it
   ever reached live validation) the Python fixture used `provider=1`,
   guessed rather than looked up; the real
   `SECURE_AGGREGATION_PROVIDER_SECAGG_NO_DROPOUT_EXPERIMENTAL` value
   is `2` (`proto/worker/worker.proto`). Both hardcoded hex digests
   were recomputed and corrected in
   `python/tests/test_user_level_attestation.py` and
   `cpp/coordinator/tests/signed_envelope_verifier_test.cpp` once the
   mistake was found (by the second live-validation run, since the
   first fix alone did not by itself resolve the *test's* assertion
   until the fixture's own input was also corrected — the two bugs
   were independent, not layered).

Neither bug reflects a defect in the underlying cryptography or
protocol design — both are exactly the class of "two independently
hand-written canonicalizers must agree on every byte" error a
cross-language golden fixture and a real live multi-container run are
specifically built to catch, and both are now fixed with a permanent
regression test in place.

## Live validation

`scripts/validate_secure_user_level_dp.py` +
`infra/compose/docker-compose.secure-user-level-dp.yml` (a thin
`FL_WORKER_RUN_ID` override on the prior slice's compose stack — same
real mTLS, real Ed25519 signing, real containers). Creates a
single-round 3-client FedAvg run with `privacy.mode=user_level_dp`,
`weighting=uniform`, `noise_multiplier=1.0`, `target_delta=1e-5`, and
a deliberately tiny `initial_clipping_bound=0.01` — real PyTorch
gradients on even a small toy model virtually always exceed this, so
clipping is exercised by ordinary real training, not a synthetic
injected value.

**22/22 checks passed** on the run incorporating both bugfixes above
(the first live run correctly caught bug #1 and failed 9/22; the
second run, after fixing the golden fixture's own wrong `provider`
guess and confirming 8/8 in the gRPC-gated Docker suite, passed
clean):

- all three workers reach `READY_FOR_MASKED_TRAINING`,
- all three workers log `secure user-level DP clipping applied` —
  real worker-side global L2 clipping genuinely engaged on real
  training gradients, not merely wired but unexercised,
- all three workers' signed, attested masked updates are accepted by
  the coordinator,
- the run reaches `COMPLETED` and `model_version` genuinely advances
  `v0 → v1` after exactly one round,
- the coordinator's own structured event log independently confirms
  `AGGREGATION_COMPLETED → MODEL_VERSION_UPDATED → CHECKPOINT_COMPLETED
  → RUN_COMPLETED`,
- no worker ever fell back to the cleartext `ClientResult` path.

This does not and cannot prove the exact numeric noise value added —
the production run uses the real OS-CSPRNG-backed
`CryptoSecureNoiseProvider`, not a deterministic test provider. The
deterministic-noise proof (noise engages, is applied exactly once, and
changes the result) lives in
`secure_aggregation_session_manager_test.cpp`'s dedicated noise test
block, described below.

## Full regression, final numbers

- **C++, protobuf-free (local Windows/MSVC)**: `ctest --test-dir
  build/cpp-debug -C Debug` — 7/7 suites passed, including new
  quantization-margin tests (`fixed_point_encoding_test.cpp`) and a new
  secure-path accountant-commit-once test
  (`user_level_dp_test.cpp`).
- **C++, gRPC-gated (Docker, mirroring the CI `cpp-grpc` job)**: 8/8
  test executables passed, including new noise-injection/fixed-weight-
  mismatch tests (`secure_aggregation_session_manager_test.cpp`) and a
  new attestation-verification + cross-language golden-fixture test
  (`signed_envelope_verifier_test.cpp`).
- **Python**: `python -m pytest python/tests` — **454 passed, 1
  skipped** (up from 413 before this slice — 41 new tests: clipping
  edge cases, attestation construction/signing/tamper-detection, and
  the cross-language golden fixture).
- **Live 3-worker Docker validation**: 22/22, described above.
- **Terminology check**: passing. **Proto contract verification**:
  passing.

## What remains bounded or deferred (honest, per the audit doc's own scope statement)

Consistent with — not a retreat from — the audit doc's pre-declared
scope split:

- **Bounded, not exhaustive**: security events reuse the existing
  secure-aggregation event vocabulary
  (`kSecureAggregationMaskedUpdateRejected` for attestation rejections,
  `kSecureAggregationSessionAborted` for privacy-mode-incompatible/
  budget-exhausted rejections, `kSecureAggregationSessionCompleted`
  for round completion) rather than the full ~20-name
  `SECURE_USER_LEVEL_DP_*` vocabulary Work Area W suggested; metrics
  are not separately instrumented this slice (no new Prometheus
  counters); statistical noise validation is covered by the
  deterministic-provider test proving noise engages/is applied
  once/changes the result, not a separate bounded-sample statistical
  smoke test against the production CSPRNG provider; C++ test coverage
  targets the session-manager/accountant/attestation logic directly,
  not a second live-RPC gRPC test harness for the full
  `SubmitMaskedClientUpdate` attestation path (RPC-level correctness is
  proven live via Docker validation instead, matching the immediately
  prior slice's own precedent).
- **Deferred, disclosed**: Go read-only APIs and web secure-aggregation
  observability (no HTTP/UI surface for user-level-DP session state
  exists yet — matches the prior slice's own AG/AH deferral, restated
  here for the same proportionality reason); performance benchmarking
  (no new benchmark harness entries); the individually-named Work Area
  AK doc list is consolidated — `docs/secure-user-level-dp-semantics.md`
  covers what `secure-user-level-dp-configuration.md`,
  `-clipping.md`, `-quantization-sensitivity.md`,
  `-privacy-attestation.md`, `-central-noise.md`, `-accounting.md`,
  `-budget-lifecycle.md`, and `-restart-behavior.md` would each have
  separately restated, cross-referenced by section rather than
  duplicated across eight thin files.
- **Explicitly out of scope, unchanged from every prior secure-
  aggregation slice**: secure hybrid DP, secure adaptive clipping,
  threshold secret sharing, dropout recovery, partial-cohort
  finalization, malicious-client clipping verification, variable user
  weights under secure aggregation, replace-one adjacency (reserved in
  the wire enum, never produced), random-subsampling amplification
  (reserved in the wire enum, never produced), attestation-as-
  cryptographic-proof, TEE/TPM, range proofs, ZK proofs, Byzantine-
  robust aggregation, independent privacy/cryptographic review.

See [known-limitations.md](known-limitations.md)'s "Secure User-Level
Differential Privacy Runtime slice" section for the itemized gap list
in that document's standard format, and [plan.md](plan.md) for the
corresponding status-update entry.
