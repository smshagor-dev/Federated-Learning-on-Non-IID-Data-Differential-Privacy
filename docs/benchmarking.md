# Benchmarking

## Status

**Implemented and run.** This is a working benchmark harness with real,
locally-measured results (see below). It is not Google Benchmark (see
"Why not Google Benchmark" below), and it has not been run on any machine
other than the one used to write this document, so treat the numbers as
illustrative of relative cost between algorithms/sizes, not as
absolute/portable performance claims.

## Harness

Source: `cpp/benchmarks/aggregation_benchmark.cpp`, built as the
`fl_aggregation_benchmark` CMake target.

```bash
cmake -S cpp -B build/cpp-release -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp-release --config Release
build/cpp-release/Release/fl_aggregation_benchmark.exe          # quick mode (default)
build/cpp-release/Release/fl_aggregation_benchmark.exe --full    # full grid, slower
```

`make cpp-benchmark` builds Release and writes quick-mode output to
`benchmarks/results/aggregation_benchmark_latest.csv`.

### Why not Google Benchmark

This repository has no C++ package manager configured (no vcpkg/conan
lockfile). Pulling Google Benchmark via CMake `FetchContent` would add a
multi-minute from-source build to every benchmark invocation and a network
dependency to CI. Given the scope of this milestone, a small
`std::chrono`-based harness was used instead: warm-up run, N timed
repetitions, median/mean reported, same methodology Google Benchmark uses
internally. The CMake target is isolated (`fl_aggregation_benchmark`) so it
can be swapped for a real `benchmark::State`-based harness later without
changing how it's invoked from `make cpp-benchmark`.

### Coverage

* **Client counts:** 10, 100, 500 (quick mode runs all three only for the
  `tiny` model size; `cnn_sized` and `resnet18_sized_approx` are capped at
  100 clients in quick mode to keep default runs under ~1 minute. `--full`
  removes the cap and also sweeps 500 clients for every size.)
* **Model sizes:**
  * `tiny` — 2 tensors, 512 total parameters.
  * `cnn_sized` — 3 tensors, 100,000 total parameters (a small-CNN-scale
    parameter count).
  * `resnet18_sized_approx` — 4 tensors, 500,000 total parameters. This is
    a **scaled-down approximation** of ResNet-18 (~11M real parameters),
    chosen deliberately: at 500 clients, an 11M-parameter model would
    require roughly 500 × 11M × 8 bytes ≈ 44 GB of resident memory just for
    client deltas, which is not something to allocate by default in a
    benchmark run on a shared development machine. Real ResNet-18-scale
    benchmarking is listed as a known limitation below.
* **Algorithms:** FedAvg, FedAdagrad, FedAdam, FedYogi (FedProx and
  SCAFFOLD share FedAvg's/each other's aggregation cost profile and are not
  separately benchmarked).
* **Weighting:** uniform, sample-count.
* **Measured per configuration:** median and mean aggregation latency
  (5 timed repetitions after 1 warm-up), updates/second, total client-delta
  bytes processed, checkpoint serialization latency, and checkpoint
  checksum-validation (deserialize) latency.

Peak memory is not directly measured (no cross-platform RSS sampling was
added); `bytes_processed` is reported instead as a proxy, since it is exact
and platform-independent.

## Environment (this run)

```text
hardware_concurrency=12
build_type=release_or_ndebug
compiler=msvc_1944
OS: Windows 11 Pro (build 26200)
```

Run with default (quick) mode; wall-clock time for the full quick-mode
sweep was approximately 55 seconds on this machine.

## Results (this run, quick mode)

Full raw CSV: `benchmarks/results/aggregation_benchmark_latest.csv`
(git-ignored — machine-specific numbers are not committed; regenerate with
`make cpp-benchmark`). Condensed highlights, sample-count weighting:

| model_size | clients | algorithm  | median_ms | updates/sec | checkpoint_serialize_ms |
|---|---|---|---|---|---|
| tiny | 10 | fedavg | 0.042 | 238,663 | 0.005 |
| tiny | 500 | fedavg | 1.92 | 260,092 | 0.007 |
| tiny | 500 | fedyogi | 2.05 | 244,260 | 0.605 |
| cnn_sized | 100 | fedavg | 73.1 | 1,367 | 0.008 |
| cnn_sized | 100 | fedadam | 68.2 | 1,467 | 119.5 |
| resnet18_sized_approx | 100 | fedavg | 470.4 | 213 | 0.008 |
| resnet18_sized_approx | 100 | fedyogi | 464.7 | 215 | 560.8 |

## Interpretation guidelines

* **Aggregation latency scales roughly linearly with `clients × parameters`.**
  This matches the implementation: every weighting strategy and every
  FedOpt moment update is a single pass over each client's tensors.
* **FedOpt algorithms (Adagrad/Adam/Yogi) cost noticeably more than FedAvg**
  at the same size, because each client update still costs one weighted-average
  pass, plus the server does 2-4 additional whole-model tensor passes for
  the moment updates. This is consistent with the formulas in
  [fedopt.md](fedopt.md), not evidence of an inefficiency to fix.
* **Checkpoint serialize/checksum-validate latency depends on optimizer
  state size, not client count** — it is proportional to the number of
  model parameters in `first_moment`/`second_moment`, and is why FedAvg
  (which carries no persisted moments) checkpoints in microseconds while
  FedOpt checkpoints take tens to hundreds of milliseconds at
  `resnet18_sized_approx` scale. If checkpoint latency becomes a bottleneck
  at real model scale, that is the first place to optimize (e.g. binary
  tensor encoding instead of the current human-readable text format — see
  [checkpoint-format.md](checkpoint-format.md)).
* **Do not extrapolate these numbers to unmeasured configurations** (real
  ResNet-18 size, 500 clients at `cnn_sized`/`resnet18_sized_approx`, GPU
  execution, or a different machine). Run `--full` and/or a real
  ResNet-18-sized manifest and re-measure before making any performance
  claim about those cases.

## Known limitations

* No real Google Benchmark integration (see rationale above).
* No cross-platform peak memory sampling.
* `resnet18_sized_approx` is 500K parameters, not the real ~11M; a true
  ResNet-18-scale benchmark is deferred (see
  [known-limitations.md](known-limitations.md)).
* No Python-legacy-vs-C++ throughput comparison is included: the legacy
  prototype's `Server.aggregate` and the C++ core are not directly
  comparable black boxes (different memory layout, no equivalent CLI
  entrypoint for repeated timed calls) and fabricating a "N× faster" number
  from a mismatched comparison would violate the "do not invent benchmark
  results" requirement. The Python↔C++ *parity* tests
  (`tests/baseline/test_python_cpp_compat.py`) validate correctness, not
  relative speed.
