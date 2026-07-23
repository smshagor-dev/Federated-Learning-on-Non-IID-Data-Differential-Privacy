# Milestone 2 Report: C++ Aggregation Core and Python Compatibility

## Scope reminder

This milestone covers the C++ aggregation core (tensor/update validation,
weighting, FedAvg/FedProx/SCAFFOLD/FedOpt, checkpointing) and Python↔C++
golden compatibility. Coordinator, scheduling, gRPC services, and every
Milestone 4+ algorithm (FedSAM, Ditto, Per-FedAvg, Opacus, secure
aggregation, Ray/Flower, async aggregation) are explicitly out of scope and
untouched beyond what already compiled.

## Starting state (before this pass)

Most of Milestone 2's C++ core was **already implemented, uncommitted, in
the working tree** when this pass began: `TensorBuffer`/`TensorCollection`
with full validation, `Aggregator`/`AggregatorRegistry`/`WeightingStrategy`/
`UpdateValidator`, working FedAvg/FedProx/SCAFFOLD/FedAdagrad/FedAdam/FedYogi
aggregation, and CTest coverage (`smoke.cpp`, `golden.cpp`, a new
`validation.cpp`). This was verified by reading the source and running the
existing test suite, not assumed from the (pre-existing, uncommitted)
`PROJECT_STATUS_REPORT.md`, which itself substantially understates what was
already present in the working tree at session start (see the final
report's "Differences from PROJECT_STATUS_REPORT.md" section).

What was genuinely missing or non-compliant with the task's explicit
requirements, and what this pass added, is below.

## What this pass changed

### 1. `ServerOptimizer` refactor (C8 compliance)

**Before:** `FedOptAggregator::aggregate()` had one function with
`if (algorithm_ == kFedAdagrad) ... else if (kFedAdam) ... else ...`
branches for the three FedOpt variants — exactly the "one large
conditional function" the task explicitly prohibits.

**After:** `ServerOptimizer` is now a proper abstraction (as required by
the task's list of mandatory C++ types), with `FedAdagradOptimizer`,
`FedAdamOptimizer`, `FedYogiOptimizer` as independent classes and a
`make_server_optimizer()` factory. `FedOptAggregator` now only owns shared
cohort validation and weighting, delegating the per-tensor formula to the
selected optimizer.

**Verified behavior-preserving:** the full CTest suite and the
independently-computed `tests/baseline/test_cpp_golden_parity.py` values
pass identically before and after the refactor (same 6-decimal-place
tolerances, same expected numbers) — see [fedopt.md](fedopt.md).

### 2. Aggregator/optimizer checkpointing (C10 — was entirely missing)

**Before:** `coordinator.hpp`'s `CheckpointStore` only serialized run-state
fields (`run_id`, `run_state`, `round_id`, `model_version`, `algorithm`,
`selected_clients`, `optimizer_step` — just the integer step, not the
actual moment tensors) to an in-memory string. There was no file I/O, no
atomic write, no checksum, no schema version, and critically **no
first/second moment tensor persistence at all** — a FedOpt run could not
actually be resumed from this.

**After:** a new, separate module (`cpp/core/include/fl_core/checkpoint.hpp`,
`cpp/core/src/checkpoint.cpp`) implements `AggregatorCheckpoint` /
`AggregatorCheckpointStore`, persisting schema version, algorithm,
weighting strategy, model version, a manifest checksum, the full optimizer
state (step + first/second moment tensors), and the SCAFFOLD global
control variate — exactly the C10 requirement list. Atomic write
(temp file + rename, with a documented Windows caveat), FNV-1a checksum
validation, schema-version rejection, and truncated/corrupt-file rejection
are all implemented and tested. See [checkpoint-format.md](checkpoint-format.md).

### 3. CLI compatibility runner (Work Package D — was entirely missing)

**Before:** `tests/baseline/test_cpp_golden_parity.py` invoked the
`fl_aggregator_golden` *test* binary directly and parsed its hardcoded
scalar output — useful as a golden test, but not a general-purpose way for
Python to drive the C++ aggregation core with arbitrary tensors.

**After:** `cpp/core/tools/aggregate_cli.cpp` (`fl_aggregate_cli` binary) —
a small line-based stdin/stdout protocol (documented in
[tensor-format.md](tensor-format.md)) that accepts a full
manifest/cohort/options/previous-state request and returns the aggregation
result or an explicit error. `python/src/fl_platform/compat/cpp_bridge.py`
builds requests from real PyTorch tensors, invokes the CLI, and
reconstructs PyTorch state dicts from the response.

### 4. Golden Python↔C++ compatibility tests (Work Package D)

`tests/baseline/test_python_cpp_compat.py` — 12 tests, all passing,
covering every case the task lists:

| Case | Compared against |
|---|---|
| FedAvg, equal sample counts | legacy `federated.server.Server` |
| FedAvg, unequal sample counts | legacy `Server` |
| Uniform weighting | hand-computed expected delta (Server has no uniform mode) |
| Capped weighting | hand-computed expected delta |
| FedProx server behavior | legacy `Server` (fedprox algorithm) |
| SCAFFOLD model update | legacy `Server` (scaffold algorithm) |
| SCAFFOLD global control-variate update | legacy `Server.c_global` |
| FedAdagrad, first + later step | independently-written FedOpt reference |
| FedAdam, first + later step | independently-written FedOpt reference |
| FedYogi, first + later step | independently-written FedOpt reference |
| Invalid tensor (NaN) rejected | expects `CppAggregationError` |
| Stale model version rejected | expects `CppAggregationError` |
| Duplicate client rejected | expects `CppAggregationError` |

FP32 tensors and multiple tensor shapes are exercised throughout (every
FedAvg/FedProx/SCAFFOLD test uses a real `nn.Linear(2, 2)` — a `weight`
matrix and a `bias` vector, two distinct shapes — rather than a single
scalar).

Two real cross-language bugs were found and fixed while building this (not
hypothetical — both reproduced against the actual compiled binary before
being fixed):

1. `split()` in the CLI parser dropped a trailing empty field (`"a|b|"` →
   `{"a","b"}` instead of `{"a","b",""}`), which broke parsing a bare
   manifest tensor descriptor (no values segment).
2. Manifest tensor descriptors were being run through the same
   `TensorBuffer`-constructing parser as value-bearing tensors, which
   throws immediately if `element_count() != values.size()` — a
   descriptor-only entry (empty values) always failed validation. Fixed by
   giving descriptor-only fields their own non-validating parse path.

### 5. Benchmark harness (Work Package E — was entirely missing)

`cpp/benchmarks/aggregation_benchmark.cpp` (`fl_aggregation_benchmark`
target) — see [benchmarking.md](benchmarking.md) for full methodology and
this session's actual results (10/100/500 clients × 3 model sizes ×
4 algorithms × 2 weightings, quick mode capped to keep default runs under
a minute).

### 6. CI hardening (Work Package F)

`.github/workflows/ci.yml` (renamed from `milestone1-foundation.yml`,
which had exactly two jobs: a Python `unittest` run and a bare C++ smoke
build) now has 11 jobs covering Debug/Release C++ builds, clang-format,
clang-tidy, ASan/UBSan, a benchmark build+run, Python tooling (pytest,
Ruff, mypy) plus the new C++/Python compatibility tests, Go (fmt/vet/build/test
+ race, which runs on `ubuntu-latest` where cgo is available), web
(lint/typecheck/test/build), protobuf (install `protoc`, contract check,
generation), and infra (`docker compose config` + best-effort image
builds).

### 7. Documentation

New: `docs/cpp-aggregation-architecture.md`, `docs/tensor-format.md`,
`docs/update-validation.md`, `docs/fedopt.md`, `docs/scaffold-state.md`,
`docs/checkpoint-format.md`, `docs/protobuf-generation.md`,
`docs/benchmarking.md`, `docs/testing.md`, `docs/known-limitations.md`,
`docs/milestone-1-validation.md`, this file.

## Algorithms — implemented / tested status

| Algorithm | Server aggregation | Tested against legacy/reference | Notes |
|---|---|---|---|
| FedAvg | Implemented | Yes (legacy `Server`) | |
| FedProx | Implemented (= FedAvg server-side) | Yes (legacy `Server`) | Client proximal term is Python-side, unaffected |
| SCAFFOLD | Implemented | Yes (legacy `Server`) | Per-client control-variate storage deferred — see limitations |
| FedAdagrad | Implemented | Yes (independent reference) | |
| FedAdam | Implemented | Yes (independent reference) | |
| FedYogi | Implemented | Yes (independent reference) | Second-moment can theoretically go negative under adversarial deltas — documented, not silently clamped |

## Not implemented (by explicit instruction)

FedSAM, Ditto, Per-FedAvg, Opacus training, secure aggregation
cryptography, Ray/Flower execution, asynchronous aggregation, the Go
database layer, and live dashboard integration are unchanged scaffolds, per
the task's explicit boundary.

## Release-gate status (Milestone 2 items)

See the final report's "Milestone 1 / 2 release-gate status" section for
the consolidated pass/fail/blocked table across every command actually
run.
