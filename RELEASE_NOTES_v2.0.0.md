# Federated Learning Platform v2.0.0

**Release:** v2.0.0  
**Status:** Release-ready  
**Prepared:** 2026-08-24  
**Python package:** `fl-platform==2.0.0`  
**Python requirement:** `>=3.11`  
**Validated main baseline:** `339c1d113cee2e32e6f3240bf75a8f43bc9234f0`

## Overview

Version **2.0.0** is a major platform release that turns the repository from a collection of federated-learning components into a substantially more complete, reproducible, privacy-aware, secure, and restart-capable federated-learning execution platform.

This release combines two clearly separated execution identities:

- **Root simulator / local runtime** for reproducible single-machine federated experiments, benchmark matrices, dataset partitioning, evaluation, checkpointing, and research validation.
- **Distributed platform** built around the C++ coordinator, Python workers, Go control plane, protobuf/gRPC contracts, durable execution state, worker/task lifecycle management, security controls, and secure-aggregation runtime.

The release focuses on executable behavior and validated claims. Unsupported combinations remain fail-closed rather than being silently downgraded or advertised as implemented.

---

## Highlights

- Unified local and distributed execution lifecycle under the Go control plane.
- Real benchmark matrix runner with repeated-seed statistical analysis.
- Expanded datasets: MNIST, FashionMNIST, CIFAR-10, and CIFAR-100.
- IID, Dirichlet, pathological class-skew, and quantity-skew partitioning.
- Realized non-IID heterogeneity measurement and exact partition fingerprints.
- FedAvg, FedProx, SCAFFOLD foundations plus FedSAM, Ditto, and first-order Per-FedAvg expansion.
- Shared-backbone/local-head personalization architecture.
- Persistent personalized model storage, model registry, and dataset registry.
- Global and per-client evaluation with fairness/tail metrics.
- Sample-level, user-level, and hybrid privacy infrastructure with stronger accounting and fail-closed compatibility rules.
- Target-epsilon calibration and same-adjacency RDP composition hardening.
- Durable local checkpoint, pause, restart, and resume lifecycle.
- Restart-durable distributed round deadlines, retry budgets, and full-cohort-first settlement behavior.
- Secure aggregation session, masked-update, dropout, deadline, abort, and restart-recovery hardening.
- Durable execution event observability and automatic reconciliation.
- Signed distributed partition references and worker-side partition parity.
- mTLS/PKI, signed task/result paths, replay protection, security journals, key/trust handling, and secret scanning.
- Full v2 release CI matrix validated green.

---

# What’s New in v2.0.0

## 1. Unified Execution Platform

v2.0.0 introduces a canonical execution lifecycle for both local and distributed runs.

### Added

- Production Go control-plane execution API under `/api/v1/executions`.
- Local execution backend that launches the existing Python root runtime rather than duplicating training logic.
- Distributed execution backend backed by the coordinator/worker runtime.
- Canonical lifecycle states and persisted execution metadata.
- Durable local execution records under the control-plane data directory.
- Authenticated execution API coverage.
- Optimistic durable state transitions and execution-event persistence.
- Startup execution reconciliation after control-plane restart.
- Runtime-safe automatic reconciliation for stable executions.
- Periodic reconciliation in the Go API process, with configurable `FL_EXECUTION_RECONCILE_INTERVAL`.
- Automatic propagation of backend completion, round progress, model version, and worker-count updates without requiring clients to manually refresh execution state.

### Reliability behavior

- Persisted `CANCELED` and `FAILED` states remain authoritative even if stale backend artifacts exist.
- Unrecoverable local in-flight executions fail closed instead of remaining as ghost `RUNNING` records.
- Transitional lifecycle operations are protected from background reconciliation overwrites.

---

## 2. Federated Algorithm Expansion

The platform keeps the established FedAvg/FedProx/SCAFFOLD training foundation and adds real algorithm-expansion implementations.

### FedAvg

- Existing weighted federated averaging path retained and validated.
- Used as the baseline for benchmark comparisons and multiple compatibility tests.

### FedProx

- Existing proximal local-training implementation retained.
- Supported by benchmark and privacy-compatible paths where configured.

### SCAFFOLD

- Existing control-variate training path retained.
- Runtime checkpointing now preserves SCAFFOLD control-variate state.
- DP-enabled SCAFFOLD remains intentionally fail-closed where its privacy release/control-variate semantics are not fully proven.

### FedSAM

- Added real two-pass Sharpness-Aware Minimization local training.
- Perturb → second forward/backward pass → restore → optimizer update flow.
- C++ coordinator recognizes FedSAM while reusing the weighted aggregation core.
- Dedicated validation and benchmark coverage added.

### Ditto

- Added real dual-model training.
- Maintains a global model path plus a client-personalized model regularized against the global reference.
- Supports warm/cold personalized-model starts.
- Personalized checkpoints are stored separately and are never aggregated into the global model.

### Per-FedAvg

- Added first-order Per-FedAvg implementation.
- Deterministic support/query split.
- Inner adaptation on a copied model.
- First-order meta-gradient computation without differentiating through the full inner loop.
- Small-client fallback behavior.

### Aggregation manifest enforcement

- Added explicit aggregation manifests to define which tensors are globally aggregated.
- Personalized/frozen tensors are kept separate from canonical aggregated tensor manifests.
- Coordinator rejects incompatible tensor submissions instead of silently accepting them.
- Unknown algorithm wire values now fail instead of silently defaulting to FedAvg.

---

## 3. Personalization Architecture

### Added

- Shared-backbone/local-head personalization model architecture.
- Parameter-prefix based shared/personalized tensor classification.
- Support for existing `GroupNormCNN` and personalizable bridge models.
- Schema hashing and model description utilities.
- Shared and personalized state-dict extraction utilities.

### Personalized model store

- Filesystem-backed persistent personalized model storage.
- Atomic writes.
- SHA-256 checksums.
- Ownership validation.
- Schema validation.
- `torch.load(..., weights_only=True)` restore path.
- Path-traversal protection for identifier-derived paths.
- Bounded LRU cache.

### Coordinator personalization support

- Personalization metric persistence in coordinator checkpoints.
- `GetPersonalizationSummary` gRPC support.
- Global-vs-personalized result summaries.

---

## 4. Model Registry and Dataset Registry

### Model Registry

Added Python and Go model registries with a shared lifecycle model:

`DRAFT -> VALIDATED -> ACTIVE -> DEPRECATED -> ARCHIVED`

Capabilities include:

- Model name/version tracking.
- Schema-hash-gated validation.
- Persistent registry storage.
- Go API integration.
- Web registry UI.

### Dataset Registry

Added Python and Go dataset registries.

- Python owns real dataset access and real partition computation.
- Go stores dataset/partition metadata without moving raw training data into the control plane.
- Dataset partition manifests retain strategy and client-level allocation metadata.
- Registry APIs and web UI are available for platform workflows.

---

## 5. Dataset Expansion

The executable root dataset set now includes:

- **MNIST**
- **FashionMNIST**
- **CIFAR-10**
- **CIFAR-100**

The desktop configuration UI was updated to expose the expanded dataset and partition choices, while advanced partition parameters remain available through YAML/CLI configuration.

---

## 6. Non-IID Partitioning and Distributed Partition Parity

### Root runtime partition strategies

v2.0.0 supports real deterministic partition generation for:

- `iid`
- `dirichlet`
- `pathological`
- `quantity_skew`

### Reproducibility metadata

Each benchmark/root execution can preserve:

- Exact per-client sample indices.
- Per-client label histograms.
- SHA-256 partition manifests/fingerprints.
- Realized heterogeneity metrics.
- Effective runtime configuration.

### Distributed parity

Distributed workers now understand signed/versioned canonical partition references and reconstruct deterministic local shards from the accepted task contract.

The worker partition reference supports:

- IID
- Dirichlet label skew
- Pathological class restriction
- Quantity skew
- Explicit partition seed and related parameters

The coordinator still does not transport raw training samples. Workers reconstruct their own deterministic shard semantics locally after the task reference has passed the existing acceptance/security pipeline.

Legacy IID worker behavior is preserved for legacy synthetic references.

---

## 7. Realized Heterogeneity Measurement

Added machine-readable realized non-IID measurements rather than relying only on the configuration that generated a partition.

Metrics include:

- Client count.
- Total samples.
- Number of labels.
- Mean client sample count.
- Sample-count standard deviation.
- Quantity coefficient of variation.
- Minimum/maximum client sample counts.
- Mean/minimum normalized label entropy.
- Mean/maximum Jensen-Shannon divergence from the global label distribution.
- Mean/minimum class coverage.
- Mean effective label count.
- SHA-256 fingerprint of the realized partition histograms.

This makes it possible to archive what partition was actually executed, not only the nominal Dirichlet alpha or other generation parameters.

---

## 8. Benchmark Matrix and Scientific Reproducibility

### New benchmark runner

Added `scripts/run_benchmark_matrix.py` to execute the actual root runtime in a fresh process for each benchmark cell.

The matrix supports:

- Multiple datasets.
- Multiple algorithms.
- Multiple partition conditions.
- Non-private and private target-epsilon conditions.
- Multiple deterministic seeds.
- Resume behavior.
- Dry-run mode.
- Partial/smoke execution.
- Isolated result directories.

### Benchmark outputs

The benchmark pipeline generates machine-readable and tabular artifacts including:

- Plan metadata.
- Status metadata.
- Observations.
- Summaries.
- Pairwise algorithm comparisons.
- JSON outputs.
- CSV outputs.
- Commit/runtime/specification provenance.

### Exact matched-seed comparison

Matched algorithm comparisons require exact partition parity for the same seed. If the partition hash differs, the comparison fails instead of comparing non-equivalent client allocations.

### Statistical analysis

Added deterministic statistical primitives:

- Percentile-bootstrap confidence intervals.
- Paired matched-seed comparisons.
- Mean paired difference.
- Sample standard deviation of paired differences.
- Cohen’s `dz` effect size.
- Win rate.
- Exact paired sign-flip tests for small sample sets.
- Deterministic Monte Carlo paired sign-flip testing for larger sets.
- Holm-Bonferroni multiple-comparison correction.
- Default minimum of five unique replicates/seeds for normal benchmark analysis.

### Provenance

Benchmark observations preserve fields such as:

- Benchmark ID.
- Dataset ID.
- Partition ID/hash.
- Algorithm ID.
- Privacy target.
- Seed.
- Metric name/value.
- Runtime identity.
- Commit SHA.
- Specification hash.

---

## 9. Evaluation and Fairness Metrics

### Final-model evaluation

- Root runtime persists the actual final global model once after the final communication round.
- Empty final Poisson cohorts are handled without losing the final global state.
- Intermediate benchmark rounds are not polluted with unnecessary final-model checkpoint writes.
- Held-out evaluation runs against the actual final checkpoint.

### Deterministic held-out client allocation

- Preserves per-label proportions derived from each training partition.
- Assigns every held-out sample exactly once.
- Avoids duplicate held-out assignments.
- Deterministically repairs integer-allocation cases that would otherwise leave a client empty.

### Client and fairness metrics

The evaluation/reporting path includes metrics such as:

- Global accuracy.
- Mean client accuracy.
- P10 client accuracy.
- Worst-client accuracy.
- Client accuracy dispersion.
- Jain fairness index.
- Client-loss tail metrics.
- Global-vs-personalized accuracy where personalization is available.
- Fairness gap.
- Worst/best client summaries.

Missing, invalid, zero-sample, or unavailable personalized results are handled explicitly instead of being silently dropped.

---

## 10. Differential Privacy Hardening

v2.0.0 substantially hardens privacy configuration, accounting, compatibility, and reproducibility.

### Target privacy calibration

- Added target client-level privacy budget support for the default root experiment path.
- Gaussian noise multiplier can be recalibrated after round/sample-rate/runtime overrides so the claimed target epsilon does not silently drift.
- Explicit manual noise overrides clear target-epsilon calibration claims when appropriate.

### Accounting correctness

- Added same-adjacency RDP composition utilities.
- Mixed/incompatible adjacency composition is rejected.
- Separate ledgers remain useful for traceability but do not incorrectly imply that same-adjacency releases avoid composition.
- Effective privacy configuration is archived after runtime overrides/calibration.

### Sample-level DP

- Opacus-backed sample-level DP training path retained and expanded.
- Per-task/sample privacy records and accounting infrastructure.
- Budget enforcement tests and integration paths.
- Secure-randomness capability checks.

### User-level DP

- User-level clipping/accounting and privacy records.
- Partial-cohort sensitivity calculation is tied to the actual accepted cohort where relevant.
- Restart/recovery privacy state and ledger behavior are validated.

### Hybrid DP

- Sample-level and user-level mechanisms remain distinguishable because they protect different neighboring relations.
- Hybrid paths preserve separate accounting semantics instead of combining unlike privacy guarantees into one misleading number.

### Adaptive clipping

- Adaptive clipping support is retained with privacy-binding/attestation infrastructure.
- Population-sampling/accounting caveats are documented instead of overstating an end-to-end guarantee.

### Fail-closed SCAFFOLD privacy boundary

DP-enabled SCAFFOLD remains blocked where control-variate privacy semantics are not covered by a validated release/accounting model. This includes unsupported sample-level, user-level, or hybrid combinations.

---

## 11. Deterministic Checkpointing, Pause, and Resume

v2.0.0 adds a real round-boundary resumable local runtime.

### Checkpoint state now preserves

- Global model state.
- Server round count/runtime state.
- Python RNG state.
- NumPy RNG state.
- Torch RNG state.
- CUDA RNG state where applicable.
- Dedicated privacy-noise generator state.
- Client sampler state.
- Privacy accountant state.
- Training history.
- Elapsed time.
- SCAFFOLD global and client control variates.

### Checkpoint safety

- Uses `torch.load(..., weights_only=True)`.
- NumPy RNG state is serialized using primitive values compatible with weights-only restore.
- Configuration fingerprint mismatches are rejected.
- SHA-256 sidecar verification protects against accidental checkpoint corruption/change.
- Python and Go both verify durable checkpoint evidence before accepting resume state.

### Pause/resume lifecycle

- Pause is requested through the execution artifact control directory.
- Pause is honored at a safe completed communication-round boundary.
- Local PAUSED state requires validated checkpoint evidence.
- Resume relaunches the same canonical execution from the validated checkpoint.
- CSV history appends on resume instead of truncating previous rounds.
- Durable PAUSED state survives control-plane restart when checkpoint evidence remains valid.
- A run may pause after the final training round but before final reporting; resume then performs finalization without repeating training.

---

## 12. Distributed Round Runtime Hardening

### Restart-durable deadlines

- Round deadlines are absolute and persist through coordinator restart.
- Restart does not reset timeout windows.

### Full-cohort-first settlement

- The coordinator does not release a round simply because the fastest quorum arrived.
- The normal path prefers the full selected cohort before deadline/settlement.
- Partial-cohort settlement is allowed only at the configured settlement/deadline boundary when quorum requirements are met.
- Below-quorum rounds fail closed.

### Retry and lease durability

- Task retry budgets persist across restart.
- Task lease expiry continues to work after restart.
- Disappeared workers cannot leave a round hanging indefinitely.
- Timeout/fault classification is exposed through round observability.

### Coordinator tick contract

Round finalization follows the explicit coordinator advance/tick contract. Duplicate submit-path advancement was removed, preventing double progression, timestamp regressions, CLI round overshoot, restart inconsistencies, and pause-state errors.

The regression suite explicitly validates full-cohort finalization on the next coordinator tick before deadline.

---

## 13. Secure Aggregation

v2.0.0 includes a significantly hardened secure-aggregation runtime for validated supported configurations.

### Core secure aggregation capabilities

- Secure cohort handshake.
- Signed/frozen cohort roster handling.
- Client key advertisement.
- Pairwise mask generation.
- Fixed-point encoding.
- Tensor masking.
- Masked client updates.
- Secure aggregation session state machine.
- Persistent secure aggregation session store.
- Adaptive-clipping bindings.
- User-level privacy attestation bindings.
- Coordinator-side secure aggregation security events.

### Dropout and failure handling

- Dropout-related secure-aggregation behavior added for the supported synchronous secure path.
- Durable session/security events are consumed by execution reconciliation.
- Live session aborts propagate into the normal execution cancellation/failure path.
- Coordinator-restart abort evidence can mark an execution failed when the previous secure backend run is no longer recoverable.
- Independent secure-session deadline watchdog prevents progress from depending solely on worker polling.
- `COHORT_FROZEN` sessions with zero masked contributions are covered by timeout handling.
- Optional secure-session admin bindings support list/get/abort where the coordinator exposes those RPCs.
- Older coordinators returning `UNIMPLEMENTED` for optional secure-admin APIs are treated as lacking that optional capability rather than breaking ordinary execution reconciliation.
- Transport/authentication failures remain visible and are not silently downgraded.

### Recovery behavior

- Secure aggregation recovery reconciliation is integrated with durable execution state.
- Security-event cursor persistence prevents replaying already-consumed secure events after process restart.
- Recovery/abort/timeout ordering has dedicated regression coverage.

---

## 14. Security and Worker Identity

### Transport and identity

- mTLS-capable worker/coordinator transport infrastructure.
- Development PKI lifecycle tooling and CI verification.
- Worker TLS configuration and certificate inspection support.

### Signed messages and task integrity

- Signed coordinator task verification.
- Signed worker result/security-message infrastructure.
- Trust-bundle validation.
- Coordinator signing-key handling.
- Worker signing identities.
- Signing-key rotation paths.
- Sequence-state persistence.
- Replay protection.
- Idempotency state.
- Accepted-task journaling.

### Security events and auditability

- Structured security events.
- Security event journal.
- Security audit journal.
- Durable execution/security event integration.
- Batching of durable execution event writes.
- Prohibited-material checks on security-runtime CI output.
- Repository secret scan for private-key/credential markers.

---

## 15. Durable Execution Observability

### Added

- Durable backend execution event journal.
- Batched event writes.
- Persisted security-event reconciliation cursor.
- Round-level observability.
- Timeout/failure event classification.
- Model-version and worker-count reconciliation persistence.
- Execution state updates independent of client refresh requests.

This provides a more reliable source of truth across control-plane/coordinator restarts and long-running distributed executions.

---

## 16. Go Control Plane Expansion

The Go control plane now covers more than project/run bookkeeping.

### Added or expanded

- Unified execution API and services.
- Local and distributed execution lifecycle integration.
- Automatic execution reconciliation.
- Model registry APIs/services.
- Dataset registry APIs/services.
- Algorithm metadata and configuration validation.
- Personalization summary integration.
- Go-native fairness metric calculations.
- Secure aggregation recovery/admin capability integration.
- Durable repository/event state.
- Generated protobuf/gRPC bindings and compatibility checks.

---

## 17. C++ Coordinator Expansion

### Added or expanded

- New algorithm enum support for FedSAM, Ditto, and Per-FedAvg aggregation contracts.
- Aggregation manifest enforcement.
- Personalization metric storage.
- Personalization summary API.
- Full checkpoint persistence of personalization metrics.
- Restart-safe round deadlines/retries.
- Secure aggregation session/runtime state.
- Secure aggregation recovery and deadline behavior.
- Worker identity/security state stores.
- Replay-protection state.
- Accountant monotonicity state.
- Signing-key registries.
- Task sequence and idempotency stores.
- Trusted-key bundle handling.
- Security event/audit journals.

### Correctness fix

Unknown algorithm values no longer silently fall back to FedAvg.

---

## 18. Python Worker and Runtime Expansion

### Added or expanded

- FedSAM, Ditto, and Per-FedAvg implementations.
- Personalization architecture and model persistence.
- Model/dataset registries.
- Evaluation services.
- Benchmark matrix/statistical utilities.
- Realized heterogeneity metrics.
- Canonical partition-aware distributed worker adapter.
- Signed accepted partition reference handoff.
- Sample-level/user-level/hybrid privacy infrastructure.
- Secure aggregation signing/masking/attestation support.
- Deterministic checkpoint restore.
- Strict type checking across the maintained Python source tree.

---

## 19. Web Dashboard Expansion

### Experiment builder

- Dynamic algorithm configuration fields loaded from the algorithm API.
- Removed reliance on hardcoded display-name-based configuration fields.
- Corrected frontend/Go algorithm configuration contract mismatch.

### New pages

- **Model Registry** — `/models`
- **Dataset Registry** — `/datasets`
- **Algorithm Comparison** — `/compare`

### Run dashboard

Added personalization/fairness reporting including:

- Global vs personalized accuracy.
- Fairness gap.
- Worst/best client summaries.
- Per-client result table.
- Explicit unavailable/empty states when personalization data is absent or the coordinator is unavailable.

---

## 20. API and Protobuf Contract Changes

The v2 algorithm/personalization contract expansion is additive.

### Experiment contract

Added configuration messages/fields for:

- FedSAM.
- Ditto.
- Per-FedAvg.
- Personalization.

### Coordinator contract

Added/expanded:

- Aggregation manifest.
- Aggregation manifest binding on model/task contracts.
- Personalization metric record.
- Personalization result submission fields.
- Personalization summary request/response and RPC.
- Secure aggregation/session/admin contracts used by the distributed runtime.

Existing protobuf field numbers were not intentionally renumbered or removed by the algorithm expansion, and CI verifies contract compatibility/generation.

---

# Correctness and Reliability Fixes

v2.0.0 includes a large set of release-hardening fixes in addition to new features.

## Privacy correctness

- Fixed target-epsilon drift after runtime overrides through recalibration.
- Prevented manual noise configuration from retaining misleading target-epsilon claims.
- Corrected same-adjacency composition semantics.
- Kept SCAFFOLD+DP unsupported paths fail-closed.

## Algorithm/runtime correctness

- Unknown algorithm wire names now fail instead of falling back to FedAvg.
- Aggregated vs personalized tensor manifests are separated correctly.
- Personalization metrics now survive coordinator checkpoint/restart.
- Frontend and Go algorithm config shapes were aligned.

## Checkpoint/restart correctness

- RNG, privacy generator, accountant, sampler, model, and SCAFFOLD state restore deterministically.
- Checkpoint configuration mismatch and byte tampering are rejected.
- CSV round history survives resume without truncation.

## Distributed scheduling correctness

- Removed duplicate round advancement.
- Fixed restart timestamp regressions caused by incorrect synthetic finalization timing.
- Fixed Python CLI-bridge round overshoot/restart/pause behavior.
- Aligned full-cohort finalization with explicit scheduler ticks.
- Preserved absolute deadlines and retry budgets across restart.

## Build/CI correctness

- Fixed Go execution-event generation syntax regressions.
- Fixed C++ `advance` wrapper collision with `std::ranges::advance` on GCC/libstdc++.
- Fixed C++/Go formatting failures.
- Fixed generated protobuf build assumptions in Docker/CI.
- Removed accidental validation-only production markers.
- Resolved 24 Ruff lint blockers before release.
- Applied repository-wide Ruff formatter output for reported drift.
- Fixed final strict mypy error in the partition-aware worker adapter.

---

# Behavioral Changes to Know Before Upgrading

- The platform now rejects more invalid or unsupported configurations instead of silently coercing them.
- Unknown C++ coordinator algorithm identifiers fail instead of defaulting to FedAvg.
- DP-enabled SCAFFOLD combinations without proven accounting/control-variate semantics fail closed.
- Local canonical execution validates privacy/accountant/security/scheduling compatibility before starting the Python subprocess.
- Explicit manual DP noise can invalidate/clear a configured target-epsilon calibration claim.
- Round finalization is owned by the explicit coordinator scheduling tick, not by duplicate result-submission progression.
- Full-cohort-first distributed scheduling may wait for the remaining selected clients until settlement/deadline rather than immediately finalizing at fastest quorum.
- Durable PAUSED local executions require valid checkpoint evidence.
- Runtime and benchmark artifacts now carry more provenance and partition information; consumers should not assume older minimal summary schemas.

---

# Known Limitations and Explicit Boundaries

v2.0.0 is release-ready for the capabilities validated by the repository, but it does not claim universal production support for every possible federated-learning configuration.

## Execution identity

- `root-simulator` and `distributed-platform` are distinct execution identities.
- Root benchmark results must not be presented as measurements of the distributed runtime unless the distributed runtime was actually executed.

## Local execution

- Canonical local execution supports synchronous scheduling.
- Unsupported local distributed-security claims are rejected rather than simulated.
- Unexpected control-plane restart cannot reattach an arbitrary still-running local subprocess unless the execution had already reached a valid durable PAUSED checkpoint state.

## Checkpoint integrity

- SHA-256 sidecars detect corruption/change of checkpoint bytes.
- SHA-256 alone is not keyed authenticity against an attacker able to rewrite both the checkpoint and its digest sidecar.

## Differential privacy

- Privacy guarantees must be interpreted according to their neighboring relation; sample-level and client-level numbers are not interchangeable.
- Same-adjacency releases require composition.
- Opacus GDP accounting is experimental and can underestimate privacy expenditure; it should not be treated as a production guarantee without independent validation.
- Some experiment/test paths intentionally allow non-secure RNG for speed. Security-sensitive final privacy runs should enable the validated secure-randomness mode required by their threat model.
- SCAFFOLD with unsupported DP modes remains blocked.

## Secure aggregation

- Secure aggregation is only claimed for combinations explicitly wired and validated by the runtime.
- Unsupported asynchronous, per-run, or threshold-recovery combinations remain fail-closed.
- Optional secure-session admin RPCs may be unavailable against older coordinator builds.
- Secure aggregation does not by itself provide a universal malicious-client defense or formal security certification.

## Personalization storage

- Personalized model files can contain client-specific information.
- The filesystem store provides path/scheme/checksum/ownership protections but does not by itself provide encryption at rest or a complete external per-client authorization system.

## Distributed data

- Raw training samples are not transported through the Go control plane/coordinator by the partition-parity layer.
- Canonical distributed worker partition references reconstruct deterministic worker-local synthetic shard semantics for the supported integration path; this should not be confused with centralized raw-data movement.

## Certification

- v2.0.0 is not a formal privacy/security certification.
- It does not claim Internet-scale production certification, Byzantine robustness, or protection against every malicious participant model.

---

# Validation Status

The final v2 release-readiness CI run completed successfully before this release-note preparation.

## Final CI gate

**GitHub Actions CI run #496 — PASS**

### Python

- **629 tests passed**.
- Ruff lint: **PASS**.
- Ruff format check: **PASS**.
- **177 files** confirmed formatted.
- Strict mypy: **PASS**.
- **110 source files** type-checked.

### C++

- Debug build/tests: **PASS**.
- Release build/tests: **PASS**.
- ASan: **PASS**.
- UBSan: **PASS**.
- clang-format: **PASS**.
- clang-tidy: **PASS**.
- gRPC-gated build/tests: **PASS**.
- Quick benchmark job: **PASS**.

### Go

- gofmt: **PASS**.
- `go vet`: **PASS**.
- Unit/integration tests: **PASS**.
- Race detector: **PASS**.
- Build: **PASS**.

### Contracts, infrastructure, and security

- Protobuf compatibility check: **PASS**.
- Protobuf generation: **PASS**.
- Docker Compose configuration/build: **PASS**.
- Development PKI lifecycle verification: **PASS**.
- Secret scan: **PASS**.
- Security-runtime validation subset: **PASS**.
- Prohibited-material output check: **PASS**.
- Terminology gate: **PASS**.

---

# Main v2.0.0 Development Waves Included

This release consolidates the major implementation waves merged into the v2 line, including:

- Privacy correctness hardening.
- Benchmark foundation and real benchmark execution.
- Dataset/evaluation expansion.
- Algorithm expansion and personalization.
- Unified execution engine/control plane.
- Deterministic checkpoint pause/resume.
- Startup and periodic execution reconciliation.
- Restart-durable distributed round runtime.
- Round deadline/retry/fault-tolerance hardening.
- Durable execution event observability.
- Secure aggregation recovery/reconciliation.
- Secure aggregation dropout/runtime hardening.
- Distributed partition parity.
- v2 release candidate/runtime hardening.
- Scheduler regression fixes.
- CI lint/format/type release-readiness cleanup.

Representative merged PRs include #5, #8, #10, #13, #16, #20, #26, #28, #33-#43, #44-#49 and their associated validation/release-hardening work.

---

# Upgrade Notes

## Python

v2.0.0 requires Python **3.11 or newer** for the maintained Python platform package.

For a development install:

```bash
python -m pip install -e "python[dev]"
```

Install the root runtime requirements when using the full legacy/root experiment stack:

```bash
python -m pip install -r requirements.txt
```

## Protobuf generation

Regenerate bindings using the repository scripts/Make targets documented in the project when modifying protobuf contracts. CI verifies compatibility and generation success.

## Existing experiment configurations

Review existing configurations for:

- Algorithm compatibility.
- Privacy neighboring relation.
- Target epsilon vs manual noise settings.
- Local vs distributed runtime identity.
- Scheduling mode.
- Secure aggregation/security requirements.
- Checkpoint persistence settings when pause/resume is required.

Configurations that were previously tolerated through implicit fallback may now fail closed intentionally.

---

# Quick Start

Clone and enter the repository:

```bash
git clone https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy.git
cd Federated-Learning-on-Non-IID-Data-Differential-Privacy
```

Install Python dependencies:

```bash
python -m pip install -r requirements.txt
python -m pip install -e "python[dev]"
```

For root-runtime, distributed-runtime, Docker, API, benchmark, security, and platform-specific execution commands, use the current `README.md`, `RUNTIME.md`, and documentation under `docs/` as the source of truth.

---

# Release Summary

v2.0.0 is the first release in this repository that brings the benchmark, privacy, algorithm, personalization, execution-control, recovery, distributed-coordination, observability, partition-parity, and secure-aggregation work into one validated platform baseline.

The most important change is not a single algorithm or API. It is the combination of **reproducible execution, explicit runtime identities, fail-closed capability boundaries, deterministic recovery, measurable non-IID behavior, stronger privacy accounting, durable distributed state, and a fully validated cross-language CI gate**.

This release is intended to provide a substantially stronger foundation for repeatable federated-learning experimentation and continued platform development without overstating unsupported privacy, security, or deployment claims.

---

## Release Checklist

- [x] Python package version set to `2.0.0`.
- [x] C++ platform version set to `2.0.0`.
- [x] Release-runtime hardening merged.
- [x] Distributed partition parity merged.
- [x] Scheduler regression fix merged.
- [x] Ruff lint blockers resolved.
- [x] Ruff formatting clean.
- [x] Strict mypy clean.
- [x] Full CI matrix green.
- [x] Security-runtime validation green.
- [x] PKI and secret-scan gates green.
- [x] v2.0.0 release notes prepared.
- [ ] Create/push `v2.0.0` Git tag.
- [ ] Publish the GitHub Release.

> The final two checklist items are intentionally left unchecked until the actual Git tag and GitHub Release are published.
