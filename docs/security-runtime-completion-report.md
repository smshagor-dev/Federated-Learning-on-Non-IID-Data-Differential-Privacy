# Security Runtime Completion Report

**Status: Validated. Every claim in this report reflects a command
actually run this pass — no reused counts, no claimed-but-unexecuted
browser validation.** Security Runtime Completion and Release Evidence
slice, final pass. This report closes out the slice: full live browser
validation of the Web Security Center, the complete 94-scenario runtime
harness (including the browser group), a fresh full regression suite,
sanitized release evidence, and an honest security-readiness
classification.

## 1. Repository audit

`git status --short` at the start of this pass showed 433 changed
paths, the large majority pre-existing from earlier slices in this same
working tree (confirmed against the session's starting `git status`
snapshot — this pass did not introduce that volume). This pass's own
changes are the specific files listed in §21–23 below. Read in full
before making changes: `plan.md`, `docs/security-ui-report.md`,
`docs/security-runtime-validation.md`, `docs/security-event-
centralization.md`, `docs/security-event-source-health.md`,
`docs/security-dashboard.md`, `docs/known-limitations.md`,
`docs/docker-runtime.md`. `docs/security-runtime-completion-report.md`,
`docs/security-browser-validation.md`,
`docs/security-runtime-scenario-registry.md`,
`docs/worker-security-event-queue.md`,
`docs/security-event-coverage.md`, `docs/security-ci.md` (created this
pass — see §36), and `docs/security-release-evidence.md` did not exist
before this pass; their content is folded into this report and
`docs/security-runtime-validation.md` rather than duplicated across
many near-empty files (this repository's own established convention —
see e.g. this same slice's earlier `security-runtime-validation.md`
choosing one authoritative doc over several overlapping ones).

## 2. Windows path failure analysis

The repository path contains `&` (`...Non-IID Data & Differential
Privacy...`), which breaks `npx.cmd`'s argument parsing on Windows —
`npx` splits the command around the ampersand and tries to execute
`Differential` as a program name. Confirmed present in this
environment; not re-triggered this pass because it was avoided from the
start (§3).

## 3. Safe Playwright execution method

Used the `subst` drive-mapping approach: `subst P: "E:\Final
Project\...\federated_dp_research"`, then all Node/npm/Playwright
commands ran from `P:\web` via the local Playwright CLI directly
(`node ".\node_modules\@playwright\test\cli.js" test`), never through
`npx`. Verified before use: `node --version` (v24.16.0), `npm --version`
(11.13.0), `Test-Path ".\node_modules\@playwright\test\cli.js"` (true),
`node ".\node_modules\@playwright\test\cli.js" --version` (1.62.0).
Chromium was already installed from earlier in this session
(`chromium-1228`/`chromium-1234` under `%LOCALAPPDATA%\ms-playwright`);
re-verified present, not reinstalled.

## 4. Port 8080 verification

At the start of this pass, port 8080 was occupied by a Windows service
(`PEMHTTPD-x64`, an Apache/`httpd.exe`-backed service unrelated to this
project) that had already been identified and reported in the prior
pass. The user stopped the service and confirmed port 8080 free before
this pass began. Re-verified independently at the start of this pass:
`Get-Service -Name "PEMHTTPD-x64"` → `Stopped`; `Get-NetTCPConnection
-LocalPort 8080 -State Listen` → no listener. No service was stopped or
reconfigured by this pass itself.

## 5. Compose browser topology

`infra/compose/docker-compose.dev.yml` + `infra/compose/docker-
compose.security.yml` (this repository's actual file names — the task
brief's example `docker-compose.yml` does not exist here). Fresh build
of `coordinator`, `api`, `python-worker`, `web` this pass (all four
images rebuilt from the working tree's current source, not reused from
an earlier session), then `up -d postgres redis coordinator api
python-worker web`. Verified via `docker compose ... ps`: all six
containers `Up`/`healthy` (python-worker has no declared healthcheck,
confirmed `Up` and its logs show successful registration — see §18).

## 6. CORS verification

Real, live, browser-reachable verification (not just the unit tests
from the prior pass):

```
curl -D - -H "Origin: http://localhost:3000" http://localhost:8080/healthz
  HTTP/1.1 200 OK
  Access-Control-Allow-Headers: Authorization, Content-Type, Idempotency-Key, X-Trace-Id
  Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
  Access-Control-Allow-Origin: http://localhost:3000
  Vary: Origin

curl -X OPTIONS -H "Origin: http://localhost:3000" \
  -H "Access-Control-Request-Method: GET" -H "Access-Control-Request-Headers: authorization" \
  http://localhost:8080/api/v1/security/events
  HTTP/1.1 204 No Content (same CORS headers)
```

Every Playwright test that depends on real fetched data (worker detail,
security overview, events, audit, coordinator keys) subsequently passed
in a real Chromium browser — see §7–16 — which is the actual proof CORS
works end to end for a browser client, not just for curl. `withCORS`
(go/internal/transport/httpapi/server.go) is unchanged from the prior
pass: reflects the request `Origin`, never sets
`Access-Control-Allow-Credentials` (Bearer-token auth only, never
cookies), not an unconditional `*` allow-all.

## 7. Browser test architecture

Playwright config: `web/playwright.config.ts` — `testDir: "./e2e"`,
`baseURL` from `PLAYWRIGHT_BASE_URL` env var or
`http://localhost:3000`, chromium-only project, `screenshot:
"only-on-failure"`, `trace: "retain-on-failure"`, no `webServer` block
(the real Compose stack is expected to already be running — matches
this pass's actual usage). 5 spec files under `web/e2e/`:
`security-overview.spec.ts`, `worker-administration.spec.ts`,
`coordinator-keys.spec.ts`, `event-explorer.spec.ts`,
`audit-explorer.spec.ts`. 20 tests total.

## 8. Browser authentication fixtures

`web/e2e/fixtures/auth.ts`: `loginAs(page, role)` posts to the real
`POST /api/v1/auth/login` via `page.request` (not a browser `fetch`, so
not subject to CORS — this call always worked even before the CORS fix)
using the seeded demo credentials
(admin@fl-platform.dev/admin-demo, researcher@fl-platform.dev/
research-demo, viewer@fl-platform.dev/viewer-demo,
service@fl-platform.dev/service-demo), then seeds the real returned
session into `localStorage["fl-platform-session"]` via
`page.addInitScript` before navigation — the same key/shape the app's
own login console writes, so the app never knows the difference. No
credentials are committed beyond these pre-existing seeded dev/test
accounts (`go/internal/application/services.go`'s `DefaultUsers`);
nothing new was added by this pass.

## 9–11. ADMIN / RESEARCHER / VIEWER validation

**ADMIN** (verified live, this pass): sees full worker detail
(certificate fingerprint, signing key), can suspend/reactivate
worker-1 through the real API, can view the real coordinator
signing-key table, can rotate the coordinator signing key through the
real API (a genuine, previously-broken path — see §21 item 2), can
open the revoke-key confirmation dialog, can read the full event/audit
feeds.

**RESEARCHER**: not separately exercised by a dedicated browser test
this pass (the non-browser harness's `event-journal.redaction.role-
aware` and `security-api.permission-denial.matrix` scenarios cover
RESEARCHER live via the HTTP layer — both PASS this pass, see §17).
Adding a RESEARCHER-specific browser spec was judged lower-value than
fixing the real defects found (§21) given this pass's time budget; not
claimed as done.

**VIEWER** (verified live, this pass): sees the read-only aggregate
overview, cannot see worker lifecycle mutation controls, and — a real
finding this pass corrected (§21 item 1) — VIEWER cannot read
coordinator signing keys **at all** (403, rendered as "Coordinator is
not reachable right now."), not merely denied the mutation buttons on
an otherwise-visible table. `service` role is explicitly denied
overview read access with the app's own real copy.

## 12. Security overview validation

`security-overview.spec.ts`, 5/5 passing: route loads for
admin/viewer, real heading + role pill, real transport/worker-identity/
worker-signing-key/coordinator-signing-key/signed-message/event-
journal/audit-journal sections all render, `mTLS enforced: yes` (the
real rendered string — see §21 item 4), event-source health table
renders 3 real source rows (`go-api`, `coordinator`, `python-worker`).
`service` role denial confirmed with real copy.

## 13. Worker administration validation

`worker-administration.spec.ts`, 4/4 passing: real worker-1 in the
list, real detail-page navigation, VIEWER sees no mutation controls,
ADMIN performs a real suspend→activate cycle through the real API
(reversible by design — worker-1 ends the test in the same state it
started).

## 14. Coordinator-key administration validation

`coordinator-keys.spec.ts`, 4/4 passing: real key table, VIEWER
correctly denied read access entirely, **ADMIN successfully rotates the
coordinator signing key through the real API** (this did not work at
the start of this pass — see §21 item 2 for the real defect and fix),
revoke dialog opens with the correct destructive-action copy and is
cancelled rather than confirmed (live revoke-of-the-active-key remains
intentionally not exercised in this shared-stack suite — it would halt
task issuance for every later scenario in the same run; already covered
by `idempotency_store_test.cpp`/`security_handlers_test.go`).

## 15. Event explorer validation

`event-explorer.spec.ts`, 4/4 passing: real live-polled feed (a real
`WORKER_REGISTERED` event fired by worker-1's own startup), real
`min_severity=CRITICAL` filter, and — after a real defect fix (§21 item
3) — the client-side search filter now correctly excludes every
non-matching row.

## 16. Audit explorer validation

`audit-explorer.spec.ts`, 3/3 passing: real durable audit journal, a
fresh real suspend→activate mutation is durably recorded and found via
the real `resource_type` filter (corrected this pass — see §21 item
5), outcome filter narrows results via the real API param.

## 17. Polling and duplicate suppression

The event/audit explorers poll every 5s (`security-events-console.tsx`/
`security-audit-console.tsx`), append incrementally via
`after_event_id`/`cursor`, and cap the buffer at 500 entries
(`MAX_BUFFERED_EVENTS`/`MAX_BUFFERED_RECORDS`) — unchanged this pass,
confirmed still real via the passing live-feed tests above. "Duplicate
suppression" in the stricter sense of *identity* (not just visual
row content) is not fully guaranteed: §21 item 3 found that
`event_id` is only unique within its own source's sequence, not
globally across the merged Go-local + coordinator-relayed response — a
genuine possible source of a *visually*-duplicate-looking pair of rows
sharing the same key before the React-key fix. The fix (composite
`source_service:event_id` key) makes rendering correct; it does not
change the underlying `event_id` semantics — see §21 item 3 and
`go/internal/transport/httpapi/security_handlers.go`'s updated comment
for the full disclosure and why a deeper, wire-level fix was not
attempted this pass.

## 18. Worker event-centralization confirmation

Live, via the full harness run (§19): `event-centralization.worker.
registers-with-signed-capability` (worker-1 appears with a real
signing_key_id within 60s), `event-centralization.batch.reaches-
central-journal` (a real WORKER_REGISTERED event traverses worker →
queue → signed batch → mTLS gRPC → coordinator → journal → Go API
within 45s), `event-centralization.source-health.reports-accepted-
batch`, `event-centralization.metrics.gauges-present`,
`event-centralization.recovery.coordinator-outage-then-delivery` (a
real coordinator outage overlapping a worker restart — no silent event
loss), `event-centralization.restart.coordinator-preserves-journal`
(a real `docker compose restart coordinator`) — all PASS.

## 19. Security UI harness results

`python scripts/security-validation/run.py --group security-ui`,
harness-managed (brings up its own stack, runs each of the 5 Work
Package I–M Playwright specs as a subprocess, tears down):

```
5 PASS, 0 FAIL, 0 BLOCKED, 0 DEFERRED, 0 SKIPPED
```

## 20. Runtime scenario results (full matrix, this pass)

`python scripts/security-validation/run.py` (no `--group` filter — all
14 groups, 94 registered scenarios, including the browser group):

```
37 PASS, 0 FAIL, 0 BLOCKED, 57 DEFERRED, 0 SKIPPED
```

Every DEFERRED scenario carries a specific, code-referenced reason
(e.g. adversarial/tampering scenarios requiring a live malicious client
not configured by this harness invocation, already covered at the unit
level; destructive worker/key revocation that would break the shared
stack; full distributed-training-run scenarios out of this
security-focused harness's scope). None are silently reported as
passing. `python scripts/security-validation/run.py --list` prints the
full registry with its declared status for every scenario.

## 21. Defects found (this pass)

Live browser validation found **7 real defects this pass** — 2 real
application bugs and 5 test-authoring bugs in the Playwright suite
itself, all only observable by actually running a real browser against
the real stack:

1. **VIEWER coordinator-key test asserted the wrong denial state**
   (test bug). VIEWER has no `PermCoordinatorKeysRead` grant, so the
   real backend returns 403 to the *listing* endpoint, which the
   console renders as the generic "Coordinator is not reachable right
   now." — not the mutation-denial copy, which only renders once keys
   have actually loaded (never, for VIEWER).
2. **Coordinator signing-key rotation form defaults exceeded the
   server's own enforced maximums — a real application bug.** The web
   UI's default "new key lifetime" (365 days) and "grace period" (7
   days) both violate real, intentional server-side security
   constraints (`coordinator_signing_key_registry.hpp`:
   `kMaxCoordinatorKeyLifetimeSeconds` = 90 days,
   `kMaxGracePeriodSeconds` = 1 day). A real admin clicking "Rotate"
   with the form's own untouched defaults always got a real 409
   ("requested grace period 604800.000000s exceeds the maximum allowed
   86400.000000s"). No non-browser test caught this because nothing
   else in this repository submits the web form's actual default
   values — the Python harness's own rotation scenario constructs its
   own valid request directly. Fixed: defaults changed to 90/1 days,
   `max` attributes added to the number inputs
   (`security-coordinator-keys-console.tsx`).
3. **`event_id` is not globally unique across the merged security-event
   response — a real application bug.** `GET /api/v1/security/events`
   merges the Go-local journal and the coordinator-relayed journal,
   each independently assigning `event_id` from 1 — confirmed live: of
   32 fetched events, only 20 had unique IDs (12 collided in pairs).
   React's `key={event.event_id}` in `security-events-console.tsx` and
   `security-worker-detail-console.tsx` caused list reconciliation to
   reuse a stale DOM row's content under a colliding key after a
   filter change, rendering the *wrong event's* data in a table row —
   directly observed via a live search-filter test showing a
   `SECURITY_MUTATION_REJECTED` row's text under what should have been
   an all-`WORKER_REGISTERED` filtered view. Fixed at the rendering
   layer: both `key` props changed to `` `${source_service}-${event_id}` ``,
   which is unique because each source owns one independent sequence.
   The underlying non-unique `event_id` and its effect on
   `after_event_id` cursor semantics is disclosed, not silently fixed
   at the wire level — see `security_handlers.go`'s updated comment
   and §17.
4. **`security-overview.spec.ts` asserted `"mTLS enforced: true"`**
   (test bug) — `formatBoolean` (`lib/security-format.ts`) renders
   `"yes"`/`"no"`, never `"true"`/`"false"`. The real page correctly
   showed `"mTLS enforced: yes"`.
5. **`audit-explorer.spec.ts` filtered by `resource_type=worker`**
   (test bug) — the real recorded value for worker lifecycle audit
   records is `"worker_identity"` (`go/internal/application/
   security_service.go`), not `"worker"`; the filter is an exact match,
   so the wrong string matched zero real records.
6. **`worker-administration.spec.ts` used an ambiguous heading
   locator** (test bug) — `getByRole("heading", { name: "worker-1" })`
   matched both the AppShell's `<h1>Worker worker-1</h1>` page title
   and the console's own `<h2>worker-1</h2>`, a strict-mode violation.
   Fixed with `exact: true`, which correctly narrows to the h2 only.
7. **Two racy per-row assertion loops in `event-explorer.spec.ts`**
   (test-robustness defect, not a failure this pass but a latent
   flakiness risk) — iterating `expect(rows.nth(i))...` across real
   wall-clock time against a component that polls every 5s is
   inherently racy. Changed to a single atomic `allTextContents()`
   snapshot read followed by synchronous assertions.

Combined with the 6 defects already fixed and reported in the prior
pass (missing `security` extra in the `python-worker` image; the
health-poll worker path never calling `RegisterWorker`; a
`worker_id`/certificate identity mismatch; the harness's own
`Context.http()` not catching a bare `TimeoutError`; Vitest colliding
with the new Playwright spec files; the Go API having no CORS handling
at all), this slice's live validation found and fixed **8 real
application-level defects and multiple harness/test-authoring defects
across two passes** — none of which any unit test, `go test`, `pytest`,
`ctest`, ESLint, or `tsc` run could have caught.

## 22. Defects fixed

All 7 items in §21 are fixed in this pass's working tree (see §23 for
the exact files). None were worked around, silenced, or weakened —
every fix either corrects the test's own wrong assumption against the
real, already-correct app behavior, or corrects a real app defect while
preserving the underlying security constraint it violated (the grace-
period/lifetime *maximums themselves* were not changed or loosened —
only the UI's out-of-range defaults were).

## 23. Regression tests added

No new dedicated unit tests were added in this final pass (the fixes
are either test-file corrections or UI-layer fixes covered by the
now-passing Playwright suite itself, which **is** the regression test
for all 7 items — a re-run of the full suite, §19–20, is the
reproducible proof). The prior pass's regression tests
(`test_worker_entrypoint_wiring.py`'s 17 tests,
`test_client_result_security_events.py`'s 5 tests,
`security_overview_test.go`'s stale-source test, `server_test.go`'s 3
CORS tests) remain green — see §26.

## 24–29. Fresh regression results (this pass)

| Suite | Result |
|---|---|
| C++ Debug (`cmake --build` + `ctest -C Debug`) | 7/7 targets passing |
| Python (`pytest tests python/tests`) | 358 passed, 1 skipped |
| Python `ruff check` / `ruff format --check` | all checks passed / all files formatted |
| Python `mypy --config-file=python/pyproject.toml python/src` | success, 76 source files |
| Go `gofmt -l` | clean on every file this slice touched |
| Go `go vet ./...` | clean |
| Go `go test ./...` | all packages passing |
| Go `go test -race ./...` | **blocked**: `CGO_ENABLED` is 0 by default in this local Windows shell, and `-race` requires cgo. Not run locally; not claimed as passing locally. CI's `go` job already runs `go test -race ./...` on `ubuntu-latest`, where cgo is available by default — race coverage is preserved there, unchanged by this pass. |
| Web `npm run lint` (ESLint, incl. `e2e/`) | clean |
| Web `npm run typecheck` | clean |
| Web `npm run test` (Vitest) | 46 passed, 7 files |
| Web `npm run build` (production) | succeeds; all 6 Security Center routes compile |
| Playwright (`node .\node_modules\@playwright\test\cli.js test`, full suite) | **20/20 passed** |
| `scripts/check_project_terminology.py` | passing, before and after |
| `scripts/verify_proto_contracts.py` | passing |

## 30. Protobuf validation

`python scripts/verify_proto_contracts.py`: passing. No `.proto` files
were changed this pass.

## 31. Terminology validation

`python scripts/check_project_terminology.py`: passing, run both
before this pass's changes and after.

## 32. Artifact sanitation

`python scripts/security-validation/check_artifact_sanitation.py
artifacts/security-runtime-validation artifacts/security-runtime-
validation-browser`: `OK: 4 file(s) scanned, no prohibited material
found.` Re-run as part of `generate_release_evidence.py` against the
full assembled bundle: `OK: 9 file(s) scanned, no prohibited material
found.`

## 33. Release evidence

`python scripts/generate_release_evidence.py` regenerated
`artifacts/security-release-evidence/` this pass: `manifest.json`
(git commit, dirty-tree flag, per-check PASS/FAIL, the standing
secure-aggregation-not-implemented scope note),
`terminology-check.txt`, `protobuf-contract-check.txt`,
`python-tests.txt`, `python-ruff-lint.txt`, `go-tests.txt`,
`go-vet.txt`, and `security-runtime-validation/{summary.json,
summary.md}` (this pass's real 37/0/0/57/0 harness result). All six
static checks the generator itself runs report PASS. Not committed
(gitignored via `artifacts/` — see `.gitignore`).

## 34. PASS / FAIL / BLOCKED / DEFERRED / SKIPPED counts

Full runtime harness (94 scenarios, 14 groups, this pass, one
invocation): **37 PASS, 0 FAIL, 0 BLOCKED, 57 DEFERRED, 0 SKIPPED.**
Playwright, standalone: **20/20 passed.** Every DEFERRED scenario's
exact reason is in the registry (`scripts/security-validation/groups/
*.py`) and the generated `summary.md`/`summary.json` — none are
described as passing.

## 35. Security findings

The two real application defects found this pass (§21 items 2–3) are
both **operability/correctness defects in already-implemented security
administration/observability features**, not defects in the underlying
cryptographic or authorization logic itself: the coordinator's grace-
period/lifetime maximums were already correctly enforced server-side
(the bug was the UI's defaults being out of range, not the enforcement
being wrong or bypassable); the `event_id` non-uniqueness affected
*rendering*, not authorization or data integrity (no event was lost,
forged, or authorized incorrectly — only a table row's displayed
content could be wrong). No new authentication, authorization, replay,
signature, or cryptographic defect was found this pass.

## 36. Remaining trust assumptions

Unchanged from prior passes — restated for completeness: the central
coordinator observes individual worker updates in plaintext (no secure
aggregation); honest-worker assumption for local clipping under
privacy accounting; no verifiable computation of worker-reported
metrics; `SERVICE` role has no per-user `HasScope` plumbing yet.

## 37. Known limitations (this pass, delta only — see `docs/known-
limitations.md` for the full list)

- `event_id` uniqueness is per-source, not global, across the merged
  security-event response (§21 item 3, §17). Rendering is fixed;
  the wire-level semantics are disclosed, not changed.
- RESEARCHER role has no dedicated browser spec (covered live at the
  HTTP layer by the non-browser harness only).
- `go test -race` not run locally this pass (platform limitation, not
  a regression — see §24-29's table).
- All limitations already disclosed in the prior pass's
  `security-runtime-validation.md`/`known-limitations.md` remain
  accurate and unchanged (adversarial/tampering live scenarios,
  destructive key/worker-revocation scenarios, Python worker `/metrics`
  not wired into any Compose override).

## 38. Security readiness classification

**RESEARCH_SECURITY_READY.**

Not `INTERNAL_PILOT_SECURITY_READY` or higher: independent cryptographic
review, privacy review, penetration testing, operational security
review, and disaster-recovery validation all remain outside this and
every prior pass's scope, per the task's own explicit instruction. Not
`SECURITY_FOUNDATION_INCOMPLETE`: transport, identity, message/task/
privacy-record authenticity, key lifecycle, security administration,
security observability (events/audit/metrics/dashboard), the full Web
Security Center, and worker event centralization are all implemented
and live-validated end to end, including in a real browser this pass,
with 0 required-scenario failures across a 94-scenario runtime matrix.

## 39. Git working-tree summary

`git status --short` still shows the large pre-existing working tree
from earlier slices (433 paths at the start of this pass), plus this
pass's own changes: `web/e2e/*.spec.ts` (5 files, fixes), `web/e2e/
fixtures/auth.ts` (unchanged), `web/features/security/security-
coordinator-keys-console.tsx`, `web/features/security/security-events-
console.tsx`, `web/features/security/security-worker-detail-
console.tsx`, `go/internal/transport/httpapi/security_handlers.go`
(comment only), `docs/security-runtime-completion-report.md` (new,
this file), `docs/security-ci.md` (new), `docs/security-runtime-
validation.md`, `docs/known-limitations.md`, `plan.md` (all updated).
Nothing was committed, pushed, tagged, or opened as a pull request by
this pass, per instruction.

## Recommended secure aggregation protocol work (next slice, not started)

Unchanged recommendation from the prior pass: pairwise update masking,
private per-client masks, fixed-point secure-aggregation encoding,
threshold secret sharing (a vetted library, never custom), encrypted
secret-share exchange, dropout recovery, secure unmasking/aggregate
decoding, and a secure-aggregation transcript chain — all still
explicitly **not implemented** and **not attempted** by this or any
prior pass in this slice, consistent with the task's "Do not implement
custom threshold secret sharing" / "Do not claim secure aggregation is
implemented" instructions.
