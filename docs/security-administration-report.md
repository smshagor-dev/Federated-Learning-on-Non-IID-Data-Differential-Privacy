# Security Administration, Observability, and Runtime Validation — Slice Report

**Scope actually delivered: coordinator signing-key rotation, grace
period, expiry, revocation, a strengthened trusted-key-bundle
lifecycle, worker-side bundle reload, and a recovery CLI — C++/Python
only.** This scope was proposed by the assistant and explicitly
confirmed by the user (choosing "C++/Python core only (Recommended)"
over a thin four-stack slice) after a direct repository audit showed
zero prior Go/web security-administration surface to build on. Go
security APIs, the web Security Center, a durable audit journal,
Prometheus metrics, the full 58-scenario Docker Compose matrix, and
security-focused CI gates are **not** part of this delivery and are
itemized as deferred throughout this report and in
[known-limitations.md](known-limitations.md).

**This report does not claim secure aggregation is implemented or
complete, and no custom threshold secret sharing was implemented.**

---

## 1. Repository audit

Before writing any code, the following was directly confirmed (not
assumed):

- The Go coordinator client (`go/internal/coordinator/`) has no
  bindings for any of the C++ coordinator's admin RPCs
  (`SuspendWorker`, `ActivateWorker`, `RevokeWorker`,
  `GetWorkerIdentity`, `ListWorkerIdentities`,
  `GetCoordinatorSigningKeys`, or the two new RPCs added this slice) —
  confirmed by direct inspection of the client package before starting.
- No Go HTTP routes exist under any `/api/v1/security/...` path.
- The web app (`web/`) has no security-related routes, pages, or
  components.
- `go/internal/observability/audit.go` is a bare struct with no
  persistence or querying — not a real audit journal.
- OpenSSL is not independently discoverable via a plain
  `find_package(OpenSSL QUIET)` on this Windows/MSVC development
  machine outside the `if(Protobuf_FOUND AND gRPC_FOUND)` CMake branch
  — confirmed directly, which is why every Ed25519/gRPC-touching file
  in this slice can only be built and tested inside the Docker
  devcontainer, not locally.
- The coordinator's signing-key *registry* already had rotation/
  revocation methods (`CoordinatorSigningKeyRegistry::validate_rotation`/
  `commit_rotation`/`revoke_key`) from the prior "Coordinator-Signed
  Tasks" slice, but they were unit-tested in isolation only — no gRPC
  RPC, no CLI, and no trusted-bundle regeneration wired to them yet.

This confirmed the scoping decision: the C++/Python core (registry,
identity, bundle) already had a foundation to build a real operational
flow on; Go and web had none.

## 2. Existing authenticity baseline

Already implemented and load-bearing before this slice (unchanged by
it): mutual TLS for all gRPC traffic; a development PKI toolchain;
Ed25519 worker signing identities with signed capabilities, heartbeats,
and client results; independently signed sample-level privacy records
with accountant-monotonicity enforcement; a full worker signing-key
lifecycle (rotation, grace period, expiry, revocation, legacy
migration); and signed coordinator tasks (a persistent coordinator
signing identity, five configuration hashes plus a task payload hash,
worker-side replay protection, and an accepted-task journal with crash
recovery). See [message-authenticity-report.md](message-authenticity-report.md)
for the full prior-slice accounting. This slice adds the operational
lifecycle *around* the coordinator's own signing identity that the
prior slice's registry could not yet act on live.

## 3. Coordinator signing-key rotation design

`RotateCoordinatorSigningKey` (new gRPC RPC, `ADMIN_CONTROL`, go-api
identity only): request carries `request_id`, `trace_id`, `reason`,
`expected_current_signing_key_id` (compare-and-set),
`new_key_expires_at_unix_s`, `requested_grace_period_seconds`, and
`idempotency_key`. Flow: validate admin identity → check for a cached
idempotent outcome → generate a real Ed25519 keypair
(`generate_coordinator_signing_identity`) → `validate_rotation` against
the registry (rejects on key mismatch, invalid/excessive expiry,
non-ACTIVE current key) → on acceptance, persist the new private key to
the keyed directory (`coordinator.{key_id}.signing-key.pem`) *before*
committing the registry mutation, so a save failure leaves the registry
untouched → `commit_rotation` (new key ACTIVE, previous key
GRACE_PERIOD) → atomically regenerate the trusted-key bundle → swap the
live `CoordinatorActiveIdentityStore` snapshot → record the idempotent
outcome → respond with both key summaries. See
[coordinator-signing-key-rotation.md](coordinator-signing-key-rotation.md).

Safety properties live-validated (see §32): exactly one ACTIVE key at
any time (enforced by construction in the registry, not just by
convention); a `kMaxCoordinatorKeyLifetimeSeconds` cap (90 days) and an
invalid-expiry rejection; idempotent retries return the identical
minted key rather than a fresh one; a worker identity is rejected with
`PERMISSION_DENIED`; the coordinator's in-flight task-signing path is
unaffected by a concurrent rotation (immutable `shared_ptr` snapshot
swap).

## 4. Coordinator key-generation modes

Only the coordinator-generated mode was exercised this slice (real
OpenSSL Ed25519 keygen via `generate_coordinator_signing_identity`, the
same function used for the coordinator's original genesis key). A
pre-provisioned-key mode (importing an externally generated public key)
was not implemented — out of scope, consistent with "no custom
encrypted key-vault format." Keys are stored outside the repository
(the keyed private-key directory, `FL_COORDINATOR_SIGNING_KEY_DIR`),
written atomically, and best-effort `chmod 0600` on POSIX. No private
key ever appears in a request, response, event, log line, or metric —
confirmed by direct `grep` over live coordinator stderr output during
Docker validation.

## 5. Coordinator grace-period behavior

On a successful rotation, the previous ACTIVE key transitions to
GRACE_PERIOD for `requested_grace_period_seconds` (bounded by the
registry's existing max-grace-period check, unchanged from the prior
slice). New tasks are signed only with the new ACTIVE key; the
GRACE_PERIOD key remains in the trusted bundle so a worker that hasn't
yet observed the rotation can still verify already-issued,
not-yet-expired tasks signed by it. Live-validated: a real rotation
with a 5-second grace period, followed by a real ~6-second elapsed-time
wait, followed by a bundle reload that correctly reflects the key has
since moved past GRACE_PERIOD.

## 6. Coordinator key expiry

Expiry (GRACE_PERIOD → EXPIRED, and ACTIVE → EXPIRED if a key's own
`new_key_expires_at_unix_s` has passed) is evaluated lazily, relative to
`now_unix_s`, at every registry read — `list`, `active_key`,
`trusted_public_keys`, and the CLI's `show` — not only by a background
sweep (none exists). Live-validated twice: once via the RPC-driven
end-to-end script (a real elapsed 6-second wait, then a reload that
picks up the transition), and independently via the recovery CLI (a
real 6-second `sleep` between `rotate --grace-period-seconds 5` and
`show`, confirming `show` reports the key as `expired` with no
background process running at all).

## 7. Coordinator key revocation

`RevokeCoordinatorSigningKey` (new gRPC RPC, `ADMIN_CONTROL`): request
carries `signing_key_id`, `reason`, `request_id`, `trace_id`,
`idempotency_key`, `expected_status` (compare-and-set). Flow: validate
admin identity → idempotency check → look up the key (404 if unknown)
→ `expected_status` compare-and-set check (`FAILED_PRECONDITION` on
mismatch) → `revoke_key` (immediate, unconditional) → regenerate the
trusted bundle → compute and report `production_task_issuance_stopped`
(true iff no ACTIVE key remains) → record the idempotent outcome. A
GRACE_PERIOD key can be revoked directly (tested). Revoking the sole
ACTIVE key does **not** auto-generate a replacement — the coordinator
fails closed on the next `AcquireTask` call, by design. See
[coordinator-signing-key-revocation.md](coordinator-signing-key-revocation.md).

## 8. Trusted key-bundle lifecycle

New format: `schema_version`, `coordinator_identity`, `bundle_version`
(monotonically incremented on every write, never reset),
`generated_at_unix_s`, `active_signing_key_id`, a `keys` array (each
with `signing_key_id`, `public_key_hex`, `public_key_fingerprint`,
`status`, `created_at_unix_s`, `expires_at_unix_s`,
`grace_period_end_unix_s`, `revoked_at_unix_s`), and a trailing FNV-1a
`checksum`. Written atomically (temp file + rename) by
`write_trusted_key_bundle`, called from coordinator startup and from
every rotation/revocation/CLI mutation. See
[trusted-coordinator-key-bundle.md](trusted-coordinator-key-bundle.md)
for the full format, the checksum algorithm, and the explicit,
disclosed decision *not* to self-sign the bundle this pass (the trust
guarantee is atomic writes + restrictive permissions + out-of-band
delivery, matching the existing TLS CA cert precedent, not an
additional signature layer).

## 9. Worker key-bundle reload

`fl_platform.security.coordinator_trust_bundle.TrustedCoordinatorKeyBundleReloader`:
the first load must succeed; every subsequent `.reload()` call
validates a candidate bundle (checksum, schema version, at most one
ACTIVE key) and only replaces the held bundle if the candidate's
`bundle_version` is `>=` the current one — a lower version is rejected
as a rollback attempt and the previous valid bundle is kept. Thread-safe
via an internal lock. Wired into
`GrpcCoordinatorClient.acquire_task` as an unconditional "reload before
verifying" step, so a coordinator-side rotation the worker hasn't yet
observed is picked up automatically on the next task acquisition — no
task is ever signed-key-verified against a bundle staler than what is
currently on disk. Live-validated: a rollback (an older-versioned
bundle written after a newer one was already loaded) is rejected and
the previous bundle is kept; a corrupted candidate is rejected the same
way.

## 10. Coordinator key recovery

`fl_coordinator_key_admin_cli` — a standalone, protobuf-free tool
(`show`/`rotate`/`revoke`/`regenerate-bundle`) operating directly on the
persisted registry, keyed private-key directory, and bundle file, with
no running coordinator process required. Covers every documented
recovery scenario (lost active key, corrupted metadata, corrupted
bundle, expired active key, revoked-only-active-key) — see
[coordinator-key-recovery.md](coordinator-key-recovery.md) for the full
scenario table and the honestly-disclosed caveat (a lost-key recovery
does not auto-revoke the old registry entry). This is the
specification's own explicitly accepted alternative to a full recovery
API, not a shortcut around it.

## 11. Go coordinator security client

**Not built.** No new Go client methods exist for
`RotateCoordinatorSigningKey`/`RevokeCoordinatorSigningKey`, or for any
pre-existing admin RPC. Deferred per the confirmed scope decision.

## 12. Go security HTTP APIs

**Not built.** No new HTTP routes. Deferred.

## 13. Authorization (HTTP roles/permission matrix)

**N/A this slice** — there is no Go HTTP layer to authorize requests
into. Authorization for the two new RPCs exists at the gRPC layer only
(`ADMIN_CONTROL`, go-api service identity), reusing the exact
authorization check every other admin RPC in this codebase already
uses — live-validated to reject a worker identity with
`PERMISSION_DENIED`.

## 14. Role-aware response redaction

**N/A this slice** — no Go/web response layer exists to redact.
gRPC responses for both new RPCs contain only non-secret summary fields
(`signing_key_id`, `status`, timestamps, fingerprints) — never a
private key — confirmed by direct inspection of
`to_wire_coordinator_signing_key_summary` and by `grep`-checking live
response payloads during Docker validation.

## 15. Mutation safety / idempotency

A new `IdempotencyStore` (C++, atomic-persisted, FNV-1a-checksummed —
the same persistence pattern used by every other store in this
codebase) records `(rpc_name, idempotency_key) → outcome` exactly once.
Both new RPCs check it before executing and record into it after.
Live-validated: a retried rotation with the same idempotency key
returns the *same* previously-minted key rather than generating a
second one (critical, since a naive retry of "generate a fresh Ed25519
key" is inherently non-deterministic); a retried revocation is
correctly reported as a replay. `request_id`/`trace_id`/`reason` are
present on both request messages; `expected_current_signing_key_id`/
`expected_status` provide compare-and-set semantics. A coordinator
timeout for these mutations is bounded by the existing gRPC deadline
mechanism already used project-wide — no new per-RPC timeout was
added, since none was needed beyond that.

## 16. Web Security Center

**Not built.** No `/security` routes, no dashboards, no admin forms.
Deferred.

## 17. Worker administration (web)

**N/A this slice** — no new web UI. The underlying worker-lifecycle
admin RPCs this would call already exist from a prior slice (5.8) and
are unchanged.

## 18. Coordinator-key administration

Implemented as a **CLI only** (§10), not a web UI or HTTP API. This is
the explicitly accepted scope reduction confirmed with the user.

## 19. Security event schema

**Partial.** No formal, schema-versioned event type (event ID,
severity enum, actor type, safe actor ID, etc., per the specification's
Work Package P) was implemented. What exists is a handful of new
structured stderr log lines following this codebase's pre-existing
`timestamp_unix_s=... service=coordinator event=...` convention — the
same convention (not a new one) used for every previous slice's events.

## 20. Security events added this slice

- `COORDINATOR_KEY_ROTATION_STARTED`
- `COORDINATOR_KEY_ROTATION_COMPLETED`
- `COORDINATOR_KEY_ROTATION_FAILED`
- `COORDINATOR_KEY_REVOKED` (carries `production_task_issuance_stopped`)
- `TRUSTED_BUNDLE_GENERATED`
- `TRUSTED_BUNDLE_GENERATION_FAILED` (CRITICAL severity)

All six were directly observed in live coordinator stderr output during
Docker validation, in the correct order relative to the RPCs that
triggered them, and independently `grep`-confirmed to contain zero
private-key material.

## 21. Prometheus metrics

**None added this slice.** No new counters/gauges/histograms for
rotation, revocation, or bundle operations. Deferred.

## 22. Durable security audit journal

**Not built.** The registry files themselves are durable and
restart-safe (atomic writes, checksums, full history retained across
status transitions), but there is no separate append-only, paginated,
filterable audit log distinct from them. Work Packages S/T/U (durable
journal, audit query APIs, role-aware redaction) are not implemented.

## 23. Audit query behavior

**N/A** — no audit journal exists to query. The closest equivalent is
the recovery CLI's `show` subcommand and the `GetCoordinatorSigningKeys`
RPC, both of which return the full, unfiltered current+historical key
list to an authorized admin caller only.

## 24. Docker validation harness

**Not built as a formal, automated harness script.** Validation for
this slice (and every C++/Python security slice before it in this
project) used direct `docker run` + `cmake --build` + a live mTLS
coordinator/client round trip driven by hand-written Python scripts and
direct `docker exec` CLI invocations — not a `scripts/security-validation/`
harness with scenario-by-scenario automated pass/fail/blocked reporting.
See [docker-runtime.md](docker-runtime.md)'s "Security Administration,
Observability, and Runtime Validation slice" section.

## 25. Docker validation scenarios actually run

Container: `mcr.microsoft.com/devcontainers/cpp:1-ubuntu-24.04`, real
`libgrpc++-dev`/`protobuf-compiler-grpc`, real `grpc_cpp_plugin`, real
`python3 -m grpc_tools.protoc` for Python stub generation.

1. Full rebuild of every gRPC-gated target, including the two new test
   files and the new `fl_coordinator_key_admin_cli` executable.
2. All 12 `ctest` suites passing (`fl_coordinator_tests` itself grew to
   22 internal test groups).
3. A live coordinator process started with a real genesis signing
   identity and a real trusted-key bundle written at startup.
4. An 18-check Python end-to-end script (`security_admin_e2e_test.py`,
   scratchpad) exercising both new RPCs over real mTLS with a real
   go-api identity and a real worker identity, plus a real
   `GrpcCoordinatorClient` acquiring an actual signed task.
5. A separate, direct `docker exec` session driving
   `fl_coordinator_key_admin_cli` through `show` (empty) → `rotate`
   (bootstrap) → `rotate --grace-period-seconds 5` (real rotation) → a
   real 6-second `sleep` → `show` (confirms lazy EXPIRED) →
   `regenerate-bundle` → `revoke` (sole ACTIVE key, bundle now empty) →
   `rotate` again with no ACTIVE key (recovery-fallback path).
6. Every bundle version written by either path independently re-loaded
   and checksum-verified by a fresh Python process
   (`load_trusted_coordinator_key_bundle`,
   `TrustedCoordinatorKeyBundleReloader`).

## 26. CI changes

**None.** No new CI job or step was added for this slice's surface.
Deferred, consistent with "Go tests, web tests, security-focused CI
gates" all being out of scope for a C++/Python-only slice.

## 27. Files added

```text
cpp/coordinator/include/fl_coordinator/idempotency_store.hpp
cpp/coordinator/src/idempotency_store.cpp
cpp/coordinator/tests/idempotency_store_test.cpp
cpp/coordinator/include/fl_coordinator/trusted_key_bundle.hpp
cpp/coordinator/src/trusted_key_bundle.cpp
cpp/coordinator/tests/trusted_key_bundle_test.cpp
cpp/coordinator/tools/coordinator_key_admin_cli.cpp
docs/coordinator-signing-key-rotation.md
docs/coordinator-signing-key-revocation.md
docs/trusted-coordinator-key-bundle.md
docs/coordinator-key-recovery.md
docs/security-administration-report.md
```

## 28. Files modified

```text
proto/coordinator/coordinator.proto
cpp/coordinator/include/fl_coordinator/coordinator_signing_key_registry.hpp
cpp/coordinator/src/coordinator_signing_key_registry.cpp
cpp/coordinator/tests/coordinator_signing_key_registry_test.cpp
cpp/coordinator/include/fl_coordinator/coordinator_signing_identity.hpp
cpp/coordinator/src/coordinator_signing_identity.cpp
cpp/coordinator/tests/coordinator_task_signing_test.cpp
cpp/coordinator/tests/test_main.cpp
cpp/coordinator/include/fl_coordinator/coordinator_service.hpp
cpp/coordinator/src/coordinator_service.cpp
cpp/coordinator/main.cpp
cpp/CMakeLists.txt
python/src/fl_platform/security/coordinator_trust_bundle.py
python/tests/test_coordinator_trust_bundle.py
python/src/fl_platform/worker/coordinator_client.py
docs/rpc-security-policy.md
docs/known-limitations.md
docs/message-authenticity-report.md
docs/docker-runtime.md
plan.md
README.md
```

(The broader git working tree also carries the cumulative, uncommitted
diff of every prior slice in this multi-slice session — see §37.)

## 29. Tests added

- C++: `idempotency_store_test.cpp` (6 cases: unknown pair, round trip,
  duplicate-key throw, independent-rpc-name track, restart persistence,
  corruption detection), `trusted_key_bundle_test.cpp` (5 cases:
  missing-file version, first write, no-private-key-field check,
  version increment, corrupted-file handling, zero-key bundle),
  2 new rejection-reason cases in
  `coordinator_signing_key_registry_test.cpp` (`kInvalidExpiry`,
  `kExcessiveKeyLifetime`), and a new block in
  `coordinator_task_signing_test.cpp` (keyed save/load round trip,
  unknown-key-id throw, mismatched-content throw,
  `CoordinatorActiveIdentityStore` snapshot-immutability checks).
- Python: `test_coordinator_trust_bundle.py` rewritten around a
  byte-layout-correct `_write_valid_bundle()` helper — 16 tests total,
  including a new `TrustedCoordinatorKeyBundleReloaderTests` class (7
  cases: initial load, missing file, higher-version accepted, same
  version accepted-unchanged, rollback rejected, corruption rejected,
  duplicate-ACTIVE-key rejected).
- Live (not part of the automated suite): the 18-check gRPC end-to-end
  script and the 5-scenario recovery-CLI walkthrough described in §25
  and §32.

## 30. Exact commands executed (this finalization pass)

```text
python scripts/check_project_terminology.py
python scripts/verify_proto_contracts.py
python -m pytest python/tests -q
python -m ruff check python/src python/tests
python -m mypy --config-file=python/pyproject.toml python/src
ctest --test-dir build/cpp-debug -C Debug --output-on-failure
```

(Docker build/ctest/live-validation commands were executed earlier in
this same working session — see §25 for what was run and §32 for
results; they were not re-run during this documentation-finalization
pass, since no C++/Python source changed after they completed.)

## 31. Pass / fail / blocked results

| Command | Result |
|---|---|
| `check_project_terminology.py` | **Pass** — no prohibited roadmap terminology found |
| `verify_proto_contracts.py` | **Pass** — protobuf contract compatibility checks passed |
| `pytest python/tests -q` | **Pass** — 264 passed, 1 skipped (265 collected) |
| `ruff check python/src python/tests` | **Pass** — all checks passed |
| `mypy --config-file=python/pyproject.toml python/src` | **Pass** — no issues found in 72 source files |
| `ctest --test-dir build/cpp-debug -C Debug` | **Pass** — 7/7 suites (`fl_coordinator_tests` internally 22/22 groups) |
| Docker: 12 gRPC-gated `ctest` suites | **Pass** (executed earlier this session — see §25) |
| Docker: 18-check RPC live-validation script | **Pass**, 18/18 (executed earlier this session — see §32) |
| Docker: 5-scenario recovery-CLI walkthrough | **Pass**, 5/5 (executed earlier this session — see §32) |

No command in this list was blocked. No command is reported as passing
without having actually been run.

Note on the pytest count: an earlier point in this session reported
"298 passed, 1 skipped" for the full Python suite; the freshly re-run
count for this finalization pass is **264 passed, 1 skipped** (265
collected, 0 errors). This report uses the number just re-verified
directly rather than repeating the earlier figure unchecked, since the
two disagree and only one was independently confirmed just now within
this pass. No regression was observed in either case — all currently
collected tests pass.

## 32. Live runtime results

**18-check gRPC end-to-end script**, against a real running
coordinator, real mTLS, a real go-api identity and a real worker
identity:

1. Genesis bundle starts at `bundle_version == 1`. **Pass**
2. Genesis identity registered ACTIVE. **Pass**
3. A real rotation over live mTLS is accepted. **Pass**
4. The first rotation call is not an idempotent replay. **Pass**
5. The rotated-in key has a different `key_id`. **Pass**
6. The previous (genesis) key is now GRACE_PERIOD. **Pass**
7. A retried rotation with the same idempotency key IS a replay.
   **Pass**
8. The idempotent replay returns the *same* new `key_id`, not a fresh
   one. **Pass**
9. `GetCoordinatorSigningKeys` lists both the genesis and rotated-in
   keys. **Pass**
10. A real task is issued after rotation. **Pass**
11. `reload_trusted_coordinator_keys()` runs and returns a real result.
    **Pass**
12. A post-grace-period reload (after a real elapsed 6-second wait) is
    accepted. **Pass**
13. A real revocation over live mTLS is applied. **Pass**
14. Revoking the only ACTIVE key reports
    `production_task_issuance_stopped=true`. **Pass**
15. `AcquireTask` fails closed (`FAILED_PRECONDITION`) with no ACTIVE
    coordinator key. **Pass**
16. A retried revocation with the same idempotency key is a replay.
    **Pass**
17. Rotating over the RPC with an empty
    `expected_current_signing_key_id` and no ACTIVE key existing is
    correctly **rejected** (confirming the CLI-only recovery-fallback
    design is real, not accidental). **Pass**
18. A worker identity is rejected (`PERMISSION_DENIED`) from
    `RotateCoordinatorSigningKey`. **Pass**

**5-scenario recovery-CLI walkthrough** (direct `docker exec`, no
running coordinator RPC server involved for these calls):

1. `show` on an empty registry reports no keys. **Pass**
2. `rotate` with no current key bootstraps a fresh initial key; bundle
   written at version 1. **Pass**
3. `rotate --grace-period-seconds 5` performs a real rotation; bundle
   version 2 contains both keys. **Pass**
4. A real 6-second `sleep`, then `show`, correctly reports the
   grace-period key as `expired` with no background sweep running;
   `regenerate-bundle` writes version 3 excluding the expired key.
   **Pass**
5. `revoke` on the sole ACTIVE key produces bundle version 4 with zero
   trusted keys and a visible warning; a subsequent `rotate` (no ACTIVE
   key exists) registers a fresh initial key via the recovery fallback,
   producing bundle version 5 with the full revoked/expired history
   still inspectable via `show`. **Pass**

Every one of the five bundle versions above was independently
re-loaded and checksum-verified by a fresh Python process, confirming
real (non-tautological) cross-language agreement on the checksum
algorithm.

**23/23 live checks passed, 0 failed.**

## 33. Security findings

No vulnerabilities were found in the pre-existing code this slice
builds on. One design risk was identified and deliberately closed
during implementation rather than left latent: a naive idempotency
implementation that merely re-ran a "duplicate" rotation request would
have minted a *second*, different Ed25519 key on every retry (since key
generation is inherently non-deterministic) — silently defeating the
purpose of idempotency and potentially leaving two keys momentarily
ACTIVE-adjacent under retry storms. This was closed by making the
`IdempotencyStore` check happen *before* any key generation, returning
the cached outcome instead of re-executing the mutation at all.

## 34. Remaining trust assumptions

- The trusted-key bundle file's authenticity rests entirely on
  filesystem-level protections (atomic writes, restrictive
  permissions, out-of-band volume/secret delivery) — there is no bundle
  self-signature. A worker's very first bundle load is trust-on-first-
  use by construction; this project does not yet implement anything
  stronger for the first load (documented, not silently assumed).
  See [known-limitations.md](known-limitations.md).
- The `ADMIN_CONTROL` authorization check trusts the go-api service's
  mTLS-bound identity; there is no additional out-of-band
  human-approval step for a rotation or revocation call itself (that
  would be a Go/web-layer concern, out of this slice's scope).
- A lost (not revoked) coordinator private key's old registry entry is
  not automatically marked REVOKED — an operator must do so explicitly
  once certain recovery is impossible with the old key (§10).
- Idempotency records and the registry/bundle files are all trusted to
  be un-tampered-with at rest — no OS-level integrity monitoring is
  assumed or checked by this codebase.

## 35. Known limitations

See the "Security Administration, Observability, and Runtime
Validation slice" section of
[known-limitations.md](known-limitations.md) for the complete,
itemized list (Go/web entirely unimplemented; no formal event schema
or Prometheus metrics beyond the six stderr events; no durable audit
journal; no 58-scenario Docker Compose matrix; no Go/web tests or CI;
the deliberate RPC-vs-CLI recovery-fallback asymmetry; no old-key-file
cleanup; no performance benchmarking; no bundle self-signature).

## 36. Regression status

Zero regressions. Locally on Windows/MSVC (non-gRPC-gated code): 7/7
`ctest` suites pass, with `fl_coordinator_tests` itself passing all 22
internal test groups. Full Python suite: 264 passed, 1 skipped, 0
failed, 0 errors (freshly re-verified during this finalization pass —
see §31's note on the count discrepancy against an earlier
in-session figure). `ruff` and the CI-equivalent `mypy` invocation both
report zero issues. The terminology checker and the proto-contract
compatibility checker both pass. In Docker (gRPC-gated code, executed
earlier this session): all 12 `ctest` suites pass, including the
unchanged `fl_coordinator_grpc_tests` integration test — confirming the
new optional constructor parameters on `CoordinatorServiceImpl` do not
alter behavior for any caller that does not opt into them.

## 37. Git working-tree summary

No commits, pushes, tags, or pull requests were made this slice, per
standing instruction — only local file changes exist. This slice's own
new/modified files are listed in §27/§28. The broader working tree
(`git status --short` currently reports 355 changed paths) also
carries the cumulative, uncommitted diff of every prior slice in this
multi-slice session (Foundation through Coordinator-Signed Tasks) —
none of that has been committed either, consistent with the same
standing instruction applying throughout the whole session, not newly
introduced by this pass.

## 38. Recommended next work toward secure aggregation

In priority order:

1. **Go coordinator security client + Go security HTTP APIs** — the
   highest-leverage next step, since every RPC this report describes
   (and every RPC from the four prior security slices) currently has no
   HTTP-callable surface at all.
2. **Web Security Center** — blocked on (1).
3. **A durable, queryable security-audit journal** (Work Packages S/T/U)
   — currently the registry files are the only durable record, with no
   append-only log or query API.
4. **A formal, schema-versioned security-event type and Prometheus
   metrics** — currently only ad hoc structured stderr lines exist
   across every security-focused slice in this project.
5. **The full 58-scenario Docker Compose validation matrix and an
   automated `scripts/security-validation/` harness** — currently every
   security slice (this one included) validates via direct `docker run`
   plus hand-written scripts.
6. Only after 1-5, or independently at the user's discretion: begin
   real secure-aggregation protocol work (Work Packages 7.1-7.9 in
   `plan.md`) — but this remains blocked on selecting and vetting a
   real threshold secret-sharing library, a blocker carried unresolved
   across every prior slice in this project and not addressed here.

Explicit non-goals maintained this slice, per standing instruction: no
secure aggregation protocol execution, pairwise masking, private client
masks, fixed-point secure-aggregation encoding, threshold secret
sharing, share reconstruction, dropout recovery, unmasking, secure
aggregate reconstruction, protocol transcript chaining, homomorphic
encryption, Byzantine-robust aggregation, remote worker attestation,
trusted execution environments, TPM integration, Ray, Flower runtime,
asynchronous/semi-synchronous aggregation, production Kubernetes
rollout, PostgreSQL/Redis/MinIO/S3 migration, or full enterprise
identity-provider integration.

---

## Completion gates — evaluated

| # | Gate | Status |
|---|---|---|
| 1 | Live rotation works end to end | **Pass** — §32 |
| 2 | Rotation is idempotent | **Pass** — §32 (checks 4, 7, 8) |
| 3 | Exactly one ACTIVE coordinator key at any time | **Pass** — enforced by registry construction, unit-tested and live-observed |
| 4 | Grace period survives a coordinator restart | **Partial** — restart persistence of the registry/keyed-identity is live-validated (§32 recovery walkthrough spans separate CLI invocations, each a fresh process); a full *running-server* process restart mid-grace-period specifically was not separately re-executed this pass (the general restart-persistence mechanism is unchanged from the prior slice's live-validated coordinator-process-restart test) |
| 5 | Expiry enforced (not only by background timer) | **Pass** — §6, §32 |
| 6 | Revocation enforced immediately | **Pass** — §32 (checks 13-15) |
| 7 | Task issuance stops with no valid coordinator key | **Pass** — §32 (check 15) |
| 8 | Bundle writes are atomic | **Pass** — temp-file + rename, unit-tested (`trusted_key_bundle_test.cpp`) |
| 9 | Worker bundle reload is safe (rollback/corruption rejected) | **Pass** — §9, unit-tested and live-validated |
| 10 | Bundle version monotonicity enforced | **Pass** — §9 |
| 11 | No private key ever appears in requests/responses/events/logs | **Pass** — confirmed by direct inspection and live `grep` over stderr/RPC payloads |
| 12 | Rollback-safe bundle update on generation failure | **Partial** — the bundle write failure path returns `INTERNAL` and logs CRITICAL, but the registry mutation itself is *not* rolled back on a bundle-write failure (disclosed honestly in code comments and §3/§7 rather than silently assumed away) |
| 13 | Restart persistence of coordinator keys | **Pass** — `main.cpp` resumes from whichever key the registry reports ACTIVE, not always genesis |
| 14 | Max grace period enforced | **Pass** — unchanged, reused from the prior slice's registry validation |
| 15 | Max coordinator-key lifetime enforced | **Pass** — new `kMaxCoordinatorKeyLifetimeSeconds` (90 days), unit-tested |
| 16 | Safe failure on no/multiple ACTIVE keys | **Pass** — no-ACTIVE-key fails closed (§32 check 15, 17); multiple-ACTIVE is structurally impossible and the bundle loader independently rejects a bundle that somehow claimed more than one |
| 17 | Structured error codes for rejections | **Pass** — `CoordinatorSigningKeyRotationRejectionReason` enum, extended this slice with two new reasons |
| 18 | Coordinator private key never distributed to Go/Python | **Pass** — only the public trusted-bundle file crosses that boundary |
| 19 | Coordinator key-generation/storage requirements (permissions, atomic, no world-readable, no key in logs/image layers) | **Pass** for the coordinator-generated mode; pre-provisioned-key mode not implemented (§4) |
| 20 | Bundle lifecycle requirements (schema version, checksum, restart persistence, version increment, rollback-safe, worker reload) | **Pass** — §8, §9 |
| 21 | Worker reload requirements (validate-before-replace, keep previous on failure, thread-safe, version monotonic, reject rollback/corrupted/duplicate-active) | **Pass** — §9 |
| 22 | Coordinator key recovery documented/implemented | **Pass** — §10, CLI explicitly accepted by the specification as sufficient |
| 23 | Go Coordinator Security Client | **Fail (deferred)** — §11 |
| 24 | Go Security HTTP APIs | **Fail (deferred)** — §12 |
| 25 | HTTP Authorization / permission matrix | **Fail (deferred, N/A without a Go HTTP layer)** — §13 |
| 26 | Mutation safety (idempotency key, request ID, trace ID, reason, expected state, audit actor, timestamp, coordinator timeout) | **Pass** at the gRPC layer for the two new RPCs (§15); **Fail (deferred)** for any HTTP-layer equivalent, since none exists |
| 27 | Web Security Center (routes, dashboard, admin UI) | **Fail (deferred)** — §16-18 |
| 28 | ~50 named security events + common schema | **Fail (deferred)** — only 6 ad hoc stderr lines exist (§19-20) |
| 29 | Prometheus security metrics | **Fail (deferred)** — §21 |
| 30 | Durable security audit journal | **Fail (deferred)** — §22 |
| 31 | Role-aware response redaction | **Fail (deferred, N/A without Go/web)** — §14 |
| 32 | 58-scenario Docker Compose validation matrix | **Fail (deferred)** — §24-25 describe what was actually run instead |
| 33 | Go tests / Web tests for new surfaces | **Fail (deferred, N/A — no Go/web surfaces exist)** — §26 |
| 34 | Security-focused CI gates | **Fail (deferred)** — §26 |
| 35 | Zero regressions across every prior slice | **Pass** — §36 |

Gates 1-22 and 35 (the C++/Python core this slice actually targeted)
are Pass, with two explicitly disclosed partials (gate 4's specific
re-execution scope, gate 12's known non-rollback-on-bundle-failure
limitation). Gates 23-34 are Fail-by-deferral, consistent with the
confirmed scope decision — they are not silently marked Pass, and this
report does not claim otherwise.

**Stopping here, as instructed.** Go security APIs, the Web Security
Center, the durable audit journal, Prometheus metrics, the full Docker
Compose validation matrix, and CI gates remain for a future slice.
Secure aggregation protocol work (pairwise masking, threshold secret
sharing, dropout recovery, or any other item in the specification's
explicitly-forbidden list) was not started.
