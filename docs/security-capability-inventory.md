# Security Capability Inventory (Work Package A)

**This is a living, audited table — not documentation-as-proof.**
Rewritten for the Security Runtime Completion and Release Evidence
slice to use the column set that slice's Work Package A specifies
(Capability / Unit-tested / Cross-language tested / Integration-tested
/ Docker-tested / Browser-tested / CI-tested / Restart-tested /
Failure-tested / Current status / Evidence location / Remaining
limitation), replacing the narrower 12-column table this file
previously used. Every "Docker-tested"/"Browser-tested" claim below was
re-verified this pass via a real, live `docker compose` run of
`scripts/security-validation/run.py` (not reused from an older pass —
see [security-runtime-validation.md](security-runtime-validation.md)
for the exact command and fresh counts) and a real Playwright browser
run against the live stack. Update this table whenever new work lands;
do not let it drift back into being aspirational.

Legend: **Y** = yes, real and verified this pass. **Y (prior)** = yes,
verified in an earlier slice, not re-exercised this pass beyond what a
regression run already covers. **N** = not done. **N/A** = does not
apply to this capability.

## Transport and trust model

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mTLS transport status (`GetTransportSecurityStatus`) | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `transport.mtls.status.enforced`, `security-overview.spec.ts` | No cert-tampering scenario exercised live (DEFERRED) |
| Go API's own mTLS client identity accepted by coordinator | Y | Y | Y | **Y** | N/A | Y | N | N | Implemented + live-validated | `transport.mtls.go-api-identity.accepted` | N/A |
| Security trust model (`GetSecurityTrustModel`) | Y | Y | Y | **Y** | N | Y | N | N | Implemented + live-validated | `transport.mtls.trust-model.reachable` | No web UI panel dedicated to this endpoint alone (folded into overview) |
| Invalid/mismatched/cross-worker certificate rejection | Y (unit) | N | N | N | N | N | N | N | Unit-tested only | `signed_envelope_verifier_test.cpp`, `peer_identity_test.cpp` | Not exercised against a real live mTLS handshake this pass — DEFERRED, requires a second issued-but-wrong-identity cert per scenario |

## Worker identity lifecycle

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Worker identity listing | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `worker-identity.list.visible`, `worker-administration.spec.ts` | N/A |
| Worker identity detail + VIEWER redaction | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `worker-identity.detail.viewer-redacted` | Only worker-identity/audit views are redacted; key listings are all-or-nothing |
| Worker suspend / activate (reversible) | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `worker-identity.lifecycle.suspend-then-activate`, `worker-administration.spec.ts` | N/A |
| Worker revocation (terminal) | Y | Y | N | N | N | Y | N | N | Implemented, unit/mock only | `coordinator_service_test.cpp` | Not exercised live in the shared-stack harness — terminal action would break every later scenario needing worker-1; DEFERRED with reason |
| Active-lease cancellation on suspend | Y | N | N | N | N | Y | N | N | Implemented, unit only | `coordinator_service_test.cpp` | Requires a live in-flight task lease to observe — DEFERRED |

## Worker and coordinator signing keys

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Worker's first signing key registered ACTIVE (via signed capability) | Y | Y | Y | **Y** | N | Y | N | N | Implemented + live-validated | `worker-keys.initial.registered-active`, `signed-messages.capability.signature-accepted` | N/A |
| Worker signing-key rotation / grace period / expiry / revocation | Y | N | N | N | N | Y | N | N | Implemented, unit only | `signing_key_registry_test.cpp` | Requires orchestrating a full key-rotation RPC round trip not configured by this harness invocation — DEFERRED |
| Coordinator signing-key listing | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `signed-tasks.coordinator-key.active`, `coordinator-keys.spec.ts` | N/A |
| Coordinator signing-key rotation (real Ed25519 keygen, idempotent) | Y | Y | Y | Y (prior) | **Y** | Y | N | N | Implemented + live-validated | `coordinator-keys.spec.ts` (real rotation, additive) | Not re-exercised via the Python harness this pass (browser suite covers it live instead) |
| Coordinator signing-key revocation | Y | Y | Y | Y (prior) | N (dialog-only) | Y | N | N | Implemented + live-validated (prior pass) | `docs/coordinator-signing-key-revocation.md` | Not exercised live in *this* pass's browser suite — would halt task issuance for the rest of the shared-stack run; the dialog opens/cancels instead, real revoke stays covered by `idempotency_store_test.cpp`/Go handler tests |

## Signed messages and coordinator tasks

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Signed capability statement (RegisterWorker) | Y | Y | Y | **Y** | N/A | Y | N | N | Implemented + live-validated | `signed-messages.capability.signature-accepted`, `event-centralization.worker.registers-with-signed-capability` | N/A |
| Signed heartbeat / client-result / privacy-record acceptance | Y | Y | N | N | N | Y | N | N | Implemented, unit/cross-language only | `test_grpc_coordinator_client.py`, `coordinator_service_test.cpp` | Requires a live training-run round trip not configured by this harness invocation — DEFERRED |
| Client-result accept/reject local worker event emission | **Y (new)** | N | N | N | N | Y | N | N | Implemented + unit-tested this pass | `test_client_result_security_events.py` (5 tests) | Not yet exercised over a live training round (same DEFERRED reason above) |
| Tampering rejection (signature/payload-hash/replay/sequence) | Y | N | N | N | N | Y | N | N | Implemented, unit only | `signed_envelope_verifier_test.cpp`, `replay_protection_store_test.cpp` | Adversarial live scenarios DEFERRED — see `docs/security-runtime-validation.md` |
| Coordinator task signing / issuance / verification / replay / reissue | Y | Y | N | N | N | Y | N | N | Implemented, unit/cross-language only | `coordinator_task_signing_test.cpp`, Python `coordinator_task_verifier` tests | Requires a full `CreateRun->StartRun->AcquireTask` flow not configured by this harness — DEFERRED |

## Worker security-event centralization (new this slice)

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Real mTLS + signing identity wired into the Python worker entrypoint | **Y (new)** | N/A | N/A | **Y** | N/A | Y | N | N | Implemented + live-validated | `test_worker_entrypoint_wiring.py` (17 tests), live worker logs (`tls=True signed=True security_events=True`) | N/A |
| `_grpc_call` gRPC-error translation (was previously unhandled) | **Y (new)** | N/A | N/A | **Y** (indirectly) | N/A | Y | N | N | Implemented + unit-tested this pass — real bug fix | `test_worker_entrypoint_wiring.py::GrpcCallErrorTranslationTests` | N/A |
| Worker registers on startup even with no run_id configured | **Y (new)** | N/A | N/A | **Y** | N/A | Y | N | N | Implemented + live-validated — real bug fix | `test_worker_entrypoint_wiring.py::HealthPollLoopRegistrationTests`, live `event-centralization.worker.registers-with-signed-capability` | N/A |
| Worker-side persistent security-event queue + signed batch submission | Y (prior) | N/A | Y | **Y** | N/A | Y | Y | N | Implemented + live-validated | `event-centralization.batch.reaches-central-journal` | N/A |
| `SubmitWorkerSecurityEvents` RPC verification (signature/replay/schema) | Y (prior) | N/A | N | N | N/A | Y | N | N | Implemented, unit only | `coordinator_service_test.cpp` | Adversarial live scenarios (tampered/replayed/oversized batch) DEFERRED |
| Coordinator outage overlapping a worker restart: no silent event loss | N/A | N/A | **Y (new)** | **Y** | N/A | Y (scheduled) | **Y** | **Y** | Implemented + live-validated this pass | `event-centralization.recovery.coordinator-outage-then-delivery` | N/A |
| Coordinator restart preserves the central security-event journal | N/A | N/A | **Y (new)** | **Y** | N/A | Y (scheduled) | **Y** | N | Implemented + live-validated this pass | `event-centralization.restart.coordinator-preserves-journal` | N/A |
| Event source health + staleness threshold | **Y (new)** | N/A | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated this pass | `security_overview_test.go::TestSecurityEventSourcesMarksStaleSourceAfterThreshold`, `security-overview.spec.ts` | Single fixed threshold, not per-source-type configurable (by design) |

## Event and audit journals

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Security event journal endpoint, pagination, severity filter | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `event-journal.endpoint.real`, `event-journal.pagination-and-filters.real`, `event-explorer.spec.ts` | N/A |
| Event redaction by role (ADMIN vs RESEARCHER) | Y | Y | Y | **Y** | N | Y | N | N | Implemented + live-validated | `event-journal.redaction.role-aware` | N/A |
| Event journal survives an api container restart | N/A | N/A | Y | **Y** | N | Y (scheduled) | **Y** | N | Implemented + live-validated | `event-journal.restart.persists` | N/A |
| Event/audit journal corruption detection and recovery | Y | Y | N | N | N | Y | N | N | Implemented, unit only | C++/Python/Go journal test suites' corrupted-line tests | Not exercised live (would require injecting a malformed line into a running container's volume) — DEFERRED |
| Security audit journal endpoint, cursor pagination, role gating | Y | Y | Y | **Y** | **Y** | Y | N | N | Implemented + live-validated | `audit-journal.endpoint.real-and-paginated`, `audit-journal.detailed-access.permission-gated`, `audit-explorer.spec.ts` | N/A |
| Permission-denied mutation produces a real, observable event | N/A | N/A | **Y (verified live this pass)** | **Y** | N | Y | N | N | Implemented + live-validated | `event-journal.permission-denial.produces-event` | N/A |
| HTTP mutation idempotency (Idempotency-Key, byte-identical replay) | Y | N/A | Y | **Y** | N | Y | N | N | Implemented + live-validated | `security-api.mutation.idempotent-replay` | In-memory only (Go side), lost on process restart — documented trade-off |
| Permission-denial matrix (VIEWER/RESEARCHER/invalid token) | Y | N/A | Y | **Y** | N | Y | N | N | Implemented + live-validated | `security-api.permission-denial.matrix` | N/A |

## Metrics

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `fl_security_events_total` counter, correctly typed | Y | N/A | Y | **Y** | N | Y | N | N | Implemented + live-validated | `metrics.security-events.counter-present` | N/A |
| Event-source gauges (`records`/`batches`/`distinct_workers`), correctly typed | Y | N/A | Y | **Y** | N | Y | N | N | Implemented + live-validated | `metrics.event-source.gauges-typed` | N/A |
| No high-cardinality (per-worker/per-task) labels | N/A | N/A | **Y (live-checked)** | **Y** | N | Y | N | N | Implemented + live-validated | `metrics.cardinality.no-per-worker-or-per-task-labels` | N/A |
| No duplicate metric registration | N/A | N/A | **Y (live-checked)** | **Y** | N | Y | N | N | Implemented + live-validated | `metrics.registration.no-duplicates` | N/A |
| Python worker's own `/metrics` endpoint scrapeable | Y (unit) | N/A | N | N | N | Y | N | N | Unit-tested only | `fl_platform.security.metrics`'s own test suite | `metrics_port` defaults to 0/disabled and no compose override publishes it — DEFERRED, not required by any current scenario |

## Web Security Center (new this slice)

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Security Overview page (role-gated: admin/researcher/viewer; service denied) | Y | N/A | N/A | **Y** | **Y (new)** | Y (scheduled) | N | N | Implemented + browser-validated this pass | `security-overview.spec.ts` (5 tests) | N/A |
| Worker Administration page (list, detail, real suspend/activate) | Y | N/A | N/A | **Y** | **Y (new)** | Y (scheduled) | N | N | Implemented + browser-validated this pass | `worker-administration.spec.ts` (4 tests) | Revoke not exercised live in the browser suite (shared-stack, terminal action) |
| Coordinator-Key admin page (list, real rotation, revoke dialog) | Y | N/A | N/A | **Y** | **Y (new)** | Y (scheduled) | N | N | Implemented + browser-validated this pass | `coordinator-keys.spec.ts` (4 tests) | Live revoke-of-active-key not exercised in the browser suite (same shared-stack reason) |
| Event Explorer page (live-polled feed, severity/type filters) | Y | N/A | N/A | **Y** | **Y (new)** | Y (scheduled) | N | N | Implemented + browser-validated this pass | `event-explorer.spec.ts` (3 tests) | N/A |
| Audit Explorer page (live-polled feed, actor/action/resource filters) | Y | N/A | N/A | **Y** | **Y (new)** | Y (scheduled) | N | N | Implemented + browser-validated this pass | `audit-explorer.spec.ts` (3 tests) | N/A |

## Runtime-validation harness and CI (new this slice)

| Capability | Unit | Cross-lang | Integration | Docker | Browser | CI | Restart | Failure | Status | Evidence | Remaining limitation |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Modular scenario harness (14 groups, versioned registry, JSON+Markdown reports) | N/A | N/A | N/A | **Y** | N/A | Y | N/A | N/A | Implemented + live-validated | `scripts/security-validation/` (94 registered scenarios: 37 real, 57 DEFERRED, 0 BLOCKED) | N/A |
| CI: required PR-subset security-runtime job | N/A | N/A | N/A | Runs in CI | N/A | **Y (new)** | N/A | N/A | Implemented this pass | `.github/workflows/ci.yml`'s `security-runtime-pr` job | Not yet observed passing on a real GitHub Actions run (added, not yet merged/executed remotely) |
| CI: scheduled full security-runtime matrix (incl. browser suite) | N/A | N/A | N/A | Runs in CI | Runs in CI | **Y (new)** | N/A | N/A | Implemented this pass | `.github/workflows/security-runtime-full.yml` | Same as above — not yet observed on a real scheduled run |
| CI artifact sanitation check | **Y (new)** | N/A | N/A | N/A | N/A | **Y (new)** | N/A | N/A | Implemented + self-tested this pass | `scripts/security-validation/check_artifact_sanitation.py`, smoke-tested against a deliberate fixture | Text-based content only; does not inspect binary screenshot/video content (disclosed) |
| Reproducible sanitized release-evidence bundle | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Implemented this pass | `scripts/generate_release_evidence.py` -> `artifacts/security-release-evidence/` | Not yet run against a fully clean tree for this specific report (see `security-runtime-validation.md` for the fresh regression counts it aggregates) |

## What changed since the prior audit (this slice)

- Fixed three real, previously-undiscovered defects, each caught only
  by this slice's live Docker Compose validation (never by a unit
  test): the `python-worker` image missing the `security` extra
  (`pynacl`/`cryptography`), the health-poll-only worker path never
  calling `RegisterWorker`, and a `worker_id`/certificate identity
  mismatch (`python-worker-1` vs the issued `worker-1` cert).
- Wired real mTLS, a real persistent signing identity, and real
  security-event centralization into the actual Python worker
  entrypoint (`__main__.py`) — previously present in
  `GrpcCoordinatorClient` but never actually invoked with real
  parameters by the deployed container.
- Added local worker-side `CLIENT_RESULT_ACCEPTED`/`REJECTED` and
  `PRIVACY_RECORD_ACCEPTED`/`REJECTED` event emission — previously the
  one signed-message RPC (`SubmitClientResult`) with no local event at
  all.
- Built the modular `scripts/security-validation/` harness (94
  registered scenarios across 14 groups) replacing the prior single
  flat script, with real Docker-based execution, not just static
  registration.
- Added a real Playwright browser-test suite for all five Web Security
  Center routes (Work Packages G–M), run against the live Compose stack
  with real HTTP APIs — no mocked mutations.
- Added event-source staleness detection with a documented, fixed
  threshold; added two new CI workflows (PR-subset and scheduled full
  security-runtime validation) and a CI/local artifact-sanitation
  check.
- Still not built (see [known-limitations.md](known-limitations.md)):
  live adversarial/tampering scenarios for most signed-message and
  task paths (covered at the unit level only), worker signing-key
  rotation/revocation exercised live (destructive to a shared harness
  stack), a Grafana security dashboard, Python worker `/metrics`
  wired into any Compose override.
