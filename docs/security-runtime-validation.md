# Security Runtime Validation

**Status: Fully validated, including the browser matrix.** Security
Runtime Completion and Release Evidence slice, final pass. This
document records what was actually run and what it actually showed —
no claim below is asserted without a real, reproduced result from this
pass. Per this project's own stated policy, none of the numbers below
are reused from an earlier pass's snapshot — every count here is a
fresh run, including a full, clean, harness-managed run of the browser
group (previously blocked by a local port-8080 conflict that has since
been resolved — see
[security-runtime-completion-report.md](security-runtime-completion-report.md)
for the full final report, including 2 additional real application
defects this final pass found and fixed live in a real browser).

## Regression suites (fresh run, this pass)

| Suite | Result |
|---|---|
| C++ Debug (`cmake` + `ctest -C Debug`, local Windows/MSVC, protobuf-free `fl_coordinator_tests`) | 7/7 targets passing |
| Python (`pytest tests python/tests`) | 358 passed, 1 skipped (was 336 in the prior slice's snapshot — +22 new: `test_worker_entrypoint_wiring.py` (17), `test_client_result_security_events.py` (5)) |
| Python `ruff check` / `ruff format --check` | all checks passed / all files already formatted |
| Python `mypy --config-file=python/pyproject.toml python/src` | success, no issues, 76 source files |
| Go `go build ./...` | clean |
| Go `go vet ./...` | clean |
| Go `go test ./...` | all packages passing (3 new: `TestCORSPreflightRequestReturnsNoContentWithoutInvokingTheHandler`, `TestCORSHeadersPresentOnARealCrossOriginResponse`, `TestCORSNoOriginHeaderMeansNoAllowOriginHeader`; plus `TestSecurityEventSourcesMarksStaleSourceAfterThreshold`) |
| Go `gofmt` | clean on every file touched this slice (`internal/transport/httpapi/*`) |
| Web `npm run test` (Vitest) | 46 passed, 7 files (unchanged count from the prior slice, but see the vitest/Playwright collision bug below — this number was not trustworthy until that was fixed) |
| Web `npm run lint` (ESLint, incl. `e2e/`) | clean |
| Web `npm run typecheck` | clean |
| Web `npm run build` (production) | succeeds; all 6 Security Center routes compile (`/security`, `/security/workers`, `/security/workers/[workerId]`, `/security/coordinator-keys`, `/security/events`, `/security/audit`) |
| `scripts/check_project_terminology.py` | passing, before and after |
| `scripts/verify_proto_contracts.py` | passing |
| Playwright (`node .\node_modules\@playwright\test\cli.js test`, full 5-spec suite, real browser) | **20/20 passed** |
| `scripts/security-validation/run.py` (all 14 groups incl. `security-ui`, live Docker, one invocation) | **37 PASS, 0 FAIL, 0 BLOCKED, 57 DEFERRED, 0 SKIPPED** |
| `scripts/security-validation/check_artifact_sanitation.py` (harness output) | OK, 4 files scanned, no prohibited material |
| `scripts/generate_release_evidence.py` (full bundle) | OK, 9 files scanned, no prohibited material; all 6 static checks PASS |

Do not reuse these numbers in a future slice without re-running.
`go test -race ./...` was not run locally this pass (`CGO_ENABLED=0` by
default in this Windows shell; `-race` requires cgo) — not claimed as
passing locally; CI's `go` job runs it on `ubuntu-latest`, unaffected.

## The live runtime-validation harness

`scripts/security-validation/run.py` (no `--group` filter) brings up
`postgres`+`redis`+`coordinator`+`api`+`python-worker`+`web` over the
real mTLS override (`docker-compose.security.yml`, extended to
`python-worker` too) and runs every registered scenario in all 14
groups, including `security-ui` (the 5 browser specs, each run as a
`playwright test` subprocess against the live stack).

Final tally, one clean invocation, after every fix in this document and
[security-runtime-completion-report.md](security-runtime-completion-report.md)
was applied and reverified:

```
37 PASS, 0 FAIL, 0 BLOCKED, 57 DEFERRED, 0 SKIPPED
```

Highlights among the 32 real, live PASSes: `event-centralization.
worker.registers-with-signed-capability` (worker-1 appears in the
coordinator's identity registry with a real signing_key_id within 60s
of container start); `event-centralization.batch.reaches-central-
journal` (a real `WORKER_REGISTERED` event traverses the full
production path — worker's local queue → signed batch → mTLS gRPC →
`SubmitWorkerSecurityEvents` → coordinator journal → Go HTTP API — and
is queryable within 45s); `event-centralization.recovery.coordinator-
outage-then-delivery` (a real coordinator outage overlapping a worker
restart: no silent event loss); `event-centralization.restart.
coordinator-preserves-journal` (a real `docker compose restart
coordinator` — event count never decreases); `recovery.coordinator-
health.reflects-real-outage` (a real stop/start cycle, status
transitions correctly).

Every DEFERRED scenario carries a specific, code-referenced reason
(e.g. "requires a live CreateRun→StartRun→AcquireTask flow not
configured by this harness invocation — already covered by
coordinator_task_signing_test.cpp...") — never a blanket "not done."
See `scripts/security-validation/groups/*.py` for the full registry;
`python scripts/security-validation/run.py --list` prints it without
running anything.

## Real defects found and fixed by live validation

Every one of these was invisible to unit tests, `go test`, `pytest`,
`ctest`, Vitest, ESLint, or `tsc` — all six were only observable by
actually building the real Docker images and running the real stack.
This is the entire reason this slice's "do not substitute scratchpad-
only validation for an automated runtime test" instruction exists.

1. **`python-worker`'s Docker image never installed the `security`
   extra.** `infra/docker/python-worker.Dockerfile` ran `pip install -e
   ./python`, which only installs `[project.dependencies]` — `pynacl`/
   `cryptography` live under `[project.optional-dependencies].security`
   in `python/pyproject.toml`. The container crashed on startup with
   `ModuleNotFoundError: No module named 'nacl'` before reaching any of
   this slice's new TLS/signing code. A host-based `pytest` run never
   hit this because the host environment already has `pynacl`
   installed. Fixed: `pip install -e "./python[security]"`.
2. **The health-poll-only worker path never called `RegisterWorker`.**
   `_run_health_poll_loop` (the default container mode — no `run_id`
   configured) only ever called `client.health()` in a loop;
   `RegisterWorker` was only reachable via `WorkerService.run()`'s
   training-loop path. Fixed: `_run_health_poll_loop` now registers
   once on startup (`RegisterWorker` is run-agnostic — it ignores its
   `spec`/`now` arguments entirely), logs and continues into the health
   loop even if that registration fails.
3. **`worker_id`/certificate identity mismatch.**
   `docker-compose.security.yml`'s `python-worker` override mounted the
   `certs/dev/workers/worker-1` mTLS certificate (SPIFFE identity
   `spiffe://federated-platform/worker/worker-1`) but never overrode
   `FL_WORKER_WORKER_ID`, leaving the Dockerfile's baked-in default
   `python-worker-1` in place. Every signed RPC was rejected:
   `PERMISSION_DENIED: worker_id 'python-worker-1' does not match the
   authenticated certificate identity`. Fixed: `FL_WORKER_WORKER_ID:
   worker-1` added to the override.
4. **The harness's own `Context.http()` didn't catch a bare
   `TimeoutError`.** Only `urllib.error.URLError` was caught; a
   connect-time timeout is wrapped in `URLError` by `urlopen`, but a
   slow-but-eventually-arriving response body read past the 10s socket
   timeout raises a bare `TimeoutError` that `urlopen` does not wrap.
   `recovery.coordinator-health.reflects-real-outage`'s real,
   correctly-written retry loop crashed with an uncaught `TimeoutError`
   right after a `docker compose start coordinator` (the Go API's own
   handler blocking on its coordinator gRPC dial while the container
   was still coming up) instead of simply treating that one slow poll
   as "not ready yet" and retrying. Fixed: `Context.http()` now catches
   `TimeoutError` the same way it catches `URLError`.
5. **Vitest collected the new Playwright spec files.** Vitest's default
   include glob (`**/*.{test,spec}.*`) also matched `web/e2e/*.spec.ts`,
   which use Playwright's own `test`/`describe` — every `npm run test`
   invocation after the Playwright suite was added failed with
   "Playwright Test did not expect test.describe() to be called here,"
   masking the real (passing) Vitest suite behind unrelated collection
   errors. Fixed: `vitest.config.ts` now excludes `e2e/**`.
6. **No CORS handling existed anywhere in the Go API — the most
   significant finding.** The web app (`http://localhost:3000`) and the
   API (`http://localhost:8080`) are different origins under Docker
   Compose's port-per-service model. Every client-side `fetch()` from
   `web/lib/api.ts`/`web/lib/security-api.ts` is therefore cross-origin,
   and the browser's same-origin policy silently blocked every one of
   them — curl, Go's own `httptest`-based tests, and this harness's
   Python `urllib` calls all bypass CORS entirely and never surfaced
   it. This means the Web Security Center — and plausibly other
   client-side-fetching pages in this application — had never actually
   been confirmed working against the real Dockerized API in an actual
   browser before this slice's Playwright work; prior validation was
   component tests, an API-layer test, and a production build, none of
   which run inside a real browser enforcing CORS. Fixed: a `withCORS`
   middleware in `go/internal/transport/httpapi/server.go` (reflects
   the request's `Origin` header, handles `OPTIONS` preflight requests,
   never sets `Access-Control-Allow-Credentials` since this API is
   Bearer-token authenticated and never cookie-authenticated), with 3
   new Go tests.

A later pass (once the browser suite could finally run cleanly, see
below) found two further real application defects this way: the
coordinator signing-key rotation form's own default values (365-day
expiry, 7-day grace period) both exceeded the server's real enforced
maximums (90 days, 1 day), so a real admin using the form's untouched
defaults always got a real 409; and `event_id` is only unique within
its own source's sequence, not globally across the Go-local +
coordinator-relayed merged response, which caused a React key collision
and visibly wrong row content in the Event Explorer after a filter
change. Both are fixed — full writeup in
[security-runtime-completion-report.md](security-runtime-completion-report.md).

## Browser (`security-ui`) harness group

The 5 Playwright spec files (`web/e2e/security-overview.spec.ts`,
`worker-administration.spec.ts`, `coordinator-keys.spec.ts`,
`event-explorer.spec.ts`, `audit-explorer.spec.ts`) and the harness
group that runs them (`scripts/security-validation/groups/
security_ui.py`) were used directly against the live stack to find and
fix real defect #6 below (CORS) and, in a later pass, two further real
application defects (coordinator signing-key rotation form defaults
exceeding the server's own enforced maximums; `event_id` not being
globally unique across the merged event-journal response, causing a
React key collision) — full detail in
[security-runtime-completion-report.md](security-runtime-completion-report.md).

Standalone (`node .\node_modules\@playwright\test\cli.js test` from
`web/`, full 5-spec suite): **20/20 passed.**

Harness-managed (`python scripts/security-validation/run.py --group
security-ui`, brings up its own stack, runs each spec as a subprocess,
tears down):

```
5 PASS, 0 FAIL, 0 BLOCKED, 0 DEFERRED, 0 SKIPPED
```

## Cross-language golden fixture — real, not tautological

Unchanged this slice — see [security-events.md](security-events.md)'s
"Canonical serialization and payload checksum" section for the
methodology (independently compiled/run in C++, pasted into all three
languages' test fixtures, each language's own encoder reproduces the
identical checksum).

## What this pass did not attempt (stated honestly)

- Live adversarial/tampering scenarios (tampered batch, invalid
  signature, wrong-worker-identity, replay, oversized batch, and the
  signed-message/task equivalents) — DEFERRED, unit-tested only. See
  each scenario's `unsupported_reason` in the registry.
- Worker signing-key rotation/revocation and worker revocation exercised
  live — destructive to the shared harness stack every other scenario
  in the same run depends on.
- Load/performance testing of journal rotation under sustained write
  volume.
- A dedicated RESEARCHER-role browser spec (RESEARCHER is exercised
  live at the HTTP layer by the non-browser harness instead — see
  `event-journal.redaction.role-aware`/`security-api.permission-denial.matrix`).
- `go test -race ./...` locally (platform limitation — see the
  regression table above; CI still runs it).
