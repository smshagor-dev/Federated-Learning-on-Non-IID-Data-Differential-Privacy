# Research Correctness Contract

Status: active research gate for PhD-oriented work.

This contract defines the minimum scientific and implementation requirements that must hold before the repository makes publication-facing claims.

## 1. Canonical execution identity

Every experiment must declare one runtime identity:

- `root-simulator`: `python main.py --cli`
- `distributed-platform`: `docker compose -f infra/compose/docker-compose.dev.yml up --build`

See `RUNTIME.md`. Results from the two runtimes must not be mixed without a parity study.

## 2. Privacy definitions

### Client-level central DP in the root simulator

The protected unit is one client's entire round contribution under add/remove-client adjacency. The root mechanism assumes:

1. clients are sampled independently using Poisson sampling with probability `q`;
2. each selected client contributes at most one update;
3. the update is clipped to a global L2 bound `C`;
4. the trusted server aggregates clipped updates;
5. one Gaussian noise vector with standard deviation `sigma * C` is added to the aggregate sum;
6. RDP is composed across released rounds and converted to `(epsilon, delta)`.

Changing any of these assumptions invalidates the reported epsilon unless the accountant is changed accordingly.

### Sample-level DP

Sample-level DP protects one local training example and is accounted separately using the worker-side Opacus path. A sample-level epsilon is not a substitute for client-level epsilon.

### Adaptive clipping and private statistics

A private statistic may keep a separate ledger for auditability, but ledger separation does not automatically imply privacy-composition separation.

If a private statistic and the model release both protect the same user/client neighboring relation and are jointly released, publication-facing user/client privacy must compose the two mechanisms. `federated/privacy_research.py::compose_same_adjacency_rdp` provides the explicit same-adjacency primitive for RDP mechanisms.

Mechanisms with different neighboring relations are rejected by that function and must remain separately reported.

## 3. DP + SCAFFOLD publication boundary

The active root implementation maintains SCAFFOLD control variates in addition to the global-model update. Until a formal privacy analysis establishes how that state/release path interacts with client-level adjacency and clipping/noise, DP-enabled SCAFFOLD is not a supported privacy-guaranteed configuration.

The root server therefore fails closed when `algorithm=scaffold` and client-level DP is enabled.

Allowed research use:

- SCAFFOLD without DP as an optimization baseline;
- FedAvg/FedProx with the root client-level DP mechanism;
- a future DP-SCAFFOLD implementation only after its release/state semantics and accountant are explicitly specified and tested.

## 4. Privacy-budget calibration

Primary PhD experiments must be parameterized by privacy budgets rather than arbitrary noise values.

Recommended target grid:

```text
epsilon in {1, 2, 4, 8}
delta chosen relative to the protected population and reported explicitly
```

Use `scripts/calibrate_client_level_dp.py` to solve for `sigma` at fixed `q`, rounds and `delta`.

The calibration routine returns the privacy-safe side of the binary search: the achieved epsilon is not greater than the requested target, within floating-point precision.

## 5. Non-IID research requirements

Dirichlet alpha alone is not a sufficient characterization of realized heterogeneity. Every partition used in a thesis/paper must archive:

- partition strategy and parameters;
- dataset/version/checksum;
- partition seed;
- client sample counts;
- per-client label histograms where labels exist;
- realized heterogeneity metrics;
- partition/manifest hash.

The research program should evaluate at least:

- IID control;
- label-distribution skew;
- quantity skew;
- pathological class restriction;
- feature/covariate shift where supported by a real dataset;
- mixed heterogeneity.

Synthetic label assignment is acceptable for unit tests, not as the sole evidence for a publication benchmark on real datasets.

## 6. Experimental statistics

A publication result must not be based on a single random seed.

Minimum protocol:

- at least 5 independent seeds for ordinary benchmark cells;
- report mean and standard deviation;
- report a 95% confidence interval or a clearly justified alternative;
- retain per-seed raw metrics;
- use paired tests when comparing algorithms on matched dataset/partition/seed conditions;
- report an effect size in addition to p-values when statistical tests are used;
- include worst-client/fairness metrics, not only global accuracy;
- report communication rounds and wall-clock/runtime cost separately.

For large/high-cost studies, fewer repetitions are permitted only when the power/precision trade-off is stated before interpreting the result.

## 7. Required ablations

Any proposed adaptive or heterogeneity-aware algorithm must include ablations that isolate at least:

- client-selection adaptation;
- clipping adaptation;
- noise/privacy-budget scheduling;
- aggregation/personalization behavior;
- the heterogeneity estimator itself;
- interactions between the above components.

A new method must be compared against unchanged baselines under the same data partitions, seeds, model family, optimizer budget and privacy definition.

## 8. Reproducibility record

Every publication run must persist:

```text
commit_sha
runtime_identity
experiment_spec_hash
dataset_id + dataset_checksum
partition_id + partition_hash
model/config hash
algorithm configuration
privacy definition
q, C, sigma, delta, epsilon
all random seeds
software versions
hardware/device summary
raw per-round metrics
final summary
```

A figure/table is derived evidence; the raw per-seed data is the source evidence.

## 9. Security claims

Secure aggregation must never be described as protection against poisoning, Byzantine behavior, dishonest clipping, Sybil attacks, or a fully malicious coordinator unless a separate mechanism proves that property.

Security and privacy claims must identify the threat model, trust assumptions, protected payload, dropout behavior and validation scope.

## 10. Research novelty gate

Adding another known FL algorithm to the registry is engineering work, not by itself a PhD contribution.

The target scientific direction is a measurable, testable contribution around trustworthy FL under multi-dimensional heterogeneity. A candidate method should jointly reason about some combination of:

- participation/sampling policy;
- client/update clipping;
- privacy-noise allocation;
- optimization or aggregation;
- personalization/fairness;
- secure aggregation compatibility.

The contribution must be expressed as falsifiable hypotheses and evaluated against strong baselines under controlled privacy budgets.

## 11. Release gate for research claims

Before a result is labelled publication-ready:

1. tests covering its accountant/algorithm assumptions pass;
2. the exact runtime path is validated;
3. all experiment configurations and partition hashes are archived;
4. repeated-seed statistical summaries exist;
5. privacy claims match the implemented adjacency and sampling model;
6. unsupported combinations fail closed rather than silently producing a number;
7. known limitations are stated next to the claim, not hidden in unrelated documentation.
