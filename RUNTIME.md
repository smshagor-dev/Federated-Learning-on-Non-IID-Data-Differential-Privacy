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
- `utils/`
- `desktop/`

The root runtime supports real torchvision training on:

- MNIST
- FashionMNIST
- CIFAR-10
- CIFAR-100

It provides deterministic IID, Dirichlet label-skew, pathological class-skew, and quantity-skew client partitions; FedAvg/FedProx/SCAFFOLD execution; central client-level DP for the supported algorithms; exact partition manifests; final model checkpoints; global test metrics; matched held-out per-client evaluation; and machine-readable result summaries.

For terminal-only execution:

```bash
python main.py --cli
```

## Held-out client evaluation

After the final communication round, the root CLI persists the final global model once and builds a held-out client partition from the official dataset test split.

For each class, test samples are assigned across clients according to the label proportions realized in the training partition. The evaluation split therefore reflects the concrete client heterogeneity used for training without evaluating on training examples.

The runtime writes:

```text
results/checkpoints/global_model_<algorithm>.pt
results/evaluation_partition/partition_indices.npz
results/evaluation_partition/partition_manifest.json
results/client_evaluation_<algorithm>.csv
```

`summary.json` also contains client-level accuracy/loss summaries including mean, p10, worst-client, dispersion, and Jain fairness metrics.

The weighted held-out client accuracy is validated against the global test accuracy because the held-out client partitions form an exact, non-overlapping cover of the official test set.

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

## Execution control plane

The Go API exposes the durable execution lifecycle under `/api/v1/executions`. Canonical execution records keep backend identity, immutable specification hash, backend run ID, current round, model version, worker counts, timestamps, revision and lifecycle status.

At process startup, configured backends receive a recovery reconciliation pass. This pass is allowed to reconcile transitional records because the previous control-plane process may have stopped in the middle of a lifecycle operation.

After startup, the control plane runs automatic runtime reconciliation every two seconds by default. The interval can be changed with a positive Go duration:

```bash
FL_EXECUTION_RECONCILE_INTERVAL=5s
```

Periodic reconciliation intentionally skips `STARTING`, `PAUSING`, `RESUMING` and `CANCELING` records. This prevents a background backend snapshot from overwriting a lifecycle request that is actively changing state. Stable executions such as `RUNNING` and `PAUSED` are refreshed automatically, so backend completion and round/worker/model changes do not depend on a client manually requesting `?refresh=true`.

A temporary reconciliation failure on one configured backend does not stop reconciliation of other backends. Per-execution and per-backend failures are logged and retried on later cycles.

For the local backend, Pause is checkpoint-safe at communication-round boundaries. The canonical execution process writes a deterministic runtime checkpoint and PAUSED marker before exiting with its dedicated pause status. Resume validates the checkpoint evidence and relaunches from that exact round state. Checkpoint bytes are verified against their SHA-256 sidecar before restore and before the Go control plane accepts PAUSED/RESUME evidence. SHA-256 detects changed/corrupted bytes; it is not a keyed authenticity mechanism against an actor who can rewrite both files.

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

The multi-seed runner is:

```bash
python scripts/run_benchmark_matrix.py --dry-run
```

Remove `--dry-run` to execute the matrix. Every cell launches the root runtime in a fresh process and writes its own config, logs, training partition, held-out evaluation partition, final model checkpoint, per-client evaluation CSV, round CSV files and `summary.json`.

Benchmark observations include global metrics and held-out client metrics, so algorithm comparisons can measure average utility and client-level tail performance under the same concrete data split.

## Precedence

If another document conflicts with this file, executable code and this runtime contract take precedence until the stale document is corrected.
