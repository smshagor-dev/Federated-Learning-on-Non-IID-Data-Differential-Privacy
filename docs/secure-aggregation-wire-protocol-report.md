# Secure Aggregation Wire Protocol and Live No-Dropout Execution — Completion Report

**Status: real, versioned wire contracts plus a real, tested,
in-memory coordinator orchestration class now exist on top of the
prior slice's cryptographic/math core. There is still no live,
gRPC-reachable secure-aggregation protocol.** See
[secure-aggregation-wire-protocol-audit.md](secure-aggregation-wire-protocol-audit.md)
for the starting-state audit and
[secure-aggregation-wire-protocol-foundation.md](secure-aggregation-wire-protocol-foundation.md)
for the Tier 1/Tier 2 scope decision this report evaluates against.
Every completion gate in the task specification is evaluated below;
none is skipped, and Tier 2 gates are marked BLOCKED/DEFERRED with a
reason, never reported as passing.

## 1–6. Repository audit, starting state, math/crypto core status

Covered in full by
[secure-aggregation-wire-protocol-audit.md](secure-aggregation-wire-protocol-audit.md)
(protocol-surface table, confirmed real gaps) and
[secure-aggregation-no-dropout-core-report.md](secure-aggregation-no-dropout-core-report.md)
(the prior slice's cryptographic/math core, unchanged and re-verified
this slice — 100% Docker ctest pass, still tested, still real).

## 7. Protobuf contracts — Implemented

Every enum/message the task specification lists is added, additive
only (no existing field renumbered, no existing message touched at
all — a stricter boundary than required, chosen deliberately given how
much live logic `ClientTrainingTask`/`SubmitClientResultRequest`
already carry). `SecureAggregationProvider` lives in `worker.proto`
(not `coordinator.proto`) to keep the import graph acyclic — a
genuine mid-implementation correction, documented in
secure-aggregation-wire-protocol-foundation.md §4.2.

## 8. Message types and sequences — Implemented (schema only)

`MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT`/`_MASKED_UPDATE`
and `MESSAGE_STREAM_SECURE_AGGREGATION` added to `SignedWorkerEnvelope`.
Replay-key composition documented
(secure-aggregation-wire-protocol-foundation.md §4.4): one stream per
`(worker_id, signing_key_id)`, session binding enforced by a handler-
level payload field check, key-advertisement-before-masked-update
ordering enforced by the cohort state machine, not the sequence
number. **No RPC handler constructs a `ReplayCandidate` on this stream
yet — BLOCKED on Tier 2's live RPC handlers.**

## 9. Session manager — Implemented

`SecureAggregationSessionManager` (`secure_aggregation_session_manager.{hpp,cpp}`):
real, thread-safe, in-memory, implements every method the task
specification's suggested interface lists
(`create_session`/`advertise_key`/`freeze_cohort`/
`submit_masked_update`/`finalize`/`abort`/`find`/`list`) as a concrete
class (not an abstract interface — matches this codebase's established
single-implementation convention, see the header's own comment).

## 10. Session creation — Bounded

`create_session()` validates provider/participant-uniqueness/cohort-
size/domain-bounds-safety in full. **Not hooked into a live round**
(`RunManager`/`RoundManager` untouched) — Tier 2's Work Package E.

## 11. Secure task binding — Deferred

No field added to `ClientTrainingTask`. Deliberately deferred to Tier
2's Work Package F rather than added unused this pass (see the scope
doc's revised §4.2 for why).

## 12. Ephemeral key lifecycle (Python) — Deferred

No Python code this slice. `WorkerService.run()` unchanged, confirmed
zero secure-aggregation awareness.

## 13. Key-advertisement RPC — Declared, UNIMPLEMENTED

`AdvertiseSecureAggregationKey` returns explicit `grpc::StatusCode::UNIMPLEMENTED`
with a documented reason (same precedent as `GetRound`). The
*validation logic* Work Package G specifies (participant/deadline/
public-key/session-match checks) is real and tested inside
`SecureAggregationSessionManager::advertise_key` — just not reachable
via this RPC yet. Signature/replay/sequence/mTLS verification (Work
Package G steps 1–21) is **BLOCKED** on live RPC wiring.

## 14. Cohort freeze — Implemented (partial)

`freeze_cohort()` requires every configured participant to have
advertised (never freezes partially), builds the full `FrozenCohortRoster`
with a real `compute_cohort_commitment` value. **Not signed**
(`coordinator_signing_key_id`/`signature` empty — no live
`CoordinatorSigningIdentity` injected this pass, BLOCKED on Tier 2's
RPC-handler wiring).

## 15. Frozen roster signing — Blocked

Real Ed25519 signing machinery exists in this codebase
(`coordinator_signing_identity.cpp`) and is architecturally ready to
reuse, but is not invoked from `freeze_cohort()` this pass. Reason:
the manager does not own a coordinator signing identity; injecting one
is part of the not-yet-written RPC-handler wiring.

## 16–17. Roster retrieval / Python verification — Deferred

`GetFrozenCohortRoster` is declared, UNIMPLEMENTED. No Python
verification code exists.

## 18. Cohort commitment — Implemented and tested

`freeze_cohort()`'s commitment is computed via the exact same
`compute_cohort_commitment` function from the prior slice (not a
second implementation) and is checked in the session-manager capstone
test against an independent direct call — real, passing evidence.
Cross-language (Python-side roster verification) is Deferred (§16–17).

## 19. Local training integration — Deferred

No change to `WorkerService`/`task_runner.py`.

## 20–21. Fixed-point encoding / tensor masking (wire-connected) — Implemented (library), Bounded (integration)

The prior slice's `encode_value`/`derive_tensor_mask_stream`/
`mask_tensor` are unchanged, still real and tested. This slice's own
capstone test uses them directly, driven through the session manager's
public API with real generated protobuf message types — genuine
integration evidence, just not through a live RPC.

## 22. Weight masking — Implemented and tested (library + capstone)

Same status as §20–21 — `derive_weight_mask`/`mask_encoded_value`
exercised through the manager's capstone test with correct FedAvg-
weighted-average output.

## 23. Masked update contract — Implemented (message), Bounded (validation)

`MaskedClientUpdate`/`SecureAggregationMaskedTensor` are real,
generated wire types. `submit_masked_update()` validates session/
participant/duplicate/deadline/cohort-commitment/checksum in full, and
validates per-tensor shape against the *first-received contribution's
own shape* (not an independently-sourced `ModelManifest` — this
manager has no access to one this pass, a documented simplification,
not a gap silently dropped).

## 24. Masked update signing — Deferred

No signing code constructs a real `SignedWorkerEnvelope` around a
`MaskedClientUpdate` this pass (Python-side, Tier 2).

## 25. Coordinator verification (live RPC) — Blocked

`SubmitMaskedClientUpdate` is declared, UNIMPLEMENTED. The manager's
own `submit_masked_update()` performs a real subset of Work Package
O's checks (see §23); mTLS/signature/replay/sequence verification
(steps 1–15) require live RPC wiring — Tier 2.

## 26. Cleartext prohibition — Structural, not yet enforced live

`MaskedClientUpdate` structurally carries no cleartext tensor field at
all (Work Package P's requirement enforced by the message shape
itself). A live coordinator-side rejection of a `SubmitClientResult`
carrying a model update under a secure-mode task requires the task-
binding work (§11) to exist first — Deferred.

## 27. Contribution persistence — Deferred (in-memory only)

The manager holds contributions in memory for the session's lifetime;
no disk persistence, no restart recovery this pass. Work Package Q's
access-controlled directory / atomic writes / retention policy is
Deferred.

## 28. Deadline enforcement — Implemented (checked at call time)

`advertise_key()`/`submit_masked_update()` both reject a call made
past their respective configured deadlines. **No background timer**
proactively aborts a session whose deadline passed with no further
calls arriving — Bounded, not the full Work Package R.

## 29. Dropout abort — Implemented and tested

`abort(session_id, DROPOUT, ...)` works and is tested; the session-
manager test suite includes an explicit scenario (a 2-participant
cohort where one drops out after freeze) proving `finalize()` refuses
to produce a partial aggregate and the caller correctly calls
`abort(kDropout)` instead — real, passing evidence for the core
no-dropout policy's enforcement point.

## 30. Participant status changes — Deferred

No live wiring to `TaskDispatcher::cancel_lease_for_worker`/
`SuspendWorker`/`RevokeWorker` this pass (the audit confirmed this
primitive exists and is reusable; not yet connected).

## 31–32. Complete-cohort finalization / aggregate decoding — Implemented and tested

`finalize()` requires exact cohort completeness, sums masked tensors
and masked weight via the prior slice's real `sum_masked_tensors`/
`sum_masked_values`, decodes, rejects a non-positive weight sum, and
divides — proven against a hand-computed expected FedAvg-weighted
average in the capstone test (passing).

## 33. Secure FedAvg — Bounded

`finalize()` produces a real `fl::core::AggregationResult` (the same
type FedAvg's own `Aggregator::aggregate` returns) via direct weighted-
average computation over the decoded masked-sum — deliberately
**not** via `AggregatorRegistry`/`Aggregator::aggregate` (which takes
individual cleartext `ClientUpdate`s the coordinator must never see
under secure aggregation — see the manager header's own design-
rationale comment). Not wired into a live round's actual model-version
advance — Deferred.

## 34. Sample-private secure FedAvg — Deferred

`sample_privacy_record_hash` field exists on the wire
(`MaskedClientUpdate`), unused this pass.

## 35. Unsupported privacy combinations — Deferred

`SecureAggregationRejectionReason` enum values exist on the wire
(no dedicated USER_LEVEL/HYBRID/adaptive-clipping rejection values were
added this pass beyond the general rejection-reason set — Work
Package V's specific compatibility-matrix enforcement is Deferred,
requires live task binding).

## 36. Restart behavior — Deferred

No coordinator-restart session-recovery logic (no persistence to
recover from, §27). `SecureAggregationAbortReason::kCoordinatorRestart`
exists and is wired through the state machine/proto mapping, ready for
Tier 2 to use.

## 37. Security events — Deferred

No new `SecurityEventType` values added, no emission call sites.

## 38. Metrics — Deferred

No new metrics.

## 39. Go APIs — Deferred

No change to `go/internal/coordinator/security_client.go` or any HTTP
handler. `secure_aggregation_available` remains the pre-existing,
honest `false`.

## 40. Web observability — Deferred

No new route, no new components.

## 41. C++ tests — Implemented

28 checks across the prior slice's 3 non-gRPC-gated groups (unchanged,
re-verified) plus 3 new standalone gRPC-gated executables this slice
contributes to (`fl_secure_aggregation_crypto_tests`,
`fl_secure_aggregation_tensor_mask_tests`, unchanged from prior slice)
plus the new `fl_secure_aggregation_session_manager_tests` (this
slice): create_session validation (4 rejection cases), advertise_key
validation (4 cases), freeze_cohort incompleteness rejection, abort
(reason-required, double-abort rejection, reason round-trip), find/
list, and the full 3-participant capstone (real X25519/HKDF/ChaCha20
through the manager's complete public API) plus a dedicated incomplete-
cohort/dropout-then-abort scenario. **15/15 test suites pass** in the
full Docker gRPC-gated ctest run (up from 14/14 before this slice).

## 42. Python tests — Not applicable this slice

No Python secure-aggregation wire-protocol code was written (Tier 2).

## 43. Cross-language fixtures — Deferred

Requires live signing code (§15, §24) — Tier 2.

## 44. Security-property tests — Bounded

The session-manager test suite proves several real security-relevant
properties directly (duplicate-session/advertisement/contribution
rejection, all-zero-public-key rejection, cohort-commitment mismatch
rejection, incomplete-cohort finalize refusal, checksum-mismatch
rejection) but not through a live signed/replayed RPC path (Tier 2).

## 45. Validation harness — Deferred

No `secure-aggregation-no-dropout` group added — the audit confirmed
the registration point (`scripts/security-validation/registry.py`) is
simple to extend once there is a live protocol to write real (non-
`SKIPPED`) scenarios against.

## 46. Docker runtime (multi-worker) — Deferred

Docker Compose worker topology confirmed single-instance, hand-pinned
(audit finding). Not parameterized this pass.

## 47–48. Performance methodology / results — Not applicable this slice

No live protocol to benchmark yet.

## 49. CI — Deferred

No new CI job. `cpp-grpc`'s existing target list does not yet include
`fl_secure_aggregation_session_manager_tests` or the other two new
standalone executables — a real, disclosed follow-up (adding 3 lines
to `.github/workflows/ci.yml`'s `cpp-grpc` job target list), not done
this pass given the size of everything else delivered.

## 50. Artifact sanitation — Not applicable this slice

No new secret-shaped artifacts are produced by anything built this
pass (the session manager is in-memory only, no logs/files written).

## 51–54. Files, commands, results, regression counts

**Files added**: `docs/secure-aggregation-wire-protocol-audit.md`,
`docs/secure-aggregation-wire-protocol-foundation.md`,
`docs/secure-aggregation-wire-protocol-report.md` (this file);
`cpp/coordinator/include/fl_coordinator/secure_aggregation_session_manager.hpp`
+ matching `.cpp`; `cpp/coordinator/tests/secure_aggregation_session_manager_test.cpp`.

**Files modified**: `proto/coordinator/coordinator.proto` (additive:
3 enums, 13 messages, 6 RPCs), `proto/worker/worker.proto` (additive:
2 enums, 3 messages, 2 MessageType values, 1 MessageStream value),
`cpp/coordinator/include/fl_coordinator/coordinator_service.hpp` +
`.cpp` (6 new UNIMPLEMENTED RPC overrides), `cpp/CMakeLists.txt` (new
source + new standalone test target), `scripts/generate_protos.sh`
(python3 resolution fix — a real, disclosed bug fix, see
known-limitations.md), `docs/known-limitations.md`, `plan.md`.

**Commands executed, fresh, this session**:
```
git status --short
python scripts/check_project_terminology.py           # pass, before and after
python scripts/verify_proto_contracts.py               # pass, before and after every proto change
docker run ... bash scripts/generate_protos.sh generated   # real C++/Python/Go binding regeneration
cmake --build build/cpp-docker-secagg -j"$(nproc)" && ctest --test-dir build/cpp-docker-secagg --output-on-failure
  # 15/15 suites pass (Docker/Ubuntu 24.04, real gRPC-gated build)
cmake --build build/cpp-debug --target fl_coordinator_tests  # local Windows/MSVC, unaffected, 28/28 pass
python -m pytest python/tests -q  # 328 passed, 6 skipped (unaffected by this slice, re-run for regression evidence)
```
Docker throwaway build directory removed after validation
(`rm -rf build/cpp-docker-secagg`).

**Not run this session** (all Tier-2-blocked or not applicable, per
§39–50 above): `go test`, `npm run test/build`, Playwright, real
Docker Compose multi-worker validation, performance benchmarks,
artifact sanitation scan, new CI job execution.

## 55. Security findings

The `generate_protos.sh` `python`-vs-`python3` bug (§9 of
known-limitations.md's new section) is the one concrete finding this
slice surfaced — not a vulnerability, but a real gap in build-
reproducibility that could have silently left Python gRPC stubs stale
across any Debian-based CI/Docker run before this fix.

## 56. Remaining trust assumptions

Unchanged from the prior slice's threat model
(secure-aggregation-threat-model.md) — nothing in this slice changes
what is or is not trusted, since nothing here is live yet.

## 57. Known limitations

See `docs/known-limitations.md`'s new "Secure Aggregation Wire
Protocol and Live No-Dropout Execution slice" section — the
authoritative, itemized list this report summarizes.

## 58. Git working-tree summary

Nothing committed, pushed, tagged, or opened as a pull request, per
this slice's explicit instruction. See §51 above for the full file
list.

## 59. Threshold secret-sharing dependency status

Unchanged: no vetted dependency selected, no threshold secret sharing,
Shamir secret sharing, or dropout recovery implemented anywhere in
this slice, per the Threshold Secret-Sharing Blocker.

## 60. Recommendations

- **Recommended secure user-level privacy integration**: not before
  Work Package V's compatibility-matrix enforcement is live (Tier 2)
  — attempting it earlier would risk a silent privacy-mode downgrade
  path with no rejection logic yet in place to catch it.
- **Recommended dropout recovery**: only after a vetted threshold
  secret-sharing dependency exists (unresolved, §59) — this slice's
  own capstone test is the concrete proof of *why*: dropout provably
  breaks mask cancellation, and there is currently no cryptographically
  reviewed way to recover from that without threshold reconstruction.
- **Recommended next slice**: wire `SecureAggregationSessionManager`
  into `CoordinatorServiceImpl` for the three read/write RPCs with the
  smallest live-verification surface first
  (`GetSecureAggregationSession`/`ListSecureAggregationSessions`/
  `AbortSecureAggregationSession` — read/admin-authenticated, no
  signature verification pipeline required), before attempting the
  much larger `AdvertiseSecureAggregationKey`/`SubmitMaskedClientUpdate`
  handlers (Work Packages G/O's full verification pipelines).
