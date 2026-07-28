# Experiment Registry Design

This document defines the durable operational foundation that sits
between the typed Python experiment specification and future
statistical-analysis or dashboard work.

## Goals

The registry must provide:

- immutable experiment specification snapshots
- durable experiment and per-seed run state
- append-safe metric and event journals
- failed-run preservation
- retry lineage
- safe cancellation
- restart recovery
- bounded multi-seed orchestration
- typed Go research APIs over the durable store

This design does not attempt to implement:

- statistical significance analysis
- publication reporting
- the browser research dashboard
- distributed production-scale scheduling
- dropout recovery or threshold cryptography

## Coverage Map

| Topic | Authoritative section |
|---|---|
| Safe experiment identifiers | Identifier Policy |
| Immutable specification snapshots | Immutable Snapshot |
| On-disk layout | Storage Layout |
| Experiment lifecycle | Experiment Lifecycle |
| Run lifecycle | Run Lifecycle |
| Journals | Event and Metric Journals |
| Environment and artifact manifests | Environment and Artifact Manifests |
| Recovery and corruption policy | Recovery and Corruption Policy |
| API and RBAC shape | Typed Go API Surface |

## Identifier Policy

Two identifiers are distinct:

- `experiment_id`: operational identifier chosen or requested by the
  caller, validated for filesystem safety and immutability
- `specification_hash`: immutable content identity derived from the
  canonical experiment specification

### Experiment ID rules

- ASCII lowercase letters, digits, `_`, and `-` only
- 3 to 64 characters
- must start with a letter
- no path separators
- no `..`
- no control characters
- case-normalized to lowercase at validation time
- immutable once created

The registry never makes trust decisions from a display name.

## Immutable Snapshot

Experiment creation performs:

1. validate the Python `ExperimentSpecification`
2. recompute the canonical `specification_hash`
3. reject a supplied mismatched hash
4. persist the canonical specification JSON snapshot
5. persist a detached SHA-256 checksum file for the snapshot
6. persist a registry record that copies the immutable identity fields
7. persist dataset and partition provenance bindings

After creation, the following are immutable:

- specification payload
- specification hash
- dataset identity
- dataset checksum
- partition-manifest hash
- declared seed set

Any change requires a new experiment.

## Storage Layout

Research state lives under the control-plane data root:

```text
<control-plane-data-dir>/
  research/
    experiments/
      <experiment_id>/
        specification.json
        specification.sha256
        registry.json
        state.json
        compatibility.json
        environment.json
        artifacts.json
        idempotency.json
        events.jsonl
        runs/
          <seed-id>/
            run.json
            state.json
            environment.json
            artifacts.json
            summary.json
            metrics.jsonl
            failures.jsonl
        locks/
```

Key rules:

- every path component after `experiments/` is validated, never
  user-concatenated blindly
- immutable files are created once and never silently overwritten
- mutable state files use atomic compare-and-replace semantics
- journals are append-only
- runtime artifacts are stored outside source-controlled fixtures

## Experiment Registry Record

The primary experiment summary record is versioned and contains only
safe, bounded metadata. Suggested fields:

- schema version
- experiment ID
- display name
- research question
- specification hash
- dataset ID, version, checksum
- partition-manifest hash
- model ID
- algorithm ID
- privacy mode
- secure aggregation provider
- adaptive clipping enabled
- declared seed count
- current state
- successful, failed, canceled, blocked run counts
- record version
- created/updated timestamps
- created actor reference
- degraded flag and degraded reason
- environment manifest hash
- artifact manifest hash

## Experiment Lifecycle

Allowed experiment states:

- `CREATED`
- `VALIDATED`
- `PREPARING`
- `READY`
- `RUNNING`
- `CANCEL_REQUESTED`
- `CANCELED`
- `COMPLETED`
- `COMPLETED_WITH_PARTIAL_RUNS`
- `FAILED`
- `BLOCKED`
- `CORRUPTED`

Every transition records:

- prior state
- next state
- timestamp
- actor or system source
- reason
- transition ID
- expected record version

Invalid transitions are rejected.

## Run Lifecycle

Each declared seed has a durable run record and zero or more attempts.
Initial required run states:

- `CREATED`
- `PREPARING`
- `RUNNING`
- `EVALUATING`
- `COMPLETED`
- `FAILED`
- `CANCELED`
- `BLOCKED`
- `LOST`
- `CORRUPTED`

Runs are never deleted when an attempt fails. A retry creates a new
attempt record linked to the failed one.

## Concurrency Model

Every mutable experiment and run state carries a monotonic
`record_version`.

Mutations must:

- load the current record
- verify the expected version
- write a new version atomically
- reject stale updates with a conflict result

This is intentionally stronger than the current list-style repositories.

## Event and Metric Journals

Research event and metric persistence follows the existing security
journal pattern:

- append-only JSONL
- typed records
- per-record checksum
- cursor pagination
- corruption accounting

Metrics are intentionally bounded by a registry of known metric names
and scopes. Hybrid privacy stores sample-level and user-level epsilon
separately. No combined epsilon record is permitted.

## Environment and Artifact Manifests

Environment manifests capture reproducibility metadata only:

- OS / architecture
- tool and dependency versions
- determinism mode
- secure aggregation provider
- git revision
- dirty-tree flag
- sanitized diff-summary hash when dirty

Artifact manifests register only safe artifacts with:

- relative safe path
- type
- byte size
- MIME type
- SHA-256 checksum
- public-safe flag
- sanitization status

## Recovery and Corruption Policy

Startup recovery scans:

- all experiments with active states
- all runs with active states
- event and metric journals for parse/checksum failures

Recovery rules:

- stale active runs become `LOST` or `FAILED` according to policy
- experiments with missing required immutable files become `CORRUPTED`
- corrupted records are never silently rewritten in place
- cancellation stops future unstarted seeds but preserves completed and
  failed history

## Bounded Orchestration

The first orchestration layer is deliberately synthetic and bounded:

- create one run record per declared seed
- execute through a modular adapter interface
- preserve one record per attempt
- stop launching new seeds after cancellation
- compute aggregate experiment status from per-seed outcomes

This is an operational foundation, not a scientific benchmark engine.

## Typed Go API Surface

Introduce a separate typed research API rather than mutating the
existing legacy `/api/v1/experiments` contract.

Proposed route family:

- `GET /api/v1/research/experiments`
- `POST /api/v1/research/experiments`
- `GET /api/v1/research/experiments/{id}`
- `POST /api/v1/research/experiments/{id}/validate`
- `POST /api/v1/research/experiments/{id}/start`
- `POST /api/v1/research/experiments/{id}/cancel`
- `POST /api/v1/research/experiments/{id}/retry`
- `GET /api/v1/research/experiments/{id}/runs`
- `GET /api/v1/research/experiments/{id}/events`
- `GET /api/v1/research/experiments/{id}/metrics`
- `GET /api/v1/research/experiments/{id}/artifacts`
- `GET /api/v1/research/runtime/health`

The Go API owns:

- typed request/response contracts
- permission checks
- role-aware serialization
- HTTP error mapping

The durable store remains the source of truth.

## RBAC Shape

Research APIs should follow the security API model:

- permission constants, not scattered role checks
- explicit role-aware views
- no post-serialization field deletion

Suggested permissions:

- `research.experiments.create`
- `research.experiments.validate`
- `research.experiments.list`
- `research.experiments.read`
- `research.experiments.runs.read`
- `research.experiments.metrics.read`
- `research.experiments.events.read`
- `research.experiments.artifacts.read`
- `research.experiments.start`
- `research.experiments.cancel`
- `research.experiments.retry`
- `research.runtime.health.read`

## Implementation Boundary

The most practical first implementation sequence is:

1. Python durable registry and bounded orchestration foundation
2. cross-language canonical fixtures for specification/hash/state enums
3. typed Go models and services that expose the durable registry safely
4. typed HTTP routes, RBAC, and health

That sequence matches the repository's current strengths:

- the Python side already owns the typed experiment specification
- the Go side already owns durable control-plane APIs and RBAC patterns
