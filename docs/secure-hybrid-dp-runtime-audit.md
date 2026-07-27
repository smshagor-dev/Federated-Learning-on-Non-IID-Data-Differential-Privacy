# Secure Hybrid Differential Privacy Runtime — Audit (Work Area A)

Written before implementation, per this project's established working
method. Covers the required audit of the existing sample-level and
user-level DP mechanisms, classifies each for reuse under a combined
secure hybrid mode, records code-to-documentation discrepancies, and
states explicitly what gets full depth, what gets real-but-bounded
depth, and what is deliberately deferred with a disclosed reason. This
mirrors the scope-statement precedent of every prior oversized slice in
this project.

## Critical framing (confirmed by direct code reading, not assumed)

A **cleartext** hybrid DP mode already exists and is fully wired,
tested, and reported "Complete" in `plan.md`: `PrivacyMode::kHybridDp`
composes sample-level DP (Opacus, worker-side) with the **non-secure-
aggregation** flavor of user-level DP (coordinator-side clip+noise in
`RunInstance::finalize_round`, see `docs/hybrid-dp.md`,
`cpp/coordinator/tests/hybrid_dp_test.cpp`). **This slice does not
touch that mechanism at all.**

What does not exist is a hybrid mode that composes sample-level DP with
the **secure-aggregation** flavor of user-level DP (worker-side
clipping + fixed-point encoding + pairwise masking + coordinator-side
central noise on the masked sum). Today, `AcquireTask` explicitly
rejects `kHybridDp` under secure aggregation:

```cpp
// cpp/coordinator/src/coordinator_service.cpp:1501-1505
} else if (run.privacy_mode() == fl::core::PrivacyMode::kHybridDp) {
    secure_config.set_privacy_mode_compatible(false);
    secure_config.set_privacy_incompatibility_reason(
        "SECURE_AGGREGATION_HYBRID_DP_UNSUPPORTED: hybrid DP includes the same "
        "coordinator-side whole-update clipping as user-level DP");
```

This slice's job is to lift that rejection by composing the two
*already-built, already-live-validated* mechanisms — not to build
either mechanism from scratch, and not to touch the cleartext hybrid
path.

## A pre-existing, inert scaffold found during this audit

`MaskedClientUpdate.sample_privacy_record_hash` (field 20,
`proto/worker/worker.proto:519-523`) already exists on the wire,
already documented as "present only alongside sample-level (or hybrid)
DP... binds the independently-signed `SignedSamplePrivacyRecord`", and
is **already covered by the masked update's own outer signature**
(`signed_envelope_verifier.cpp:673`, inside
`masked_client_update_payload_hash_input`). But it is currently:

- Hardcoded to `""` by the worker
  (`python/src/fl_platform/worker/service.py:855`), regardless of
  whether `task.sample_level_dp_active` is true.
- Never read or verified anywhere in `coordinator_service.cpp` (zero
  references).

This means the wire format, the signing-bytes function, and the
verification-covers-it property were all put in place in a prior slice
in anticipation of hybrid — but the actual binding was never completed.
**This is a real, pre-existing correctness gap that affects
`SAMPLE_LEVEL_DP` alone under secure aggregation today** (already
documented as supported in `docs/secure-aggregation-privacy-
compatibility.md`'s Table 1), not just the new hybrid mode — a worker
training under sample-level DP and submitting via secure aggregation
today produces a real, valid `SignedSamplePrivacyRecord` in-process
(via the same `run_private_local_training` path) but never actually
transmits or binds it; the coordinator has never verified a
sample-level record for any secure-aggregation-bound submission. This
slice closes that gap as part of building the hybrid submission path
(the mechanism is identical for `SAMPLE_LEVEL_DP`-under-secure-
aggregation and the sample-level half of `HYBRID_DP`-under-secure-
aggregation — see the semantics doc).

## Component classification

| Component | Classification | Notes |
|---|---|---|
| `PrivacyMode::kHybridDp` (C++/proto enum) | Reusable unchanged | Already exists; already flows through `ClientTrainingTask.sample_level_dp_active`/`sample_level_privacy` regardless of secure aggregation |
| Sample-level Opacus training (`task_runner.py::run_private_local_training`) | Reusable unchanged | Not modified — the worker calls it exactly as it does today for plain `SAMPLE_LEVEL_DP` |
| `SignedSamplePrivacyRecord` + its canonicalization/signing (`signed_envelope.py`, `signed_envelope_verifier.cpp`) | Reusable unchanged | Same message, same hash function, same verification function reused verbatim — only the *transport* (which RPC/message carries it) is new |
| Sample-level accountant (`SampleLevelAccountant`, Python-only) | Reusable unchanged | No C++ sample-level accountant exists or is needed |
| Sample-level budget enforcement (`SampleBudgetEnforcer`) | Reusable unchanged | Worker-side; unaffected by secure aggregation |
| `AccountantMonotonicityStore` (C++, coordinator-wide) | Reusable unchanged | Already general-purpose (keyed by run/client/worker/accountant_type), called identically from the new masked-path verification block |
| Worker-side global L2 clipping (`user_level_clipping.py`) | Reusable unchanged | Not modified |
| `SignedUserLevelPrivacyAttestation` + verification | Reusable unchanged | Not modified |
| Central Gaussian noise (`SecureAggregationSessionManager::finalize()`) | Reusable unchanged | Not modified — hybrid's noise/division/model-apply path is identical to plain secure `USER_LEVEL_DP`'s |
| `UserLevelAccountant` + commit-once guard | Reusable unchanged | Not modified |
| `AcquireTask`'s privacy-mode gate | Requires lifecycle modification | New `kHybridDp` branch added, running both sub-mechanism validations |
| `MaskedClientUpdate.sample_privacy_record_hash` | Requires lifecycle modification | Wire field exists; worker must populate it, coordinator must verify it (closes the pre-existing gap above) |
| `SubmitMaskedClientUpdateRequest` | Requires additive extension | No field today carries the actual `SignedSamplePrivacyRecord` envelope/payload — must be added, mirroring `SubmitClientResultRequest`'s existing two-message pattern |
| `SubmitMaskedClientUpdate` handler | Requires lifecycle modification | New verification block (signature/binding/replay/monotonicity/budget-contradiction) reusing the exact cleartext-path logic, plus a per-worker sample-level ledger append |
| `RunInstance::user_level_ledger_`/`sample_level_ledger_` | Reusable with additive binding | A new narrow method to append a sample-level entry outside the `submit_client_result` cleartext flow |
| A dedicated `HybridPrivacyConfiguration`/`HybridPrivacyBinding` message (as literally suggested by the task spec) | **Deferred, disclosed — see scope statement** | Both sub-configurations are *already* independently hashed and *already* independently bound into the signed task and the masked update's own signature (see below); a third, redundant top-level struct/signature is not built |
| Threshold secret sharing / dropout recovery | Incompatible / unsafe | Explicitly out of scope, unchanged |
| Variable user weighting / adaptive clipping under hybrid | Incompatible | Rejected exactly as they already are for plain `USER_LEVEL_DP` |

## Why no new `HybridPrivacyConfiguration`/`HybridPrivacyBinding` message

Confirmed by direct reading of `coordinator_task_signing.cpp`:
`privacy_configuration_hash(task)` (line 356) already hashes the
sample-level sub-configuration and is already folded into
`coordinator_task_signing_bytes()`; `secure_user_level_dp_configuration_hash`
(added in the prior slice) already hashes the user-level sub-
configuration and is already folded in too. **Both sub-hashes are
therefore already independently, cryptographically bound into the one
signed task** — a worker cannot forge either sub-configuration without
invalidating the coordinator's Ed25519 signature. Inventing a third,
combined "hybrid configuration hash" would either (a) duplicate
information already authoritatively hashed twice, or (b) require a
mathematically-reviewed composition rule this task's own instructions
explicitly forbid inventing ("Do not define a single combined
epsilon" / no unreviewed cross-unit composition). The same reasoning
applies to `MaskedClientUpdate`: `sample_privacy_record_hash` is
already covered by the update's own outer signature, and
`user_level_attestation` is already independently signed and
field-bound (worker/client/run/round/task/session/model_version
cross-checked) exactly like every other structural-binding check in
this codebase. Requiring both to be present and valid *is* the hybrid
binding — a third redundant signature is not required by any
documented threat boundary, matching this task's own explicit
instruction not to add one without justification.

## Documentation discrepancies found

The task's own "Required Working Method" step 3 lists 17 docs. Several
do not exist under those exact names:

| Requested doc | Actual doc |
|---|---|
| `docs/secure-user-level-runtime-report.md` | `docs/secure-user-level-dp-runtime-report.md` |
| `docs/secure-user-level-publication-boundary.md` | `docs/secure-user-level-dp-publication-boundary.md` |
| `docs/secure-aggregation-masked-runtime-report.md` | `docs/secure-aggregation-masked-runtime-audit.md` (no separate "-report" doc; the audit doc's own completion section covers this) |
| `docs/privacy-accounting.md` | `docs/privacy-mathematics.md` |
| `docs/privacy-budget-policy.md` | `docs/privacy-budget-policies.md` |
| `docs/security-runtime-scenario-registry.md` | `scripts/security-validation/registry.py` (no doc; the registry is self-documenting code) |

## Scope statement

This task's literal specification (Work Areas A through beyond AK,
truncated in the source specification itself past 50,000 characters)
is, again, far larger than any single slice can cover at uniform,
maximal depth — larger, in fact, than the immediately prior two
oversized slices combined. Following this project's own established
precedent (four prior oversized slices, each with a disclosed
Full/Bounded/Deferred split), and specifically mirroring the precedent
set by the **first** secure user-level DP slice (which built the core
mechanism in C++/Python and explicitly deferred Go/web observability
to a **follow-up** slice, which then actually happened), this slice is
scoped as follows:

**Full depth** (real, working, tested, live-validated code):
- Work Area A (this audit), B (semantics doc).
- Work Area D/E: hybrid compatibility gate in `AcquireTask`, reusing
  the existing sample-level and user-level validation ladders in
  sequence; no new combined-config message (see above).
- Work Area G/H/I: worker-side hybrid order (sample-level training →
  whole-update construction → user-level clipping → encode → mask),
  reusing every existing pure-math function unchanged.
- Work Area K/L/M: closing the `sample_privacy_record_hash` gap —
  `SubmitMaskedClientUpdateRequest` additively extended, coordinator
  verification block reusing the cleartext path's exact signature/
  binding/replay/monotonicity/budget-contradiction logic.
- Work Area N/O/P (bounded): dual budget/publication-boundary
  semantics — the *already-existing* per-mechanism commit points
  (sample-level commits at durable masked-submission acceptance,
  user-level commits at central-noise-applied-and-model-published) are
  wired together correctly for the hybrid case; a full 8-state formal
  state-machine enum (`SAMPLE_BUDGET_RESERVED`, ...,
  `SAMPLE_RECONCILIATION_REQUIRED`) is not implemented as literal new
  persisted state — the existing mechanisms' own idempotency/replay
  guards already provide the real safety property (exactly-once
  commit), and this is documented precisely rather than wrapped in an
  unused enum.
- Work Area U: compatibility matrix updated for real.
- Work Area V (bounded): a representative hybrid event vocabulary,
  wired at real call sites, not the full ~22 suggested names.
- Work Area AA/AB/AC: real C++ and Python tests, including a
  cross-language golden fixture for the sample-record binding (the
  exact class of bug two prior slices' own live validation caught).
- Work Area AE/AF (bounded): a real runtime-validation harness group
  and a real multi-worker live Docker validation proving the mechanism
  end to end — not the literal 71-item/24-scenario enumeration, but
  real, substantial, passing live evidence.
- Work Area AK (bounded/consolidated): this audit + semantics doc +
  targeted updates to `known-limitations.md`,
  `secure-aggregation-privacy-compatibility.md`, `plan.md` — not 8+ new
  near-duplicate files.

**Deferred, disclosed with reasons, never reported as done** (mirroring
the first secure-user-level-DP slice's own precedent — Go/web
observability was deferred there too, then genuinely built in a
dedicated follow-up slice):
- Work Area W/X/Y/Z: new Prometheus metrics, Go read-only hybrid APIs,
  role-aware redaction for hybrid, and a Web hybrid observability page.
  A real, immediate reason beyond "this project's precedent": the
  *existing* `/security/secure-aggregation/privacy` page and its Go API
  already correctly report on the user-level mechanism for any run,
  including a hybrid one, once model-version/budget/round data exists —
  extending it with sample-level-specific fields is additive UI work
  independent of whether the underlying hybrid mechanism itself is
  correct, and is exactly the kind of separately-schedulable follow-up
  this project has already done once.
- Work Area AH: performance benchmarking — not attempted, disclosed.
- Work Area AI: new CI *jobs* — new tests land in the existing broad
  `cpp-grpc`/`python`/`go`/`web` jobs' full-suite invocation, matching
  every prior slice's identical finding that no new CI job structure is
  actually needed.
- Work Area AJ: artifact sanitation — extended only if a genuinely new
  sensitive-data shape appears beyond what the existing patterns
  (private keys, signatures, payload hashes, mask-key fields) already
  catch; disclosed either way in the final report.
- The formal 8-state `HYBRID_*` worker state-machine enum (Work Area F)
  — the real state transitions already exist as ordinary Python control
  flow with real exception handling (see item 4 of the audit's
  investigation); a parallel, separately-maintained enum that doesn't
  drive any new behavior would be documentation dressed as code. The
  states are documented in prose in the semantics doc instead.
- Every item under "Explicitly Out of Scope" in the task (secure
  adaptive clipping, variable user weights, threshold secret sharing,
  dropout recovery, TEE/TPM, ZK proofs, Byzantine-robust aggregation,
  and the rest) — none of it is touched, matching the task's own
  explicit prohibition.

See [secure-hybrid-dp-semantics.md](secure-hybrid-dp-semantics.md) for
the formal mechanism specification this audit's scope statement sets up.
