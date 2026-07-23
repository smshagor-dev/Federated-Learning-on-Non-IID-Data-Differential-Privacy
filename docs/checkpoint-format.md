# Aggregator/Optimizer Checkpoint Format

## Status: implemented and tested, including crash-recovery-shaped tests
(corrupt/truncated file rejection, atomic overwrite). Not "production
ready" in the sense of having been exercised against a real crash during
an actual long-running coordinator process — see caveats below.

Implementation: `cpp/core/include/fl_core/checkpoint.hpp` /
`cpp/core/src/checkpoint.cpp` (`AggregatorCheckpoint`,
`AggregatorCheckpointStore`). This is deliberately separate from
`fl_core::coordinator::CheckpointStore` (in `coordinator.hpp`), which
persists run-state-machine/round metadata and is Milestone 3 coordinator
scaffolding; `AggregatorCheckpointStore` persists exactly what an
aggregator/optimizer needs to resume deterministically, independent of any
coordinator.

## What is persisted

| Field | Type | Notes |
|---|---|---|
| `schema_version` | `uint32` | Currently `1`; load rejects any other value explicitly |
| `algorithm` | `AggregationAlgorithm` | fedavg / fedprox / scaffold / fedadagrad / fedadam / fedyogi |
| `weighting` | `WeightingStrategyType` | sample_count / uniform / capped_sample_count / normalized_bounded |
| `model_version` | `string` | |
| `manifest_checksum` | `string` | see below |
| `optimizer_state.step` | `uint64` | FedOpt step counter (0 for algorithms that don't use it) |
| `optimizer_state.first_moment` | `TensorCollection` | empty for FedAvg/FedProx/SCAFFOLD |
| `optimizer_state.second_moment` | `TensorCollection` | empty for FedAvg/FedProx/SCAFFOLD |
| `scaffold_control` | `TensorCollection` | global control variate; empty for non-SCAFFOLD algorithms |

`compute_manifest_checksum(manifest)` hashes `model_id`, `model_version`,
and every tensor's `(name, dtype, shape)` (not values) with FNV-1a, so a
checkpoint can be checked against "is this the manifest I think it is"
before being applied, without re-validating every tensor descriptor by
hand. It intentionally does **not** include tensor values — it identifies
the *shape* of the model, not a specific set of weights.

## Wire format

Same line-based `key=value` / tensor-field encoding as
[tensor-format.md](tensor-format.md), terminated by a checksum line:

```text
schema_version=1
algorithm=fedadam
weighting=sample_count
model_version=v3
manifest_checksum=<16 hex chars>
optimizer_step=7
first_moment_count=1
first_moment_tensor=weight|f32|2|0.5,-0.25
second_moment_count=1
second_moment_tensor=weight|f32|2|0.1,0.2
scaffold_control_count=0
checksum=<16 hex chars, FNV-1a over everything above this line>
```

The checksum covers the entire body (everything before the final
`checksum=` line) with FNV-1a 64-bit, encoded as lowercase hex. This is a
detection checksum, not a cryptographic one — it exists to catch
truncation and accidental corruption (disk errors, an interrupted write
that a filesystem didn't fully flush), not to authenticate the file
against tampering by an adversary with disk access.

## Atomic write

`AggregatorCheckpointStore::save_to_file`:

1. Serializes the checkpoint to a string.
2. Writes the full string to `<path>.tmp`, flushes, and checks the stream
   state.
3. Calls `std::filesystem::rename(<path>.tmp, <path>)`.
4. If the rename fails (this happens on Windows when `<path>` already
   exists — POSIX `rename()` replaces the destination atomically, but
   `std::filesystem::rename` does not guarantee that cross-platform), falls
   back to `remove(<path>)` then `rename(<path>.tmp, <path>)`.

**Caveat, stated plainly:** step 4's fallback is not perfectly atomic on
Windows — there is a small window between `remove` and `rename` where
neither the old nor the new checkpoint exists on disk. On POSIX (Linux/macOS)
`rename()` alone replaces the destination atomically and step 4 never
triggers. This is documented here rather than silently assumed away; a
fully crash-safe Windows story would need a platform-specific
`ReplaceFile`/`MoveFileEx` call, which was not implemented in this
milestone.

## Load / corruption handling

`AggregatorCheckpointStore::load_from_file` reads the whole file and calls
`deserialize`, which:

1. Locates the trailing `\nchecksum=` line; if missing, throws
   `CheckpointCorruptionError` (covers a completely truncated file with no
   checksum footer at all).
2. Recomputes FNV-1a over everything before that line and compares against
   the stored value; mismatch throws `CheckpointCorruptionError` (covers
   partial writes, bit rot, and manual tampering).
3. Parses every `key=value` field; a malformed line, an unknown
   algorithm/weighting string, or a missing `schema_version` all throw
   `CheckpointCorruptionError`.
4. Rejects any `schema_version` other than `AggregatorCheckpoint::kSchemaVersion` (1)
   explicitly, with a message naming the unsupported version, rather than
   attempting to interpret unknown fields.
5. Reconstructs each `TensorCollection` and cross-checks the declared
   `_count` against the number of tensor lines actually present, throwing
   on mismatch (covers truncation that happens to still leave a valid
   checksum-line-shaped tail, which the checksum check alone would not
   otherwise catch since it only verifies byte-for-byte integrity of
   whatever bytes are physically present).

No corrupt or truncated checkpoint is ever returned as a *partial* result —
every failure mode above raises before constructing an `AggregatorCheckpoint`.

## Tests (`cpp/core/tests/checkpoint.cpp`, CTest `fl_checkpoint_tests`)

* In-memory serialize → deserialize round trip preserves every field.
* Manifest checksum differs for two manifests that differ only in
  `model_version`.
* A single flipped byte in the serialized payload is rejected.
* A payload truncated to half its length is rejected.
* A `schema_version` field rewritten to `99` is rejected (schema check,
  independent of whether the checksum happens to also fail after the
  rewrite).
* File-based: `save_to_file` leaves no `.tmp` file behind, the file exists
  and round-trips, and a **second** `save_to_file` call correctly replaces
  an existing checkpoint (exercises the overwrite/replace path, not just
  first-write).
* A checkpoint file manually corrupted on disk (overwritten with a
  non-parseable payload) is rejected by `load_from_file`, not silently
  accepted.

## Explicitly not verified

"Crash recovery" here means: a checkpoint written before an aggregator
process was killed can be re-read afterward, correctly, or is rejected if
incomplete. It does **not** mean this has been tested against an actual
coordinator process that crashes mid-round in a running system — no such
coordinator/round-lifecycle integration exists yet in this milestone (see
[known-limitations.md](known-limitations.md)). Calling this
"production-ready crash recovery" would overstate what has actually been
exercised.
