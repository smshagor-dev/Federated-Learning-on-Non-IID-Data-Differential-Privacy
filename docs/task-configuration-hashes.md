# Task Configuration Hashes

**Status: implemented, cross-language-parity-tested (real golden
fixture, C++ ↔ Python), live-validated.**

## Why five hashes plus a payload hash

`SignedCoordinatorTask`'s signature covers the whole task via
`task_payload_hash`, but the five configuration hashes exist
separately for auditability and defense-in-depth: a worker (or an
operator inspecting logs) can tell *which specific aspect* of a task
changed without recomputing everything, and each hash is a natural
place to attach its own domain-separation prefix. See
[canonical-security-serialization.md](canonical-security-serialization.md)
for the shared canonicalization rule (`json.dumps(payload,
sort_keys=True, separators=(",",":"), ensure_ascii=True)` on the
Python side; a hand-written alphabetical-key `std::ostringstream`
encoder on the C++ side).

**Scope discipline**: every field list below is exactly what
`fl.coordinator.v1.ClientTrainingTask` carries on the wire *today* —
this codebase's established rule of never inventing fields nothing
else populates. Where the wire format is narrower than the full
specification's request (e.g. Personalization Configuration), that is
called out explicitly rather than silently narrowed.

## The five hashes

| Hash | Domain prefix | Fields (alphabetical) |
|---|---|---|
| Training Configuration | `FL_PLATFORM_TRAINING_CONFIG_V1\x00` | `algorithm`, `batch_size`, `fedprox_mu`, `learning_rate`, `local_epochs`, `momentum`, `weight_decay` |
| Model Configuration | `FL_PLATFORM_MODEL_CONFIG_V1\x00` | `aggregation_manifest` (nested: `frozen_parameter_names`, `personalized_parameter_names`, `schema_hash`, `shared_parameter_names`), `model_manifest` (ordered array of `{byte_length, checksum, dtype, name, shape}`), `model_version` |
| Dataset Partition | `FL_PLATFORM_DATASET_PARTITION_V1\x00` | `client_id`, `dataset_reference`, `run_id` |
| Privacy Configuration | `FL_PLATFORM_PRIVACY_CONFIG_V1\x00` | `sample_level_dp_active` alone when `false`; when `true`, adds `accountant`, `epsilon_budget`, `max_grad_norm`, `noise_multiplier`, `poisson_sampling`, `sample_budget_policy`, `target_delta` |
| Personalization Configuration | `FL_PLATFORM_PERSONALIZATION_CONFIG_V1\x00` | `frozen_parameter_names`, `personalized_parameter_names` (from `aggregation_manifest` — see scoping note below) |

**Personalization Configuration scoping note**: the fuller
`fl.experiment.v1.PersonalizationConfig` message (mode,
backbone/head parameter *prefixes*, checkpoint retention policy,
local-model-initialization policy, global-to-local sync policy) is set
once at `CreateRun` time and is not restated on `ClientTrainingTask` —
`AggregationManifest`'s personalized/frozen parameter *names* are the
only per-task personalization signal the wire format actually carries.
This is a deliberate scoping decision, not a silent gap.

## Task payload hash

Domain prefix `FL_PLATFORM_COORDINATOR_TASK_PAYLOAD_V1\x00`. Binds
every `ClientTrainingTask` sibling field: `attempt`,
`dataset_partition_hash` (embeds the *hex digest*, not the raw fields),
`lease_expires_at`, `lease_id`, `model_configuration_hash`,
`privacy_configuration_hash`, `round_id`, `task_available`, `task_id`,
`training_configuration_hash`. Embedding the sub-hashes' hex digests
(rather than duplicating their raw fields) keeps this hash's own field
list short while still transitively covering everything the five
configuration hashes do.

## A real cross-language bug this caught

Two independent, real bugs were found only once both sides were
actually run against the same fixed input and their SHA-256 digests
compared (a live Docker build, not a paper argument):

1. **Float-formatting threshold mismatch.** C++'s
   `std::to_chars(double)` with no explicit format picks scientific
   vs. fixed notation at a different threshold than Python's
   `repr()`/`json.dumps()` — e.g. `to_chars(0.0001)` (no explicit
   format) produced `"1e-04"`, while Python's `json.dumps(0.0001)`
   produces `"0.0001"`. This broke `training_configuration_hash`
   (`weight_decay=0.0001` in the golden fixture). Fixed by explicitly
   choosing `std::chars_format::fixed` for `1e-4 <= |value| < 1e16`
   (value `0` counts as fixed) and `std::chars_format::scientific`
   otherwise — confirmed by direct comparison against real
   `json.dumps()` output at several boundary values (`0.0001`,
   `0.00001`, `1e16`, `9999999999999998.0`), not assumed.
   [canonical-security-serialization.md](canonical-security-serialization.md)'s
   existing caveat ("only verified correct for Unix-timestamp
   magnitudes") already flagged this exact risk for any future hash
   using non-timestamp doubles — this is that future case.
2. **Key-ordering bug**: the hand-written C++
   `privacy_configuration_hash` encoder wrote `sample_level_dp_active`
   *first* (matching field-declaration order), but Python's
   `json.dumps(..., sort_keys=True)` always alphabetizes — and
   `"sample_level_dp_active"` alphabetically sorts *after*
   `"sample_budget_policy"` and *before* `"target_delta"`, not first.
   Fixed by rewriting the C++ encoder in strict alphabetical order.

Both are documented here rather than silently fixed and forgotten,
per this project's "real bugs found by actually running this" ethos.

## How cross-language parity was actually proven

Same methodology as every prior golden fixture in this project (see
[canonical-security-serialization.md](canonical-security-serialization.md)):
`python/tests/test_coordinator_task_signing.py::GoldenFixtureTests`
computes six SHA-256 hex digests from a fixed
`TaskConfigurationFields` input; the identical six literal hex strings
are pasted into
`cpp/coordinator/tests/coordinator_task_signing_test.cpp` and asserted
against the C++ encoder's output for an identical fixed
`ClientTrainingTask`. Both sides independently compute their own
output; if they had disagreed on a single byte, the SHA-256 digests
would not match. They do, after the two fixes above.
