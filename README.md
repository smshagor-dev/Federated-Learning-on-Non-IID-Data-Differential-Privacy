# Federated Learning on Non-IID Data with Differential Privacy

A multi-language federated-learning platform for training and benchmarking models across heterogeneous client data with differential privacy, deterministic partitioning, held-out client evaluation, reproducible artifacts, and distributed-service components.

![C++20](https://img.shields.io/badge/C%2B%2B-20-00599C?style=for-the-badge&logo=cplusplus)
![Go](https://img.shields.io/badge/Go-Control%20Plane-00ADD8?style=for-the-badge&logo=go)
![Python](https://img.shields.io/badge/Python-PySide6%20Dashboard-3776AB?style=for-the-badge&logo=python)
![PyTorch](https://img.shields.io/badge/PyTorch-ML%20Runtime-EE4C2C?style=for-the-badge&logo=pytorch)
![CMake](https://img.shields.io/badge/CMake-Build%20System-064F8C?style=for-the-badge&logo=cmake)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=for-the-badge&logo=docker)

[![CI](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy?style=flat-square)](LICENSE)

![Federated Learning](https://img.shields.io/badge/Federated-Learning-2563EB?style=flat-square)
![Non-IID Data](https://img.shields.io/badge/Non--IID-Data%20Heterogeneity-7C3AED?style=flat-square)
![Differential Privacy](https://img.shields.io/badge/Differential-Privacy-0F766E?style=flat-square)
![Client-Level DP](https://img.shields.io/badge/Client--Level-Differential%20Privacy-059669?style=flat-square)
![RDP Accounting](https://img.shields.io/badge/RDP-Privacy%20Accounting-0891B2?style=flat-square)
![Secure Aggregation](https://img.shields.io/badge/Secure-Aggregation-334155?style=flat-square)
![Personalized FL](https://img.shields.io/badge/Personalized-Federated%20Learning-C026D3?style=flat-square)
![Distributed Systems](https://img.shields.io/badge/Distributed-Federated%20Runtime-4B5563?style=flat-square)
![gRPC](https://img.shields.io/badge/gRPC-Protocol%20Buffers-244C5A?style=flat-square)
![Experiment Tracking](https://img.shields.io/badge/Experiment-Reproducibility-0369A1?style=flat-square)

![FedAvg](https://img.shields.io/badge/FedAvg-Supported-16A34A?style=flat-square)
![FedProx](https://img.shields.io/badge/FedProx-Supported-16A34A?style=flat-square)
![SCAFFOLD](https://img.shields.io/badge/SCAFFOLD-Supported-16A34A?style=flat-square)
![Per-FedAvg](https://img.shields.io/badge/Per--FedAvg-Auxiliary-F59E0B?style=flat-square)
![FedSAM](https://img.shields.io/badge/FedSAM-Auxiliary-F59E0B?style=flat-square)
![Ditto](https://img.shields.io/badge/Ditto-Auxiliary-F59E0B?style=flat-square)

![MNIST](https://img.shields.io/badge/Dataset-MNIST-0284C7?style=flat-square)
![CIFAR-10](https://img.shields.io/badge/Dataset-CIFAR--10-0284C7?style=flat-square)
![Dirichlet Partition](https://img.shields.io/badge/Partition-Dirichlet%20Label%20Skew-EA580C?style=flat-square)
![Pathological Partition](https://img.shields.io/badge/Partition-Pathological%20Shards-EA580C?style=flat-square)
![Poisson Sampling](https://img.shields.io/badge/Sampling-Poisson-9333EA?style=flat-square)
![Fixed Sampling](https://img.shields.io/badge/Sampling-Fixed%20Without%20Replacement-9333EA?style=flat-square)

## Platform Overview

The repository contains two explicit execution paths:

1. **Root runtime** — single-machine federated orchestration for real torchvision datasets, heterogeneous client partitioning, FedAvg/FedProx/SCAFFOLD, client-level DP, held-out per-client evaluation, plots, manifests, checkpoints, and multi-seed benchmark matrices.
2. **Distributed platform** — Go API/control-plane services, a C++ coordinator, Python workers, gRPC/Protocol Buffers, persistence, security components, observability, and Docker Compose infrastructure.

The two paths are separate runtime identities. See [RUNTIME.md](RUNTIME.md) for the source-of-truth boundary.

## Root Runtime Capabilities

| Capability | Status |
|---|---|
| MNIST | Supported |
| FashionMNIST | Supported |
| CIFAR-10 | Supported |
| CIFAR-100 | Supported |
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
| Exact training partition archival | Supported |
| Final model checkpoints | Supported |
| Matched held-out client partition | Supported |
| Per-client accuracy/loss metrics | Supported |
| Worst-client and p10 metrics | Supported |
| Jain fairness metric | Supported |
| Machine-readable JSON summaries | Supported |
| Multi-seed benchmark matrix | Supported |
| Bootstrap confidence intervals | Supported |
| Matched-seed algorithm comparison | Supported |
| PySide6 desktop UI | Supported |

## Root Architecture

```text
main.py
  |
  +--> config + runtime validation
  |
  +--> experiment_runtime.py
  |      |
  |      +--> data/partitioner.py
  |      |      +--> MNIST / FashionMNIST / CIFAR-10 / CIFAR-100
  |      |      +--> IID / Dirichlet / pathological / quantity skew
  |      |
  |      +--> federated/client.py
  |      +--> federated/server.py
  |      |      +--> FedAvg / FedProx / SCAFFOLD
  |      |      +--> clipping / central Gaussian noise
  |      |
  |      +--> RDP accountant
  |      +--> per-round metrics + plots + training partition artifacts
  |
  +--> final global-model checkpoint
  |
  +--> matched held-out client partition
  |      +--> official test split only
  |      +--> label proportions derived from training clients
  |
  +--> per-client accuracy/loss + fairness metrics
  |
  +--> summary.md + summary.json
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
- CMake and a C++20 toolchain for native components
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

The desktop launches the same `main.py --cli --config ...` runtime used by terminal runs.

### CLI

```bash
python main.py --cli
```

### MNIST + FedAvg + IID

```bash
python main.py --cli \
  --dataset MNIST \
  --algo fedavg \
  --partition iid \
  --dp off
```

### FashionMNIST + FedProx + Dirichlet skew

```bash
python main.py --cli \
  --dataset FASHIONMNIST \
  --algo fedprox \
  --partition dirichlet \
  --alpha 0.1 \
  --dp off
```

### CIFAR-100 + quantity skew

```bash
python main.py --cli \
  --dataset CIFAR100 \
  --algo fedavg \
  --partition quantity_skew \
  --quantity-skew-sigma 1.5 \
  --dp off
```

### CIFAR-10 + FedProx + client-level DP

```bash
python main.py --cli \
  --dataset CIFAR10 \
  --algo fedprox \
  --partition dirichlet \
  --alpha 0.1 \
  --dp on \
  --rounds 50
```

## Datasets

The root runtime uses official torchvision train/test splits.

| Dataset | Train samples | Test samples | Classes | Input channels |
|---|---:|---:|---:|---:|
| MNIST | 60,000 | 10,000 | 10 | 1 |
| FashionMNIST | 60,000 | 10,000 | 10 | 1 |
| CIFAR-10 | 50,000 | 10,000 | 10 | 3 |
| CIFAR-100 | 50,000 | 10,000 | 100 | 3 |

MNIST and FashionMNIST are resized to `32x32`, allowing the same GroupNorm CNN family to be used across all four root datasets. The classifier output dimension is selected from the dataset class count.

## Training Client Partitioning

Training partitions are created only from the dataset training split.

### IID

All sample indices are shuffled with the configured seed and divided across clients.

### Dirichlet label skew

For every class, client proportions are sampled from a Dirichlet distribution.

```yaml
data:
  partition: dirichlet
  alpha: 0.1
```

Smaller `alpha` values produce stronger realized label concentration.

### Pathological class skew

Samples are ordered by label, divided into shards, and assigned so clients receive a restricted class subset.

```yaml
data:
  partition: pathological
  classes_per_client: 2
```

### Quantity skew

Client sample counts follow log-normal allocation weights while sample assignment remains label-agnostic.

```yaml
data:
  partition: quantity_skew
  quantity_skew_sigma: 1.0
```

Every strategy is deterministic for a fixed dataset/configuration/seed and enforces the configured minimum client size.

## Training Partition Artifacts

Every root CLI run writes the exact client assignment:

```text
results/partition/partition_indices.npz
results/partition/partition_manifest.json
```

The manifest includes:

- dataset name
- strategy and parameters
- partition seed
- exact partition SHA-256
- client sample counts
- per-client label histograms
- quantity coefficient of variation
- normalized label entropy
- Jensen-Shannon divergence to the global distribution
- class coverage
- effective label count

The concrete realized split is therefore retained independently of configuration labels such as `alpha=0.1`.

## Held-out Client Evaluation

Global test accuracy alone can hide large differences between clients. After training finishes, the root runtime evaluates the final global model separately for every client using only held-out test data.

### How the evaluation split is constructed

1. The official test split is loaded.
2. For each class, the runtime measures how that class was distributed across training clients.
3. Test examples of that class are allocated across clients using the same realized proportions.
4. Integer allocation is deterministic.
5. Every test example is assigned exactly once.
6. No test example is duplicated.
7. Every client receives at least one held-out sample when the test set is large enough; minimal deterministic redistribution is used only when proportional rounding would leave a client empty.

This produces a client-level held-out view that follows the concrete heterogeneity of the training population without reusing training examples.

### Client metrics

For each final global model the runtime reports:

- mean client accuracy
- weighted client accuracy
- median client accuracy
- p10 client accuracy
- worst-client accuracy
- best-client accuracy
- client-accuracy standard deviation
- client-accuracy range
- Jain accuracy index
- mean client loss
- weighted client loss
- p90 client loss
- worst-client loss

The weighted client accuracy is checked against the global test accuracy because the client evaluation partitions form an exact non-overlapping cover of the same official test set.

### Evaluation artifacts

```text
results/checkpoints/global_model_<algorithm>.pt
results/evaluation_partition/partition_indices.npz
results/evaluation_partition/partition_manifest.json
results/client_evaluation_<algorithm>.csv
```

`summary.json` embeds the aggregated client metrics and references the concrete artifacts.

## Federated Algorithms

### FedAvg

Selected clients train from the global model and return local model deltas. The server aggregates their contributions into the next global model.

### FedProx

FedProx adds a proximal term that limits local movement away from the current global model.

```yaml
algorithm:
  name: fedprox
  mu: 0.01
```

### SCAFFOLD

SCAFFOLD uses control variates to reduce client drift. It is available in the root runtime when client-level DP is disabled.

DP-enabled SCAFFOLD fails before execution because the current client-level privacy mechanism does not cover the additional control-variate state/release path.

## Differential Privacy

The root private path uses trusted-server client-level central DP.

For each released communication round:

1. selected client updates are computed;
2. complete client updates are clipped to a global L2 bound `C`;
3. clipped contributions are aggregated;
4. central Gaussian noise is applied;
5. RDP is accumulated across rounds;
6. RDP is converted to `(epsilon, delta)`.

### Target epsilon

```yaml
dp:
  enabled: true
  update_clip_norm: 1.5
  target_epsilon: 4.0
  target_delta: 1.0e-5
```

`main.py` recalibrates the Gaussian noise multiplier after runtime overrides are applied. Changing the number of rounds therefore does not silently reuse a sigma calibrated for a different release count.

The actual runtime parameters are archived in:

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

When `--noise` is explicitly supplied, the effective configuration clears `target_epsilon` so the output does not imply that a target budget was enforced.

## Benchmark Matrix

The benchmark runner invokes the actual root runtime in a separate process for each benchmark cell.

### Plan only

```bash
python scripts/run_benchmark_matrix.py --dry-run
```

### Execute

```bash
python scripts/run_benchmark_matrix.py \
  --benchmark-id multi-dataset-baseline \
  --datasets MNIST,FASHIONMNIST,CIFAR10,CIFAR100 \
  --algorithms fedavg,fedprox \
  --partitions iid,dirichlet:0.1,quantity_skew:1.0 \
  --epsilons none,2,4,8 \
  --seeds 11,23,37,53,71 \
  --rounds 50 \
  --resume
```

A benchmark condition requires at least five unique seeds by default.

The aggregation layer provides:

- mean and sample standard deviation
- median/min/max
- deterministic percentile-bootstrap confidence intervals
- matched-seed differences
- Cohen's `dz`
- paired sign-flip tests
- Holm-Bonferroni adjusted p-values
- exact per-seed training-partition hash verification

Benchmark observations include global utility, runtime cost, privacy values, client drift, clipping behavior, and held-out client tail/fairness metrics.

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
            ├── evaluation_partition/
            │   ├── partition_indices.npz
            │   └── partition_manifest.json
            ├── checkpoints/
            │   └── global_model_<algorithm>.pt
            ├── client_distribution.csv
            ├── client_evaluation_<algorithm>.csv
            ├── run_<algorithm>.csv
            ├── summary.md
            └── summary.json
```

## Root Run Artifacts

```text
results/
├── _effective_runtime_config.yaml
├── partition/
│   ├── partition_indices.npz
│   └── partition_manifest.json
├── evaluation_partition/
│   ├── partition_indices.npz
│   └── partition_manifest.json
├── checkpoints/
│   └── global_model_<algorithm>.pt
├── client_distribution.csv
├── client_evaluation_<algorithm>.csv
├── distribution.png
├── run_<algorithm>.csv
├── summary.md
├── summary.json
└── generated plots
```

`summary.json` is the machine-readable interface consumed by the benchmark runner.

## Configuration

Main configuration: [config.yaml](config.yaml)

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

evaluation:
  eval_batch_size: 256
```

## Distributed Platform

Start the development stack with:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

The stack contains:

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

Distributed-service capabilities remain separate from the single-machine root runtime unless explicit parity validation exists.

## Validation

### Root + Python tests

```bash
python -m pytest tests python/tests
```

### Ruff

```bash
python -m ruff check .
python -m ruff format --check .
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

GitHub Actions also runs native builds, formatting/type checks, sanitizers, protobuf validation, PKI verification, secret scanning, infrastructure validation, and security-runtime checks.

## Privacy and Security Boundaries

Differential privacy guarantees depend on the exact adjacency definition, client-sampling model, clipping bound, noise multiplier, number of releases, weighting assumptions, and accountant implementation.

Secure aggregation is not, by itself, a defense against:

- model poisoning
- Byzantine behavior
- dishonest clipping
- Sybil clients
- a fully compromised coordinator

The project does not claim formal security certification, regulatory compliance, or validated Internet-scale deployment.

See [docs/runtime-correctness.md](docs/runtime-correctness.md) for enforced runtime and benchmark boundaries.

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
│   ├── client_evaluation.py
│   ├── metrics.py
│   └── partition_metrics.py
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
- IID, label skew, class skew, and quantity skew are implemented in the root runtime; feature/covariate shift is not yet a root partition strategy.
- The desktop exposes the expanded dataset and partition choices; advanced quantity-skew tuning remains available through YAML/CLI configuration.
- Secure aggregation and additional personalization algorithms use separate platform paths.
- Production-scale orchestration and external security certification are not claimed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidance and [SECURITY.md](SECURITY.md) for security reporting.

## License

Copyright (c) 2026 Md Shahanur Islam Shagor.

Licensed under the Apache License 2.0. See [LICENSE](LICENSE).
