# Protobuf Generation

## Status

* **Contract compatibility checking:** implemented and passing, without
  requiring `protoc` at all (`scripts/verify_proto_contracts.py`).
* **Actual code generation:** scripted and documented, but **not run in
  this environment** — `protoc` is not installed on the machine this
  milestone was executed on (`protoc --version` → command not found on
  Windows, `where protoc` → nothing). This is stated plainly rather than
  claimed as passing; see the CI job below for where generation actually
  runs.

## Supported protoc version

Documented (and enforced by the generation scripts' preflight check) as
**libprotoc 25.x or newer**. This is not independently pinned via a
lockfile/container image in this milestone; the CI job below installs
whatever `apt`'s `protobuf-compiler` package provides on `ubuntu-latest`,
which was 25.x at time of writing. A fully reproducible pin (a specific
protoc release binary, hash-checked) is listed as a follow-up in
[known-limitations.md](known-limitations.md).

## Why generated code is not committed

`generated/` is gitignored. Policy: **generated protobuf bindings are
build artifacts, regenerated from `proto/*.proto` on demand, never
committed.** This avoids the two usual failure modes of committing
generated code — (a) generated code silently drifting from its `.proto`
source because someone edited the generated file directly, and (b) merge
conflicts in large generated files that carry no reviewable information.

Because nothing is committed, "staleness" cannot mean "diff the generated
output against a committed copy" — there is nothing committed to diff
against. Instead, "freshness" is enforced two ways:

1. **Contract compatibility check** (`scripts/verify_proto_contracts.py`,
   wired as `make proto-check`) parses every tracked `.proto` file directly
   and asserts specific message/field-number pairs match an expected table
   (see below). This runs with no dependencies (pure Python regex parsing)
   and is part of every `make proto` / `make proto-check` invocation,
   local or CI.
2. **Generation-succeeds check** (CI only, where `protoc` is actually
   installed): regenerate C++/Python/Go bindings from `proto/` into a throwaway
   directory on every CI run and fail if `protoc` errors. Since the source
   of truth (`proto/*.proto`) is what's checked, and generation is
   re-run from that exact source every time, there is no possible drift
   between "what's committed" and "what generates" to detect — the
   generation step running successfully *is* the freshness guarantee.

## Local generation

```bash
# bash / macOS / Linux
scripts/generate_protos.sh [output_dir]     # default: generated/

# Windows PowerShell
scripts/generate_protos.ps1 [-OutDir <dir>]
```

Both scripts:

1. Fail fast with a clear message if `protoc` is not on `PATH` (rather than
   silently no-op-ing).
2. Generate a `FileDescriptorSet` (`descriptors/fl_contracts.pb`, with
   `--include_imports`) — useful for any future language/tool that can
   consume descriptor sets without a dedicated codegen plugin.
3. Generate C++ (`--cpp_out`) and Python (`--python_out`) bindings
   unconditionally.
4. Generate Go bindings (`--go_out`) **only if** `protoc-gen-go` is also on
   `PATH`, with a warning (not a failure) if it's missing — Go bindings
   depend on an extra plugin binary that a `protoc`-only install doesn't
   provide.
5. TypeScript bindings are **not generated**: nothing in the current
   architecture consumes protobuf-over-gRPC from the web dashboard (the
   Next.js app talks to the Go API over REST/JSON — see
   `docs/current-architecture.md`), so generating TS bindings would be
   dead code. This will need revisiting once/if the dashboard talks to
   gRPC directly.

## `make proto` / `make proto-check`

```makefile
proto:
	python scripts/verify_proto_contracts.py
	# generation only runs if protoc is actually available:
	@if command -v protoc >/dev/null 2>&1; then scripts/generate_protos.sh generated; \
	else echo "protoc unavailable; generation skipped after compatibility verification"; fi

proto-check:
	python scripts/verify_proto_contracts.py
```

`make proto-check` never requires `protoc` and always either passes or
fails on a real contract violation — it is safe to run in any environment,
which is why it is the command listed in the release-gate validation list.
`make proto` additionally attempts real generation when possible, and
degrades to a clearly-logged skip (not a silent no-op, not a false
success) when `protoc` is absent, exactly as required.

## Contract compatibility coverage

`scripts/verify_proto_contracts.py` currently asserts field numbers for:

* `experiment/experiment.proto`: `RunConfiguration`
* `worker/worker.proto`: `TensorManifest`, `ClientTask`, `ClientResult`
* `coordinator/coordinator.proto`: `RoundState`, `RunState`
* `privacy/privacy.proto`: `PrivacyLedger`
* `events/events.proto`: `CoordinatorEvent`
* `common/artifact.proto`: `ArtifactReference`

This covers the required serialization surfaces (experiment configuration,
client task, client update metadata, tensor manifest, round state, privacy
ledger, event) named in the release gate. Enum value stability, dtype
mapping, run-state mapping, algorithm mapping, timestamp fields, and
checksum fields are all represented as explicit field-number checks on the
messages above (e.g. `TensorManifest.dtype`, `TensorManifest.checksum`,
`RunState.state`) — there is not yet a *separate* enum-value-number check
(only field numbers are asserted, not the numeric value each enum member
resolves to). Adding an explicit enum-value assertion is a natural
follow-up once the enums stabilize past their current scaffold state.

## CI

See `.github/workflows/ci.yml`, job `protobuf` — installs `protobuf-compiler`
on `ubuntu-latest`, runs `protoc --version`, `make proto-check`, and
`make proto` (real generation, since `protoc` is present there).
