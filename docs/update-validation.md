# Client Update Validation

## Status: implemented and tested

Implementation: `UpdateValidator::validate_cohort` and
`validate_manifest_against_update` in `cpp/core/src/aggregation.cpp`.
Every `Aggregator::aggregate()` call runs full cohort validation before
touching any tensor math — there is no code path that aggregates an
unvalidated cohort.

## What is validated

Per update, against the `ModelManifest` and `AggregationOptions`:

| Field | Rule |
|---|---|
| `run_id` | Must equal `options.run_id` (when the context specifies one) |
| `round_id` | Must equal `options.round_id` (when the context specifies one) |
| `client_id` | Must be non-empty |
| `update_id` | Must be non-empty |
| `nonce` | Must be non-empty |
| `base_model_version` | Must equal `manifest.model_version` exactly (both stale *and* future versions are rejected — there is no "close enough" version match) |
| `algorithm` | Must equal `options.algorithm` |
| `sample_count` | Must be positive (zero and negative — the field is unsigned, so "negative" is caught at the point of use, e.g. protobuf/CLI parsing — are rejected) |
| tensor set | Every tensor in `manifest.tensors` must be present exactly once in `update.delta`; the delta's tensor count must equal the manifest's tensor count (this catches both missing and unexpected/extra tensors) |
| tensor shape/dtype | Each delta tensor's shape and dtype must match its manifest descriptor |
| tensor values | Each tensor is validated by `TensorBuffer::validate()` (see [tensor-format.md](tensor-format.md)) — this is where NaN/Inf/truncated-buffer rejection happens |
| `control_delta` | When the algorithm requires it (SCAFFOLD), every manifest tensor must also have a corresponding, valid `control_delta` entry |

Across the whole cohort, `validate_cohort` additionally rejects:

* Duplicate `client_id` within the cohort.
* Duplicate `update_id` within the cohort.
* Duplicate `nonce` within the cohort.
* An empty cohort (zero updates).
* An empty/malformed `ModelManifest` (`model_id`/`model_version` must be
  non-empty).

All rejections raise `std::invalid_argument` with a specific message (never
a generic/opaque failure), and no partial aggregation occurs — validation
runs to completion (or throws) before any tensor accumulation starts.

## Explicitly not implemented in Milestone 2

* **Wrong worker_id / worker health checks** — `worker_id` is carried on
  `ClientUpdate` and round-tripped through the CLI protocol and
  checkpoints, but nothing currently validates it (no worker registry
  exists yet; that is Milestone 3's `WorkerMetadata`/coordinator scope).
* **Signature/envelope verification** — `python/src/fl_platform/security/envelope.py`
  has a scaffold for signed envelopes and nonce replay guarding, but the
  C++ aggregation core does not call into it; update authenticity is out
  of scope until Milestone 7 per `plan.md`.
* **Incompatible-algorithm-family checks beyond exact match** — e.g. no
  attempt to reject a FedAvg update against a SCAFFOLD-configured
  aggregation with a friendlier error than "algorithm does not match
  context"; the check exists, the message is generic.

## Test coverage

* `cpp/core/tests/validation.cpp` — 8 explicit rejection paths (duplicate
  client, duplicate update id, duplicate nonce, stale model version, zero
  sample count, unexpected tensor, non-finite tensor value, invalid
  contribution cap), run as CTest `fl_validation_tests`.
* `tests/baseline/test_python_cpp_compat.py::RejectionParityTests` —
  invalid tensor (NaN), stale model version, and duplicate client id,
  verified through the full Python → CLI → C++ round trip, not just the
  C++ unit test binary directly.
