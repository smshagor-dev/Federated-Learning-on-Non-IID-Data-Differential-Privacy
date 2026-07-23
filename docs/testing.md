# Testing

This document is the index of what runs where. For narrative results from
one specific run, see [milestone-1-validation.md](milestone-1-validation.md)
and [milestone-2-report.md](milestone-2-report.md).

## Python

* Runner: **pytest** (`pytest.ini`: `testpaths = tests, python/tests`,
  `--ignore=tests/baseline` to avoid double-collecting the
  `tests/<name>.py` shim + `tests/baseline/<name>.py` real-module pair —
  see the comment at the top of any `tests/test_*.py` shim file).
* Legacy prototype unittest-style tests (`tests/baseline/test_legacy_baselines.py`)
  and the new C++/Python compatibility tests
  (`tests/baseline/test_python_cpp_compat.py`) both run fine under plain
  `unittest` too (`python -m unittest discover`), since they're written as
  `unittest.TestCase` subclasses — pytest just discovers and runs them.
* Lint: **Ruff** (`ruff.toml`), scoped to `python/src`, `python/tests`,
  `tests/`, with `tests/baseline` excluded from lint (that directory holds
  test fixtures ported from/matching the legacy prototype's looser style,
  intentionally not held to the same line-length/import-order rules as new
  package code).
* Types: **mypy** `strict = true` (`python/pyproject.toml`), scoped to
  `python/src` only (test code and the legacy prototype are not
  type-checked).
* New in this milestone: `tests/baseline/test_python_cpp_compat.py` (12
  tests, `unittest.TestCase`-based, `setUp` skips the whole class if
  `fl_aggregate_cli` hasn't been built) and
  `python/src/fl_platform/compat/` (the adapter itself, covered by mypy
  strict and Ruff like the rest of `python/src`).

## C++

* Runner: **CTest**, four binaries registered as tests:
  * `fl_core_smoke` — build-info + one FedAvg + one SCAFFOLD sanity check +
    one state-machine transition (this predates this milestone).
  * `fl_aggregator_golden` — prints deterministic numeric output for every
    algorithm/weighting combination; consumed both directly by CTest
    (exit code) and by `tests/baseline/test_cpp_golden_parity.py` (parses
    stdout and asserts specific values).
  * `fl_validation_tests` — 8 explicit rejection-path assertions (see
    [update-validation.md](update-validation.md)).
  * `fl_checkpoint_tests` — round trip, tamper/truncation/schema-version
    rejection, and file-based atomic-write tests (new this milestone; see
    [checkpoint-format.md](checkpoint-format.md)).
* Debug and Release configurations are both configured, built, and tested
  independently (`build/cpp-debug`, `build/cpp-release`).
* `clang-format` / `clang-tidy` / AddressSanitizer / UndefinedBehaviorSanitizer
  targets exist in the Makefile (`cpp-format-check`, `cpp-tidy`, `cpp-asan`,
  `cpp-ubsan`) but require `clang-format`/`clang-tidy`/a Clang toolchain,
  none of which are installed on the Windows machine this milestone was
  executed on (MSVC-only). They run in CI on `ubuntu-latest` instead — see
  `.github/workflows/ci.yml`. ThreadSanitizer is intentionally **not**
  wired up anywhere: it is not supported under MSVC and is not required to
  run locally by the task scope; it is documented as a Linux-only,
  not-yet-added CI job in [known-limitations.md](known-limitations.md).

## Go

* `go fmt ./...`, `go vet ./...`, `go build ./...`, `go test ./...` all run
  from the `go/` module directory (there is no root-level `go.mod`; running
  these from the repo root fails with "directory prefix . does not contain
  main module").
* `go test -race ./...` requires cgo (`CGO_ENABLED=1`) and a C compiler.
  Neither gcc nor clang is installed in this Windows environment
  (`where gcc`/`where clang` both fail), so race tests **do not run
  locally here** — this is a real, environment-specific limitation, not
  something silently skipped without comment. CI runs race tests on
  `ubuntu-latest`, which has gcc preinstalled.
* Test files: `go/internal/application/services_test.go`,
  `go/internal/transport/httpapi/server_test.go`,
  `go/internal/bootstrap/persistence_test.go`,
  `go/internal/observability/telemetry_test.go`. Coverage includes run
  lifecycle transitions, an explicit invalid-transition test, a new
  duplicate-state-changing-request test (queuing an already-queued run),
  authentication success/failure, role-based authorization (viewer denied
  project creation), the health endpoint, the audit-events endpoint, and
  persistence reload-from-disk.

## Web

* `npm run lint` — ESLint via an explicit flat config
  (`web/eslint.config.mjs`: `@eslint/js` recommended + `typescript-eslint`
  recommended + `@next/eslint-plugin-next` recommended + React Hooks
  rules), invoked directly (`node ./node_modules/eslint/bin/eslint.js .`)
  so it never hits Next.js's interactive "no ESLint config found" prompt.
* `npm run typecheck` — `tsc --noEmit --incremental false`.
* `npm run test` — Vitest (`web/vitest.config.ts`, jsdom environment,
  `@testing-library/react`), two test files:
  `web/tests/metric-card.test.tsx` (component test) and
  `web/tests/api.test.ts` (API helper test) — satisfying the "at least one
  meaningful dashboard component test" and "at least one API helper test"
  requirements.
* `npm run build` — `next build` (production build + Next's own type/lint
  pass).
* Playwright E2E: **not added.** The dashboard currently reads from a
  scaffolded/demo-data-backed API client (per
  `docs/current-architecture.md`), not a stable, running backend; adding
  E2E tests against a system that isn't wired end-to-end would either mock
  so much that the test proves little, or be flaky against a backend this
  milestone does not stand up. Deferred until Milestone 3+ connects the Go
  API to a real persistence/coordinator backend, per the task's own
  instruction to defer E2E when "a minimal stable application flow and
  backend mock are [not] already available."

## Contract / compatibility tests

* `scripts/verify_proto_contracts.py` (`make proto-check`) — field-number
  stability, no `protoc` required.
* `tests/baseline/test_cpp_golden_parity.py` — C++ binary output vs.
  independently-derived-in-Python expected values, for every
  algorithm/weighting combination in `golden.cpp`.
* `tests/baseline/test_python_cpp_compat.py` — full Python↔C++ round trip
  through `fl_aggregate_cli`, PyTorch tensors, compared against the legacy
  `federated.server.Server` (FedAvg/FedProx/SCAFFOLD) or an independently
  written FedOpt reference (FedAdagrad/FedAdam/FedYogi). See
  [milestone-2-report.md](milestone-2-report.md) for the full case list and
  results.
