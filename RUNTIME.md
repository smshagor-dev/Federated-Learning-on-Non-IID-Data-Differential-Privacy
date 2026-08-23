# Runtime Source of Truth

This file defines the executable runtime boundaries for the repository and prevents documentation from drifting away from actual behavior.

## Root runtime

The root command:

```bash
python main.py
```

runs the single-machine federated-learning workflow defined by:

- `main.py`
- `experiment_runtime.py`
- `federated/`
- `data/`
- `models/`
- `desktop/`

It supports real MNIST and CIFAR-10 datasets, deterministic client partitioning, FedAvg/FedProx/SCAFFOLD execution, central client-level DP for the supported algorithms, round metrics, exact partition manifests, and machine-readable result summaries.

For terminal-only execution:

```bash
python main.py --cli
```

## Distributed platform runtime

The multi-service platform is a separate runtime:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

Its topology is:

```text
Web -> Go API -> C++ coordinator -> Python worker(s)
            \-> Python experiment command writer
```

PostgreSQL, Redis, MinIO, MLflow, Prometheus, Grafana and OpenTelemetry development services are supplied by the Compose stack.

The two runtimes share algorithms, privacy semantics and dataset concepts, but they are not interchangeable execution paths. A feature implemented in one path must not be reported as active in the other unless parity/integration validation exists.

## Capability rule

A capability is **implemented** only when executable source exists. It is **validated** only when there is execution evidence appropriate to the claimed scope. Configuration, documentation, a test file, or CI YAML alone is not runtime evidence.

Every benchmark result must identify which runtime produced it:

- `root-simulator`
- `distributed-platform`

and retain the exact commit SHA, effective configuration, dataset/partition identity, random seed, partition hash and privacy parameters.

## Privacy boundary

The root runtime supports client-level central DP for FedAvg and FedProx under Poisson client sampling and uniform client weighting.

DP-enabled SCAFFOLD is intentionally fail-closed. The control-variate state/release path is not included in the current client-level privacy guarantee. Non-private SCAFFOLD remains available.

Sample-level DP, client-level DP and private adaptive statistics must retain independent ledgers. When multiple releases protect the same neighboring relation and are reported under one client-level guarantee, their RDP costs must be composed. Mechanisms with different neighboring relations remain separately reported.

## Target-epsilon execution

When `dp.target_epsilon` is configured, `python main.py` recalibrates the Gaussian noise multiplier after runtime overrides are applied. This prevents a sigma calibrated for one round count or sample rate from being reused while still claiming the same privacy budget.

The effective runtime configuration records the final sigma, achieved epsilon and parameter source.

An explicit CLI `--noise` value is a manual override. In that case the effective runtime config clears `target_epsilon` so the system does not imply that a target budget was enforced.

Standalone calibration:

```bash
python scripts/calibrate_client_level_dp.py \
  --target-epsilon 4 \
  --sample-rate 0.2 \
  --rounds 50 \
  --delta 1e-5
```

## Benchmark execution

The real multi-seed runner is:

```bash
python scripts/run_benchmark_matrix.py --dry-run
```

Remove `--dry-run` to execute the matrix. Every cell launches the root runtime in a fresh process and writes its own config, logs, exact partition indices, partition manifest, round CSV files and `summary.json`.

## Precedence

If another document conflicts with this file, executable code and this runtime contract take precedence until the stale document is corrected.
