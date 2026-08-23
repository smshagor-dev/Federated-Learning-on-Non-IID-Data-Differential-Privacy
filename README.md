# Federated Learning on Non-IID Data with Differential Privacy

A multi-language federated-learning platform for training and benchmarking models across heterogeneous client data with differential privacy, deterministic partitioning, reproducible artifacts, and distributed-service components.

![Python](https://img.shields.io/badge/Python-PyTorch-3776AB?style=for-the-badge&logo=python)
![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C?style=for-the-badge&logo=cplusplus)
![Go](https://img.shields.io/badge/Go-Control%20Plane-00ADD8?style=for-the-badge&logo=go)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker)
![gRPC](https://img.shields.io/badge/gRPC-Protocol%20Buffers-244C5A?style=for-the-badge)

[![CI](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy?style=flat-square)](LICENSE)

## What the Project Provides

The repository contains two explicit execution paths:

1. **Root runtime** — a single-machine federated-learning workflow for real MNIST/CIFAR-10 training, client partitioning, FedAvg/FedProx/SCAFFOLD, differential privacy, metrics, plots, manifests, and repeatable benchmark runs.
2. **Distributed platform** — Go API/control-plane services, a C++ coordinator, Python workers, gRPC/Protocol Buffers, security components, persistence, observability, and Docker Compose infrastructure.

The two paths share concepts but are not treated as identical. See [RUNTIME.md](RUNTIME.md) for the exact runtime boundary.

### Root runtime capabilities

| Capability | Status |
|---|---|
| MNIST | Supported |
| CIFAR-10 | Supported |
| IID partition | Supported |
| Dirichlet label skew | Supported |
| Pathological class skew | Supported |
| Quantity skew | Supported |
| FedAvg | Supported |
| FedProx | Supported |
| SCAFFOLD | Supported without client-level DP |
| Client-level central DP | Supported for FedAvg/FedProx |
| Target-epsilon noise calibration | Supported |
| RDP accounting | Supported |
| Poisson client sampling | Supported |
| Fixed-size client sampling | Supported when DP is disabled |
| Exact partition index archival | Supported |
| Per-round CSV metrics | Supported |
| Machine-readable JSON summaries | Supported |
| Multi-seed benchmark matrix | Supported |
| Bootstrap confidence intervals | Supported |
| Matched-seed algorithm comparison | Supported |
| PySide6 desktop UI | Supported |

## Architecture

```text
                         +----------------------+
                         |      main.py         |
                         +----------+-----------+
                                    |
                         +----------v-----------+
                         | experiment_runtime.py|
                         +----+-------------+---+
                              |             |
                    +---------v--+       +--v----------------+
                    | data/      |       | federated/        |
                    | partitioner|       | client + server   |
                    +------+-----+       +--------+----------+
                           |                      |
                    +------v------+       +-------v-----------+
                    | MNIST /     |       | FedAvg / FedProx  |
                    | CIFAR-10    |       | / SCAFFOLD        |
                    +-------------+       +-------+-----------+
                                                  |
                                         +--------v-----------+
                                         | clipping + DP +    |
                                         | RDP accounting     |
                                         +--------+-----------+
                                                  |
                                         +--------v-----------+
                                         | metrics / manifests|
                                         | CSV / JSON / plots |
                                         +--------------------+
```

Distributed path:

```text
Web UI
  |
  v
Go API / control plane
  |
  +--------------------------+
  |                          |
  v                          v
C++ gRPC coordinator   Python command service
  |
  v
Python worker(s)

Supporting services: PostgreSQL, Redis, MinIO, MLflow,
Prometheus, Grafana, OpenTelemetry
```

## Installation

### Requirements

- Python 3.11+
- Git
- PyTorch-compatible CPU or GPU environment
- Docker Desktop / Docker Engine for the distributed stack
- CMake and C++20 toolchain when building native components directly
- Go toolchain for direct control-plane development

### Clone

```bash
git clone https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy.git
cd Federated-Learning-on-Non-IID-Data-Differential-Privacy
```

### Python dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Run the Root Runtime

### Desktop UI

```bash
python main.py
```

### CLI

```bash
python main.py --cli
```

### Example: FedAvg on MNIST without DP

```bash
python main.py --cli \
  --dataset MNIST \
  --algo fedavg \
  --partition iid \
  --dp off
```

### Example: FedProx with Dirichlet label skew and DP

```bash
python main.py --cli \
  --dataset CIFAR10 \
  --algo fedprox \
  --partition dirichlet \
  --alpha 0.1 \
  --dp on \
  --rounds 50
```

### Example: quantity skew

```bash
python main.py --cli \
  --dataset MNIST \
  --algo fedavg \
  --partition quantity_skew \
  --quantity-skew-sigma 1.5 \
  --dp off
```

### Example: pathological class skew

```bash
python main.py --cli \
  --dataset CIFAR10 \
  --algo fedprox \
  --partition pathological \
  --classes-per-client 2 \
  --dp off
```

## Data Partitioning

The root runtime partitions the actual training split loaded by torchvision.

### IID

All sample indices are shuffled with the configured seed and divided across clients.

### Dirichlet label skew

For every class, client proportions are drawn from a Dirichlet distribution.

Smaller `alpha` values create stronger label concentration. For example:

```yaml
data:
  partition: dirichlet
  alpha: 0.1
```

### Pathological class skew

Samples are sorted by label, divided into shards, and assigned so each client receives a small number of class-dominated shards.

```yaml
data:
  partition: pathological
  classes_per_client: 2
```

### Quantity skew

Client sizes are drawn from log-normal weights while sample assignment remains label-agnostic.

```yaml
data:
  partition: quantity_skew
  quantity_skew_sigma: 1.0
```

Every strategy enforces `min_partition_size` and produces deterministic client assignments for a fixed dataset and seed.

## Exact Partition Artifacts

Every CLI run writes the concrete partition used by training:

```text
results/partition/partition_indices.npz
results/partition/partition_manifest.json
```

The manifest includes:

- dataset name
- partition strategy
- partition parameters
- partition seed
- exact partition SHA-256
- client sample counts
- per-client label histograms
- realized quantity skew
- normalized label entropy
- Jensen-Shannon divergence to the global distribution
- class coverage
- effective label count

This means a configured value such as `alpha=0.1` is not the only evidence retained; the actual realized split is also recorded.

## Federated Algorithms

### FedAvg

Selected clients train from the global model and return local model deltas. The server aggregates the updates into the next global model.

### FedProx

FedProx adds a proximal term that penalizes local movement away from the current global model. The coefficient is configured through:

```yaml
algorithm:
  name: fedprox
  mu: 0.01
```

### SCAFFOLD

SCAFFOLD uses control variates to reduce client drift. It is available in the root runtime when client-level DP is disabled.

DP-enabled SCAFFOLD fails before execution because the current client-level privacy guarantee does not cover the additional control-variate state/release path.

## Differential Privacy

The root private path uses trusted-server client-level central DP.

For each selected client update:

1. compute the complete update delta;
2. apply a global L2 clipping bound `C`;
3. aggregate clipped client contributions;
4. add central Gaussian noise;
5. account privacy loss across released rounds with RDP;
6. convert the accumulated RDP value to `(epsilon, delta)`.

### Target epsilon

The default configuration can specify a privacy target:

```yaml
dp:
  enabled: true
  update_clip_norm: 1.5
  target_epsilon: 4.0
  target_delta: 1.0e-5
```

`main.py` recalibrates the Gaussian noise multiplier after runtime overrides are applied. Changing `--rounds` therefore does not silently reuse a noise multiplier calibrated for a different number of releases.

The final effective values are archived in:

```text
results/_effective_runtime_config.yaml
```

### Standalone calibration

```bash
python scripts/calibrate_client_level_dp.py \
  --target-epsilon 4 \
  --sample-rate 0.2 \
  --rounds 50 \
  --delta 1e-5
```

### Manual noise override

```bash
python main.py --cli --noise 3.0
```

When `--noise` is provided explicitly, the effective configuration clears `target_epsilon`. This prevents the output from implying that the target budget was enforced when the user manually selected sigma.

## Benchmark Matrix

The repository includes a real multi-process benchmark runner. It does not fabricate benchmark values; each cell invokes `main.py --cli` and consumes the resulting `summary.json`.

### Inspect a benchmark plan

```bash
python scripts/run_benchmark_matrix.py --dry-run
```

### Execute a benchmark

```bash
python scripts/run_benchmark_matrix.py \
  --benchmark-id mnist-fedavg-fedprox \
  --datasets MNIST \
  --algorithms fedavg,fedprox \
  --partitions iid,dirichlet:0.1,quantity_skew:1.0 \
  --epsilons none,2,4,8 \
  --seeds 11,23,37,53,71 \
  --rounds 50 \
  --resume
```

Each benchmark condition requires at least five unique seeds by default.

The aggregation layer provides:

- mean
- sample standard deviation
- median/min/max
- deterministic percentile-bootstrap confidence intervals
- matched-seed differences
- Cohen's `dz`
- paired sign-flip tests
- Holm-Bonferroni adjusted p-values
- exact per-seed partition-hash matching before algorithm comparison

### Benchmark output

```text
benchmarks/runs/<benchmark-id>/
├── plan.json
├── status.json
├── observations.json
├── summary.json
├── summary.csv
├── comparisons.json
├── comparisons.csv
└── cells/
    └── <cell-id>/
        ├── cell.json
        ├── config.yaml
        ├── run.log
        └── results/
            ├── _effective_runtime_config.yaml
            ├── partition/
            │   ├── partition_indices.npz
            │   └── partition_manifest.json
            ├── client_distribution.csv
            ├── run_<algorithm>.csv
            ├── summary.md
            ├── summary.json
            └── plots
```

## Root Run Artifacts

A normal CLI execution writes:

```text
results/
├── _effective_runtime_config.yaml
├── partition/
│   ├── partition_indices.npz
│   └── partition_manifest.json
├── client_distribution.csv
├── distribution.png
├── run_<algorithm>.csv
├── summary.md
├── summary.json
└── generated plots
```

`summary.json` is the machine-readable interface used by the benchmark runner.

## Metrics

The root runtime tracks metrics such as:

- global test accuracy
- global test loss
- cohort size
- participation rate
- average client loss
- raw client drift
- clipped client drift
- mean unclipped update norm
- mean clipping factor
- fraction of clients clipped
- aggregate noise norm
- client-model weight variance
- current epsilon for private runs
- wall-clock duration

## Configuration

The main configuration file is [config.yaml](config.yaml).

Key sections:

```yaml
system:
  seed: 42
  device: auto
  results_dir: results

data:
  dataset: CIFAR10
  partition: dirichlet
  alpha: 0.1
  classes_per_client: 2
  quantity_skew_sigma: 1.0
  min_partition_size: 10

federated:
  num_clients: 20
  sample_rate: 0.2
  sampling_strategy: poisson
  aggregation_weighting: uniform
  rounds: 50
  local_epochs: 2
  batch_size: 64
  server_lr: 1.0

algorithm:
  name: fedprox
  mu: 0.01

dp:
  enabled: true
  update_clip_norm: 1.5
  target_epsilon: 4.0
  target_delta: 1.0e-5
```

## Distributed Platform

Start the development stack with:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

The stack contains project services and supporting infrastructure including:

- C++ gRPC coordinator
- Go API/control plane
- Python worker
- Python command service
- web application
- PostgreSQL
- Redis
- MinIO
- MLflow
- Prometheus
- Grafana
- OpenTelemetry Collector

The distributed path includes additional algorithm, privacy, security, identity, event, registry, and secure-aggregation components that are separate from the root runtime.

## Validation

### Root tests

```bash
python -m unittest discover -s tests -p "test_*.py"
```

### Python package tests

```bash
python -m pytest python/tests -q
```

### Go

```bash
cd go
go test ./...
go vet ./...
go build ./...
```

### C++

```bash
cmake -S cpp -B build/cpp
cmake --build build/cpp
ctest --test-dir build/cpp --output-on-failure
```

### Documentation/runtime validation

```bash
python scripts/validate_repository_docs.py
```

The GitHub Actions workflow also runs linting, formatting, type checks, native builds, sanitizers, protobuf checks, PKI verification, secret scanning, infrastructure validation, and security-runtime checks.

## Privacy and Security Boundaries

Differential privacy guarantees depend on the exact adjacency definition, sampling model, clipping bound, noise multiplier, number of releases, weighting assumptions, and accountant implementation.

Secure aggregation is not a general defense against malicious participants. In particular, it does not automatically prevent:

- model poisoning
- Byzantine behavior
- dishonest clipping
- Sybil clients
- a fully compromised coordinator

The project does not claim formal security certification, regulatory compliance, or a validated Internet-scale deployment.

See [docs/runtime-correctness.md](docs/runtime-correctness.md) for the enforced execution and benchmark boundaries.

## Project Layout

```text
.
├── main.py
├── experiment_runtime.py
├── config.yaml
├── data/
├── federated/
├── models/
├── utils/
├── desktop/
├── python/
│   └── src/fl_platform/
│       └── benchmark/
├── cpp/
├── go/
├── proto/
├── web/
├── infra/
├── scripts/
│   ├── calibrate_client_level_dp.py
│   └── run_benchmark_matrix.py
├── tests/
└── docs/
```

## Current Limitations

- The root runtime is single-machine orchestration rather than a real cross-device network deployment.
- Root client-level DP currently supports FedAvg and FedProx, not SCAFFOLD.
- The real root dataset set is currently MNIST and CIFAR-10.
- IID, label skew, class skew and quantity skew are implemented in the root runtime; feature/covariate shift is not yet a root partition strategy.
- Secure aggregation and additional personalization algorithms use separate platform paths.
- Production-scale orchestration and external security certification are not claimed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [SECURITY.md](SECURITY.md) for security reporting.

## License

Copyright (c) 2026 Md Shahanur Islam Shagor.

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
