# Secure Cohort Handshake and Signed Roster Runtime — Design Decision

**Status: design decision, written before implementation, per this
project's established working method.** Covers the audit findings that
shape this pass's architecture and the concrete design choices for
each of the 20 in-scope items. See
[secure-aggregation-wire-protocol-audit.md](secure-aggregation-wire-protocol-audit.md)
and
[secure-aggregation-wire-protocol-foundation.md](secure-aggregation-wire-protocol-foundation.md)
for the prior slice's audit/design (still accurate; not re-derived
here except where this slice adds new findings).

## Mid-implementation correction: session creation cannot live in `RunInstance`

`run_manager.hpp`/`.cpp` are part of the non-gRPC-gated `fl_coordinator`
library (builds on this Windows/MSVC machine without any generated
protobuf headers). `SecureAggregationSessionManager` requires the real
generated `coordinator.pb.h`/`worker.pb.h` types and is therefore only
compiled into gRPC-gated targets. Giving `RunInstance` a
`SecureAggregationSessionManager*` member (the originally-planned
design, item 3/6 below) would force `run_manager.hpp` to include a
gRPC-gated header, breaking every local Windows build of the
non-gRPC-gated coordinator library — unacceptable, caught before
writing any code that would have caused it, not after.

**Corrected design**: session creation moves entirely into
`coordinator_service.cpp`'s `AcquireTask` handler (already gRPC-gated,
already depends on both `RunManager` (protobuf-free) and can safely
also depend on `SecureAggregationSessionManager`). `RunInstance`
already exposes everything needed via its existing, unmodified public
API: `round_snapshot(round_id).selected_clients` is the round's real
cohort. **Zero changes to `run_manager.hpp`/`.cpp`/`RunConfig` are
needed** — a smaller, safer diff than originally planned, and the
"coordinator-wide `FL_SECURE_AGGREGATION_ENABLED` opt-in" decision
below is now a flag on `CoordinatorServiceImpl` itself, not on
`RunConfig`. The deadline sweep (item 15) is called from `AcquireTask`'s
handler for the same reason, not from `RunInstance::advance()`.

## New audit findings (this slice)

- `RunConfig`/`RunInstance` (`run_manager.hpp`/`.cpp`) have **no**
  secure-aggregation-aware field or hook. `RunInstance::begin_round()`
  (the real, live per-round entry point, `run_manager.cpp:771`) selects
  `current_cohort_` (a `std::vector<std::string>` of client/worker IDs)
  before enqueuing tasks — this is the mechanically correct, minimal-
  risk hook point for "coordinator creates secure session before task
  issuance," but it requires a way to *decide* a given run wants secure
  aggregation. Modifying `CreateRunRequest`/`experiment.proto` to add a
  real per-run wire-configurable flag is out of proportion for this
  pass (touches Go/web run-creation surfaces this task explicitly
  excludes). **Decision**: a coordinator-process-wide opt-in,
  `FL_SECURE_AGGREGATION_ENABLED` (bool env var, default false), read
  once in `main.cpp` and passed into `RunManager`. When enabled, *every*
  run's *every* round creates a real secure-aggregation session using
  that round's real selected cohort. Documented honestly as a
  coordinator-wide simplification, not a per-run feature flag — a real,
  disclosed scope boundary, not a silent gap.
- The six RPCs named in this task's "Verified Starting State" do
  **not** include a session-creation RPC. Combined with the finding
  above, this confirms the intended architecture is exactly "the
  coordinator owns and creates sessions as a side effect of its own
  round lifecycle," not a client-facing creation request — consistent
  with item 1's phrasing ("Coordinator ownership of secure sessions").
  No seventh RPC is added.
- **No explicit "freeze" RPC exists either.** `AdvertiseSecureAggregationKey`'s
  handler is therefore the natural place for freeze to happen
  automatically: when the accepted advertisement completes the cohort
  (`key_advertisement_count == cohort_size`), the handler immediately
  calls `freeze_cohort()` in the same request — mirroring the session
  manager's own established auto-transition philosophy
  (`COHORT_FORMING -> KEY_ADVERTISEMENT` already auto-transitions on
  the first advertisement).
- `coordinator_task_signing.cpp`'s `task_payload_hash` deliberately does
  **not** fold in every sibling hash — `personalization_configuration_hash`
  is already a sibling field on `SignedCoordinatorTask`, hashed and
  bound into the actual signed bytes (`coordinator_task_signing_bytes`)
  independently, never mixed into `task_payload_hash`'s own canonical
  JSON. **Decision**: add `secure_aggregation_configuration_hash` the
  same way — a new sibling hash, folded into the real signature via
  `coordinator_task_signing_bytes`, never touching the existing,
  stable `task_payload_hash` computation. This avoids any risk to
  already-tested, already-live task-signing behavior.
- `WorkerIdentityRegistry`'s persistence pattern (tab-separated record
  lines + `record_count=` header + FNV-1a `checksum=` trailer +
  temp-file-then-rename atomic write, fail-closed on any corruption) is
  the established convention this slice's new
  `SecureAggregationSessionStore` replicates exactly.
- `CoordinatorActiveIdentityStore::current()` /
  `sign_with_coordinator_identity(identity, bytes)` are the exact,
  reusable real-signing primitives `freeze_cohort()` needs — confirmed
  present and already used identically for task signing.

## Scope decisions, item by item

1. **Coordinator ownership** — `SecureAggregationSessionManager` is
   constructed once in `main.cpp` (in-memory, no path — matches the
   prior slice), a pointer is injected into both `RunManager` (for
   round-start session creation) and `CoordinatorServiceImpl` (for RPC
   handlers) — the same "optional pointer, `nullptr`-defaulted"
   constructor-injection convention used for every other store in this
   codebase.
2. **Safe session persistence** — new `SecureAggregationSessionStore`
   (`secure_aggregation_session_store.{hpp,cpp}`), storing only
   `session_id, run_id, round_id, state, created_at, completed_at,
   abort_reason, failure_reason` (never key material, never masked
   values — matches Work Package Q's persistence prohibition list).
   Injected into `SecureAggregationSessionManager` as an optional
   dependency; the manager calls `store_->record_transition(...)` at
   the end of every one of its six mutating methods.
3. **Session creation before task issuance** — superseded by the
   "Mid-implementation correction" above: lives in
   `CoordinatorServiceImpl::AcquireTask` instead of `RunInstance::begin_round()`,
   gated on the coordinator-wide `secure_aggregation_enabled_` member
   (from `FL_SECURE_AGGREGATION_ENABLED`), created lazily on the first
   `AcquireTask` call for a given `(run_id, round_id)` using
   `RunInstance::round_snapshot(round_id).selected_clients` as the real
   cohort. No `RunConfig` field was added.
4. **Secure task binding** — new `SecureAggregationTaskBinding` message,
   new `ClientTrainingTask.secure_aggregation` field (19, additive),
   new `SignedCoordinatorTask.secure_aggregation_configuration_hash`
   field (18, additive, folded into the real signed bytes). Populated
   in `AcquireTask`'s handler via a new manager query,
   `find_binding_for_participant(run_id, round_id, worker_id)`.
5. **Python secure-task verification** — new check in
   `verify_coordinator_task` (`coordinator_task_verifier.py`), same
   position/pattern as the existing training-config-hash check; new
   `secure_aggregation_configuration_hash()` function in a new
   `python/src/fl_platform/security/coordinator_task_signing.py`-sibling
   location (actually added to that same existing module, mirroring
   its other hash functions) for byte-exact cross-language parity.
6. **Fresh worker X25519 key generation** — reuses
   `fl_platform.secure_aggregation.crypto.generate_x25519_keypair()`
   directly (already real, tested, from the prior slice) — no new
   key-generation code needed.
7. **Signed key-advertisement construction** — new
   `python/src/fl_platform/secure_aggregation/key_advertisement.py`:
   builds `SecureAggregationKeyAdvertisement`, computes its payload
   hash, signs via the worker's existing Ed25519
   `WorkerSigningIdentity`, wraps in a `SignedWorkerEnvelope`
   (`message_type=MESSAGE_TYPE_SECURE_AGGREGATION_KEY_ADVERTISEMENT`,
   `message_stream=MESSAGE_STREAM_SECURE_AGGREGATION`).
8. **Live `AdvertiseSecureAggregationKey` RPC** — full SIGNED_WORKER_MESSAGE
   pipeline replicated from `Heartbeat`/`RotateWorkerSigningKey`
   (mTLS -> identity lookup -> status check -> signing-key resolve ->
   envelope decode -> payload-hash recompute -> Ed25519 verify ->
   replay/sequence validate -> domain call
   (`manager->advertise_key`) -> auto-freeze-if-complete -> replay
   commit -> security event emit).
9. **Complete-cohort freeze** — automatic, inside item 8's handler (see
   audit finding above), not a separate RPC.
10. **Coordinator-signed frozen roster** — `freeze_cohort()` gains an
    optional `const CoordinatorSigningIdentity*` parameter; when
    provided (always, from the live RPC handler), computes real
    canonical roster bytes + `sha256_hex` payload hash + real Ed25519
    signature via `sign_with_coordinator_identity`.
11. **Live `GetFrozenCohortRoster` RPC** — mTLS + participant-membership
    check + worker-status check, returns the already-signed roster
    stored on the session record.
12. **Python frozen-roster verification** — new function in
    `key_advertisement.py` (or a sibling `frozen_roster.py`):
    recomputes the same canonical bytes, verifies the Ed25519 signature
    against the trusted coordinator key bundle (reusing
    `coordinator_trust_bundle.py`'s existing lookup).
13. **Read-only session RPCs** — `GetSecureAggregationSession`/
    `ListSecureAggregationSessions`, thin wrappers over
    `manager->find()`/`manager->list()`.
14. **Administrative session abort** — `AbortSecureAggregationSession`,
    ADMIN_CONTROL-gated (same identity check as
    `SuspendWorker`/`RevokeWorker`), wraps `manager->abort()`.
15. **Deadlines** — already enforced inside
    `advertise_key()`/`submit_masked_update()` (prior slice); this
    slice adds `sweep_expired_advertisement_deadlines()`, called
    unconditionally at the top of `AcquireTask` (not from
    `RunInstance::advance()`, per the mid-implementation correction
    above) whenever a secure aggregation manager is configured, aborting
    any session past its `key_advertisement_deadline_unix_s` with an
    incomplete cohort.
16. **Restart abort** — `SecureAggregationSessionStore` gets a
    `reconcile_after_restart(now) -> vector<string>` method: scans
    every persisted record, and for any non-terminal one, appends a
    new terminal record (`ABORTED`, `kCoordinatorRestart`) directly —
    there is nothing live to abort against (the in-memory manager
    starts empty on every restart), so this is a log-level
    reconciliation, not a live `CohortStateMachine` transition. Called
    once from `main.cpp` at startup, emits one `SecurityEvent` per
    reconciled session.
17. **Minimal events and metrics** — a small, representative set of new
    `SecurityEventType` values
    (`SECURE_AGGREGATION_SESSION_CREATED/COHORT_FROZEN/
    KEY_ADVERTISEMENT_ACCEPTED/KEY_ADVERTISEMENT_REJECTED/
    SESSION_ABORTED/RESTART_ABORTED`), emitted from the real call
    sites above. No new Prometheus metric this pass (Go/web are
    explicitly out of scope; a C++-native metrics endpoint remains the
    same disallowed new dependency documented in every prior slice) —
    the events themselves, journaled and queryable via the existing
    `ListSecurityEvents` RPC, are the "minimal" observability surface
    this item is scoped to.
18. **Three-worker Docker handshake validation** — Compose worker
    topology parameterized to 3 real `python-worker` services (`-1`,
    `-2`, `-3`), each with its own issued cert/signing key, driven by a
    new validation script exercising the real end-to-end handshake.
19. **CI** — add the new test targets to `.github/workflows/ci.yml`'s
    `cpp-grpc` job target list.
20. **Documentation** — this doc, an audit-style completion report, and
    `known-limitations.md`/`plan.md` updates.

## Explicitly out of scope (restated, unchanged from the task's own boundary)

Masked-update submission and everything downstream of it
(`SubmitMaskedClientUpdate` stays `UNIMPLEMENTED`), FedAvg execution
through secure aggregation, sample-private/user-level/hybrid/adaptive-
clipping secure modes, dropout recovery, threshold secret sharing, Go
session APIs, web secure-aggregation pages.
