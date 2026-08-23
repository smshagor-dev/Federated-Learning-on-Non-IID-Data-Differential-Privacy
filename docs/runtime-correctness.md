# Runtime Correctness Contract

This document defines the minimum implementation and validation requirements for trustworthy benchmark results from this repository.

## 1. Execution identity

Every run must declare one runtime identity:

- `root-simulator`: `python main.py --cli`
- `distributed-platform`: `docker compose -f infra/compose/docker-compose.dev.yml up --build`

Results from different runtime identities must not be combined unless parity has been validated for the compared path.

## 2. Client-level differential privacy

The root client-level mechanism uses add/remove-client adjacency and assumes:

1. independent Poisson client sampling with probability `q`;
2. at most one contribution from each selected client per round;
3. one global L2 clipping bound `C` for each client update;
4. aggregation by the trusted server;
5. one Gaussian noise vector with standard deviation derived from `sigma` and `C`;
6. RDP composition across released rounds followed by conversion to `(epsilon, delta)`.

Changing the sampling or aggregation assumptions requires a matching accountant change.

Sample-level DP protects a different neighboring relation and remains separately reported.

If multiple mechanisms protect the same neighboring relation and their outputs are released together, their RDP costs must be composed before reporting one overall client-level epsilon. `federated/privacy_budget.py::compose_same_adjacency_rdp` enforces this boundary.

## 3. SCAFFOLD privacy boundary

The root runtime keeps SCAFFOLD control variates in addition to the model update. The current client-level DP path does not cover that extra state/release channel.

Therefore:

- SCAFFOLD without DP is allowed;
- FedAvg and FedProx with client-level DP are allowed;
- DP-enabled SCAFFOLD fails before execution.

## 4. Privacy-budget calibration

Benchmark matrices should use target epsilon values rather than reusing a fixed noise multiplier after changing round count or client sample rate.

`scripts/calibrate_client_level_dp.py` solves for `sigma` at fixed `q`, rounds and `delta`.

When `dp.target_epsilon` is present, `main.py` recalibrates after runtime overrides and archives the resulting effective configuration.

## 5. Client partition evidence

Every executed partition must persist:

- dataset name;
- partition strategy and parameters;
- partition seed;
- exact client indices;
- client sample counts;
- per-client label histograms;
- realized heterogeneity metrics;
- exact partition hash.

The root runtime writes:

```text
results/partition/partition_indices.npz
results/partition/partition_manifest.json
```

Supported real-data partition strategies are:

- IID;
- Dirichlet label skew;
- pathological class restriction;
- quantity skew.

## 6. Repeated-run statistics

A benchmark condition requires at least five unique seeds by default.

The benchmark package provides:

- mean and sample standard deviation;
- deterministic percentile-bootstrap 95% confidence intervals;
- matched-seed differences;
- Cohen's dz effect size;
- paired sign-flip tests;
- Holm-Bonferroni correction across multiple comparisons.

Algorithm comparisons must use the same exact partition hash for each matched seed.

## 7. Reproducibility record

Every completed root run persists:

```text
_effective_runtime_config.yaml
partition/partition_indices.npz
partition/partition_manifest.json
client_distribution.csv
run_<algorithm>.csv
summary.md
summary.json
plots
```

Every benchmark cell also persists its generated config, cell descriptor and process log.

## 8. Security boundary

Secure aggregation hides protected individual update payloads from the coordinator in the implemented secure path. It does not, by itself, prevent poisoning, Byzantine behavior, dishonest clipping, Sybil clients or a fully compromised coordinator.

Security statements must match the concrete threat model and runtime path.

## 9. Benchmark release gate

A benchmark result is considered complete only when:

1. the executable path completes successfully;
2. its effective config is archived;
3. exact partition artifacts are archived;
4. raw per-round metrics are present;
5. privacy parameters match the actual runtime configuration;
6. unsupported privacy/algorithm combinations fail closed;
7. repeated-seed summaries meet the configured replicate minimum;
8. matched algorithm comparisons verify identical per-seed partition hashes.
