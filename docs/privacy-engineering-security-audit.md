# Privacy Engineering Phase Security / Trust-Boundary Audit

Scope: everything added or changed for the Privacy Engineering phase —
sample-level DP (Opacus, per-training-example), user-level DP (C++
coordinator-side clip+noise), adaptive clipping, hybrid DP, the privacy
ledger and budget-policy enforcement, worker privacy capability
advertisement, the Go privacy HTTP APIs, the web Privacy Center, and the
new Prometheus metrics. Gaps carried over unchanged from earlier phases
(coordinator TLS, secure aggregation itself) are not re-audited here —
see [known-limitations.md](known-limitations.md).

This phase is different in kind from prior audits: the primary risk is
not "can an attacker corrupt data or escalate privilege" (though that is
still checked below) but **"does the system's trust model match what it
claims to guarantee."** Section 0 states that trust model explicitly,
because every other finding in this document is only meaningful relative
to it.

## 0. Trust model (read this first)

**This is central differential privacy, not secure aggregation.**
Secure aggregation is explicitly out of scope for this phase (see the
project's Critical Constraints) and nothing here should be read as
providing it. Concretely:

* **The C++ coordinator sees every client's complete, unclipped delta in
  plaintext before applying user-level DP's clip+noise step.** This is
  inherent to central DP, not a bug: `RunInstance::finalize_round`
  (`cpp/coordinator/src/run_manager.cpp`) receives `ClientUpdate.delta`
  in full, clips it, aggregates, then adds noise. A coordinator operator
  with code-level or process-level access to this machine can observe
  any individual client's raw update. The privacy guarantee protects
  what is *released* (the noised aggregate, and the epsilon/delta
  accounting of that release) — it does not protect the coordinator
  operator from the clients, only the clients' *published output* from
  everyone else. Anyone deploying this system must trust whoever
  operates the coordinator process.
* **Workers are trusted to honestly compute and report sample-level
  epsilon.** `SampleLevelLedgerEntry` is computed by Opacus inside the
  Python worker and submitted to the coordinator via
  `SubmitClientResultRequest.sample_level_privacy`; the coordinator
  **stores and relays this value without recomputing or verifying it**
  (see `run_manager.cpp`'s `submit_client_result` and
  `docs/privacy-ledger.md`'s authority-split note, once written). A
  compromised or buggy worker could report a smaller epsilon than it
  actually incurred, and nothing in this system detects that. This is a
  genuine, unresolved trust-boundary limitation, not an oversight —
  verifying a worker's DP-SGD accounting cryptographically (e.g. via
  attestation) is out of scope for this phase.
* **Worker privacy-capability advertisement
  (`WorkerPrivacyCapabilities.supports_sample_level_dp`) is
  self-reported and unverified.** The reference Python worker computes
  this truthfully (`fl_platform.privacy.accounting.opacus_capabilities`
  probes `importlib.metadata` for a real Opacus install — see
  `coordinator_client.py`'s `register_worker`), and the C++ coordinator
  gates task assignment on it (`RunInstance::acquire_task`'s
  compatible-worker-only check). But nothing prevents a different,
  malicious worker implementation from advertising `true` and then
  never actually applying Opacus. The guarantee is "the reference
  implementation doesn't lie," not "the protocol cannot be lied to."
* **Noise generation is not cryptographically secure.**
  `SecureNoiseProvider` (C++) seeds `std::mt19937_64` from
  `std::random_device`; the Python worker's `supports_secure_random`
  capability is always reported `false`. Neither is a CSPRNG. See
  known-limitations.md's Privacy Engineering Phase section.
* **Transport is unencrypted by default**, unchanged from earlier
  phases (`grpc.WithTransportCredentials(insecure.NewCredentials())` in
  `go/internal/coordinator/grpc_client.go`) — but this phase adds
  materially more sensitive payload to that unencrypted channel
  (`PrivacyConfig`, `SampleLevelLedgerEntry`, full privacy ledgers) than
  existed before it. Anyone deploying this beyond a single trusted host
  needs to add TLS/mTLS themselves; nothing here does.

## 1. Path traversal / filesystem boundary

| Component | Check | Result |
|---|---|---|
| C++ checkpoint serialization (privacy fields) | New fields (`user_level_accountant_steps`, `adaptive_clip_value`, the three ledgers) are appended to the *existing* checkpoint body at a path already derived only from `run_id` (validated at `CreateRun` time, not from these new fields) | **Pass — no new path-derivation surface.** `checkpoint_path()` is unchanged by this phase. |
| Privacy ledger entry encoding (`encode_sample_level_entry` etc.) | Tab-separated line format, same convention as pre-existing `encode_round_result`/`encode_personalization_metric` | **Consistent, not a regression** — a `client_id` containing a literal tab would produce a field-count mismatch on parse and `throw std::runtime_error`, not silent corruption or a path issue (same property the pre-existing round-result encoding already has). |
| Python worker metrics (`fl_platform/privacy/metrics.py`) | Does `ensure_metrics_server_started` ever take a caller-controlled path? | **N/A** — binds a TCP port number only (`WorkerConfig.metrics_port`, an int), no filesystem interaction. |

## 2. Unsafe deserialization

| Component | Check | Result |
|---|---|---|
| C++ checkpoint restore (`restore_from_checkpoint`'s new privacy fields) | Parsed via `std::stod`/`std::stoull` on `split()`-delimited fields, matching the pre-existing pattern for every other checkpoint field | **Pass** — `std::stod`/`std::stoull` throw `std::invalid_argument`/`std::out_of_range` on malformed input (caught by the same top-level exception handling every other checkpoint field already relies on), never silently misinterpret data. Checksum-verified before any field parsing begins (pre-existing FNV1a check, now also covering the new fields since they're part of the same body). |
| Go JSON decoding of `CreateRunRequest.Privacy` and the three privacy response types | Standard `encoding/json` into plain structs, no `interface{}`-typed fields | **Pass** — same pattern as every other Go request/response type; verified by reading `client.go`'s new type definitions. |
| Python `WorkerPrivacyCapabilities` protobuf decode (`coordinator_client.py`) | Standard generated protobuf getters, no custom unpickling | **Pass**. |

## 3. Tamper / corruption detection

| Component | Check | Result |
|---|---|---|
| C++ checkpoint (privacy fields) | Same FNV1a checksum as the rest of the checkpoint body (see `docs/checkpoint-format.md`) | **Pass** — a tampered/truncated privacy field is caught by the existing whole-body checksum before any field is read, exactly like every pre-existing checkpoint field. No new corruption class was introduced by adding more fields to an already-checksummed body. |
| Sample-level ledger entries submitted by a worker (`SubmitClientResultRequest.sample_level_privacy`) | Are `run_id`/`round_id`/`client_id` in the submitted entry checked against the request's own (lease-validated) result fields? | **Fixed during this audit.** Originally decoded the entry's own embedded `run_id`/`round_id`/`client_id` as submitted, with no cross-check against the outer result — a buggy (not necessarily malicious) worker could have submitted an entry stamped for the wrong run/round/client and had it silently accepted into that ledger. `coordinator_service.cpp`'s `SubmitClientResult` now rejects (`std::invalid_argument`, mapped to a non-OK gRPC status) any submission where these three fields don't match; regression test in `coordinator_service_test.cpp`. |

## 4. Authorization (RBAC)

| Route group | Required role(s) | Consistent with existing pattern? |
|---|---|---|
| `GET .../privacy/metrics`, `.../privacy/ledger`, `.../privacy/projection` | Viewer, Researcher, Admin, Service (read) | Yes — same catch-all `/api/v1/coordinator/runs/` auth check as every other per-run read route (personalization, fairness, algorithm-summary). |
| `GET /api/v1/coordinator/workers` | Viewer, Researcher, Admin, Service (read) | Yes — matches the read pattern above. |
| `GET /api/v1/privacy/compatibility` | Viewer, Researcher, Admin, Service (read) | Yes, though this endpoint serves static reference data with no per-run/per-user sensitivity at all — the role requirement is conservative, not strictly necessary. |
| `POST /api/v1/coordinator/runs` (now also accepting `privacy_config`) | Researcher, Admin (write) | Yes — no new bypass; privacy config rides the same request as every other `CreateRun` field, behind the same existing check. |

No new role was introduced this phase; all new routes reuse the existing
four roles and `AuthService.Authorize`.

## 5. Sensitive-data exposure

* **`GET .../privacy/ledger` exposes per-client `client_id` +
  per-round `epsilon` for sample-level DP to any authenticated
  Viewer who can see the run.** This reveals cohort membership (which
  clients participated in which rounds) and each client's individual
  accounted epsilon, not just aggregate statistics. This is the same
  RBAC granularity already accepted for per-client personalization
  accuracy in the Algorithm Expansion phase (see
  known-limitations.md), extended here to privacy-accounting data
  specifically — flagged explicitly because "which client trained
  when, and how much of their own privacy budget they spent" is
  arguably more sensitive than an accuracy number. No field-level
  restriction exists to hide `client_id` from a Viewer while still
  showing round-level epsilon.
* **Raw per-client update norms are never exposed, anywhere.**
  `clip_client_delta` (`cpp/core/src/privacy.cpp`) computes a norm
  internally to decide its scale factor but never returns or logs it;
  this is enforced by the function's own contract and exercised by
  `cpp/core/tests/privacy_test.cpp`. Verified: no call site anywhere in
  `run_manager.cpp`, the gRPC service, the Go API, or the web frontend
  reads a per-client norm.
* **Raw per-client adaptive-clipping over-threshold status is never
  exposed.** `AdaptiveClipController::step` privatizes the count
  immediately (adds Gaussian noise) before computing the fraction it
  returns; only the noised fraction ever leaves the function. Verified
  by `cpp/core/tests/privacy_test.cpp`'s adaptive-clipping test group
  and by inspection of `AdaptiveClipStepResult`'s fields (no raw-count
  field exists to leak).
* **User-level ledger entries carry no client identity at all** — by
  design, `UserLevelLedgerEntry` is a per-round, per-run aggregate
  (`num_clients` as a count, never a list of IDs). This is the correct
  shape for a mechanism that protects "one client's complete round
  contribution" as a single indistinguishable unit; exposing which
  specific clients contributed to a given round's user-level release
  would itself weaken that guarantee. Verified against the struct
  definition in `run_manager.hpp` and the wire message in
  `privacy.proto`.
* **The Prometheus `fl_privacy_epsilon` gauge is labeled by `run_id`,
  not `client_id`.** Sample-level's contribution to that gauge is the
  worst-case (max) epsilon across clients for the run (see
  `PrivacyMetricsSnapshot`'s doc comment), not a per-client breakdown —
  Prometheus/Grafana consumers cannot recover individual client epsilon
  from this metric, only from the authenticated `/privacy/ledger`
  endpoint above. The unauthenticated `GET /metrics` endpoint therefore
  does **not** carry the per-client exposure the previous bullet
  describes.
* **The web Privacy Center panel renders the same per-client ledger
  data** any authenticated Viewer's token can already fetch from the
  API directly — no additional exposure beyond what section 4's RBAC
  table already covers, but worth noting since it's now visibly
  rendered in a UI rather than requiring a direct API call.

## 6. Injection

* **C++**: no new `system()`/`popen()`/`exec*` call sites in any file
  touched this phase (grepped `cpp/core/src/privacy.cpp`,
  `cpp/coordinator/src/run_manager.cpp`,
  `cpp/coordinator/src/coordinator_service.cpp` — zero matches).
* **Go**: no new SQL (this project has none); no new
  string-concatenated shell/template calls in
  `go/internal/privacy/compatibility.go` or the new HTTP handlers —
  purely static Go data structures and `encoding/json`.
* **Python**: `fl_platform/privacy/metrics.py` and
  `fl_platform/privacy/accounting.py`'s `opacus_capabilities` use only
  `importlib.metadata` and `prometheus_client`'s public API — no
  `subprocess`/`os.system`/`eval`/`exec` call sites were added (grepped
  all four across `python/src/fl_platform/privacy/` and the touched
  parts of `python/src/fl_platform/worker/` — zero matches outside
  pre-existing, unrelated code).

## 7. Audit trail

`CreateRun`'s audit event (`coordinator.run.create`) now includes
`privacy_mode` in its recorded details
(`go/internal/application/coordinator_service.go`) — a run created with
DP enabled is distinguishable in the audit log from one that isn't. No
other new mutation exists this phase (the privacy endpoints are all
read-only); nothing bypasses the existing `AuditService.Record` path.

## Summary

No new *conventional* vulnerability class (path traversal, injection,
unsafe deserialization, unchecked RBAC) was introduced by this phase's
additions — the one finding (section 3's un-cross-checked
`run_id`/`round_id`/`client_id` on submitted sample-level entries) was
low-severity and has been fixed as part of this audit, not left open.
The more important
content of this document is Section 0: **this system provides central
differential privacy under an explicit, stated trust model** (trusted
coordinator operator, honest workers, non-cryptographic randomness,
unencrypted transport by default) **and does not claim secure
aggregation, worker attestation, or transport confidentiality** — anyone
deploying it must accept or replace those assumptions explicitly, not
discover them by reading source code after the fact.
