# Research Evaluation Existing Capabilities Audit

This audit summarizes what already existed in the repository before the
current experiment-specification foundation work, and what remains
unfinished for a full benchmarking and reproducibility platform.

## Existing Capabilities

### Python

- dataset registry entries already tracked dataset metadata, versions, and
  checksums
- partitioning already supported `iid`, `dirichlet`, and `pathological`
  strategies with deterministic seeds
- the descriptor registry already exposed a privacy compatibility matrix
  for sample-level and user-level DP
- worker task execution already existed through
  `python/src/fl_platform/worker/task_runner.py`

### Go

- dataset registration, lifecycle transitions, and partition manifest
  storage already existed
- privacy compatibility mirroring already existed in
  `go/internal/privacy/compatibility.go`
- experiment records already existed, but only as lightly structured
  metadata with a generic `config map[string]any`

### Web

- the experiment builder already allowed editing experiment config payloads
- the dataset registry console already exposed dataset and partition
  management surfaces

## Gaps Before This Slice

The main missing layer was a typed and validated research specification
that could connect:

- dataset identity
- non-IID partition identity
- algorithm selection
- privacy compatibility
- secure aggregation constraints
- adaptive clipping constraints
- determinism and seed declarations

The repository also did not yet expose a first-class `quantity_skew`
partition implementation in Python or recognize it in the Go dataset
service.

## What This Slice Added

- `python/src/fl_platform/research/specification.py` for structured
  experiment specifications and canonical hashing
- validation that enforces the current repository safety boundary,
  including the explicit exclusion of dropout recovery
- richer partition manifests with heterogeneity metrics and dataset
  provenance
- Python support for deterministic `quantity_skew` partition generation
- Go dataset-service acceptance for `quantity_skew` manifests

## Still Pending

The repository still does not provide a complete end-to-end experimental
benchmarking platform. Important pending items include:

- typed Go experiment APIs instead of opaque config maps
- durable run/result schemas for repeated research trials
- bounded benchmark orchestration across datasets, algorithms, and privacy
  regimes
- statistical testing, confidence intervals, and report generation
- UI support for the typed experiment specification
- reproducibility workflows that tie one specification hash to concrete
  repeated executions and archived outputs
