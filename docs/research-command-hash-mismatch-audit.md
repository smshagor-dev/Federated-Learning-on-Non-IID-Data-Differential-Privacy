# Research Command Hash Mismatch Audit

## Scope

This note records the live command-integrity failure that blocked the
research-writer write path before it was fixed on Tuesday, July 28,
2026.

Affected public endpoints:

- `POST /api/v1/research/experiments/validate`
- `POST /api/v1/research/experiments`

Internal command path:

- Go public API
- Go internal research command client
- Python `POST /internal/research/commands`
- Python command-service integrity check

## Fresh Failure Shape Before The Fix

Before the fix:

- validate returned HTTP `200` with `valid: false` and
  `reason_code: request_payload_hash_mismatch`
- create returned HTTP `400` with
  `reason_code: request_payload_hash_mismatch`
- the writer persisted no durable create result for the failing public
  call
- no experiment directory was created for the failing create attempt

The failure happened before registry mutation. It was fail closed.

## Exact Divergence

The mismatch was caused by a representation split between:

- the Go value hashed in memory, and
- the JSON payload bytes Python actually received and verified.

Concrete divergence class:

- Go hashed `float64` values before final JSON wire normalization.
- Example shape:
  - Go hash input could render `noise_multiplier` as `1.0`
  - the actual `encoding/json` request body rendered the same value as
    `1`
- Python recomputed the hash from the received JSON payload subtree,
  preserving the wire number spelling through `Decimal`
  normalization.

So the two sides were hashing the same semantic payload fields, but not
the same canonical byte representation.

## Persistence Boundary

The failure boundary was the Python command-service integrity gate:

- schema accepted
- envelope accepted
- internal caller authentication accepted
- payload-hash verification failed
- command dispatch did not begin
- experiment mutation did not begin

This behavior was correct fail-closed behavior.

## Fix

The fix was applied in
[go/internal/research/command_client.go](../go/internal/research/command_client.go):

- normalize the payload through `json.Marshal`
- decode it back with `json.Decoder.UseNumber`
- canonicalize that normalized JSON form
- hash the normalized canonical bytes

This makes Go hash the same payload representation that Python verifies
from the received request body.

## Fresh Post-Fix Evidence

After the fix on Tuesday, July 28, 2026:

- validate succeeded with HTTP `200`
- create succeeded with HTTP `201`
- create exact replay returned `idempotent_replay: true`
- the writer persisted an idempotency record at:
  - `/var/control-plane/research/commands/idempotency/CreateExperiment/create-live-1.json`
- the persisted create request payload hash was:
  - `e8d25da297bd7eaa9a3fd4eded308f38a700f5b4a117da08c98bd6d329bcbe33`

## Safe Conclusion

The failure was not caused by:

- bad caller authentication
- bad command routing
- Python mutation logic
- writer unavailability
- public RBAC

It was a canonicalization mismatch at the request-payload hash boundary,
and the fix preserved integrity verification rather than bypassing it.
