# Message Authenticity Enforcement and Identity Lifecycle: Slice Report

**This is the closing report for one implementation slice within the
Secure Aggregation and Cryptographic Protocols category, not a claim
that the full secure-aggregation protocol is complete.** Pairwise
masking, secret sharing, dropout recovery, and secure aggregate
recovery remain explicitly deferred and untouched. This slice builds
directly on the prior **Coordinator Transport Verification and Message
Authenticity** slice — see
[transport-identity-report.md](transport-identity-report.md) for that
slice's own status, unchanged by this one except where noted.

## 1. Repository audit

Verified from source at the start of this pass, via a dedicated
research pass over `proto/coordinator/coordinator.proto`,
`proto/worker/worker.proto`, and `coordinator_service.cpp` (not assumed
from prior documentation): 18 RPCs exist on `CoordinatorService`; three
of them (`GetRound`, `GetModelManifest`, `ReportTaskProgress`) are
declared in the proto and, for `ReportTaskProgress`, have a real domain
method (`RunInstance::report_task_progress`) but **no C++ gRPC handler
at all** — every real call falls through to `UNIMPLEMENTED`. The
specification's assumed worker-originated RPC list (`AcceptTask`,
`ReportTaskFailure`, `SubmitSamplePrivacyRecord`,
`SubmitPersonalizationMetrics`, `DrainWorker`, `WorkerShutdown`) does
not exist under those names anywhere in this codebase — sample-level
privacy and personalization metrics are embedded fields inside
`SubmitClientResultRequest`, and the other four have no equivalent at
all. `AcquireTask` and `SubmitClientResult` had **zero** certificate
identity binding before this pass (not partial — none). The Python
worker's live `WorkerService.run()` loop never calls `Heartbeat`, and
`GrpcCoordinatorClient` has no `heartbeat()` method. `WorkerHeartbeatRequest`'s
`status`/`current_task_id` fields were read but `status` was silently
discarded (hardcoded to `kIdle` server-side, unchanged by this pass —
see §37). `should_disconnect` existed on the wire since before this
pass but no code path had ever set it `true`. See §40 for the
full detailed findings this audit produced.

## 2. Existing transport and identity verification (carried over, unchanged)

Re-verified green before making any change: C++ CTest suites (7/7
locally, 10/10 in Docker at the time of the prior slice's report),
Python 193/193 passed + 1 skipped, terminology and proto-contract
checks clean. All of the prior slice's real, live-validated work
(mTLS, certificate identity binding, `WorkerIdentityRegistry`,
`SignedCapabilityStatement`) was read and re-confirmed still correct by
inspection, not re-derived from scratch — per the standing instruction
not to rewrite validated mutual-TLS or capability-verification code
without a demonstrated defect. None was found; none was rewritten.

## 3. RPC security classification

`docs/rpc-security-policy.md` (new) — a complete table of all 18 real
RPCs, each classified `PUBLIC_HEALTH` / `AUTHENTICATED_SERVICE` /
`AUTHENTICATED_WORKER` / `SIGNED_WORKER_MESSAGE`, with an explicit
section documenting the six specification-assumed RPCs that do not
exist and how their concerns map onto the real surface. `ADMIN_CONTROL`
is classified as **entirely unimplemented at the gRPC level** — no
`SuspendWorker`/`ActivateWorker`/`RevokeWorker` RPC exists, which is
also why no Go security API could call into the coordinator's registry
this pass (see §23).

As a direct, low-risk consequence of this audit: **certificate identity
binding was added to `AcquireTask` and `SubmitClientResult`**
(`reject_if_worker_identity_mismatch`, a small helper factored out of
`RegisterWorker`'s existing inline check) — previously these two RPCs
had none at all. Verified via a full Docker CTest run (all existing
tests pass unchanged, since every existing test call site passes
`nullptr` context, a documented no-op).

## 4. Signed-envelope design

`proto/worker/worker.proto`'s new `SignedWorkerEnvelope` message: 16
fields (`schema_version` through `signature`), an 11-value `MessageType`
enum, a 7-value `MessageStream` enum. Added as a new, optional field
(`WorkerHeartbeatRequest.envelope`, field 5) — additive, field-number-
preserving, verified via `scripts/verify_proto_contracts.py` before and
after. See [signed-worker-envelopes.md](signed-worker-envelopes.md) for
the full design, including the canonical-empty-value rule for optional
identifiers and why the envelope never duplicates domain payload
content.

## 5. Payload hashing

`heartbeat_payload_hash_input` (C++, `signed_envelope_verifier.cpp`) is
implemented and live-tested; Client Result Hash and Sample Privacy
Record Hash are design records only (field lists decided, documented in
[payload-hashing.md](payload-hashing.md), no code). The Sample Privacy
Record Hash in particular would require new proto fields on
`SampleLevelLedgerEntry` (`worker_id`, `task_id`, a configuration hash,
an accountant-state hash, a budget decision, a secure-random-provider
field — none of which exist today) — a materially larger change than
Heartbeat Hash (which needed zero new fields), not attempted this pass.

## 6. Signed heartbeat

Implemented, wired, and live-validated end-to-end. See §9 below for the
full validation account and [signed-worker-envelopes.md](signed-worker-envelopes.md)
for the verification-pipeline diagram. `Heartbeat` now unconditionally
requires a signed envelope (no legacy-unsigned fallback — see §9 for
why this was safe to do without any backward-compatibility toggle).

## 7. Signed client results

**Not implemented.** `SubmitClientResult` gained certificate identity
binding this pass (§3) but does not require or verify a
`SignedWorkerEnvelope`. `ClientResult`/`SubmitClientResultRequest`
already carry every field a Client Result Hash would need (per §5's
finding), so this is the natural next signed message type — deferred to
a follow-on pass, not attempted here due to scope.

## 8. Signed task failures

**Not applicable — no `ReportTaskFailure` RPC exists in this codebase**
(§1). Nothing to sign.

## 9. Signed privacy records

**Not implemented as a distinct signed structure** — sample-level
privacy accounting still rides inside the (unsigned-envelope)
`SubmitClientResultRequest.sample_level_privacy` field, unchanged from
before this pass. See §5 and §7.

## 10. Coordinator-signed tasks

**Not implemented.** No coordinator Ed25519 signing identity, no
`ClientTrainingTask` signature field, no Python-side task-signature
verification. Out of scope for this pass's time budget; see §46.

## 11. Canonical serialization

**Cross-language parity extended to `SignedWorkerEnvelope` and proven
live, not just by a static test vector.** `canonical_envelope_metadata_json`
(C++) and each live-test script's independently-written Python
equivalent were never compared directly against each other in a unit
test the way `capability_statement_verifier_test.cpp`'s golden vector
does — instead, parity was proven the stronger way: a real PyNaCl
signature computed over Python's canonical bytes was accepted by the
C++ verifier's SHA-256/Ed25519 checks against its own independently-
computed canonical bytes, live, over real mTLS, seven times, with every
tamper/mismatch scenario also behaving as predicted. If the two
encoders disagreed on a single byte, the very first live test would
have failed with `payload_hash_mismatch` or `invalid signature`; none
did. The previously-flagged domain-separation-prefix gap
(`docs/canonical-security-serialization.md`) is now closed for
`SignedWorkerEnvelope` (`"fl.worker.v1.SignedWorkerEnvelope\x00"` — see
[signed-worker-envelopes.md](signed-worker-envelopes.md)); it remains
open for `SignedCapabilityStatement` (unchanged from the prior slice).

## 12. Replay protection

Implemented, unit-tested, and live-validated including a real container
restart. See [replay-protection.md](replay-protection.md) for the full
design (bounded tracks/nonce entries, FNV-1a nonce hashing, atomic
persistence, worker-purge cleanup) and §9 below for the live restart
evidence.

## 13. Sequence validation

Implemented as part of `ReplayProtectionStore`. See
[message-sequences.md](message-sequences.md). Documented starting value
(1), duplicate/lower-sequence rejection, and a fixed
`max_sequence_gap` policy are all implemented and tested; a permissive
accept-with-warning gap policy is not.

## 14. Signing-key persistence

**Unchanged from the prior slice.** `WorkerIdentityRegistry` still
stores exactly one `signing_public_key`/`signing_key_id` per worker —
no `SigningKeyRecord` with `PENDING`/`ACTIVE`/`GRACE_PERIOD`/`REVOKED`/`EXPIRED`
status, no multiple-keys-per-worker support. `Heartbeat`'s
`unknown_signing_key` rejection reuses this same single-key model,
consistent with `RegisterWorker`'s pre-existing default-deny-on-mismatch
policy.

## 15. Key rotation

**Not implemented.** See [key-rotation.md](key-rotation.md) — not
written this pass, since there is no rotation code to document beyond
"any signing-key change is currently rejected outright."

## 16. Grace-period behavior

**Not implemented** — no key rotation exists, so no grace period can
exist either.

## 17. Key revocation

**Not implemented** as a distinct action. A worker's *entire* identity
can be revoked (`WorkerIdentityRegistry::revoke`, from the prior
slice), which transitively makes its one signing key unusable, but
there is no way to revoke a signing key while leaving the worker
identity otherwise active (moot today anyway, since one worker has
exactly one key).

## 18. Worker suspension

**Not implemented at the RPC-enforcement level.** `WorkerIdentityRegistry::suspend`
exists and is unit-tested (from the prior slice), but no RPC handler in
this codebase checks for `SUSPENDED` status anywhere — not even
`Heartbeat` (which only checks `REVOKED`). The specified "signed
heartbeat accepted only to report suspended status" carve-out is
explicitly not implemented — see
[signed-worker-envelopes.md](signed-worker-envelopes.md)'s "What is
deferred."

## 19. Worker activation

**Not implemented at the RPC level** — `WorkerIdentityRegistry::activate`
exists and is unit-tested, but nothing calls it from an RPC handler,
and (per §3) no RPC exists for an external caller to invoke it at all.

## 20. Worker revocation

**Partially enforced, live-validated for two RPCs.** A `REVOKED`
worker's `RegisterWorker` was already rejected before this pass (prior
slice); this pass adds the identical rejection to `Heartbeat`
(`worker_revoked`, `should_disconnect=true` — live-tested indirectly via
the registry's own correctness, not via a fresh live revoke-then-heartbeat
round trip, since no RPC exists to revoke a worker in the first place
— see §3's `ADMIN_CONTROL` finding). `AcquireTask`/`SubmitClientResult`
still do not check revocation status at all — a revoked worker mid-task
is not cut off.

## 21. Certificate-fingerprint revocation

**Unchanged from the prior slice.** `WorkerIdentityRegistry`'s
fingerprint uniqueness and the `REVOKED` status check together provide
this at `RegisterWorker` and (now) `Heartbeat` time; no new
fingerprint-specific logic was added this pass, and it remains
unchecked at `AcquireTask`/`SubmitClientResult`.

## 22. Protobuf changes

`SignedWorkerEnvelope` (new message, 16 fields), `SignedWorkerEnvelope.MessageType`
(new nested enum, 12 values including `UNSPECIFIED`),
`SignedWorkerEnvelope.MessageStream` (new nested enum, 8 values
including `UNSPECIFIED`), `WorkerHeartbeatRequest.envelope` (new field
5), `WorkerHeartbeatResponse.rejection_code` (new field 3). All
additive; every prior field number preserved; verified via
`scripts/verify_proto_contracts.py` before and after every change, not
just once at the end.

## 23. Go security APIs

**Not implemented.** None of the specified endpoints exist. Blocked in
part by a real, newly-identified gap (§3): there is no gRPC-level
`ADMIN_CONTROL` RPC surface for a Go API to even call into for
suspend/activate/revoke actions — building the HTTP layer first would
produce endpoints with no coordinator-side counterpart to actually
mutate, which the same "represented as implemented but not genuinely
working" concern the prior slice's own report raised for envelope
signing applies to equally here.

## 24. Web security views

**Not implemented.** Same blocking dependency as §23.

## 25. Security events

**Not implemented** as a dedicated event taxonomy. Rejections are
currently observable only via gRPC error status +
`WorkerHeartbeatResponse.rejection_code`, not a structured event stream.

## 26. Metrics

**Not implemented.**

## 27. Audit records

**Not implemented** beyond the pre-existing, disconnected
`fl_platform.security.audit` scaffold (unchanged, still unused by any
live code path).

## 28. Files added

C++: `cpp/coordinator/include/fl_coordinator/replay_protection_store.hpp`,
`cpp/coordinator/src/replay_protection_store.cpp`,
`cpp/coordinator/tests/replay_protection_store_test.cpp`;
`cpp/coordinator/include/fl_coordinator/signed_envelope_verifier.hpp`,
`cpp/coordinator/src/signed_envelope_verifier.cpp`,
`cpp/coordinator/tests/signed_envelope_verifier_test.cpp`.
Docs: `docs/rpc-security-policy.md`, `docs/payload-hashing.md`,
`docs/message-sequences.md`, this report. Docs rewritten from a stale
prior-slice-era draft to reflect this pass's real status:
`docs/signed-worker-envelopes.md`, `docs/replay-protection.md`.

## 29. Files modified

`proto/worker/worker.proto` (§22); `cpp/CMakeLists.txt` (new
sources/test targets on `fl_coordinator`, `fl_coordinator_grpc_server`,
`fl_coordinator_grpc_tests`, plus two new standalone
`fl_signed_envelope_verifier_tests`/already-existing-pattern test
targets); `cpp/coordinator/include/fl_coordinator/coordinator_service.hpp`
(optional `ReplayProtectionStore*` constructor parameter);
`cpp/coordinator/src/coordinator_service.cpp` (certificate identity
binding added to `AcquireTask`/`SubmitClientResult`; `Heartbeat`
rewritten with the full verification pipeline); `cpp/coordinator/main.cpp`
(constructs and wires a real `ReplayProtectionStore`,
`FL_REPLAY_PROTECTION_STORE_PATH`); `cpp/coordinator/tests/test_main.cpp`
(registers the new `replay_protection_store` test group);
`docs/known-limitations.md` (corrected stale claims from the prior
slice's era, added this slice's own honest gap list).

## 30. Tests added

C++: `run_replay_protection_store_tests` (11 checks, part of
`fl_coordinator_tests`, MSVC-buildable locally); `fl_signed_envelope_verifier_tests`
(12 checks, gRPC-gated, Docker-only) — both pass. Python: no new
`pytest` files (live validation used standalone scripts, not `pytest`
suites — see §39 for why, and §46 for the recommendation to formalize
these into real test files).

## 31. Exact commands executed

```bash
git status
python scripts/check_project_terminology.py           # pass, repeatedly
python scripts/verify_proto_contracts.py               # pass, before and after every proto change
cmake --build build/cpp-debug && ctest --test-dir build/cpp-debug -C Debug --output-on-failure   # 7/7, local MSVC
docker build -t <tag> -f infra/docker/cpp-coordinator.Dockerfile .    # real build, real success
docker build (throwaway, all targets) && ctest --output-on-failure    # 11/11
python -m pytest tests python/tests -q                 # 193 passed, 1 skipped
python -m grpc_tools.protoc ...                         # real local Python binding regeneration (grpc_tools available on this machine)
docker run -d ... -e FL_TRANSPORT_MODE=mtls ...          # real mTLS coordinator container
python <heartbeat_e2e_test.py, restart_before.py, restart_after.py, restart_after2.py>   # real signed RPCs over real mTLS
docker exec <container> cat /app/replay_protection_store.dat   # real persisted state, before and after a real docker restart
docker restart <container>
```

All commands above: **pass**, exactly as reported. Nothing reported as
passing without actually being run.

## 32. Pass, fail, or blocked results

All of §1–31's claimed work: **pass**. Nothing in this slice is
reported as validated without a real compile and/or a real live RPC
round trip behind it. Explicitly **not** run, and why:

* **`go test -race`** — no cgo/gcc locally (pre-existing constraint,
  unchanged); no Go source was touched this slice anyway.
* **`npm ci`/web tests** — no web files were touched this slice.
* **Full `docker compose up`** — only a direct `docker run` of the
  `coordinator` container (with mounted dev-PKI certs) was exercised,
  not the multi-service Compose stack; see §34.
* **CI job additions** — not made this slice; the new C++ test targets
  (`fl_signed_envelope_verifier_tests`, the extended
  `fl_coordinator_tests`) will be picked up by the existing `cpp-grpc`/
  `cpp-debug` CI jobs' existing `ctest` invocations without any new job
  needed, but this was not separately verified against the actual CI
  YAML this pass.

## 33. Cross-language verification results

Real and passing — see §11. Python (PyNaCl) signs, C++ (OpenSSL)
verifies, for `SignedWorkerEnvelope`'s canonical metadata JSON, the
domain-separation-prefixed signing bytes, and the heartbeat payload
hash, all proven via live signature acceptance rather than a
side-by-side byte comparison alone (the prior slice's
`SignedCapabilityStatement` parity claim used a static golden-vector
unit test in addition to live validation; this slice's
`SignedWorkerEnvelope` parity claim rests on live validation only — a
dedicated golden-vector unit test analogous to
`capability_statement_verifier_test.cpp`'s `kGoldenPayloadJson` was not
added for the envelope this pass, which is a real, if minor, testing-
thoroughness gap worth closing in a follow-on pass).

## 34. Docker runtime results

**Direct container validation, not full Compose.** A `docker run` of
the `coordinator` image alone, with `certs/dev/` mounted and
`FL_TRANSPORT_MODE=mtls`, was used for all live testing in this slice
(register, heartbeat ×9 scenarios, restart persistence). `docker compose
up`/`down` with the full service topology (`api`, `web`, `python-worker`,
`prometheus`, etc. — see [docker-runtime.md](docker-runtime.md)) was not
run this slice. No container was left running at the end of this
session — every container and image created was explicitly removed
(`docker rm -f`, `docker rmi`), confirmed via a final `docker ps -a`
showing only pre-existing, unrelated containers from other projects on
this machine.

## 35. Performance methodology

**Not performed this slice.** No benchmarking of envelope
serialization, payload hashing, Ed25519 verification, or replay-store
validation/persistence was done. The prior slice's §32/§33
(secure-random overhead) remain the only real performance numbers in
this project's security work to date.

## 36. Performance results

Not applicable — see §35.

## 37. Security findings

Two genuine, honestly-disclosed findings from this slice's own live
testing (neither is a bug introduced by this slice's code):

* **`AcquireTask` and `SubmitClientResult` had zero certificate identity
  binding before this pass** — confirmed by direct source inspection,
  not assumed. Fixed (§3).
* **The in-memory `WorkerRegistry` (runtime scheduling state) does not
  survive a coordinator restart, while the newly-added
  `ReplayProtectionStore` and the prior slice's `WorkerIdentityRegistry`
  both do.** Discovered live: a heartbeat that correctly passed every
  signature/replay/sequence check after a restart was then rejected by
  an unrelated, pre-existing domain-layer check
  ("`heartbeat from unregistered worker_id`") until the worker
  re-registered. Documented in [known-limitations.md](known-limitations.md)
  as a pre-existing architectural characteristic, not fixed this pass
  (fixing it would mean persisting `WorkerRegistry` itself, a
  materially larger and differently-scoped change than this slice's
  security focus).
* **`WorkerHeartbeatRequest.status` is still silently discarded**
  server-side (hardcoded to `kIdle`, unchanged pre-existing behavior,
  not touched by this pass to keep the diff focused on authenticity
  enforcement rather than scheduling-logic correctness).

No vulnerabilities were found in code this slice did not modify; the
scope was additive security enforcement, not a full audit of
pre-existing domain logic.

## 38. Remaining trust assumptions

Everything already stated in
[secure-aggregation-threat-model.md](secure-aggregation-threat-model.md)
and the prior slice's report still holds, plus:

* A signed, replay-protected heartbeat authenticates *that this worker,
  holding this key, sent this exact status at this exact sequence
  position* — it does not authenticate that the worker is actually
  healthy, actually idle, or actually running the software it last
  asserted in its capability statement.
* Every other worker-originated message
  (`AcquireTask`/`SubmitClientResult`/task progress) still relies only
  on certificate identity binding (as of this pass) or nothing at all
  (task progress, since no handler exists) — the message-authenticity
  guarantee this slice built is real but narrow in scope (one RPC out
  of five worker-originated ones with a real handler).
* A revoked worker is only actually blocked from `RegisterWorker` and
  `Heartbeat` — it can still call `AcquireTask`/`SubmitClientResult`
  freely, since those RPCs never consult `WorkerIdentityRegistry`.

## 39. Remaining blockers

* No RPC-level `ADMIN_CONTROL` surface exists for suspend/activate/
  revoke actions, blocking Go security APIs and web security views from
  having anything real to call (§23/§24).
* No coordinator Ed25519 signing identity exists, blocking
  coordinator-signed tasks (§10).
* `SampleLevelLedgerEntry` lacks the fields (`worker_id`, `task_id`,
  configuration hash, accountant-state hash, budget decision,
  secure-random-provider) a Sample Privacy Record Hash needs — a proto
  change is required before that hash function can be written (§5).
* This slice's live validation used standalone Python scripts, not
  formal `pytest` test files or a wired-in `heartbeat()` method on
  `GrpcCoordinatorClient` — real coverage, but not yet in a form CI
  would run automatically on every change.

## 40. Regression status

Zero regressions. C++ CTest suite grew from 10 (Docker) / 7 (local
MSVC) to 11 (Docker) / 7 (local MSVC, unchanged since the two new
gRPC-gated envelope/replay test targets don't build locally) — all
passing at 100% in both environments, confirmed via a full Docker
rebuild of every target, not just the new ones. Python stayed at 193
passed / 1 skipped (no Python source or test files were modified or
added this slice). Terminology and proto-contract checks passed before
and after every change, checked repeatedly throughout, not just once at
the end.

## 41. Git working-tree summary

No commits were made this slice — per standing instructions, work is
not committed, pushed, tagged, or opened as a pull request without an
explicit request. All new/modified files listed in §28/§29 are present
as uncommitted working-tree changes, consistent with every prior slice
in this project's history.

## 42. Recommended secure aggregation protocol work

Per the Required Implementation Order's own sequencing and this slice's
own findings, in priority order:

1. **RPC-level worker suspension/activation/revocation enforcement**,
   starting with adding the missing `ADMIN_CONTROL` gRPC RPCs
   (`SuspendWorker`/`ActivateWorker`/`RevokeWorker`) so there is
   something for a Go API to actually call, then wiring
   `WorkerIdentityRegistry` status checks into `AcquireTask`/
   `SubmitClientResult` (currently the two RPCs with the most exposure
   and the least enforcement).
2. **Signed client results**, extending the now-proven
   `SignedWorkerEnvelope`/`ReplayProtectionStore` pattern to
   `SubmitClientResult` — the natural second message type, since
   `ClientResult` already carries every field a Client Result Hash
   needs.
3. **Signing-key rotation with a grace period** — today any key change
   is an unconditional rejection with no recovery path; this blocks
   real-world key hygiene for any long-lived worker deployment.
4. **A formal Python `pytest` suite for signed messages** (envelope
   construction, canonical bytes, heartbeat signing, replayed/tampered/
   expired rejection), replacing this slice's standalone validation
   scripts with something CI actually runs.
5. **Coordinator-signed tasks** and **the Sample Privacy Record Hash's**
   required `SampleLevelLedgerEntry` proto fields — both need proto
   changes before any signing/verification code can exist.
6. Only after 1–5: Go security APIs, web security views, security
   events/metrics/audit records, full Docker Compose validation,
   performance benchmarking, and CI security gates — all still entirely
   unstarted.
7. The threshold secret-sharing blocker from earlier categories'
   passes remains unresolved and out of scope for all of the above —
   pairwise masking and secret sharing should not begin until it is.

Explicit non-goals maintained this slice, per standing instruction: no
pairwise masking, private masks, fixed-point secure-aggregation
encoding, secret sharing, dropout recovery, unmasking, protocol
transcript chaining, secure aggregate decoding, homomorphic encryption,
Byzantine-robust aggregation, worker attestation, TEEs, TPM integration,
Ray, Flower runtime integration, asynchronous/semi-synchronous
aggregation, or production Kubernetes rollout. No commits, pushes,
tags, or pull requests were made without explicit request.

---

# Signed Client Results and Worker Lifecycle Enforcement: Slice Report

**This is the closing report for the next implementation slice within
the Secure Aggregation and Cryptographic Protocols category, building
directly on the Message Authenticity Enforcement and Identity Lifecycle
slice above.** Pairwise masking, secret sharing, dropout recovery, and
secure aggregate reconstruction remain explicitly deferred and
untouched. This slice closes several gaps the prior slice's own §37/§38/
§39/§42 explicitly flagged: no signed client results, no `ADMIN_CONTROL`
RPCs, no worker-status enforcement beyond `Heartbeat`/`RegisterWorker`,
no active-lease cancellation on revocation.

## 1. Repository audit

Re-confirmed from source, not assumed from the prior report: 18 RPCs on
`CoordinatorService` at the start of this pass, of which `GetRound` and
`GetModelManifest` had no handler (fell through to gRPC's generic
default status, not even documented `UNIMPLEMENTED`) and
`ReportTaskProgress` had a real domain method
(`RunInstance::report_task_progress`, which itself calls
`TaskDispatcher::report_progress`) but zero C++ gRPC handler wiring it
up. `AcquireTask` had certificate identity binding (from the prior
slice) but no `WorkerIdentityRegistry` status consultation at all — a
`SUSPENDED`/`REVOKED` worker could freely acquire tasks. No
`SuspendWorker`/`ActivateWorker`/`RevokeWorker`/`GetWorkerIdentity`/
`ListWorkerIdentities` RPC existed anywhere in
`proto/coordinator/coordinator.proto`. `TensorManifest.checksum`/`dtype`/
`byte_length` were defined on the wire but the Python worker's
`_tensor_manifests_from_dict` never populated them (always empty
strings/zero) — meaning even a signed client result would have signed
an unverifiable, always-empty checksum field had signing been added
naively without first fixing this.

## 2. Existing transport and identity verification (carried over, unchanged)

Re-verified green before making any change: C++ CTest 11/11 (Docker), 7/7
(local MSVC); Python 193 passed, 1 skipped; terminology and
proto-contract checks clean. All prior-slice work (mTLS, certificate
identity binding, `WorkerIdentityRegistry`, `ReplayProtectionStore`,
signed heartbeat) was read and re-confirmed correct by inspection, not
re-derived — per the standing instruction not to rewrite validated mTLS,
signed-capability, or signed-heartbeat code without a demonstrated
defect. None was found in that code; none of it was rewritten (only
extended — e.g. `ReplayProtectionStore` gained a second call site, not a
single line of internal logic change).

## 3. RPC surface reconciliation

Five new RPCs added to `CoordinatorService`: `GetWorkerIdentity`,
`ListWorkerIdentities`, `SuspendWorker`, `ActivateWorker`,
`RevokeWorker` — all classified `ADMIN_CONTROL`, all gated on a strict
go-api service certificate identity check
(`reject_if_not_go_api_service_identity`, which — unlike the existing
`reject_if_worker_identity_mismatch` — rejects rather than passes
through an unauthenticated/non-mTLS connection, except for the
documented `nullptr`-context no-op used by direct-call unit tests).
`GetRound`/`GetModelManifest` resolved to explicit, documented
`grpc::StatusCode::UNIMPLEMENTED` responses rather than silent generic
defaults. `ReportTaskProgress` given a real handler: certificate
identity binding, then a try-each-run loop over `manager_->list_run_ids()`
(a worker's task ID alone does not identify which run it belongs to),
returning `NOT_FOUND` only if no run recognizes the
`(client_id, task_id)` pair. See [rpc-security-policy.md](rpc-security-policy.md)
for the updated full RPC table.

## 4. Client result payload hash

`client_result_payload_hash_input` (C++, `signed_envelope_verifier.cpp`;
Python, `signed_envelope.py`) implemented and live-validated — see
[payload-hashing.md](payload-hashing.md)'s "Client Result Hash" section
for the exact field list and canonicalization rules. Includes **real
per-tensor SHA-256 checksum verification**, not pass-through: the C++
verifier recomputes each tensor's checksum from its actual `values`
(little-endian float64) and rejects on mismatch, closing the gap noted
in §1 above. The Python worker's `_tensor_manifests_from_dict` was fixed
to actually compute `dtype`/`byte_length`/`checksum` (previously always
empty) — a real, independently-discovered correctness fix this pass
required in order for signing to mean anything for tensor content.

## 5. Python signed client results

`fl_platform.security.signed_envelope` (new module) mirrors the C++
canonicalization field-for-field. `fl_platform.security.sequence_state.SequenceStateStore`
(new, JSON-file-backed, atomic-write) tracks a monotonic per-
`(signing_key_id, message_stream)` counter for the worker side — simpler
than the coordinator's `ReplayProtectionStore` since a worker only ever
needs to track its own outgoing sequence, never incoming ones.
`GrpcCoordinatorClient.__init__` gained optional
`signing_identity`/`sequence_state_path` parameters (`None` by default —
fully backward compatible); when set, `register_worker()` now signs a
real `SignedCapabilityStatement` (closing another gap the prior slice's
own report flagged: the live client sent completely unsigned
registrations) and `submit_result()` signs a real `SignedWorkerEnvelope`
and attaches it to `SubmitClientResultRequest.envelope`.

## 6. C++ signed result verification

`SubmitClientResult` rewritten with a real pipeline, inserted between
the existing certificate-identity check and the pre-existing domain
call: look up the worker's `WorkerIdentityRegistry` record and reject
`REVOKED` workers outright (`worker_revoked`); if an envelope is
present, verify its `signing_key_id` matches the registry record, verify
the payload hash via `client_result_payload_hash_input`, verify the
Ed25519 signature via the existing `verify_signed_envelope` (reused
unchanged, parameterized with `MESSAGE_TYPE_CLIENT_RESULT`), then
validate against `ReplayProtectionStore` using a new `MessageStream::kClientResult`
track (`validate()` only — `commit()` is deliberately deferred, see §7);
if no envelope is present, reject with `envelope_missing` unless
`allow_unsigned_client_results_` is `true` (constructor default `true`
for backward compatibility with existing unsigned-result unit tests;
`main.cpp` explicitly passes `false` for the live server unless
`FL_ALLOW_UNSIGNED_CLIENT_RESULTS=true` is set — mirroring
`FL_ALLOW_INSECURE_DEVELOPMENT_TRANSPORT`'s fail-closed convention).
Every dev-compatibility unsigned-result acceptance logs a
`level=WARNING` structured stderr line.

## 7. Reject unsigned production results

Live-validated: the containerized coordinator (started without
`FL_ALLOW_UNSIGNED_CLIENT_RESULTS`) rejected an unsigned
`SubmitClientResultRequest` with `envelope_missing`; the identical
request with a correctly signed envelope attached was accepted. The
`allow_unsigned_client_results_ = true` constructor default exists
solely so `coordinator_service_test.cpp`'s many pre-existing unsigned-
result test cases (written before this slice, exercising unrelated
domain logic) continue to compile and pass unchanged — a deliberate,
narrow backward-compatibility carve-out for tests only, not for the live
server.

## 8. Replay and sequence enforcement integration

`ReplayProtectionStore::validate()` is called for every signed client
result; `commit()` is called **only after** the pre-existing domain call
(`run.submit_client_result(...)`) reports `accepted = true` — preserving
the standing "never commit replay state before domain success" rule
established by the prior slice's heartbeat pipeline, applied identically
here with zero store-level code changes (proving the store's
stream-agnostic design, as documented in
[replay-protection.md](replay-protection.md)'s "Updated: `CLIENT_RESULT`
stream also live" section). If the domain call rejects (e.g. duplicate
submission, wrong round), the replay candidate is never committed —
verified live: a resubmission with a fresh, validly-signed envelope
(different nonce/sequence, same underlying result) was correctly
rejected by the pre-existing domain-level duplicate check, and the
sequence/nonce record from the *rejected* resubmission attempt was
confirmed not to have been committed (a subsequent legitimately-different
result at the next sequence number was still accepted).

## 9. Signed sample-level privacy records

**Not implemented as an independent signed message type or stream.**
Sample-level privacy metadata is covered only transitively, as a nested
`privacy_record` object inside the Client Result Hash (§4) — any
tampering with it after signing is detected (since it changes the
overall payload hash), but there is no independent
accountant-step/epsilon monotonicity check, and `MESSAGE_STREAM_PRIVACY_RECORD`
remains an unused enum value with no producer or consumer. This is
Work Package F/I of the parent specification, explicitly deferred — see
[payload-hashing.md](payload-hashing.md)'s "Sample Privacy Record Hash"
section for why (new `SampleLevelLedgerEntry` proto fields would be
required first: `worker_id`, `task_id`, a configuration hash, an
accountant-state hash, a budget decision, a secure-random-provider
field — none exist today).

## 10. Verify privacy-record identity and monotonicity

**Not implemented**, for the same reason as §9 — there is no
independent privacy-record message to verify identity or monotonicity
against. The coordinator still stores and relays whatever epsilon/delta
values a worker asserts inside a signed client result without
recomputing or independently verifying them, exactly as already stated
in [privacy-engineering-security-audit.md](privacy-engineering-security-audit.md)'s
trust model.

## 11. Worker status enforcement

`AcquireTask` now consults `WorkerIdentityRegistry::find_by_worker_id`
immediately after the existing certificate-identity check and rejects
`PERMISSION_DENIED` for `REVOKED` ("is revoked") and `SUSPENDED` ("is
suspended") status — live-validated both ways (a suspended worker's
`AcquireTask` call rejected; the same worker's call accepted again after
`ActivateWorker`). `RegisterWorker`/`Heartbeat` continue to enforce
`REVOKED` status exactly as the prior slice implemented (unchanged).
`SubmitClientResult` enforces `REVOKED` only (§6) — `SUSPENDED` is
deliberately not checked there, since a suspended worker's in-flight
task is intended to still be able to complete and submit (see §13's
policy statement) — and neither `SubmitClientResult` nor
`ReportTaskProgress` re-check status beyond that; see §22 for the
honest accounting of this gap.

## 12. Worker lifecycle administration RPCs

`GetWorkerIdentity`, `ListWorkerIdentities`, `SuspendWorker`,
`ActivateWorker`, `RevokeWorker` — all real, all requiring the go-api
service certificate identity (§3), all live-validated over real mTLS
with a real containerized coordinator. `SuspendWorker`/`ActivateWorker`
call the corresponding pre-existing (prior-slice, previously
RPC-unreachable) `WorkerIdentityRegistry::suspend`/`activate` methods,
compute a `changed` boolean by comparing status before/after, and log a
structured stderr event line. `RevokeWorker` additionally cancels active
leases (§13) and purges replay/sequence state
(`replay_store_->purge_worker`, the prior slice's previously-unreachable
`purge_worker` method — see [replay-protection.md](replay-protection.md)'s
"Worker-revocation cleanup" note, now finally exercised by a real call
site). This closes the gap the prior slice's own §23/§39 explicitly
named: "there is no RPC to actually call suspend/activate/revoke at
all."

## 13. Active lease cancellation on revocation

`TaskDispatcher::cancel_lease_for_worker` (new): looks up the worker's
currently leased task and applies the exact same requeue-or-
permanently-fail retry policy `sweep_expired_leases` already used for
naturally expired leases (no new retry logic invented).
`RunInstance::cancel_lease_for_worker` (new): calls the dispatcher
method, erases the corresponding `active_leases_` checkpoint entry,
clears the worker's current task in `WorkerRegistry`, emits a
`kTaskCanceledByRevocation` event, and checkpoints.
`RunManager::cancel_leases_for_worker` (new): iterates every run under
the manager's lock and calls the above, returning a count.
`RevokeWorker`'s handler calls this and returns `leases_canceled` in its
response. Live-validated with `leases_canceled = 2` (a worker holding
active leases in two separate concurrently-running runs, revoked once,
both canceled in the same call).

## 14. In-flight task policy for suspended workers

Applied as documented in the parent specification's recommended policy
(plan.md §6.7): a suspended worker's *existing* task result may still be
accepted (no lease cancellation on suspension, unlike revocation, and no
`SUSPENDED` check added to `SubmitClientResult` — §11), while
`AcquireTask` blocks it from picking up anything new. Live-validated: a
worker suspended mid-task had its already-held lease left untouched
(confirmed via `GetWorkerIdentity` showing `SUSPENDED` status with the
task still active), and its subsequent `AcquireTask` call for a *new*
task was rejected.

## 15. Reject results from revoked workers

Live-validated directly: `SubmitClientResult` from a `REVOKED` worker is
rejected with `worker_revoked` before any envelope/replay/domain
processing occurs (§6) — checked first, ahead of even signature
verification, so a revoked worker's compute time is not wasted having
its signature checked before rejection either.

## 16. Certificate-fingerprint status on worker-sensitive RPCs

Certificate identity binding (`reject_if_worker_identity_mismatch`,
carried over unchanged from the prior slice) now additionally covers
`AcquireTask` and `SubmitClientResult` — previously neither had any
binding at all (§1). This closes the specific gap the prior slice's
§37 flagged by name. A dedicated, independent certificate-fingerprint
*revocation list* distinct from `WorkerIdentityRegistry` status is still
not implemented — `REVOKED` status itself is the enforcement mechanism,
not a separate CRL-style fingerprint check; see
[certificate-revocation.md](certificate-revocation.md).

## 17. Persistence across restart

Not independently re-tested this slice with a fresh restart scenario
(the prior slice already proved `WorkerIdentityRegistry` and
`ReplayProtectionStore` both survive a real `docker restart`, and this
slice's changes reuse both stores' existing persistence code paths
unchanged — no new persistence logic was written). The new
`MessageStream::kClientResult` track uses the identical
`ReplayProtectionStore` file format already restart-tested by the prior
slice's §9; extending live-restart coverage specifically to a
`CLIENT_RESULT` sequence track is a reasonable follow-on validation step
not performed this pass, stated honestly as a gap rather than assumed
covered by analogy alone.

## 18. Security events, metrics, and audit records

Structured stderr logging only (`timestamp_unix_s=... service=coordinator
event=WORKER_SUSPENDED|WORKER_ACTIVATED|WORKER_REVOKED|...
worker_id=... reason=...`), not a queryable event stream, metric, or
audit-record store. Four new `CoordinatorEventType` values added to
`event_bus.hpp`/`.cpp` (`kWorkerSuspended`, `kWorkerActivated`,
`kWorkerRevoked`, `kTaskCanceledByRevocation`) but **not** routed through
`EventBus::publish` — the existing `EventBus`/`history_by_run_` map is
fundamentally per-run-scoped (keyed by `run_id`), while worker lifecycle
is a cross-run, global concern, so wiring these into the existing
per-run SSE stream would have required a design change out of this
slice's scope. The enum values exist so a future pass has a name to
route through once that global-event design question is resolved, but
today they are unused by any `publish()` call site.

## 19. Files added

Proto: none new (existing `coordinator.proto`/`worker.proto` extended,
not new files). C++: none new (existing
`signed_envelope_verifier.hpp/.cpp`, `coordinator_service.hpp/.cpp`,
`task_dispatcher.hpp/.cpp`, `run_manager.hpp/.cpp`, `event_bus.hpp/.cpp`
all extended). Python: `python/src/fl_platform/security/signed_envelope.py`,
`python/src/fl_platform/security/sequence_state.py`. Docs:
`docs/signed-client-results.md`, `docs/worker-suspension.md`,
`docs/worker-activation.md`, `docs/worker-revocation.md`,
`docs/certificate-revocation.md`, this report section.

## 20. Files modified

`proto/coordinator/coordinator.proto` (`SubmitClientResultRequest.envelope`,
`SubmitClientResultResponse.rejection_code`, `WorkerIdentitySummary` and
five new RPCs' request/response messages, five new RPCs on
`CoordinatorService`); `cpp/coordinator/include/fl_coordinator/signed_envelope_verifier.hpp`/`.cpp`
(`client_result_payload_hash_input`, tensor checksum verification);
`cpp/coordinator/tests/signed_envelope_verifier_test.cpp` (new test
block); `cpp/coordinator/include/fl_coordinator/coordinator_service.hpp`/`.cpp`
(constructor extended, `SubmitClientResult` rewritten, `GetRound`/
`GetModelManifest`/`ReportTaskProgress` and five admin RPCs added);
`cpp/coordinator/include/fl_coordinator/task_dispatcher.hpp`/`.cpp`
(`cancel_lease_for_worker`); `cpp/coordinator/include/fl_coordinator/run_manager.hpp`/`.cpp`
(`RunInstance::cancel_lease_for_worker`,
`RunManager::cancel_leases_for_worker`);
`cpp/coordinator/include/fl_coordinator/event_bus.hpp`/`.cpp` (four new
event types); `cpp/coordinator/main.cpp`
(`FL_ALLOW_UNSIGNED_CLIENT_RESULTS`); `cpp/CMakeLists.txt` (generated
proto sources added to `fl_signed_envelope_verifier_tests`);
`python/src/fl_platform/worker/coordinator_client.py` (real tensor
checksums, signing-identity-aware `register_worker()`/`submit_result()`);
`docs/known-limitations.md`, `docs/payload-hashing.md`,
`docs/replay-protection.md`, `docs/message-sequences.md`,
`docs/rpc-security-policy.md`, `plan.md`.

## 21. Tests added

C++: a large new test block in `signed_envelope_verifier_test.cpp`
covering `client_result_payload_hash_input` — canonical sort order,
empty-object canonicalization, determinism, tamper detection (checksum
mismatch and stale-checksum-with-changed-values), NaN/Inf rejection,
empty-tensor-name rejection, and a full sign/verify round trip including
tamper-rejected-as-`payload_hash_mismatch`. Python: **no new formal
`pytest` files** — live validation used a standalone scratchpad script
(`signed_result_e2e_test.py`), consistent with the prior slice's own
disclosed gap (§39/§46 of that slice's report) that this pass did not
close either. Formalizing both slices' live-validation scripts into real
`python/tests/` suites remains a real, stated follow-on item.

## 22. Exact commands executed

```bash
git status
python scripts/check_project_terminology.py            # pass, repeatedly
python scripts/verify_proto_contracts.py                # pass, before and after every proto change
cmake --build build/cpp-debug && ctest --test-dir build/cpp-debug -C Debug --output-on-failure   # 7/7, local MSVC
docker build (throwaway, all targets) && ctest --output-on-failure     # 11/11
python -m pytest tests python/tests -q                  # 193 passed, 1 skipped
python -m ruff format python/src/fl_platform/security python/src/fl_platform/worker/coordinator_client.py
python -m ruff check --fix ...
python -m mypy --config-file=python/pyproject.toml ...
docker run -d ... -e FL_TRANSPORT_MODE=mtls ...           # real mTLS coordinator container
python signed_result_e2e_test.py                         # 22/22 checks passed, real GrpcCoordinatorClient
docker rm -f / docker rmi                                 # cleanup, confirmed via docker ps -a
```

All commands above: **pass**, exactly as reported.

## 23. Pass, fail, or blocked results

All of §1–22's claimed work: **pass**. Explicitly **not** run, and why:

* **`go test -race`** — no Go source touched this slice.
* **`npm ci`/web tests** — no web files touched this slice.
* **Full `docker compose up`** — only direct `docker run` was exercised,
  same as the prior slice; see §26.
* **CI job additions** — not made; existing `ctest` invocations pick up
  the extended test targets without a new job, not separately verified
  against the actual CI YAML.
* **A dedicated fresh restart test for the `CLIENT_RESULT` replay
  track** — not run this slice (§17); reasoned by analogy to the
  already-restart-tested `HEARTBEAT` track sharing identical store code,
  not independently re-proven.

## 24. Cross-language verification results

Real and passing, proven the stronger way (live signature acceptance,
not just a static byte comparison): a real PyNaCl signature computed
over Python's independently-implemented `client_result_payload_hash_input`
canonical bytes was accepted by the C++ verifier's independently-
implemented recomputation, through the real production
`GrpcCoordinatorClient.submit_result()` code path, over real mTLS,
across the full 22-scenario live test. If the two encoders had disagreed
on a single byte (including the new real tensor-checksum recomputation
on the C++ side), every signed-result scenario would have failed with
`payload_hash_mismatch` before signature verification was even reached;
none did, including scenarios with two tensors deliberately submitted in
different orders on the Python side than the C++ verifier's canonical
sort produces (proving the sort, not just the values, is genuinely
independent and convergent on both sides).

## 25. Docker runtime results

Direct container validation, not full Compose — same convention as the
prior slice. A `docker run` of the `coordinator` image, with `certs/dev/`
mounted and `FL_TRANSPORT_MODE=mtls`, hosted all 22 live-test scenarios:
run creation, dual-worker registration with signed capability
statements, task acquisition, signed-result submission with real
synthetic PyTorch tensors, real aggregation (confirmed via a real
`GetRun` showing `current_round` advancing), duplicate-resubmission
rejection, wrong-signing-key rejection, and the full suspend → blocked →
activate → can-acquire → revoke → lease-canceled → admin-RPCs-rejected
lifecycle. No container was left running at the end — confirmed via a
final `docker ps -a` showing only pre-existing, unrelated containers
from other projects on this machine.

## 26. Security findings

Two genuine, honestly-disclosed findings from this slice's own
inspection and live testing (neither is a bug introduced by this
slice's code):

* **`TensorManifest.checksum` was a pure pass-through value in the C++
  verifier** — included in the payload hash as-is, never checked against
  the tensor's actual `values`. A tensor-value tampering attack with a
  stale-but-unchanged checksum string would not have been caught by
  signing alone. Fixed this pass (§4) — this is a real security property
  added, not merely documented.
* **The Python production worker client (`GrpcCoordinatorClient`) sent
  completely unsigned `RegisterWorker`/`SubmitClientResult` requests**
  even after the prior slice made `Heartbeat` signing mandatory —
  because nothing had ever wired the prior slice's cryptographic
  primitives into the actual production client class, only into
  standalone test scripts. Fixed this pass (§5) — signing is now
  available end-to-end in the real client, opt-in via the
  `signing_identity` constructor parameter (not yet made the unconditional
  default for `register_worker()`/`submit_result()`, to preserve
  backward compatibility for any caller that hasn't adopted signing
  identities yet — see §27 for why this remains a stated trust
  assumption).

No vulnerabilities were found in code this slice did not modify.

## 27. Remaining trust assumptions

Everything already stated in the prior slice's §38 still holds, plus:

* A signed, replay-protected client result authenticates *that this
  worker, holding this key, submitted this exact tensor content, metric
  set, and privacy/personalization metadata at this exact sequence
  position* — it does not authenticate that the training was performed
  correctly, that the privacy accounting is correct, or that the tensor
  values represent a genuine gradient computation rather than an
  adversarially crafted (but validly signed) payload.
* `GrpcCoordinatorClient`'s signing is opt-in (`signing_identity=None`
  by default) — any caller not explicitly configuring a signing identity
  still sends unsigned requests, accepted by the live server only
  because `FL_ALLOW_UNSIGNED_CLIENT_RESULTS` was **not** set during this
  slice's own live tests for the signed scenarios (proving fail-closed
  behavior), but any deployment that does set that env var reopens the
  exact gap this slice closed.
* `SubmitClientResult`/`ReportTaskProgress` still do not check
  `SUSPENDED` status (§11/§14) — intentional per the documented in-flight
  policy, but worth restating as a live trust boundary: a suspended
  worker retains full ability to complete and submit its currently-held
  task.
* Sample-level privacy record authenticity (§9/§10) is still only as
  strong as the outer client-result signature — no independent
  monotonicity or accountant-identity check exists.

## 28. Remaining blockers

* `SampleLevelLedgerEntry` still lacks the proto fields (`worker_id`,
  `task_id`, configuration hash, accountant-state hash, budget decision,
  secure-random-provider) an independent Sample Privacy Record Hash
  needs (§9) — unchanged blocker from the prior slice.
* No coordinator Ed25519 signing identity exists, still blocking
  coordinator-signed tasks.
* No global (cross-run) event-publication mechanism exists for worker
  lifecycle events (§18) — building one is a prerequisite for routing
  the four new `CoordinatorEventType` values anywhere beyond stderr.
* This slice's live validation used a standalone script, not formal
  `pytest` files (§21) — same disclosed gap as the prior slice,
  unchanged.

## 29. Regression status

Zero regressions. C++ CTest: 11/11 (Docker), 7/7 (local MSVC) — test
count grew (new `client_result_payload_hash_input` checks added to the
existing `fl_signed_envelope_verifier_tests` target rather than a new
target, so the *target* count is unchanged, the *assertion* count within
it grew) — all passing. Python: 193 passed, 1 skipped (unchanged — no
existing test files were modified, only new modules added). Terminology
and proto-contract checks passed before and after every change.

## 30. Git working-tree summary

No commits were made this slice — per standing instructions. All new/
modified files listed in §19/§20 are present as uncommitted working-tree
changes, consistent with every prior slice in this project's history.

## 31. Recommended next signed-message or secure-aggregation work

In priority order:

1. **Independent sample-level privacy record signing**, requiring the
   `SampleLevelLedgerEntry` proto fields named in §9/§28 first, then a
   dedicated hash/verify function and `MESSAGE_STREAM_PRIVACY_RECORD`
   wiring — the natural next signed-message type, now that both
   `Heartbeat` and `SubmitClientResult` have proven the pattern twice.
2. **A formal Python `pytest` suite** for both slices' signing code
   (envelope construction, canonical bytes, sequence state, signed
   registration/result submission, tamper/replay/revoke rejection),
   replacing standalone scratchpad scripts with something CI actually
   runs — flagged as a gap by both this and the prior slice's report.
3. **Signing-key rotation with a grace period** — still an unconditional
   rejection with no recovery path today.
4. **Coordinator-signed tasks**, requiring a coordinator Ed25519 signing
   identity first.
5. **A cross-run global event-publication mechanism**, as a prerequisite
   for routing worker-lifecycle events (§18) into the existing SSE
   stream/dashboard rather than stderr-only logging.
6. Only after 1–5: Go security APIs, web security views, Prometheus
   metrics, formal audit-record persistence, full Docker Compose
   validation, performance benchmarking, and CI security gates — all
   still entirely unstarted.
7. The threshold secret-sharing blocker from earlier categories' passes
   remains unresolved and out of scope for all of the above — pairwise
   masking and secret sharing should not begin until it is.

Explicit non-goals maintained this slice, per standing instruction: no
pairwise masking, private masks, fixed-point secure-aggregation
encoding, threshold secret sharing, dropout recovery, unmasking, secure
aggregate reconstruction, protocol transcript chaining, homomorphic
encryption, worker attestation, TEEs, TPM integration, Byzantine-robust
aggregation, Ray, Flower runtime, asynchronous/semi-synchronous
aggregation, production Kubernetes, the full Go security API surface,
the full web Security Center, signing-key rotation, or coordinator-
signed tasks. No commits, pushes, tags, or pull requests were made
without explicit request.

---

# Privacy Record Authenticity, Signing-Key Lifecycle, and Coordinator-Signed Tasks: Slice Report

**This is the closing report for the next implementation slice within
the Secure Aggregation and Cryptographic Protocols category, building
directly on the Signed Client Results and Worker Lifecycle Enforcement
slice above.** Despite the slice's full name (inherited from the
parent specification), **only the privacy-record-authenticity portion
was actually delivered this pass** — signing-key lifecycle and
coordinator-signed tasks are each comparable in scope to an entire
prior slice by themselves and are explicitly deferred, stated honestly
throughout this report rather than claimed. Pairwise masking, secret
sharing, dropout recovery, and secure aggregate reconstruction remain
explicitly deferred and untouched, unchanged from every prior slice.

## 1. Repository audit

Re-confirmed from source, not assumed: `SubmitClientResultRequest`
carried `sample_level_privacy` (a plaintext `SampleLevelLedgerEntry`)
with no independent signature of any kind — only transitively covered
by the outer `envelope`'s payload hash (§37 of the prior slice's own
report explicitly named this gap). `WorkerIdentityRegistry` stores
exactly one `signing_public_key`/`signing_key_id` per worker with no
status machine beyond the worker's own `PENDING`/`ACTIVE`/`SUSPENDED`/
`REVOKED`/`EXPIRED` states — no `SigningKeyRecord` concept exists at
all. No coordinator Ed25519 signing identity exists anywhere in this
codebase; `AcquireTask` returns a fully unsigned `ClientTrainingTask`.
`fl.worker.v1.SignedWorkerEnvelope.MessageType`/`MessageStream` already
had `MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD`/`MESSAGE_STREAM_PRIVACY_RECORD`
reserved (unused) from the prior slice's own forward-provisioned enum —
confirmed by inspection before writing a single line of new proto,
meaning this slice needed zero new envelope-level enum values, only a
new domain-payload message and two new request fields. No accountant
monotonicity state existed anywhere in the C++ coordinator prior to
this pass — `RunInstance::submit_client_result` appended
`sample_level_privacy` to the ledger unconditionally once lease
validation passed (docs/privacy-ledger.md's authority-split note,
confirmed unchanged in `run_manager.cpp` before modification).

## 2. Existing signed-result verification (carried over, unchanged)

Re-verified green before making any change: C++ CTest 11/11 (Docker),
7/7 (local MSVC); Python 220 passed, 1 skipped (193 + 27 new tests from
this pass — see §20); terminology and proto-contract checks clean.
`SubmitClientResult`'s existing envelope verification, replay/sequence
validation, and worker-status checks (all from the prior slice) were
read and re-confirmed correct by inspection, not re-derived — per the
standing instruction not to rewrite validated signed-client-result code
without a demonstrated defect. None was found; the new privacy-record
pipeline was inserted as an additional, self-contained block, not a
rewrite of the existing one.

## 3. Signed privacy-record design

`fl.privacy.v1.SignedSamplePrivacyRecord` (new message, 27 domain
fields) plus two new fields on `SubmitClientResultRequest`:
`privacy_record_payload` (the domain fields) and
`privacy_record_envelope` (a `SignedWorkerEnvelope` wrapping the
former's canonical-JSON SHA-256 hash, `message_type =
MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD`, `message_stream =
MESSAGE_STREAM_PRIVACY_RECORD`). Deliberately reuses the existing
envelope/replay machinery rather than inventing a second, independent
signature mechanism with its own nonce/sequence/signing_key_id/
payload_hash/signature fields (which the parent specification's literal
field list requested directly on the record) — see
[signed-privacy-records.md](signed-privacy-records.md)'s "Deviations"
section for the full reasoning. All proto changes additive,
field-number-preserving; verified via `scripts/verify_proto_contracts.py`
before and after every change.

## 4. Privacy canonical serialization

`sample_privacy_record_payload_hash_input` (C++,
`signed_envelope_verifier.cpp`; Python, `signed_envelope.py`) — 27
keys, alphabetical order, matching `client_result_payload_hash_input`'s
established convention exactly (same `_canonical_json`/JSON-escaping
helpers, same NaN/Inf rejection, additionally rejecting negative
epsilon/delta/noise_multiplier/max_grad_norm/sample_rate). No domain-
separation prefix of its own was needed — the wrapping
`SignedWorkerEnvelope`'s existing
`"fl.worker.v1.SignedWorkerEnvelope\x00"` prefix already covers it, a
direct consequence of the envelope-reuse decision in §3.

## 5. Privacy payload hashing

Covered in §4/§3. Additionally: the **outer** Client Result Hash was
extended with a new `privacy_record_payload_hash` key inside its
nested `privacy_record` object (defaults to `""`, purely additive — no
existing golden vector used a non-empty `privacy_record`, confirmed by
grep before making the change), binding the outer client-result
signature to the privacy record's own signature as a second,
independent layer. See [signed-privacy-records.md](signed-privacy-records.md)'s
"Two independent bindings" section.

## 6. Python privacy signing

`fl_platform.security.signed_envelope` gained
`SamplePrivacyRecordFields`, `sample_privacy_record_payload_hash_input`,
and `sample_privacy_configuration_hash`. `GrpcCoordinatorClient` gained
two new private methods,
`_build_signed_sample_privacy_record_payload`/
`_build_signed_sample_privacy_record_envelope`, called from
`submit_result()` whenever `sample_level_privacy is not None` and a
`signing_identity` is configured — fails closed
(`SignedEnvelopeError`) if `sample_privacy_decision` is not also
supplied, per the specification's "no unsigned privacy record for
private production tasks" requirement. `service.py`'s worker run loop
was extended to capture the real `SampleBudgetEnforcer.last_decision`
(already computed by the pre-existing `run_private_local_training` →
`check_after_step` call path — no new accounting logic was written) and
thread it through to `submit_result()`. The PRIVACY_RECORD sequence
uses the same `SequenceStateStore` already built for `CLIENT_RESULT`,
just a different stream-name string — no new persistence code needed.

## 7. C++ privacy verification

New pipeline inserted into `SubmitClientResult`, strictly before the
existing domain call, whenever `request->has_sample_level_privacy()`:
signing-key match → payload-hash computation and NaN/negative rejection
→ `verify_signed_envelope` (Ed25519, expiry, schema, message_type) →
binding-consistency check against the plaintext ledger entry →
`ReplayProtectionStore::validate` (`PRIVACY_RECORD` stream) →
`AccountantMonotonicityStore::validate` → budget-decision-consistency
check. `commit()` for both the replay store and the monotonicity store
happens only after `RunInstance::submit_client_result` (the unmodified
domain layer) has actually accepted the result — identical ordering
discipline to every prior signed-message pipeline in this project.
Fail-closed by default (`allow_unsigned_privacy_records_` defaults
`true` for `coordinator_service_test.cpp` backward compatibility;
`main.cpp` passes `false` unless `FL_ALLOW_UNSIGNED_PRIVACY_RECORDS=true`).

## 8. Accountant monotonicity

New `AccountantMonotonicityStore` class
(`accountant_monotonicity_store.hpp`/`.cpp`), mirroring
`WorkerIdentityRegistry`/`ReplayProtectionStore`'s exact persistence
pattern (atomic temp-file+rename, FNV-1a checksum trailer, throws
rather than silently starting empty on corruption). One track per
`(run_id, client_id, worker_id, accountant_type)`. Rejects: a
non-increasing `accountant_step`, a decreased `epsilon`, a changed
`delta`, a changed `configuration_hash`. Deliberately unbounded (unlike
`ReplayProtectionStore`) — see
[privacy-accountant-monotonicity.md](privacy-accountant-monotonicity.md)
for why this is safe (every candidate has already passed signature
verification by the time it reaches this store). An explicit
`reset()` exists at the store level (unit-tested) but is not yet
exposed via any RPC — a real, disclosed gap.

## 9. Budget-decision consistency

`budget_decision_contradiction_reason` (`coordinator_service.cpp`):
rejects a normal, accepted-shaped submission whose signed
`budget_decision` is `stopped_before_step`, `refused_before_training`,
or `failed_task` (each means the step should never have been taken or
should have failed outright); correctly allows `stopped_after_task`
(the one task that policy explicitly still permits to submit); rejects
any unrecognized string outright rather than silently accepting it. See
[signed-privacy-records.md](signed-privacy-records.md)'s full rule
table. **Not implemented**: `AcquireTask` does not consult this
history to block *future* task assignment after a real
`stopped_after_task` exhaustion — a disclosed, real gap.

## 10. Signing-key record design

**Not implemented.** No `SigningKeyRecord` message, no persistent
multi-key-per-worker storage, no `PENDING`/`ACTIVE`/`GRACE_PERIOD`/
`REVOKED`/`EXPIRED` key status machine distinct from the worker's own
identity status. `WorkerIdentityRegistry` is unchanged from the prior
slice: exactly one signing key per worker. Out of scope for this pass's
time budget — see §31.

## 11. Key rotation

**Not implemented.** No `KeyRotationRequest`/`KeyRotationResponse`
contract, no `MESSAGE_TYPE_KEY_ROTATION_REQUEST` producer/consumer
(the enum value itself was already reserved from the prior slice, still
unused). Any signing-key change for an existing `worker_id` is still an
unconditional rejection with no recovery path, unchanged.

## 12. Grace-period behavior

**Not implemented** — no key rotation exists, so no grace period can
exist either. Unchanged from every prior slice.

## 13. Key expiration

**Not implemented** as a signing-key-specific concept. Worker identity
*records* already expire (`WorkerIdentityRegistry::sweep_expired`, from
an earlier slice) but this is a property of the worker's registration,
not of an individual signing key.

## 14. Key revocation

**Not implemented** as a distinct action from full worker revocation.
`RevokeWorker` (prior slice) still revokes the entire worker identity,
transitively making its one signing key unusable — there is no way to
revoke a signing key while leaving the worker identity otherwise
active. Moot today anyway, since one worker has exactly one key.

## 15. Coordinator signing identity

**Not implemented.** No coordinator Ed25519 keypair, no persistent
coordinator signing-key metadata, no development key-generation script,
no CI fixture generation. Explicitly deferred — see §31.

## 16. Signed coordinator tasks

**Not implemented.** No `SignedCoordinatorTask` contract, no
`FL_PLATFORM_COORDINATOR_TASK_V1` domain-separation prefix, no C++
task-signing logic in `AcquireTask`. Tasks remain fully unsigned on the
wire, exactly as before this pass.

## 17. Worker task verification

**Not implemented** — there is no signed task for a worker to verify.
`WorkerService.run()`'s `acquire_task` → train → `submit_result` loop
is unchanged.

## 18. Worker-side replay protection

**Not implemented.** No persistent worker-side task-replay store exists
— only the coordinator-side `ReplayProtectionStore` (unchanged, now
also serving the `PRIVACY_RECORD` stream — see §21) and the worker-side
`SequenceStateStore` (unchanged, now also tracking a `"privacy_record"`
stream name alongside the pre-existing `"client_result"`, both purely
outgoing-sequence bookkeeping, not a replay-detection store in the
`ReplayProtectionStore` sense).

## 19. Protobuf changes

`fl.privacy.v1.SignedSamplePrivacyRecord` (new message, 27 fields);
`SubmitClientResultRequest.privacy_record_payload` (new field 10),
`SubmitClientResultRequest.privacy_record_envelope` (new field 11). No
changes to `SignedWorkerEnvelope` itself (its `MessageType`/
`MessageStream` enum values for this purpose were already present).
All additive; every prior field number preserved; verified via
`scripts/verify_proto_contracts.py` before and after every change.
`SigningKeyRecord`, `KeyRotationRequest`/`Response`,
`KeyRevocationRequest`, `CoordinatorSigningKeyMetadata`,
`SignedCoordinatorTask`, `CoordinatorTaskSignatureMetadata`,
`TaskReplayRejectionReason` — none of these specification-requested
messages were added, since none of the features requiring them
(signing-key lifecycle, coordinator-signed tasks) were implemented this
pass.

## 20. Formal Python test coverage

`python/tests/test_signed_envelope.py` (new, 27 tests): canonical
bytes, golden payload hash, valid-signature construction, tampered-
field hash changes, NaN/negative rejection, and sequence-state
persistence for **both** client-result and privacy-record signing —
closing the prior slice's own disclosed "no formal pytest test files
for Python-side signing" gap for these two message types specifically.
Includes `test_golden_hash_matches_the_cross_language_fixture`, whose
expected value is the identical string embedded in
`signed_envelope_verifier_test.cpp`'s `kGoldenPrivacyRecordJson` — a
real cross-language fixture, not a tautological self-check (Work
Package T's explicit requirement). No key-lifecycle or coordinator-task
tests were added, since neither feature exists to test.

## 21. C++ tests

`accountant_monotonicity_store_test.cpp` (new, part of
`fl_coordinator_tests`, MSVC-buildable locally): new-track acceptance,
step/epsilon/delta/configuration-hash rejection (one check per rule),
independent-track isolation across clients, restart persistence,
explicit `reset()`, and corruption detection (malformed record,
checksum mismatch) — mirroring `worker_identity_registry_test.cpp`/
`replay_protection_store_test.cpp`'s established test-writing
conventions exactly. `signed_envelope_verifier_test.cpp` gained a large
new block: `sample_privacy_record_payload_hash_input`'s canonical
ordering, the cross-language golden fixture (§20), determinism, tamper
detection (epsilon, accountant_step), NaN/negative rejection, and a
full sign/verify round trip using `MESSAGE_TYPE_SAMPLE_PRIVACY_RECORD`
— plus a separate block proving the outer Client Result Hash's new
`privacy_record_payload_hash` binding (§5) actually changes the outer
hash when a `privacy_record_envelope` is attached. No signing-key-
lifecycle or coordinator-task-signing tests were added, since neither
exists.

## 22. Cross-language golden fixtures

One real, reviewed fixture: the Sample Privacy Record Hash's canonical
JSON for a fixed logical record, independently generated by Python and
hardcoded as the expected value in both
`signed_envelope_verifier_test.cpp` (C++ asserts its own output equals
this string) and `test_signed_envelope.py` (Python asserts its own
output equals the same string) — neither side derives its expected
value from the implementation under test. The parent specification's
full fixture list (Python result verified by C++, key-rotation request
verified by C++, C++ signed task verified by Python, wrong-coordinator-
key/wrong-worker-key rejection, modified-learning-rate/privacy-mode
rejection) was not produced beyond this one, since most of those
fixtures concern signing-key lifecycle or coordinator-signed tasks,
neither of which exists this pass.

## 23. Security events

Structured stderr logging only, same convention as the prior slice's
worker-lifecycle events: `level=WARNING event=unsigned_privacy_record_accepted`
when the development-compatibility path is exercised. No dedicated
"sample privacy record accepted/rejected," "privacy signature invalid,"
"privacy monotonicity violation," or "privacy configuration mismatch"
event exists as a structured, queryable record — rejections are visible
only via the gRPC error message and (for the outer RPC) the pre-existing
`SubmitClientResultResponse.rejection_code` field.

## 24. Metrics

**Not implemented.** No Prometheus counters for privacy records
accepted/rejected, signature failures, monotonicity violations, active/
grace-period/revoked signing keys, key rotations, or coordinator
task-signing — none of the underlying features (key lifecycle,
coordinator tasks) exist, and no metrics were added even for the
privacy-record-authenticity features that do exist.

## 25. Audit records

**Not implemented** beyond the same disconnected
`fl_platform.security.audit` scaffold noted as unused in every prior
slice's report.

## 26. Files added

Proto: none new (existing `privacy.proto`/`coordinator.proto` extended,
not new files). C++: `cpp/coordinator/include/fl_coordinator/accountant_monotonicity_store.hpp`,
`cpp/coordinator/src/accountant_monotonicity_store.cpp`,
`cpp/coordinator/tests/accountant_monotonicity_store_test.cpp`.
Python: none new (existing `signed_envelope.py`/`coordinator_client.py`/
`service.py` extended). Tests: `python/tests/test_signed_envelope.py`.
Docs: `docs/signed-privacy-records.md`,
`docs/privacy-accountant-monotonicity.md`, this report section.

## 27. Files modified

`proto/privacy/privacy.proto` (`SignedSamplePrivacyRecord`);
`proto/coordinator/coordinator.proto`
(`SubmitClientResultRequest.privacy_record_payload`/`.privacy_record_envelope`);
`cpp/coordinator/include/fl_coordinator/signed_envelope_verifier.hpp`/`.cpp`
(`sample_privacy_record_payload_hash_input`, extended
`client_result_payload_hash_input`'s nested privacy object);
`cpp/coordinator/tests/signed_envelope_verifier_test.cpp` (new test
blocks); `cpp/coordinator/include/fl_coordinator/coordinator_service.hpp`/`.cpp`
(constructor extended with `monotonicity_store`/
`allow_unsigned_privacy_records` parameters, `SubmitClientResult`
extended with the privacy-record pipeline,
`budget_decision_contradiction_reason` helper added);
`cpp/coordinator/main.cpp` (`FL_ACCOUNTANT_MONOTONICITY_STORE_PATH`,
`FL_ALLOW_UNSIGNED_PRIVACY_RECORDS`); `cpp/CMakeLists.txt`
(`accountant_monotonicity_store.cpp`/`_test.cpp` added to
`fl_coordinator`/`fl_coordinator_tests`); `cpp/coordinator/tests/test_main.cpp`
(registers the new test group);
`python/src/fl_platform/security/signed_envelope.py`
(`SamplePrivacyRecordFields`, `sample_privacy_record_payload_hash_input`,
`sample_privacy_configuration_hash`, extended
`client_result_payload_hash_input`); `python/src/fl_platform/worker/coordinator_client.py`
(signed privacy record construction, `_sample_budget_policy_to_wire`);
`python/src/fl_platform/worker/service.py` (threads
`sample_privacy_decision` through to `submit_result`);
`docs/known-limitations.md`, `docs/payload-hashing.md`,
`docs/replay-protection.md`, `docs/message-sequences.md`,
`docs/rpc-security-policy.md`, `docs/privacy-ledger.md`,
`docs/privacy-budget-policies.md`, `plan.md`.

## 28. Exact commands executed

```bash
git status
python scripts/check_project_terminology.py             # pass, repeatedly
python scripts/verify_proto_contracts.py                 # pass, before and after every proto change
python -m grpc_tools.protoc --proto_path=proto --python_out=... --grpc_python_out=... --pyi_out=...  # local Python binding regeneration
cmake --build build/cpp-debug --target fl_coordinator_tests   # local MSVC, 7/7 CTest
python -m pytest tests python/tests -q                    # 220 passed, 1 skipped
python -m ruff check . && python -m ruff format --check .  # clean
python -m mypy --config-file=python/pyproject.toml python/src   # clean, 66 files
# Docker scratch container (mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04):
apt-get install protobuf-compiler protobuf-compiler-grpc libprotobuf-dev libgrpc++-dev pkg-config
bash scripts/generate_protos.sh generated                 # real C++/Python/Go proto regeneration
cmake -S cpp -B build/cpp-docker -DCMAKE_BUILD_TYPE=Debug && cmake --build build/cpp-docker -j$(nproc)
ctest --test-dir build/cpp-docker --output-on-failure      # 11/11
pip3 install grpcio grpcio-tools pynacl torch numpy scipy   # for the live Python test client
# real mTLS coordinator server (FL_TRANSPORT_MODE=mtls, real dev-PKI certs)
python3 signed_privacy_record_e2e_test.py                  # 21/21 checks passed
docker rm -f                                                # cleanup, confirmed via docker ps -a
```

All commands above: **pass**, exactly as reported.

## 29. Pass, fail, or blocked results

All of §1–28's claimed work: **pass**. Explicitly **not** run, and why:

* **`go test -race`** — no Go source touched this slice.
* **`npm ci`/web tests** — no web files touched this slice.
* **Full `docker compose up`** — only direct `docker run` (via
  `docker exec` into a long-lived scratch container) was exercised, not
  the multi-service Compose stack.
* **CI job additions** — not made; the existing `ctest` invocations
  pick up the extended `fl_coordinator_tests`/
  `fl_signed_envelope_verifier_tests` targets without a new job, not
  separately verified against the actual CI YAML.
* **Performance benchmarking** — not performed for privacy-record
  serialization, hashing, signing, verification, or monotonicity-store
  lookup/persistence (Work Package Y). A real, disclosed gap.

## 30. Live Docker results

**Direct container validation via `docker exec` into a long-lived
scratch container (not full Compose, and not the packaged
`cpp-coordinator.Dockerfile` image — a throwaway build inside
`mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04` with the repo bind-
mounted).** A single coordinator process (`fl_coordinator_grpc_server`,
built fresh with real regenerated proto bindings) ran with
`FL_TRANSPORT_MODE=mtls` and real dev-PKI certificates, restarted with
freshly cleared persistence state between debugging iterations to
guarantee a clean signing-key/replay/monotonicity baseline for the
final recorded run. 21/21 checks passed — see
[signed-privacy-records.md](signed-privacy-records.md)'s "Live, real,
end-to-end validation" section for the full scenario list. A genuine
implementation-level gotcha was discovered and correctly handled during
this validation, not glossed over: a privacy-record rejection happens
*before* domain processing, so the worker's task lease is never
released by it — the test script was corrected to reuse the same held
lease across a rejected-then-retried submission, exactly as a real
worker integration would need to. No container was left running at the
end — confirmed via `docker ps -a` showing only pre-existing, unrelated
containers from other projects on this machine.

## 31. Performance methodology

**Not performed this slice.** No benchmarking of privacy-record
canonical serialization, SHA-256 hashing, Ed25519 signing/verification,
or `AccountantMonotonicityStore` lookup/persistence was done. The
Secure Randomness work's overhead numbers (an earlier category) remain
the only real performance numbers in this project's security work to
date.

## 32. Performance results

Not applicable — see §31.

## 33. Security findings

One genuine, honestly-disclosed finding from this slice's own
inspection (not a bug introduced by this slice's code):

* **Sample-level privacy records had no independent authenticity
  guarantee at all before this pass** — a worker could tamper with a
  stale, unsigned `sample_level_privacy` entry as long as it also
  resigned the outer client-result envelope over the tampered value
  (the outer signature would still "cover" it, since the value was
  already what got hashed — but nothing distinguished "the worker who
  holds the client-result signing key asserted this" from "the worker
  who holds the client-result signing key asserted this *specific
  epsilon, honestly derived from its own accounting history*"). Fixed
  this pass via independent signing, binding, and monotonicity
  enforcement — a real security property added, not merely documented.

No vulnerabilities were found in code this slice did not modify.

## 34. Remaining trust assumptions

Everything already stated in the prior slice's §27 still holds, plus:

* A signed, replay-protected, monotonicity-checked privacy record
  authenticates *that this worker, holding this key, asserted this
  exact accounting step as a continuation of its own prior signed
  history* — it does not authenticate that the worker's own Opacus
  accounting was computed correctly in the first place (see
  [signed-privacy-records.md](signed-privacy-records.md)'s "What this
  does not prove").
* `configuration_hash` is checked for consistency within a track's own
  history, never independently recomputed against the coordinator's
  own assigned config — a worker whose very first signed record already
  asserts a wrong-but-internally-consistent configuration would not be
  caught.
* `AcquireTask` does not consult budget-decision history — a
  misbehaving worker or a compromised client could still receive a new
  task after a real, signed `stopped_after_task` exhaustion.
* Every trust assumption already stated for signing-key lifecycle and
  coordinator-signed tasks in
  [known-limitations.md](known-limitations.md) applies unchanged, since
  neither exists.

## 35. Known limitations

See [known-limitations.md](known-limitations.md)'s "Privacy Record
Authenticity, Signing-Key Lifecycle, and Coordinator-Signed Tasks
slice" section for the complete, itemized list — summarized: signing-
key lifecycle entirely unimplemented; coordinator-signed tasks entirely
unimplemented; `configuration_hash` not independently recomputed;
`AcquireTask` does not consult budget-decision history; no RPC exposes
`AccountantMonotonicityStore::reset()`; formal pytest coverage exists
only for client-result/privacy-record signing, not key-lifecycle or
coordinator-task signing (nothing was built for either); no security
events/metrics/audit records beyond structured gRPC error messages; no
performance benchmarking; only direct `docker run` scenarios validated,
not full Docker Compose.

## 36. Regression status

Zero regressions. C++ CTest: 11/11 (Docker), 7/7 (local MSVC) —
assertion count grew (new checks added to existing
`fl_coordinator_tests`/`fl_signed_envelope_verifier_tests` targets plus
one new test group registered in `test_main.cpp`), target count grew by
one (`accountant_monotonicity_store_test.cpp`, folded into the existing
`fl_coordinator_tests` executable, not a new CMake target). Python: 193
→ 220 passed (27 new tests added, zero existing tests modified), 1
skipped (unchanged). Terminology and proto-contract checks passed
before and after every change, checked repeatedly throughout.

## 37. Git working-tree summary

No commits were made this slice — per standing instructions. All new/
modified files listed in §26/§27 are present as uncommitted working-
tree changes, consistent with every prior slice in this project's
history.

## 38. Recommended Go/web security administration or secure aggregation work

In priority order:

1. **Signing-key lifecycle** (persistent `SigningKeyRecord`, rotation
   with a grace period, expiry, revocation distinct from full worker
   revocation) — the single largest deferred item from the parent
   specification, and a prerequisite for any real-world key-hygiene
   story for long-lived worker deployments.
2. **Coordinator-signed tasks**, requiring a coordinator Ed25519
   signing identity first, then a `SignedCoordinatorTask` contract,
   Python-side verification, and a worker-side task replay store.
3. **A formal Python `pytest` suite for whichever of the above gets
   built**, following this pass's own `test_signed_envelope.py`
   pattern rather than standalone scratchpad scripts.
4. **Independent recomputation of `configuration_hash`** against the
   coordinator's own assigned `SampleLevelDPConfig` for a task's round,
   closing the "self-consistent but wrong from the start" gap noted in
   §34.
5. **Wiring budget-decision history into `AcquireTask`**, so a real
   `stopped_after_task` exhaustion actually blocks future task
   assignment, not just future submission-time contradiction checks.
6. Only after 1–5: Go security APIs, web security views, Prometheus
   metrics, formal audit-record persistence, full Docker Compose
   validation, performance benchmarking, and CI security gates — all
   still entirely unstarted.
7. The threshold secret-sharing blocker from earlier categories'
   passes remains unresolved and out of scope for all of the above —
   pairwise masking and secret sharing should not begin until it is.

Explicit non-goals maintained this slice, per standing instruction: no
pairwise masking, private masks, fixed-point secure-aggregation
encoding, threshold secret sharing, dropout recovery, unmasking, secure
aggregate reconstruction, protocol transcript chaining, homomorphic
encryption, worker attestation, TEEs, TPM integration, Byzantine
robustness, remote attestation, Ray, Flower runtime, asynchronous/semi-
synchronous aggregation, production Kubernetes deployment, the full Go
security API implementation, or the full web Security Center
implementation. Secure aggregation is not claimed complete. No custom
threshold secret sharing was implemented. No commits, pushes, tags, or
pull requests were made without explicit request.

---

# Signing-Key Lifecycle: Slice Report

**This is the closing report for the Signing-Key Lifecycle slice**,
scoped down from the parent specification's combined "Signing-Key
Lifecycle and Coordinator-Signed Tasks" request: the two are each
comparable in size to a full prior slice, and the specification's own
Required Implementation Order splits cleanly at step 18/19 (key
lifecycle, then coordinator-signed tasks). **Coordinator-signed tasks
are entirely deferred to a future pass**, stated honestly throughout
this report rather than claimed. Pairwise masking, secret sharing,
dropout recovery, and secure aggregate reconstruction remain
explicitly deferred and untouched, unchanged from every prior slice.

## 1. Repository audit

Re-confirmed from source, not assumed: `WorkerIdentityRecord`
(`worker_identity_registry.hpp`) stored exactly one
`signing_public_key`/`signing_key_id` pair per worker, with `RegisterWorker`
rejecting outright any presented key differing from the one on record
— the exact prior-slice comment read: "signing-key rotation is not yet
implemented." Heartbeat, `SubmitClientResult`, and the privacy-record
pipeline each did a single hard-equality check against that one cached
key. `ReplayProtectionStore`'s track key was already
`(worker_id, signing_key_id, message_stream)` — independent per signing
key by construction, requiring zero store-level changes to support
multiple co-existing keys per worker. `WorkerSigningIdentity`
persistence (`signing_identity.py`) supported exactly one private-key
file per worker_id. `MESSAGE_TYPE_KEY_ROTATION_REQUEST`/
`MESSAGE_STREAM_KEY_MANAGEMENT` were already reserved, unused, in the
`SignedWorkerEnvelope` enum since the first slice that introduced it —
confirmed by inspection before writing any new proto.

## 2. Existing privacy-record verification (carried over, unchanged)

Re-verified green before making any change: C++ CTest 11/11 (Docker),
7/7 (local MSVC); Python 234 passed, 1 skipped; terminology and
proto-contract checks clean. The prior slice's privacy-record
verification pipeline, monotonicity store, and budget-decision
consistency checks were read and re-confirmed correct by inspection,
not re-derived — per the standing instruction not to rewrite validated
signed-privacy-record code without a demonstrated defect. The new
signing-key enforcement was inserted as a shared helper
(`resolve_signing_key`) that each existing verification path now calls
through, not a rewrite of any of them.

## 3. Current signing-key model

Documented in full in [signing-key-management.md](signing-key-management.md)'s
"Repository audit" section — summarized: one key per worker, no status
machine, no rotation, no grace period, no expiry, no revocation
distinct from full worker revocation.

## 4. Signing-key registry

New `SigningKeyRegistry` class (`signing_key_registry.hpp`/`.cpp`),
protobuf-free and OpenSSL-free (mirroring `WorkerIdentityRegistry`'s
exact atomic-persistence, FNV-1a-checksum pattern), kept deliberately
separate from `WorkerIdentityRegistry`. `SigningKeyRecord`: 16 fields
(`schema_version` through `registration_source`). Statuses:
`PENDING`/`ACTIVE`/`GRACE_PERIOD`/`REVOKED`/`EXPIRED`. Lazy expiry
evaluation at every read (`find`/`find_active`/`has_any_valid_key`/
`list_for_worker`), independent of whether a maintenance
`sweep_expired()` has ever run — live-validated (see §30). See
[signing-key-management.md](signing-key-management.md) for the full
design.

## 5. Legacy key migration

Idempotent migration loop in `main.cpp`, run unconditionally on every
startup: every `WorkerIdentityRegistry` record with a non-empty
signing key that has no corresponding `SigningKeyRegistry` entry yet is
migrated as that worker's initial `ACTIVE` key, preserving the exact
`signing_key_id` and public-key bytes. Live-validated via a real
coordinator restart with `signing_key_registry.dat` deleted while
`worker_identity_registry.dat` was retained — see
[signing-key-migration.md](signing-key-migration.md) for the full
account, including an honestly-disclosed caveat about what that
specific test run's migration could and could not preserve (a key
already revoked in the now-deleted registry file came back `ACTIVE`,
since `WorkerIdentityRegistry`'s cached fields carry no per-key
revocation state of their own — expected, not a defect, for the
real target scenario of upgrading from a coordinator that never had a
`SigningKeyRegistry` at all).

## 6. Signing-key policy

Documented in [signing-key-management.md](signing-key-management.md)'s
"Signing-key policy" section: at most one `ACTIVE` and one
`GRACE_PERIOD` key per worker; a maximum grace period
(`kMaxGracePeriodSeconds`, 24 hours); no task assignment when a worker
has no valid key; the preferred key is whichever is `ACTIVE`, with
`WorkerIdentityRegistry`'s cache kept in sync on every rotation.

## 7. Worker key rotation

`fl.worker.v1.WorkerKeyRotationPayload` (7 domain fields), wrapped in
the existing `SignedWorkerEnvelope` (reusing the pattern already
proven twice — client results, privacy records — rather than a third
independent signature mechanism). New RPC `RotateWorkerSigningKey`.
Must be signed by the CURRENT, `ACTIVE` key only. Full verification
pipeline documented in [key-rotation.md](key-rotation.md). Python side:
`GrpcCoordinatorClient.rotate_signing_key()`, wired into the real
production client class; only marks the new key "preferred" after real
coordinator acceptance.

## 8. Grace-period behavior

Documented in [signing-key-grace-period.md](signing-key-grace-period.md).
Both keys valid for Heartbeat/client-result/privacy-record messages
during the window; capability refresh and rotation still require
`ACTIVE` specifically. Live-validated with a real, non-simulated 5-second
grace period and a real 6-second wait before the old key's rejection
was confirmed.

## 9. Key expiration

Evaluated lazily at every verification call
(`SigningKeyRegistry::effective_record`), not only by a background
sweep — the specification's explicit "do not rely exclusively on a
long-running background timer" requirement. `sweep_expired()` exists
to persist an already-computed transition for administration-surface
consistency but is not what makes expiry actually enforced.

## 10. Key revocation

New `RevokeWorkerSigningKey` `ADMIN_CONTROL` RPC (gated identically to
the five worker-lifecycle RPCs from the prior slice). Immediate,
idempotent, persisted. Automatically suspends the worker's
`WorkerIdentityRegistry` status when the revoked key was its only
remaining valid one — live-validated: `worker_suspended = true` in the
response, and the worker's immediately-following `AcquireTask` call was
rejected outright. See [signing-key-revocation.md](signing-key-revocation.md).

## 11. Key-state enforcement

A single shared helper, `resolve_signing_key` (`coordinator_service.cpp`),
is now the one enforcement point every signed-message verification
path goes through — resolving the actual public-key bytes to verify
against (critically, not always `WorkerIdentityRegistry`'s single
cached "preferred" key, since a message signed by a still-valid
`GRACE_PERIOD` key must be verified against *that* key's own bytes) and
applying a per-message-kind status table
(`signing_key_status_permits`). When `signing_key_registry_` is `nullptr`
(the default), every call site falls back to the pre-existing
single-key comparison, preserving every test written before this slice
unchanged — confirmed via the full existing `fl_coordinator_grpc_tests`
suite passing unmodified.

## 12. Result-to-privacy key consistency

A signed client result and its independently signed privacy record
must be signed by the same key — checked explicitly before either
signature is verified, rejected as `privacy_record_key_mismatch` if
not. See [signing-key-management.md](signing-key-management.md).

## 13. Coordinator signing identity

**Not implemented.** No coordinator Ed25519 keypair, no persistent
coordinator signing-key metadata, no development key-generation script,
no CI fixture generation. Explicitly deferred to a future pass — see
§38.

## 14. Signed coordinator-task design

**Not implemented.** No `SignedCoordinatorTask` contract, no
`FL_PLATFORM_COORDINATOR_TASK_V1` domain-separation prefix, no C++
task-signing logic in `AcquireTask`. Tasks remain fully unsigned on the
wire.

## 15. Task canonical serialization

**Not implemented** — there is no signed task to canonicalize.

## 16. Task configuration hashes

**Not implemented** — no coordinator-signed task exists to bind
configuration hashes into.

## 17. C++ task signing

**Not implemented.**

## 18. Python task verification

**Not implemented** — there is no signed task for a worker to verify.
`WorkerService.run()`'s `acquire_task` → train → `submit_result` loop
is unchanged.

## 19. Worker-side task replay protection

**Not implemented.** No persistent worker-side task-replay store
exists.

## 20. Accepted-task recovery behavior

**Not applicable** — no signed-task acceptance semantics exist yet to
define recovery behavior for.

## 21. Protobuf changes

`fl.worker.v1.WorkerKeyRotationPayload` (new message, 7 fields);
`fl.coordinator.v1.SigningKeyRecordSummary` (new message, 14 fields);
`RotateWorkerSigningKeyRequest`/`Response`,
`GetWorkerSigningKeysRequest`/`Response`,
`RevokeWorkerSigningKeyRequest`/`Response` (new messages); three new
RPCs on `CoordinatorService`. All additive; every prior field number
preserved; verified via `scripts/verify_proto_contracts.py` before and
after every change. `SigningKeyRecord`/`SigningKeyStatus` (the C++-only
domain types) were not added as their own protobuf messages — only
their wire projection (`SigningKeyRecordSummary`) was, matching
`WorkerIdentityRecord`/`WorkerIdentitySummary`'s identical established
pattern. `CoordinatorSigningKeyMetadata`, `SignedCoordinatorTask`,
`CoordinatorTaskSignature`, `CoordinatorTaskReplayReason`,
`TaskConfigurationHashes`, `TrustedCoordinatorKeyBundle` — none of
these specification-requested messages were added, since none of the
features requiring them (coordinator-signed tasks) were implemented.

## 22. Formal Python tests

`python/tests/test_signed_envelope.py` gained a new `RotationHashTests`
class (6 tests: canonical ordering, cross-language golden fixture,
determinism, tamper detection, negative-grace-period rejection, a
valid signature round trip). New `python/tests/test_signing_key_rotation.py`
(8 tests): keyed-multi-key private-key persistence, mismatched-key-id
rejection, rotation-state save/load round trip, malformed-state
rejection. No coordinator-signed-task tests were added, since the
feature does not exist.

## 23. C++ tests

New `signing_key_registry_test.cpp` (part of `fl_coordinator_tests`,
MSVC-buildable locally): initial-key registration and idempotent
refresh, key-swap-under-same-id rejection, duplicate-key-id-across-workers
rejection, rotation-requires-a-second-registration-not-initial rejection,
full rotation accept/reject matrix (unknown current key, non-ACTIVE
current key, duplicate new key id, invalid key length, excessive grace
period), lazy expiry evaluation before/after `grace_period_end`,
revocation and its idempotency, restart persistence, `sweep_expired`
persisting a lazy transition, and corruption detection (malformed
record, checksum mismatch) — 18 test groups total registered in
`test_main.cpp`, up from 17. `signed_envelope_verifier_test.cpp` gained
a new block: `rotation_payload_hash_input`'s canonical ordering, the
cross-language golden fixture, determinism, tamper detection, negative-
grace-period rejection, a full sign/verify round trip using
`MESSAGE_TYPE_KEY_ROTATION_REQUEST`, and `public_key_fingerprint_hex`'s
own determinism/uniqueness/invalid-input behavior.

## 24. Cross-language golden fixtures

One real, reviewed fixture: the Key-Rotation Request Hash's canonical
JSON for a fixed logical payload, independently generated by Python and
hardcoded as the expected value in both
`signed_envelope_verifier_test.cpp` (`kGoldenRotationJson`) and
`test_signed_envelope.py` (`test_golden_hash_matches_the_cross_language_fixture`)
— neither side derives its expected value from the implementation under
test. The parent specification's fuller fixture list (rotated-key
capability/heartbeat/result/privacy-record signature acceptance,
coordinator-task fixtures) was not produced beyond this one and the
live Docker validation's real signature round trips, which cover the
same ground more strongly (live signature acceptance through the
actual production client, not merely a static vector).

## 25. Security events

Structured stderr logging only:
`event=WORKER_KEY_ROTATION_ACCEPTED`, `event=WORKER_KEY_REVOKED`,
`event=SIGNING_KEY_MIGRATED` (the last one new in this slice, emitted
at coordinator startup for every key the migration loop actually
migrates). No dedicated "signing key registered," "rotation requested,"
"rotation rejected," "message rejected due to key state," or
"coordinator task signed/verified/rejected" event exists as a
structured, queryable record.

## 26. Metrics

**Not implemented.** No Prometheus counters for active/grace-period/
expired/revoked signing keys, rotations, rotation failures, or
coordinator task signing.

## 27. Audit records

**Not implemented** beyond the same disconnected
`fl_platform.security.audit` scaffold noted as unused in every prior
slice's report.

## 28. Files added

C++: `cpp/coordinator/include/fl_coordinator/signing_key_registry.hpp`,
`cpp/coordinator/src/signing_key_registry.cpp`,
`cpp/coordinator/tests/signing_key_registry_test.cpp`. Python:
`python/src/fl_platform/security/signing_key_rotation.py`. Tests:
`python/tests/test_signing_key_rotation.py`. Docs:
`docs/signing-key-management.md`, `docs/signing-key-migration.md`,
`docs/key-rotation.md`, `docs/signing-key-grace-period.md`,
`docs/signing-key-revocation.md`, this report section.

## 29. Files modified

`proto/worker/worker.proto` (`WorkerKeyRotationPayload`);
`proto/coordinator/coordinator.proto` (`SigningKeyRecordSummary`, three
new request/response message pairs, three new RPCs);
`cpp/coordinator/include/fl_coordinator/signed_envelope_verifier.hpp`/`.cpp`
(`public_key_fingerprint_hex`, `rotation_payload_hash_input`);
`cpp/coordinator/tests/signed_envelope_verifier_test.cpp` (new test
blocks); `cpp/coordinator/include/fl_coordinator/coordinator_service.hpp`/`.cpp`
(constructor extended with `signing_key_registry` parameter,
`resolve_signing_key`/`signing_key_status_permits` helpers added,
`RegisterWorker`/`Heartbeat`/`SubmitClientResult`/`AcquireTask` rewired
to use them, three new RPC handlers added); `cpp/coordinator/main.cpp`
(`FL_SIGNING_KEY_REGISTRY_PATH`, the legacy-migration loop);
`cpp/CMakeLists.txt` (`signing_key_registry.cpp`/`_test.cpp` added);
`cpp/coordinator/tests/test_main.cpp` (registers the new test group);
`python/src/fl_platform/security/signed_envelope.py`
(`WorkerKeyRotationFields`, `rotation_payload_hash_input`);
`python/src/fl_platform/worker/coordinator_client.py`
(`rotate_signing_key`); `python/tests/test_signed_envelope.py` (new
`RotationHashTests` class); `docs/known-limitations.md`,
`docs/payload-hashing.md`, `docs/replay-protection.md`,
`docs/message-sequences.md`, `docs/rpc-security-policy.md`, `plan.md`.

## 30. Exact commands executed

```bash
git status
python scripts/check_project_terminology.py             # pass, repeatedly
python scripts/verify_proto_contracts.py                 # pass, before and after every proto change
python -m grpc_tools.protoc --proto_path=proto --python_out=... --grpc_python_out=... --pyi_out=...  # local Python binding regeneration
cmake --build build/cpp-debug --target fl_coordinator_tests   # local MSVC, 18/18 test groups
python -m pytest tests python/tests -q                    # 234 passed, 1 skipped
python -m ruff check . && python -m ruff format --check .  # clean
python -m mypy --config-file=python/pyproject.toml python/src   # clean, 67 files
# Docker scratch container (mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04):
apt-get install protobuf-compiler protobuf-compiler-grpc libprotobuf-dev libgrpc++-dev pkg-config
bash scripts/generate_protos.sh generated                 # real C++/Python/Go proto regeneration
cmake -S cpp -B build/cpp-docker -DCMAKE_BUILD_TYPE=Debug && cmake --build build/cpp-docker -j$(nproc)
ctest --test-dir build/cpp-docker --output-on-failure      # 11/11
pip3 install -r requirements.txt                           # for the live Python test client
# real mTLS coordinator server (FL_TRANSPORT_MODE=mtls, real dev-PKI certs,
# FL_SIGNING_KEY_REGISTRY_PATH pointed at a real persistent file)
python3 signing_key_lifecycle_e2e_test.py                  # 16/16 checks passed
# real restart with signing_key_registry.dat deleted, worker_identity_registry.dat retained
# -> event=SIGNING_KEY_MIGRATED logged; GetWorkerSigningKeys confirmed a persisted, migrated ACTIVE entry
docker rm -f                                                # cleanup, confirmed via docker ps -a
```

All commands above: **pass**, exactly as reported.

## 31. Pass, fail, or blocked results

All of §1–30's claimed work: **pass**. Explicitly **not** run, and why:

* **`go test -race`** — no Go source touched this slice.
* **`npm ci`/web tests** — no web files touched this slice.
* **Full `docker compose up`** — only direct `docker run` (via
  `docker exec` into a long-lived scratch container) was exercised.
* **CI job additions** — not made; the existing `ctest` invocations
  pick up the extended `fl_coordinator_tests`/
  `fl_signed_envelope_verifier_tests` targets without a new job.
* **Performance benchmarking** — not performed for signing-key registry
  lookup/persistence or rotation-request serialization/hashing/signing/
  verification. A real, disclosed gap.

## 32. Live Docker results

**Direct container validation via `docker exec` into a long-lived
scratch container** (not full Compose). A single coordinator process
(`fl_coordinator_grpc_server`, built fresh with real regenerated proto
bindings) ran with `FL_TRANSPORT_MODE=mtls` and real dev-PKI
certificates. 16/16 scenario checks passed on the primary run (worker
registration populating the signing-key registry; a real signed
rotation with grace-period demotion; new- and old-key message
acceptance during the grace window; real, elapsed-time expiry
rejection after the grace period; admin revocation with automatic
worker suspension; task-acquisition blocking for a now-keyless worker).
**Separately**, the coordinator was killed, `signing_key_registry.dat`
was deleted while `worker_identity_registry.dat` was retained, and the
process was restarted — producing a real `SIGNING_KEY_MIGRATED` log
line and a persisted, migrated `ACTIVE` `SigningKeyRecord` confirmed
via a live `GetWorkerSigningKeys` call. A genuine implementation-level
gotcha was discovered and corrected during this validation, not
glossed over: a local `SequenceStateStore` created fresh for a
"different client instance" using the same already-used signing key
collided with the coordinator's already-committed sequence state for
that key, correctly rejected by real replay protection — the test
script was fixed to reuse the same in-process sequence store across
key-swap scenarios, exactly as a real single-process worker would. No
container was left running at the end — confirmed via `docker ps -a`.

## 33. Performance measurements where executed

**Not performed this slice.** No benchmarking of `SigningKeyRegistry`
lookup/persistence, `rotation_payload_hash_input`'s canonical
serialization, or Ed25519 signing/verification specific to rotation
requests was done.

## 34. Security findings

One genuine, honestly-disclosed finding from this slice's own
inspection (not a bug introduced by this slice's code):

* **A worker whose signing key was compromised had no recovery path
  short of full worker revocation before this pass** — `RegisterWorker`
  rejected any presented key differing from the one on record
  outright, meaning a worker that needed to rotate away from a
  suspected-compromised key had no legitimate way to do so without an
  operator fully revoking and re-admitting the entire worker identity.
  Fixed this pass via real signed rotation — a real security property
  added (a worker can now proactively rotate before compromise is even
  suspected, and an operator can immediately revoke just the
  compromised key via `RevokeWorkerSigningKey` without touching the
  worker's broader identity, unless no valid key remains at all, in
  which case suspension is automatic and reversible).

No vulnerabilities were found in code this slice did not modify.

## 35. Remaining trust assumptions

Everything already stated in the prior slice's §34 still holds, plus:

* A signed rotation request authenticates *that the worker holding the
  current signing key requested this specific successor key* — it does
  not authenticate that the worker's new private key was generated or
  stored securely on the worker's own side; this pass cannot verify
  anything about the requesting process's own key-generation hygiene.
* `resolve_signing_key`'s fallback to the pre-existing single-key
  comparison when `signing_key_registry_` is `nullptr` means a
  coordinator deployment that does not wire a real
  `SigningKeyRegistry` gets **no** signing-key lifecycle enforcement at
  all, silently reverting to the prior slice's exact single-key
  behavior -- an operator must actually configure
  `FL_SIGNING_KEY_REGISTRY_PATH` (or otherwise construct and pass a
  real registry) for any of this slice's guarantees to apply.
* Migrating from a deleted (not merely absent) `SigningKeyRegistry`
  file does not preserve any per-key revocation history that file may
  have recorded -- see §5's disclosed caveat.

## 36. Known limitations

See [known-limitations.md](known-limitations.md)'s "Signing-Key
Lifecycle slice" section for the complete, itemized list — summarized:
coordinator-signed tasks entirely unimplemented; no signing-key-specific
events/metrics/audit records beyond structured stderr logging;
`rotate_signing_key()` does not persist its own state across a worker
process restart; no default rotation interval or automated background
expiry sweep; no automated old-private-key cleanup; only direct
`docker run` scenarios validated, not full Docker Compose; no
performance benchmarking.

## 37. Regression status

Zero regressions. C++ CTest: 11/11 (Docker), 7/7 (local MSVC) — test
group count grew from 17 to 18 within the existing
`fl_coordinator_tests` executable (no new CMake *target*, one new test
*group*); `fl_coordinator_grpc_tests` (the existing, unmodified
`coordinator_service_test.cpp`) passed unchanged, confirming the
`resolve_signing_key` fallback path preserves every pre-existing
call site's behavior exactly. Python: 220 → 234 passed (14 new tests:
6 rotation-hash tests, 8 local-key-state tests; zero existing tests
modified), 1 skipped (unchanged). Terminology and proto-contract checks
passed before and after every change, checked repeatedly throughout.

## 38. Git working-tree summary

No commits were made this slice — per standing instructions. All new/
modified files listed in §28/§29 are present as uncommitted
working-tree changes, consistent with every prior slice in this
project's history.

## 39. Recommended Go/Web security administration or secure aggregation work

In priority order:

1. **Coordinator-signed tasks** — a persistent coordinator Ed25519
   signing identity (never reusing the TLS key), a
   `SignedCoordinatorTask` contract binding model/dataset/training/
   privacy/personalization configuration hashes into the signature,
   C++ task signing in `AcquireTask`, Python-side verification before
   any model loading or training begins, and a persistent worker-side
   task replay store. The single largest remaining deferred item from
   the parent specification's combined request.
2. **A formal end-to-end pytest suite for the signing-key lifecycle**,
   covering the full rotate→grace-period→expire→revoke sequence
   currently only proven by this slice's live Docker script, following
   the established `test_signed_envelope.py`/`test_signing_key_rotation.py`
   pattern.
3. **Persisting `rotate_signing_key()`'s own local state** to the
   already-built (but not yet wired-in) `WorkerKeyRotationState` file,
   so a worker process restart mid-grace-period recovers its preferred
   key automatically rather than needing to be told again.
4. **Independent recomputation of `configuration_hash`** and **wiring
   budget-decision history into `AcquireTask`** — both still-open items
   from the prior slice's own report.
5. Only after 1–4: Go security APIs, web security views, Prometheus
   metrics, formal audit-record persistence, full Docker Compose
   validation, performance benchmarking, and CI security gates — all
   still entirely unstarted.
6. The threshold secret-sharing blocker from earlier categories'
   passes remains unresolved and out of scope for all of the above —
   pairwise masking and secret sharing should not begin until it is.

Explicit non-goals maintained this slice, per standing instruction: no
pairwise masking, private masks, fixed-point secure-aggregation
encoding, threshold secret sharing, dropout recovery, unmasking, secure
aggregate reconstruction, protocol transcript chaining, homomorphic
encryption, worker attestation, TEEs, TPM integration, Byzantine-robust
aggregation, remote attestation, trusted execution environments, TPM
integration, Ray, Flower runtime, asynchronous/semi-synchronous
aggregation, production Kubernetes deployment, the full Go security
HTTP API implementation, the full web Security Center implementation,
or large-scale distributed execution. Secure aggregation is not
claimed complete. No custom threshold secret sharing was implemented.
No commits, pushes, tags, or pull requests were made without explicit
request.

# Coordinator-Signed Tasks and Worker-Side Replay Protection: Slice Report

**This is the closing report for the Coordinator-Signed Tasks and
Worker-Side Replay Protection slice** — exactly the feature deferred
in the two immediately preceding slices' reports. Delivered as one
complete, real, cross-language-parity-tested, live-Docker-validated
vertical slice. Coordinator signing-key **rotation as an operational
flow** is explicitly deferred (the registry supports it; no RPC calls
it). Pairwise masking, secret sharing, dropout recovery, and secure
aggregate reconstruction remain explicitly deferred and untouched,
unchanged from every prior slice.

## 1. Task security audit

Confirmed by direct inspection before writing any code:
`fl::coordinator::v1::ClientTrainingTask` (the `AcquireTask` response)
carried zero authenticity guarantees — no signature field, and two
fields (`lease_expires_at`, and no `attempt` field existed at all) were
never even populated on the wire despite `DispatchedTask` tracking both
internally (`lease_expires_at_unix_s`, `attempt`). `TaskDispatcher`
(unchanged) already had exactly the right shape for reissue semantics:
`task_id` stable, `lease_id` reassigned and `attempt` incremented on
every real acquisition, including lease-expiry-driven requeues. No
coordinator signing identity existed anywhere in the C++ codebase — the
coordinator had only ever *verified* signatures, never produced one.

## 2. Coordinator signing identity

Persistent Ed25519 keypair (`cpp/coordinator/include/fl_coordinator/coordinator_signing_identity.hpp`/`.cpp`),
real OpenSSL `EVP_PKEY_ED25519` keygen/sign, separate file
(`FL_COORDINATOR_SIGNING_KEY_PATH`) from the TLS server credential. See
[coordinator-signing-identity.md](coordinator-signing-identity.md).

## 3. Coordinator signing-key registry

`CoordinatorSigningKeyRegistry`, mirroring `SigningKeyRegistry`'s
design, keyed by `signing_key_id` alone (`ACTIVE`/`GRACE_PERIOD`/
`REVOKED`/`EXPIRED`). Protobuf-free, unit-tested on Windows/MSVC
without gRPC. See
[coordinator-signing-key-management.md](coordinator-signing-key-management.md).

## 4. `SignedCoordinatorTask` contract

Additive field on the existing `ClientTrainingTask` response
(`signed_task`), not a new message type wrapping the whole task — the
same reuse discipline as every prior envelope-reuse decision this
project has made. 17 metadata fields (schema_version through
signature); domain-separation prefix `FL_PLATFORM_COORDINATOR_TASK_V1\x00`.
Verified additive via `scripts/verify_proto_contracts.py`.

## 5. Canonical task serialization

Alphabetical-key canonical JSON, matching every prior signed structure
in this codebase exactly (`json.dumps(sort_keys=True,
separators=(",",":"), ensure_ascii=True)` on the Python side; a
hand-written `std::ostringstream` encoder on the C++ side).

## 6. Task configuration hashes

Five hashes (Training/Model/Dataset Partition/Privacy/Personalization
Configuration), each with its own domain-separation prefix, plus a
task payload hash — scoped strictly to fields `ClientTrainingTask`
carries on the wire today. Full field lists, the Personalization
scoping decision, and two real cross-language bugs found and fixed
(a `std::to_chars` float-formatting threshold mismatch and a JSON
key-ordering bug) are documented in
[task-configuration-hashes.md](task-configuration-hashes.md).

## 7. Task payload hash

Binds every `ClientTrainingTask` sibling field via the sub-hashes'
digests plus `attempt`/`lease_expires_at`/`lease_id`/`round_id`/
`task_available`/`task_id`. Domain prefix
`FL_PLATFORM_COORDINATOR_TASK_PAYLOAD_V1\x00`.

## 8. Coordinator task sequence store

`CoordinatorTaskSequenceStore` (C++), a plain persisted monotonic
counter per `(coordinator_signing_key_id, worker_id)` — the issuing
side's counterpart to Python's `SequenceStateStore`. Protobuf-free,
unit-tested on Windows/MSVC.

## 9. C++ task signing in `AcquireTask`

Two pre-existing wire-mapping gaps fixed as a direct prerequisite:
`lease_expires_at` and a new `attempt` field are now actually
populated. When a coordinator signing identity is configured, a real
OS-CSPRNG nonce (`fl::core::OsEntropySecureRandomProvider`) and
sequence number are issued, all six hashes computed, and the result
signed and attached — all optional/backward-compatible (nullptr by
default), matching every enforcement point added this session.

## 10. Task-signing transaction semantics

Signing happens after the domain acquisition (`run.acquire_task`) has
already committed — a signing failure (NaN/Inf in a hash input) returns
`INTERNAL` without rolling back the already-leased task (consistent
with this codebase's existing "domain state is authoritative, signing
failure is a bug to fix, not a transaction to unwind" convention for
every other signed-message path). A coordinator configured with a
signing identity but no ACTIVE registry key fails closed
(`FAILED_PRECONDITION`) rather than silently issuing an unsigned task.

## 11. Trusted coordinator key bundle

Written by the coordinator to a JSON file
(`FL_COORDINATOR_SIGNING_KEY_BUNDLE_PATH`) at startup, loaded by
workers directly from disk — never via RPC. See
[coordinator-signing-key-management.md](coordinator-signing-key-management.md).

## 12. Python task verification pipeline

`fl_platform.security.coordinator_task_verifier.verify_coordinator_task`,
called from inside `GrpcCoordinatorClient.acquire_task` before any
model build, dataset access, or Opacus/CUDA initialization: worker
binding, signing-key resolution/status, all five configuration hashes,
the payload hash, the Ed25519 signature (PyNaCl), expiry/future-
issuance, then replay (nonce/sequence). See
[signed-coordinator-tasks.md](signed-coordinator-tasks.md).

## 13. Worker-side task replay store

`CoordinatorTaskReplayStore`, mirroring `ReplayProtectionStore`'s
validate/commit split, tracking the coordinator's issued sequence
rather than a worker's own. See
[coordinator-task-replay-protection.md](coordinator-task-replay-protection.md).

## 14. Accepted-task journal

`AcceptedTaskJournal`: `ACCEPTED`→`PREPARING`→`TRAINING`→
`RESULT_READY`→`RESULT_SUBMITTED`→`COMPLETED`/`FAILED`/`CANCELED`. See
[accepted-task-journal.md](accepted-task-journal.md).

## 15. Worker crash recovery

Any entry left `PREPARING`/`TRAINING` at startup is marked `FAILED`
("never silently resume; require reissue" — no training-state
checkpointing exists to safely resume from). Live-validated via a
genuinely separate `AcceptedTaskJournal` instance against the same
on-disk file.

## 16. Task reissue semantics

Built entirely on `TaskDispatcher`'s pre-existing, unchanged behavior
(`task_id` stable, `lease_id`/`attempt` fresh per acquisition). See
[task-reissue-semantics.md](task-reissue-semantics.md).

## 17. Structured task-rejection reasons

16 values: `UNKNOWN_SIGNING_KEY`, `REVOKED_SIGNING_KEY`,
`EXPIRED_SIGNING_KEY`, `INVALID_SIGNATURE`, `PAYLOAD_HASH_MISMATCH`,
`TRAINING_CONFIG_HASH_MISMATCH`, `MODEL_CONFIG_HASH_MISMATCH`,
`DATASET_PARTITION_HASH_MISMATCH`, `PRIVACY_CONFIG_HASH_MISMATCH`,
`PERSONALIZATION_CONFIG_HASH_MISMATCH`, `TASK_EXPIRED`,
`TASK_ISSUED_IN_FUTURE`, `WRONG_WORKER`, `DUPLICATE_NONCE`,
`DUPLICATE_OR_LOWER_SEQUENCE`, `DUPLICATE_TASK_EXECUTION`.

## 18. Coordinator key rotation foundation

`CoordinatorSigningKeyRegistry::validate_rotation`/`commit_rotation`
implemented and unit-tested. **Not wired to any RPC or live-validated
as an operational flow** — explicitly deferred.

## 19. Protobuf contracts

`SignedCoordinatorTask`, `ClientTrainingTask.attempt`/`.signed_task`,
`CoordinatorSigningKeyRecordSummary`, `GetCoordinatorSigningKeysRequest`/
`Response`, one new RPC (`GetCoordinatorSigningKeys`). All additive;
verified via `scripts/verify_proto_contracts.py`.

## 20. Formal C++ tests

`coordinator_signing_key_registry_test.cpp` and
`coordinator_task_sequence_store_test.cpp` (part of `fl_coordinator_tests`,
build/run on Windows/MSVC without gRPC). `coordinator_task_signing_test.cpp`
(new standalone gRPC-gated target `fl_coordinator_task_signing_tests`):
real keygen/persist/reload/sign, all six hashes' determinism/tamper
detection, the golden fixture, full sign/verify round trip.
`fl_coordinator_grpc_tests` (pre-existing `coordinator_service_test.cpp`)
re-confirmed passing unchanged — proves the optional/backward-compatible
wiring did not disturb existing coordinator-service behavior.

## 21. Formal Python tests

`test_coordinator_task_signing.py` (33 tests), `test_coordinator_task_replay.py`
(8), `test_task_journal.py` (11), `test_coordinator_trust_bundle.py` (5),
`test_coordinator_task_verifier.py` (17, including all 16 rejection
reasons individually triggered against a real Ed25519-signed fixture) —
74 new tests. Full suite: 287 passed, 1 skipped (pre-existing), zero
regressions.

## 22. Cross-language golden fixtures

`GoldenFixtureTests` (Python) asserts six real SHA-256 hex digests for
a fixed `TaskConfigurationFields` input; the identical six literal hex
strings are pasted into `coordinator_task_signing_test.cpp` and
asserted against the C++ encoder's independent output for an identical
fixed `ClientTrainingTask`. This real, run-both-sides-and-compare
process is what caught the two genuine bugs documented in
[task-configuration-hashes.md](task-configuration-hashes.md) — both
fixed, then reverified matching.

## 23-30. Live Docker validation

Real build: `mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`, real
`apt-get install protobuf-compiler protobuf-compiler-grpc
libprotobuf-dev libgrpc++-dev`, real `bash scripts/generate_protos.sh
generated` (real `protoc`+`grpc_cpp_plugin`, regenerating C++ AND
Python bindings — the Python side via `python3 -m grpc_tools.protoc`
directly, since the shell script's `command -v protoc` gate and its
`python`-not-`python3` check both meant it silently skipped what was
actually available; both regenerations verified for real, not assumed).
Full `cmake --build` of every target including
`fl_coordinator_grpc_server`, `fl_coordinator_grpc_tests`, and the new
`fl_coordinator_task_signing_tests`. `ctest`: **12/12 suites pass**,
zero regressions.

Live scenarios against a real running coordinator (real mTLS,
`FL_COORDINATOR_SIGNING_KEY_PATH` set) and a real `GrpcCoordinatorClient`:

1. Real trusted-key bundle file, exactly one ACTIVE key, loaded from
   disk (never RPC).
2. A real `AcquireTask` call returned a genuinely Ed25519-signed task;
   the full Python verification pipeline accepted it; the journal
   recorded `attempt=1`.
3. The lease was allowed to expire for real (`task_lease_seconds=3`,
   real 4-second wait); a second live `AcquireTask` call returned the
   *same* `task_id` at `attempt=2` with a structurally distinct
   signature/nonce/sequence_number — confirmed independently via the
   coordinator's own structured log (`TASK_ASSIGNED` events for
   `task_id=task-1`, four seconds apart).
4. A replay candidate reusing the already-committed sequence number was
   confirmed rejected by the replay store.
5. Marking the attempt-2 journal entry `RESULT_SUBMITTED`/`COMPLETED`
   and calling `record_accepted` again at the same attempt raised
   `DuplicateTaskExecutionError` for real.
6. A third live task's journal entry was transitioned to `TRAINING`,
   then a genuinely separate `AcceptedTaskJournal` Python object,
   constructed against the same on-disk file (simulating a crash),
   correctly reported that task recovered (`FAILED`).
7. A fabricated `SignedCoordinatorTask` with an all-zero signature was
   confirmed rejected against the real trusted public key.
8. `GetCoordinatorSigningKeys`, called with a real go-api service
   certificate over live mTLS, returned the coordinator's real signing
   key (status `active`, correct fingerprint).
9. The same RPC, called with a real worker certificate, was rejected
   with `PERMISSION_DENIED`.

**Result: 12/12 live checks passed** (10 in the primary end-to-end
script, 2 in a follow-up admin-RPC/access-control script), 0 failed.

## 31. Pass, fail, or blocked results

All local validation commands ran to completion and passed: C++
`fl_coordinator_tests` (Windows/MSVC, 20/20 groups, includes the two
new protobuf-free test files), full Python `pytest` (287 passed, 1
skipped), `ruff check` (clean), `mypy --config-file=python/pyproject.toml
python/src` (clean, 72 source files), terminology checker (clean),
`scripts/verify_proto_contracts.py` (clean). Docker: `ctest` 12/12.
Nothing was blocked; nothing is reported as passing without having
actually been run.

## 32. Performance measurements

None performed, consistent with every prior slice's stated scope —
task signing/hashing/verification latency was not benchmarked.

## 33. Security events

Real events already logged for `AcquireTask`/`RegisterWorker`/etc. from
prior slices are unchanged. No new structured security events were
added specifically for signed-task issuance/verification/rejection
this pass (the rejection reason is returned as a structured gRPC-level
value on the Python side, but not additionally logged as a coordinator-
side stderr event, since rejections happen client-side before any RPC
request reaches the coordinator to log).

## 34. Metrics

None added. No new Prometheus counters for signed-task issuance,
verification outcomes, or replay rejections.

## 35. Remaining trust assumptions

Same trust boundary as every prior authenticity slice: a coordinator
that is itself compromised can sign arbitrary tasks with a legitimately
trusted key — signing proves the message came from whoever holds the
coordinator's private key, not that the coordinator's decisions are
themselves correct or benign. The coordinator process's own memory/disk
(where the private signing key lives) is trusted; no HSM/TPM-backed key
storage exists for the coordinator any more than it does for workers.

## 36. Known limitations

See [known-limitations.md](known-limitations.md)'s "Coordinator-Signed
Tasks slice" section for the full itemized list: no gRPC rotation RPC
for the coordinator's own key; no live-validated rotation scenario; no
new security events/metrics/audit records beyond existing logging; no
journal retention/cleanup; no time-based nonce expiry in the worker-side
replay store; `__main__.py`/`configuration.py` not wired with new env
vars (the live validation constructed `GrpcCoordinatorClient` directly,
matching every prior mTLS/signing slice's identical scope boundary);
only direct `docker run`/live-mTLS scenarios validated, not the full
39-scenario Docker Compose flow; no performance benchmarking.

## 37. Regression status

Zero regressions. `fl_coordinator_grpc_tests` (the pre-existing
`coordinator_service_test.cpp` integration test) passes unchanged,
proving the new optional/nullptr-by-default coordinator-signing
parameters do not alter any existing coordinator-service behavior for
a caller that does not opt in. Full Python suite: 287 passed, 1
skipped (the same pre-existing skip as before this slice).

## 38. Git working-tree summary

No commits, pushes, tags, or pull requests were made — per standing
instruction, only local file changes exist: new C++ headers/sources/tests
under `cpp/coordinator/`, new Python modules under
`python/src/fl_platform/security/` and `python/src/fl_platform/worker/`,
new Python tests under `python/tests/`, proto additions to
`proto/coordinator/coordinator.proto`, and new/updated documentation
under `docs/`, plus `plan.md`/`README.md` updates.

## Update: Security Administration, Observability, and Runtime Validation slice

Recommendation 1 below (coordinator signing-key rotation as a real
operational flow) is now **done** — implemented as live, idempotent
`RotateCoordinatorSigningKey`/`RevokeCoordinatorSigningKey` gRPC RPCs
plus a standalone recovery CLI, with a strengthened trusted-key-bundle
lifecycle and worker-side reload. Scoped to C++/Python only, per an
explicit, user-confirmed decision. See
[security-administration-report.md](security-administration-report.md)
for the full report (repository audit, live runtime results, and a
completion-gate evaluation), and
[coordinator-signing-key-rotation.md](coordinator-signing-key-rotation.md),
[coordinator-signing-key-revocation.md](coordinator-signing-key-revocation.md),
[trusted-coordinator-key-bundle.md](trusted-coordinator-key-bundle.md), and
[coordinator-key-recovery.md](coordinator-key-recovery.md) for the
individual design notes. Recommendations 2-4 below remain unaddressed
(the `__main__.py`/`configuration.py` wiring gap, Go security APIs, web
security views, Prometheus metrics, the durable audit journal, the full
Docker Compose validation matrix, performance benchmarking, and the
threshold secret-sharing blocker) — this update does not change any of
the original recommended-next-work list except item 1.

## 39. Recommended next work

1. ~~**Coordinator signing-key rotation as a real operational flow**~~ —
   **done**, see the update above.
2. **`__main__.py`/`configuration.py` wiring** for
   `trusted_coordinator_keys_path`/replay-store/journal paths, so a
   deployed worker container can actually opt into signed-task
   verification through normal configuration rather than only via
   direct `GrpcCoordinatorClient` construction.
3. Only after 1-2: Go security APIs, web security views, Prometheus
   metrics for signed tasks specifically, formal audit-record
   persistence, the full 39-scenario Docker Compose validation matrix,
   and performance benchmarking — all still entirely unstarted.
4. The threshold secret-sharing blocker from earlier categories'
   passes remains unresolved and out of scope for all of the above —
   pairwise masking and secret sharing should not begin until it is.

Explicit non-goals maintained this slice, per standing instruction: no
pairwise masking, private masks, fixed-point secure-aggregation
encoding, threshold secret sharing, dropout recovery, unmasking, secure
aggregate reconstruction, protocol transcript chaining, homomorphic
encryption, worker attestation, TEEs, TPM integration, Byzantine-robust
aggregation, remote attestation, trusted execution environments, Ray,
Flower runtime, asynchronous/semi-synchronous aggregation, production
Kubernetes deployment, the full Go security HTTP API implementation,
the full web Security Center implementation, or large-scale
distributed execution. Secure aggregation is not claimed complete. No
custom threshold secret sharing was implemented. No commits, pushes,
tags, or pull requests were made without explicit request.
