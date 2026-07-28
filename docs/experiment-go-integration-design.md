# Experiment Go Integration Design

## Decision

The authoritative writer for the research registry remains Python in the
current repository state.

Go integrates as a typed, role-aware operational reader over the same
durable storage layout.

## Why

The Python implementation already owns:

- the authoritative experiment specification semantics
- canonical specification hashing
- durable registry layout creation
- immutable snapshot rules
- run/event/metric/artifact persistence semantics
- bounded synthetic orchestration

The repository does not yet have a portable, proven cross-process lock
protocol that safely supports uncontrolled dual writers from Python and
Go against the same per-experiment files on Windows and Linux.

## Writer Policy

Current model:

- Python is the authoritative writer for research registry mutation.
- Go is the authoritative typed operational reader.
- Go may expose validation, listing, detail, journals, artifacts, and
  runtime-health views over Python-authored storage.
- Go does not create a second incompatible storage tree.
- Go does not silently mutate Python-authored immutable snapshots.

Deferred until a future lock/interoperability slice proves safety:

- Go-authored experiment creation into the shared registry
- Go-authored cancellation into the shared registry
- shared multi-writer mutation with optimistic concurrency across both
  languages

## Immediate Go Scope

This slice should add:

- typed Go research models matching the Python JSON contract
- a file-backed Go repository that reads Python-created experiments
- checksum and corruption detection for immutable research artifacts
- role-aware HTTP read APIs
- runtime-health reporting
- cross-language fixtures and parity tests for the read contract

This slice should not claim:

- safe shared multi-writer storage
- production-grade orchestration
- full statistical analysis
- browser research observability

## Operational Boundary

When the Go API encounters:

- missing required immutable files
- checksum mismatch
- invalid JSON
- unsupported schema versions

it must fail closed and report corruption or degraded state through
typed errors and runtime-health output.

## Follow-on Work

Before Go can become a shared writer, the repository still needs:

- a portable lock model both languages implement identically
- explicit lock ownership rules
- durable idempotency compatible across both languages
- parity-tested state-transition logic
- cross-language create/cancel mutation tests against the same storage
