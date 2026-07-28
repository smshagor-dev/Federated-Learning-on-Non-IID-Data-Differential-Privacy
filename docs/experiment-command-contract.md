# Experiment Command Contract

## Scope

This document defines the current authoritative request-payload hashing
contract for the research command path shared by Go and Python.

As of Tuesday, July 28, 2026:

- Go builds and sends the internal command envelope
- Python verifies payload integrity and performs durable mutation

## Hash Boundary

`request_payload_hash` covers only the command-specific `payload` field.

It does not cover:

- `schema_version`
- `command_id`
- `command_type`
- `request_timestamp`
- `expiry_timestamp`
- `caller_service`
- `actor`
- `permission_context`
- `idempotency_key`
- `expected_experiment_version`
- `correlation_id`
- authentication headers

This preserves the existing design:

- transport and caller identity are protected separately by internal
  authentication
- the request-payload hash protects the typed business payload only
- the hash field is not self-referential

## Authoritative Canonicalization Rule

Both implementations must hash the same canonical JSON payload bytes
after final JSON normalization.

Current rule:

1. Construct the final command `payload`.
2. Normalize it through JSON serialization.
3. Decode the normalized JSON into a generic value representation that
   preserves JSON number spellings for hashing.
4. Canonicalize with:
   - UTF-8
   - no BOM
   - recursively sorted object keys
   - compact separators
   - stable snake_case field names
   - preserved list order
   - explicit `null` when present in the JSON payload
   - finite JSON numbers only
5. Compute SHA-256 and encode it as lowercase hex.

## Command Payloads

### `ValidateExperimentSpecification`

Hashed payload fields:

- `specification`
- `client_specification_hash`

### `CreateExperiment`

Hashed payload fields:

- `specification`
- `client_specification_hash`

The public API request also carries idempotency and correlation metadata,
but those stay outside the current payload-hash boundary.

### `StartSyntheticExperiment`

Hashed payload fields:

- `experiment_id`
- `execution_mode`

### `CancelExperiment`

Hashed payload fields:

- `experiment_id`
- `reason`

### `GetCommandStatus`

Current command type exists in the protocol but is not part of the
public research runtime harness coverage in this slice.

### `GetWriterHealth`

Hashed payload fields:

- none; payload is the empty object `{}`.

## Specification Hash Is Separate

The immutable experiment `specification_hash` and the command
`request_payload_hash` are distinct:

- `specification_hash` identifies a normalized experiment specification
- `request_payload_hash` protects the command payload that carries the
  specification and any command-specific context

The command hash must never be treated as a substitute for the
specification hash.
