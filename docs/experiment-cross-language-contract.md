# Experiment Cross-Language Contract

## Scope

This document records the authoritative JSON contract shared between the
Python research registry and the typed Go read layer.

The current authority is the Python implementation in:

- `python/src/fl_platform/research/specification.py`
- `python/src/fl_platform/research/registry.py`

## Canonical JSON Profile

- Encoding: UTF-8
- Object keys: sorted for canonical hashing when specified
- Whitespace: none in canonical hash bytes
- Numbers: JSON finite numbers only
- NaN and Infinity: forbidden
- Enum representation: exact stable strings
- Optional fields: included with explicit `null` when the Python typed
  dataclass field is present and set to `None` in the canonical payload
- List ordering: preserved exactly as stored
- Unknown fields: rejected by the typed Go contract reader for core
  specification records
- Hash algorithm: SHA-256

## Command Payload Hash Contract

For internal research commands, `request_payload_hash` covers only the
command `payload` object, not the full transport envelope.

As of Tuesday, July 28, 2026, the authoritative cross-language rule is:

- Go hashes the final JSON-normalized payload representation that it
  actually sends.
- Python verifies the same payload subtree from the received request
  body before dispatch.
- Integer-valued floating-point fields must hash according to their
  normalized JSON wire form, not their pre-serialization in-memory
  representation.

See [experiment-command-contract.md](./experiment-command-contract.md)
and [research-command-hash-mismatch-audit.md](./research-command-hash-mismatch-audit.md).

## Authoritative Experiment Specification Fields

Top-level `ExperimentSpecification` JSON fields:

- `schema_version`
- `experiment_id`
- `experiment_name`
- `research_question`
- `dataset`
- `partition`
- `model`
- `algorithm`
- `privacy`
- `secure_aggregation`
- `adaptive_clipping`
- `runtime`
- `seeds`
- `determinism_level`
- `tags`
- `creation_timestamp`
- `specification_hash`

The canonical hash is computed over the full payload with:

- enum values rendered as strings
- `specification_hash` forced to `""`
- keys sorted
- compact JSON separators

## Core Enum Values

### Partition strategy

- `iid`
- `dirichlet`
- `pathological`
- `quantity_skew`

### Privacy mode

- `none`
- `sample_level_dp`
- `user_level_dp`
- `hybrid_dp`

### Secure aggregation provider

- `none`
- `SECAGG_NO_DROPOUT_EXPERIMENTAL`

### Adaptive clipping mode

- `disabled`
- `enabled`

### Determinism level

- `STRICT_CPU`
- `BEST_EFFORT_ACCELERATOR`
- `PERFORMANCE`

### Experiment state

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

### Run state

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

### Inclusion status

- `INCLUDED`
- `EXCLUDED`

## Durable Storage Contract

Per-experiment required files:

- `specification.json`
- `specification.sha256`
- `registry.json`
- `state.json`
- `compatibility.json`
- `environment.json`
- `artifacts.json`
- `events.jsonl`

Per-seed required files:

- `run.json`
- `state.json`
- `environment.json`
- `artifacts.json`
- `metrics.jsonl`
- `failures.jsonl`

## Journal Contract

Research event and metric journals are JSON Lines with:

- one JSON object per line
- `record_checksum`
- SHA-256 checksum over the canonical object with `record_checksum` set
  to `""`

Malformed or checksum-invalid lines are corruption signals and must not
be silently treated as valid records.

## Redaction Categories

Public-safe:

- display name
- research question
- dataset ID
- model ID
- algorithm ID
- privacy mode
- secure aggregation enabled/provider
- aggregate-safe metrics

Restricted operational detail:

- created actor
- record versions
- environment manifest hashes
- degraded reasons
- retry lineage
- failure details

Forbidden:

- dataset samples
- clear client updates
- individual update norms
- adaptive indicators
- clipping factors
- privacy noise
- private keys
- shared secrets
- tokens
- absolute host paths
