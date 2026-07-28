# Experiment Command Service Design

As of July 28, 2026, the research registry write path uses a
Python-authoritative internal HTTP/JSON command service rather than a
dual-writer file mutation model.

## Decision

The repository already had:

- a durable Python experiment registry with semantic validation,
  immutable specification persistence, idempotent experiment creation,
  bounded synthetic orchestration, and cancellation semantics
- a typed Go public API surface and typed Go read repository

It did not yet have a practical Python-side protobuf/gRPC command stack
for this registry work. To avoid blocking the safe writer boundary on a
new codegen/tooling layer, this implementation chooses bounded internal
HTTP/JSON.

## Request Path

External client
-> Go public HTTP API
-> Go auth, RBAC, request-shape validation, body-size bounds
-> typed internal command client
-> Python internal command HTTP service
-> authoritative Python validation and durable mutation
-> typed command result
-> Go role-specific public response

## Security Properties

- Go never edits research registry files directly.
- Go never invokes Python through a shell.
- Internal requests require:
  - `Authorization: Bearer <secret>`
  - `X-Service-Identity`
- The secret is intended for bounded local/dev validation only.
- The Python service rejects oversized requests.
- The Python service rejects unknown top-level command fields.
- The Go client applies deadlines and preserves context cancellation.

## Contracts

Implemented command types:

- `ValidateExperimentSpecification`
- `CreateExperiment`
- `StartSyntheticExperiment`
- `CancelExperiment`
- `GetCommandStatus`
- `GetWriterHealth`

Every command includes:

- schema version
- command ID
- command type
- request and expiry timestamps
- caller service identity
- actor reference
- permission context
- idempotency key where required
- expected experiment version where relevant
- payload hash
- correlation ID

## Idempotency

Durable command idempotency is persisted under the Python research root
in `commands/idempotency/`. Exact request-hash replays return the
original durable result. Conflicting request reuse returns a conflict.

This layer complements, rather than replaces, the registry's existing
authoritative create-idempotency behavior.

## Health

The Go public runtime-health route now combines:

- Go reader repository health
- Python writer command-service health

If the reader is healthy but the writer is unavailable, the public
health response reports reads available, writes unavailable, and overall
degraded.

## Deferred Work

This pass does not claim:

- production-grade mTLS for the internal command channel
- retry-command exposure
- Docker Compose live validation of the writer service
- runtime-validation harness scenarios for the new writer path
- CI smoke execution of the writer service
- golden cross-language fixture packs checked in as static artifacts

Those remain explicit follow-up items rather than hidden assumptions.
