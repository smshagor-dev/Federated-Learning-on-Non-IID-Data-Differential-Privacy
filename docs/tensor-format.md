# Tensor Format

## Status: implemented and tested (C++ core), scaffolded (wire protocol)

## In-memory representation (`cpp/core/include/fl_core/tensor.hpp`)

```cpp
enum class DType { kFloat32 };

struct TensorDescriptor {
    std::string name;
    std::vector<std::uint64_t> shape;
    DType dtype{DType::kFloat32};
};

class TensorBuffer {
    TensorDescriptor descriptor_;
    std::vector<double> values_;   // always double internally
};
```

Only `DType::kFloat32` is implemented. FP64, FP16, BF16, INT8, and sparse
top-K formats from `plan.md`'s long-term contract are **not** implemented —
they are out of scope for Milestone 2 (see
[known-limitations.md](known-limitations.md)). Internally, values are
always stored as `double` regardless of declared dtype, so accumulation
(sums across many clients) does not lose precision; the declared `dtype`
is what participates in validation and serialization, not the storage
type.

## Validation (`TensorBuffer::validate()`, called on construction and on
every `TensorCollection::insert`/`assign`)

Rejected, in order:

1. Empty tensor name.
2. Empty shape (rank 0 is not supported; every tensor must have at least
   one dimension).
3. Unsupported dtype (currently: anything other than `kFloat32`).
4. `element_count() != values.size()` (covers truncated and oversized
   buffers).
5. Any non-finite value (NaN, +inf, -inf) via `std::isfinite`.

`TensorDescriptor::element_count()` and `byte_length()` use overflow-safe
multiplication (checked against `SIZE_MAX` before each multiply) and reject
a zero dimension outright, so a malicious or corrupt shape cannot silently
wrap around to a small allocation.

`TensorCollection::insert` additionally rejects a duplicate tensor name;
`assign` is the same operation but permits overwriting an existing entry
(used internally when building the aggregate accumulator).

Missing/unexpected tensor detection happens one level up, in
`UpdateValidator` — see [update-validation.md](update-validation.md) — by
comparing each `ClientUpdate.delta` against the `ModelManifest`.

## Wire format used by the CLI compatibility runner and checkpoint files

Both `fl_aggregate_cli` (`cpp/core/tools/aggregate_cli.cpp`) and
`AggregatorCheckpointStore` (`cpp/core/src/checkpoint.cpp`) use the same
plain-text tensor field encoding:

```text
<name>|<dtype-tag>|<shape, dash-joined>|<values, comma-joined>
```

Example: a tensor named `weight` with shape `(2, 3)` and dtype FP32:

```text
weight|f32|2-3|0.1,0.2,0.3,0.4,0.5,0.6
```

A bare manifest descriptor (no values) uses an empty trailing segment:

```text
weight|f32|2-3|
```

This is intentionally not JSON or protobuf: it has no external dependency,
is trivial to hand-construct for tests and CLI debugging, and both
producers/consumers live in this repository. The protobuf contracts under
`proto/worker/worker.proto` (`TensorManifest`) define the intended
production wire format once workers exchange tensors over gRPC — see
[protobuf-generation.md](protobuf-generation.md). The two are not yet
unified; that unification is Milestone 3+ scope (real gRPC worker/tensor
streaming), tracked in [known-limitations.md](known-limitations.md).
