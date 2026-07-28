# Experiment Registry Report

## Scope

This report captures the current research-registry status as of Tuesday,
July 28, 2026, with emphasis on the live Docker write path through:

- `POST /api/v1/research/experiments/validate`
- `POST /api/v1/research/experiments`
- `POST /api/v1/research/experiments/{experimentId}/start`
- `POST /api/v1/research/experiments/{experimentId}/cancel`

It reflects only fresh local evidence gathered in the current working
tree.

## Verified Model

- Python remains the only authoritative durable writer.
- Go remains the public API, RBAC, typed gateway, and read layer.
- Public clients do not call the Python writer directly.
- Go and Python share the control-plane volume, but only Python mutates
  research-registry state.

## Root Cause Fixed On July 28, 2026

The live write path had been failing with
`request_payload_hash_mismatch`.

The exact divergence was not an authentication failure and not a writer
availability issue. The failure was in the request-payload hash
boundary:

- Go hashed the typed in-memory payload before final JSON wire
  normalization.
- Integer-valued `float64` fields such as `1.0` were canonicalized by
  Go as `1.0` during hashing.
- The same fields were emitted by `encoding/json` on the wire as `1`.
- Python correctly recomputed the payload hash from the received JSON
  payload subtree and therefore rejected the request fail closed.

The fix in [go/internal/research/command_client.go](../go/internal/research/command_client.go)
normalizes the payload through `json.Marshal` and `json.Decoder.UseNumber`
before hashing, so Go hashes the same semantic JSON form that Python
verifies.

See:

- [research-command-hash-mismatch-audit.md](./research-command-hash-mismatch-audit.md)
- [experiment-command-contract.md](./experiment-command-contract.md)
- [experiment-cross-language-contract.md](./experiment-cross-language-contract.md)

## Fresh Validation Evidence

Targeted checks:

- `python scripts/check_project_terminology.py`
- `python -m pytest python/tests/test_research_command_service.py python/tests/test_experiment_registry.py python/tests/test_research_specification.py -q`
- `python -m ruff check python/src/fl_platform/research python/tests/test_research_command_service.py python/tests/test_experiment_registry.py python/tests/test_research_specification.py scripts/security-validation/groups/research_registry.py scripts/security-validation/run.py`
- `python -m ruff format --check python/src/fl_platform/research python/tests/test_research_command_service.py python/tests/test_experiment_registry.py python/tests/test_research_specification.py scripts/security-validation/groups/research_registry.py scripts/security-validation/run.py`
- `go test ./internal/research ./internal/transport/httpapi -count=1`
- `go test ./...`
- `go vet ./...`
- `go build ./...`
- `docker compose -f infra/compose/docker-compose.dev.yml config`
- `python scripts/security-validation/run.py --group research-registry --no-compose --keep-stack --output-dir tmp/research-registry-runtime`

Fresh results:

- targeted Python research tests: `27 passed`
- focused Go research tests: passed
- full Go module tests: passed
- Go vet: passed
- Go build: passed
- terminology validation: passed
- Compose config: passed
- research-registry runtime group: `3 PASS, 0 FAIL, 0 BLOCKED, 0 DEFERRED, 0 SKIPPED`

## Fresh Live Docker Evidence

Fresh runtime evidence from Tuesday, July 28, 2026:

- `compose-api-1` healthy on `http://localhost:8080`
- `compose-research-writer-1` healthy on the internal Compose network
- public validate call succeeded with HTTP `200`
- public create call succeeded with HTTP `201`
- exact create replay returned `idempotent_replay: true`
- the writer persisted a durable idempotency record at:
  - `/var/control-plane/research/commands/idempotency/CreateExperiment/create-live-1.json`
- the writer persisted experiment files for `expresearch001`, including:
  - `specification.json`
  - `specification.sha256`
  - `registry.json`
  - `state.json`
  - `events.jsonl`
- per-seed run records were present for:
  - `seed-1`
  - `seed-2`
  - `seed-3`

Sanitized live values:

- authoritative specification hash:
  - `32dba015e4753e0e0134211d9f4f2f85308934e87b3e9d55323fece5c451f341`
- persisted create idempotency request payload hash:
  - `e8d25da297bd7eaa9a3fd4eded308f38a700f5b4a117da08c98bd6d329bcbe33`

## What Is Closed

- typed Python specification and registry model
- durable Python command service
- typed Go read and mutation gateway
- live validate through the public API
- live durable create through the public API
- exact create replay idempotency
- live start and cancel coverage through the registered runtime group
- viewer/service mutation denial through the registered runtime group
- combined runtime-health projection including writer status

## What Remains Partial

This category remains `PARTIAL` overall because the repository still
does not have fresh July 28, 2026 evidence in this slice for every
broader closure gate requested in the active objective, including:

- response-loss replay
- separately documented restart-persistence replay after the current fix
- separately documented writer-unavailable regression after the current fix
- separately documented corruption fail-closed regression after the current fix
- a fixed cross-language golden byte fixture set for the full runtime path
- production-grade internal transport authentication beyond the current
  bounded shared-secret development model

## Readiness Classification

Current honest classification:

- Typed
- Durable
- Python authoritative
- Go API accessible
- Authenticated command driven
- Integrity checked
- Idempotent
- Docker validated
- Runtime-harness validated
- Synthetic multi-seed capable
- Statistical analysis pending
- Web dashboard pending
- Not production-scale orchestration
