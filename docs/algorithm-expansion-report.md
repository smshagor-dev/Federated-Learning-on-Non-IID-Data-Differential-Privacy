# Algorithm Expansion Phase Final Report

## 1. Repository audit (starting point)

the Foundation, Aggregation Core, and Coordinator Runtime phases delivered: the C++ aggregation core and gRPC coordinator
runtime (real, compiled, tested), the Python worker with FedAvg/FedProx/
SCAFFOLD local training reusing the proven `federated.client.Client`
legacy path, a Go control plane (projects/experiments/runs bookkeeping
plus a coordinator-run façade), and a Next.js web dashboard. the Algorithm Expansion phase
began from that base — no Foundation-through-Coordinator-Runtime code was rewritten without a proven
defect (two were found and fixed; see §17).

## 2. the Algorithm Expansion phase architecture

See [algorithm-expansion-architecture.md](algorithm-expansion-architecture.md) for the
full component/sequence diagrams. Summary: C++ gained three new
aggregation-algorithm enum labels (zero new aggregation math — all three
reuse `WeightedAggregator`) plus an `AggregationManifest` enforcement
mechanism and personalization-metric storage. Python gained three real
training algorithms, a personalization architecture, a persistent
personalized-model store, model/dataset registries, and an evaluation
service. Go gained metadata APIs for all of the above plus a Go-native
fairness-statistics reimplementation. Web gained dynamic algorithm
config fields, two new registry pages, a personalization/fairness panel,
and an algorithm-comparison page.

## 3. Contract changes (proto)

`proto/experiment/experiment.proto`: `FedSamConfig`, `DittoConfig`,
`PerFedAvgConfig`, `PersonalizationConfig` messages; `AlgorithmConfig`
extended with fields 3-6. `proto/coordinator/coordinator.proto`:
`AggregationManifest` message + field on `ModelManifest`; field on
`ClientTrainingTask`; `PersonalizationMetricRecord` message; field on
`SubmitClientResultRequest`; `GetPersonalizationSummaryRequest`/
`PersonalizationSummaryResponse` + new RPC. All changes are additive
(new fields/messages/RPCs) — no existing field renumbered or removed.
`scripts/verify_proto_contracts.py` extended with the new expected
fields; contract-compatibility check passes.

## 4. FedSAM

See [fedsam.md](fedsam.md). Real two-pass SAM training (perturb,
forward/backward, restore in a `finally` block, optimizer step on the
second pass's gradient). 3 unit tests. No personalization. Maps to
`AggregationAlgorithm::kFedSam` → `WeightedAggregator` in C++, zero new
aggregation math.

## 5. Ditto

See [ditto.md](ditto.md). Real dual-model training (global-training +
personalized, L2-regularized against a frozen global reference). Warm/
cold start policies. 3 unit tests. Personalized checkpoint persisted via
the personalized model store, never aggregated.

## 6. Per-FedAvg (first-order)

See [per-fedavg.md](per-fedavg.md). Real seeded support/query split,
inner adaptation on a copy, first-order meta-gradient via
`torch.autograd.grad` on the adapted copy (never differentiating through
the inner loop). Small-client fallback. 4 unit tests.

## 7. Shared-backbone personalization architecture

See [shared-backbone-local-head.md](shared-backbone-local-head.md).
Parameter-name-prefix based, no custom `Module` subclassing —
`GroupNormCNN` (`"features."`/`"classifier."`) and the new
`PersonalizableBridgeModel` (`"backbone"`/`"head"`) both supported.
`compute_schema_hash`, `describe_model`, and the shared/personalized
state-dict extraction utilities are all real and tested.

## 8. Personalized model persistence

See [personalized-model-store.md](personalized-model-store.md).
Filesystem-backed, atomic writes, SHA-256 checksums, ownership checks,
schema checks, `torch.load(weights_only=True)`, path-traversal defense
(`_VALID_ID` regex on every path segment), bounded LRU cache.

## 9. Model registry

See [model-registry.md](model-registry.md). Implemented independently in
both Python (filesystem, one file per name+version) and Go (filesystem,
one combined JSON file, mirroring the `projects`/`experiments` pattern).
Identical status machine (DRAFT→VALIDATED→ACTIVE→DEPRECATED→ARCHIVED),
identical schema-hash-gated validation.

## 10. Dataset registry

See [dataset-registry.md](dataset-registry.md). Same dual-language
pattern, with one deliberate asymmetry: Python computes real partitions
(IID/Dirichlet/pathological, with real sample indices and label
histograms); Go records partition *manifests* (strategy + per-client
sample counts) supplied by a caller, since raw dataset access never
crosses into Go.

## 11. Personalized evaluation service

See [personalized-evaluation.md](personalized-evaluation.md). One
evaluation function (`evaluate_model_on_partition`) used for both global
and personalized models — no special-casing by algorithm.

## 12. Fairness metrics

See [fairness-metrics.md](fairness-metrics.md). Independently
implemented in Python and Go, unit-tested against shared worked examples
(percentile interpolation, Jain's index edge cases). Handles missing
personalized models, zero-sample clients, and non-finite values as
explicit exclusions with reasons, never silent drops.

## 13. C++ coordinator changes

3 new `AggregationAlgorithm` enum values (zero new aggregation math);
`AggregationManifest` struct + enforcement in `submit_client_result`;
personalization-metric storage with full checkpoint persistence
(`encode_personalization_metric`/`parse_personalization_metric`);
`RunInstance::personalization_summary()`; full `GetPersonalizationSummary`
gRPC RPC implementation; a genuine pre-existing bug fixed
(`algorithm_from_wire` silently defaulted unknown algorithms to FedAvg —
now throws). 2 new C++ test suites (8/8 total suites pass).

## 14. Go control-plane changes

New packages: `internal/algorithms` (metadata + config validation),
`internal/models`, `internal/datasets` (both mirroring the existing
`projects`/`experiments` repository pattern). Extended:
`internal/coordinator` (`GetPersonalizationSummary` on the `Client`
interface, `MockClient`, and `GrpcClient`), `internal/application`
(`fairness.go`, `ModelService`, `DatasetService`, algorithm-config
validation wired into `ExperimentService`), `internal/transport/httpapi`
(9 new route groups). Go protobuf stubs regenerated via a throwaway
Docker container (no local `protoc`).

## 15. Web dashboard changes

Experiment builder: dynamic per-algorithm config fields fetched live
from `GET /api/v1/algorithms` (replacing hardcoded display-name-keyed
fields, and fixing a real config-shape mismatch discovered during this
work — see §17). New pages: Model Registry (`/models`), Dataset Registry
(`/datasets`), Algorithm Comparison (`/compare`). New panel:
Personalization/fairness on the run dashboard (`/runs/[runId]`), showing
global-vs-personalized accuracy, fairness gap, worst/best client, and a
per-client table — with explicit empty/unavailable states for runs with
no personalization data or an unreachable coordinator.

## 16. Security and privacy considerations

See [algorithm-expansion-security-audit.md](algorithm-expansion-security-audit.md) for
the full pass. No new vulnerability class introduced. Path traversal,
unsafe deserialization, tamper detection, and RBAC were all already
handled correctly by design; the one genuinely new risk (personalized
models may memorize client-specific data, with no encryption-at-rest or
per-client access control beyond the filesystem) is documented explicitly
in [known-limitations.md](known-limitations.md) rather than left
implicit.

## 17. Real bugs found and fixed during this phase

1. **C++**: `algorithm_from_wire` silently defaulted unknown algorithm
   strings to `kFedAvg` — fixed to throw, matching the CLI's existing
   correct behavior.
2. **C++/manifest design**: including personalized/frozen tensor names in
   the canonical `ModelManifest.tensors` list (alongside the separate
   aggregation-manifest declaration) broke the pre-existing Aggregation
   Core phase's "delta tensor set must match manifest" rule — fixed by
   keeping those two
   lists strictly separate (documented in
   [aggregation-manifests.md](aggregation-manifests.md)).
3. **Coordinator checkpoint gap**: personalization metrics were
   in-memory only, vanishing across the CLI-bridge's process-per-call
   boundary — fixed with full checkpoint serialization, verified by a
   dedicated test that constructs a fresh `RunManager`/`RunInstance`.
4. **Web/Go contract mismatch**: the experiment builder already sent
   `config.algorithm = {name, ...fields}` (nested object); the Go-side
   validation was initially written for a flat `algorithm`/
   `algorithm_config` shape that would never have matched the real
   payload. Caught and fixed before shipping by reading the actual
   frontend code rather than assuming a shape.

## 18. Files added

~45 new files across C++ (2 test files), Python (11 new modules:
`algorithms/base.py`, `algorithms/registry.py`, `algorithms/legacy_adapter.py`,
`datasets/dataset_registry.py`, `datasets/loaders.py`,
`datasets/partitioning.py`, `evaluation/__init__.py`,
`evaluation/service.py`, `models/factory.py`, `models/model_registry.py`,
`models/personalization.py`, `personalization/store.py`, plus 2 new test
files), Go (3 new packages: `algorithms`, `models`, `datasets`; 5 new
application-layer files; 6 new HTTP handler/test files), web (7 new
files: 3 pages, 3 feature components, 1 test file), and 14 new docs (this
one plus 13 others). See §21 for the exact test-file inventory.

## 19. Files modified

~40 files: C++ (8 core/coordinator files), proto (2 files), Go (10
files), Python (11 files, mostly rewrites of the Foundation-era FedSAM/Ditto/
Per-FedAvg placeholders plus `__init__.py` export updates), web (5
files), docs (3 files: `benchmarking.md`, `docker-runtime.md`,
`known-limitations.md`), `scripts/verify_proto_contracts.py`.

## 20. Files removed

None. (Two stray manual-testing scratch directories,
`agg_manifest_scratch/`/`personalization_scratch/`, left over from this
session's own manual CLI verification, were deleted — never part of any
deliverable or tracked by git.)

## 21. Tests added

* C++: `aggregation_manifest_test.cpp`, `personalization_summary_test.cpp`
  (2 new suites, both passing; 8/8 total suites).
* Python: 15 new tests in `test_algorithm_expansion_foundations.py`, 4 new
  cross-language integration tests in
  `test_algorithm_expansion_integration.py` (71/71 total pass).
* Go: `fairness_test.go` (9), `model_service_test.go` (6),
  `dataset_service_test.go` (8), `algorithms_test.go` (12),
  `personalization_handlers_test.go` (5), `registry_handlers_test.go` (6),
  plus repository-level tests in `models`/`datasets` (4 each), plus 4 new
  experiment-algorithm-config-validation tests in `services_test.go`
  (all Go tests pass).
* Web: `algorithm-expansion-api.test.ts` (13 new tests; 21/21 total pass).

## 22. Exact commands run, with pass/fail/blocked status

See [algorithm-expansion-validation.md](algorithm-expansion-validation.md) for the full
table. All green: C++ CTest (8/8), Python pytest (71/71) + ruff (clean),
Go build/vet/test (clean, all pass), web typecheck/lint/test/build (all
four clean), Docker build (4 images) + up (7 services healthy) + live
endpoint verification + down (clean), benchmark script (real numbers
captured).

## 23. Cross-language integration results

`tests/baseline/test_algorithm_expansion_integration.py`: FedSAM two
rounds/four clients (pass), Ditto two rounds with personalized-checkpoint
persistence across a simulated worker restart plus wrong-client
rejection plus personalization-summary retrieval (pass), Per-FedAvg two
rounds/four clients (pass), local-head tensor rejected by the
coordinator's aggregation manifest (pass). All via the real CLI-bridge
process boundary (one `coordinator_cli` process per call), not mocked.

## 24. Docker runtime results

See [docker-runtime.md](docker-runtime.md)'s the Algorithm Expansion phase section and
[algorithm-expansion-validation.md](algorithm-expansion-validation.md). Full stack
(7 services) built and ran healthy; every new Go API endpoint verified
live against the real C++ coordinator over gRPC; Prometheus confirmed
scraping the new `GetPersonalizationSummary` RPC counter; clean
teardown. One check (full live-gRPC distributed round for a worker-
container-restart persistence test) was not achievable due to a
pre-existing Coordinator Runtime phase gRPC wire-mapping gap (`CreateRun` doesn't wire
`client_ids`), not an Algorithm Expansion phase regression — the underlying property was
verified via two other real, passing tests instead.

## 25. Benchmark methodology and results

See [benchmarking.md](benchmarking.md)'s the Algorithm Expansion phase section. Real,
locally-measured wall-clock numbers (warm-up + 5 timed repetitions,
median/mean) for all four algorithms at two model sizes. FedSAM measures
~1.86× FedAvg's cost at real (`GroupNormCNN`) scale — expected, not a
regression; the `tiny_bridge` scale's reversed ordering is explicitly
flagged as measurement noise, not a real result, rather than silently
included as if it meant something.

## 26. Regression status

Zero regressions detected. Every the Foundation, Aggregation Core, and Coordinator Runtime phases test (C++, Python, Go,
web) stayed green throughout this phase's work, checked repeatedly
after each change, not just once at the end.

## 27. Known limitations

See [known-limitations.md](known-limitations.md)'s new the Algorithm Expansion phase
section: Go's dataset partitioning is metadata-only (by design); Go's
registries use a different on-disk layout than Python's (functionally
equivalent); personalized models have no encryption-at-rest or per-
client access control; Go/Python fairness formulas are independently
tested, not a shared library; FedSAM is not claimed to converge better;
protobuf stubs require Docker-based regeneration in this environment.

## 28. Git working-tree summary

At the time of this report: ~40 modified files, ~45 new files (including
14 new docs), 0 deleted files (beyond two stray scratch directories that
were never tracked). No commits were made — per standing instructions,
this phase's work was not committed or pushed without an explicit
request.

## 29. Recommended the next phase scope

Candidates, none started this phase: (a) closing the `CreateRun`
gRPC wire-mapping gap (`client_ids`, a real `ModelManifest`) so a full
distributed training round can run through the live gRPC path in Docker,
not just the CLI-bridge; (b) a production PostgreSQL-backed model/
dataset registry to replace the current file/in-memory-backed ones;
(c) TLS/mTLS for the coordinator, replacing today's local-development-
grade insecure gRPC; (d) encryption-at-rest / per-client access control
for the personalized model store, given the client-specific-data risk
documented this phase; (e) secure aggregation cryptography and
sample/user-level DP, both still explicitly out of scope through
the Algorithm Expansion phase; (f) a real ResNet-18 (GroupNorm-substituted) architecture,
deferred again this phase for the same safety-under-time-budget
reason as before.

## 30. Explicit non-goals maintained this phase

Per standing instruction: no Opacus, no sample/user-level DP, no secure
aggregation cryptography, no Ray/Flower, no async/semi-sync/Byzantine-
robust aggregation, no full PostgreSQL production repos, no LLM/LoRA
federation, no mobile/edge deployment, and no work on the next phase. All
maintained throughout.
