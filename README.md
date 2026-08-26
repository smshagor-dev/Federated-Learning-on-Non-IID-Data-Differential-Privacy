# Federated Learning on Non-IID Data with Differential Privacy

A reproducible federated-learning platform for studying **data heterogeneity, privacy, robustness, secure aggregation, distributed execution, fairness, and release-grade experiment evidence**.

This repository contains two explicit runtime identities:

1. a practical **root research runtime** for controlled PyTorch experiments; and
2. a separate **distributed platform runtime** built around Python, C++20, Go, gRPC, Docker, persistence, service security, observability, and release qualification.

The project deliberately keeps implementation claims narrow. A capability is described as supported only when the corresponding executable path and validation evidence exist.

[![CI](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-ML%20Runtime-EE4C2C?logo=pytorch&logoColor=white)
![C++](https://img.shields.io/badge/C%2B%2B-20-00599C?logo=cplusplus&logoColor=white)
![Go](https://img.shields.io/badge/Go-Control%20Plane-00ADD8?logo=go&logoColor=white)
![gRPC](https://img.shields.io/badge/gRPC-Protocol%20Buffers-244C5A)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/github/license/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy)

**Source version:** `3.0.0`  
**Primary themes:** Federated Learning · Non-IID Data · Differential Privacy · Robust Aggregation · Secure Aggregation · Fairness · Reproducible Benchmarking · Distributed ML Systems

> **Math rendering note:** all academic equations in this README use renderer-independent Unicode notation inside plain text blocks. No MathJax or LaTeX renderer is required, so the formulas remain readable on GitHub web, mobile, cloned Markdown viewers, and plain-text environments.

---

## Contents

- [1. Project overview](#1-project-overview)
- [2. Runtime model](#2-runtime-model)
- [3. Capability map](#3-capability-map)
- [4. Mathematical formulation](#4-mathematical-formulation)
- [5. Federated algorithms](#5-federated-algorithms)
- [6. Non-IID data modeling](#6-non-iid-data-modeling)
- [7. Differential privacy](#7-differential-privacy)
- [8. Secure and robust aggregation](#8-secure-and-robust-aggregation)
- [9. Client-level evaluation and fairness](#9-client-level-evaluation-and-fairness)
- [10. Benchmark statistics](#10-benchmark-statistics)
- [11. Architecture](#11-architecture)
- [12. Installation](#12-installation)
- [13. Running experiments](#13-running-experiments)
- [14. Configuration](#14-configuration)
- [15. Artifacts and reproducibility](#15-artifacts-and-reproducibility)
- [16. Distributed platform](#16-distributed-platform)
- [17. Developer workflow](#17-developer-workflow)
- [18. Validation and CI](#18-validation-and-ci)
- [19. v3.0.0 release qualification](#19-v300-release-qualification)
- [20. Security and threat-model boundaries](#20-security-and-threat-model-boundaries)
- [21. Known limitations](#21-known-limitations)
- [22. Repository layout](#22-repository-layout)
- [23. Academic reporting and citation](#23-academic-reporting-and-citation)
- [24. Author and maintainer](#24-author-and-maintainer)
- [25. Contributing and license](#25-contributing-and-license)

---

## 1. Project overview

Federated learning is often summarized as “train locally, aggregate globally.” That description is useful, but incomplete. Real federated systems become difficult when clients have different label distributions, different amounts of data, different local compute budgets, different availability patterns, and different privacy or trust requirements.

This repository is designed to make those differences explicit and measurable.

The project is useful for four classes of questions:

1. **Utility** — how well does a shared model learn under heterogeneous client data?
2. **Privacy** — what utility is lost when client updates are clipped and randomized under a defined privacy mechanism?
3. **Reliability and security** — what happens when workers are delayed, duplicated, unavailable, replayed, dropped, or adversarial?
4. **Reproducibility** — can every reported result be tied to an exact configuration, seed, partition, commit, privacy budget, and artifact set?

The repository is not one monolithic simulator. It intentionally contains two runtime identities with different scopes.

---

## 2. Runtime model

### 2.1 Root research runtime

The root runtime is the shortest path from an experiment idea to a reproducible result:

```bash
python main.py --cli
```

It performs real PyTorch training on torchvision datasets and provides deterministic client partitioning, FedAvg/FedProx/SCAFFOLD, supported client-level differential privacy, held-out client evaluation, fairness metrics, checkpoints, manifests, plots, and multi-seed benchmark execution.

Primary areas:

```text
main.py
experiment_runtime.py
config.yaml
data/
federated/
models/
utils/
desktop/
```

### 2.2 Distributed platform runtime

The distributed platform is a separate multi-service runtime:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

It combines a Go control plane, C++ coordinator, Python workers, protobuf/gRPC contracts, persistence, service security, observability, heterogeneity/failure simulation, and release qualification infrastructure.

Primary areas:

```text
cpp/
go/
python/
proto/
infra/
scripts/
release/
```

> A capability implemented in one runtime must not be assumed to exist in the other. [`RUNTIME.md`](RUNTIME.md) is the source-of-truth document for runtime boundaries.

---

## 3. Capability map

### 3.1 Root runtime

| Area | Capability | Status |
|---|---|---|
| Dataset | MNIST | Supported |
| Dataset | FashionMNIST | Supported |
| Dataset | CIFAR-10 | Supported |
| Dataset | CIFAR-100 | Supported |
| Partitioning | IID | Supported |
| Partitioning | Dirichlet label skew | Supported |
| Partitioning | Pathological class skew | Supported |
| Partitioning | Quantity skew | Supported |
| Algorithm | FedAvg | Supported |
| Algorithm | FedProx | Supported |
| Algorithm | SCAFFOLD | Supported without root client-level DP |
| Privacy | Client-level central DP | Supported for qualified FedAvg/FedProx paths |
| Privacy | Target-epsilon calibration | Supported |
| Privacy | RDP accounting | Supported |
| Sampling | Poisson client sampling | Supported |
| Sampling | Fixed-size client sampling | Supported when DP is disabled |
| Evaluation | Global test evaluation | Supported |
| Evaluation | Held-out per-client evaluation | Supported |
| Fairness | p10 / worst-client / Jain metrics | Supported |
| Reproducibility | Exact partition manifests | Supported |
| Reproducibility | Final model checkpoints | Supported |
| Reproducibility | Machine-readable summaries | Supported |
| Benchmarking | Multi-seed matrix | Supported |
| Statistics | Bootstrap confidence intervals | Supported |
| Statistics | Matched-seed comparisons | Supported |
| UI | PySide6 desktop interface | Supported |

### 3.2 v3 distributed/worker support

The v3 platform contains release-qualified or explicitly bounded support for:

- FedAvg
- FedProx
- SCAFFOLD
- FedSAM
- Ditto
- Per-FedAvg
- median and trimmed-mean robust aggregation for supported non-private synchronous paths
- deterministic compute/network/availability/payload heterogeneity simulation
- mTLS identity
- signed-message verification
- replay protection
- privacy/accounting validation
- distributed metrics and observability primitives
- ARM64 worker-image build/self-test compatibility
- immutable image locks
- SBOM generation
- artifact hashing and provenance attestations

Advanced code outside the stable release contract is listed explicitly under [Known limitations](#21-known-limitations).

---

## 4. Mathematical formulation

The equations below are written in renderer-independent notation. Subscripts use `_`, superscripts are written descriptively, and Greek symbols use Unicode.

### 4.1 Client datasets

Assume there are `K` clients. Client `k` owns dataset `D_k` with `n_k` examples.

```text
D_k = {(x_i, y_i) : i = 1, ..., n_k}

n_k = |D_k|
```

For model parameters `w`, the local empirical objective is:

```text
F_k(w) = (1 / n_k) · Σ_(x,y ∈ D_k) ℓ(w; x, y)
```

Here `ℓ` is the task loss.

### 4.2 Global federated objective

The global objective combines local client objectives using non-negative client weights.

```text
minimize_w  F(w)

F(w) = Σ_(k=1..K) p_k · F_k(w)

p_k ≥ 0
Σ_(k=1..K) p_k = 1
```

Sample-count weighting:

```text
p_k = n_k / Σ_(j=1..K) n_j
```

Uniform client weighting:

```text
p_k = 1 / K
```

The runtime configuration decides which weighting rule is valid for a specific experiment. This is also relevant to privacy because weighting changes the sensitivity of the released aggregate.

### 4.3 One communication round

At communication round `t`:

1. the server holds global parameters `w_t`;
2. a client subset `S_t` is selected;
3. each selected client trains from `w_t`;
4. client `k` returns update `Δ_(k,t)`;
5. the server validates, optionally clips/transforms, aggregates, and applies the update;
6. the next model `w_(t+1)` is produced.

A generic server update is:

```text
w_(t+1) = w_t + η_s · A_t
```

where `η_s` is the server step size and `A_t` is the accepted aggregate update.

---

## 5. Federated algorithms

### 5.1 FedAvg

A local SGD step for client `k` can be written as:

```text
w_(k,t,e+1) = w_(k,t,e) - η_k · ∇ℓ_k(w_(k,t,e); B_(k,e))
```

After local training:

```text
Δ_(k,t) = w_(k,t,local) - w_t
```

For normalized aggregation weights `α_(k,t)`:

```text
Σ_(k ∈ S_t) α_(k,t) = 1

A_t = Σ_(k ∈ S_t) α_(k,t) · Δ_(k,t)

w_(t+1) = w_t + η_s · A_t
```

With `η_s = 1`, this is equivalent to weighted averaging of the accepted local models.

### 5.2 FedProx

FedProx adds a proximal penalty that discourages excessive local movement away from the current global model.

```text
Local objective:
F_k(w) + (μ / 2) · ||w - w_t||²

Gradient contribution:
∇F_k(w) + μ · (w - w_t)
```

`μ = 0` reduces the local objective to the ordinary FedAvg-style objective.

Example:

```yaml
algorithm:
  name: fedprox
  mu: 0.01
```

### 5.3 SCAFFOLD

SCAFFOLD uses a server control variate `c` and a client control variate `c_k` to reduce drift under heterogeneous local objectives.

```text
w ← w - η · [∇F_k(w) - c_k + c]
```

In the root runtime, SCAFFOLD is available only when root client-level DP is disabled. The additional control-variate state is outside the current root client-level privacy guarantee, so DP-enabled SCAFFOLD fails closed.

### 5.4 FedSAM

FedSAM uses sharpness-aware local optimization.

```text
g = ∇F_k(w)

ε = ρ · g / (||g||₂ + τ)
```

The gradient is then evaluated around the perturbed parameters `w + ε`. FedSAM belongs to the platform-worker capability surface rather than the root CLI algorithm set.

### 5.5 Ditto

Ditto maintains a personalized model `v_k` for each client while retaining a shared global model `w`.

```text
Personalized objective:
F_k(v_k) + (λ / 2) · ||v_k - w||²
```

`λ` controls how strongly the personalized model is pulled toward the shared model.

### 5.6 Per-FedAvg

Per-FedAvg optimizes a shared initialization that can adapt quickly to a client.

```text
One local adaptation step:
w'_k = w - α · ∇F_k(w)
```

The outer objective evaluates the adapted model `w'_k`. Per-FedAvg is part of the platform-worker capability surface rather than the root CLI algorithm set.

---

## 6. Non-IID data modeling

Client data are non-IID when at least two clients have different joint data distributions.

```text
P_k(X, Y) ≠ P_j(X, Y)  for some k ≠ j
```

### 6.1 IID partitioning

The training index set is shuffled under the configured seed and distributed without intentionally conditioning on class label.

For approximately equal allocation:

```text
n_k ≈ N / K
```

### 6.2 Dirichlet label skew

For each class `c`, client proportions are sampled from a symmetric Dirichlet distribution.

```text
(π_1c, ..., π_Kc) ~ Dirichlet(α, ..., α)
```

Interpretation:

- larger `α` generally produces more balanced class proportions;
- smaller `α` generally produces stronger class concentration.

Example:

```yaml
data:
  partition: dirichlet
  alpha: 0.1
```

The configured `α` is not enough for reproducible reporting. The exact partition artifact and partition hash should also be retained.

### 6.3 Pathological class skew

Samples are grouped by label, divided into shards, and assigned so each client receives a restricted class subset.

```text
|C_k| ≤ r
```

where `C_k` is the set of classes represented on client `k` and `r` is the configured class bound.

### 6.4 Quantity skew

Quantity skew changes how many examples each client owns while keeping assignment label-agnostic.

A common interpretation of the implemented log-normal weighting scheme is:

```text
z_k ~ LogNormal(0, σ_q²)

q_k = z_k / Σ_j z_j

n_k ≈ N · q_k
```

Larger `σ_q` increases imbalance in client sample counts.

### 6.5 Realized heterogeneity evidence

The runtime archives more than the requested partition parameters. The partition manifest records evidence such as:

- per-client sample counts
- per-client label histograms
- partition SHA-256
- quantity coefficient of variation
- normalized label entropy
- Jensen-Shannon divergence
- class coverage
- effective label count

This matters because two experiments can use the same nominal `α` or `σ_q` and still realize different concrete partitions when the seed or implementation changes.

---

## 7. Differential privacy

The root private path uses trusted-server **client-level central differential privacy** for qualified FedAvg/FedProx configurations.

### 7.1 Neighboring relation

The privacy unit is a whole client. Two neighboring datasets differ by the presence or absence of one client's complete contribution under the qualified sampling and weighting assumptions.

This is not sample-level DP.

### 7.2 Client-update clipping

For client update `Δ_k` and clipping threshold `C`:

```text
Δ̃_k = Δ_k · min(1, C / ||Δ_k||₂)
```

Therefore:

```text
||Δ̃_k||₂ ≤ C
```

Clipping limits the maximum contribution of a single accepted client to the private aggregation mechanism.

### 7.3 Gaussian mechanism

The clipped aggregate is randomized with Gaussian noise. Conceptually:

```text
Z ~ Normal(0, σ² · sensitivity² · I)

released_update = clipped_aggregate + Z
```

The exact sensitivity and scaling depend on the qualified runtime's sampling and weighting semantics. The effective runtime configuration should be treated as the source of truth for a reported experiment.

### 7.4 Poisson client sampling

For client `k` at round `t`:

```text
I_(k,t) ~ Bernoulli(q)
```

where `q` is the configured client sample rate.

The release-qualified root DP path uses Poisson client sampling and uniform client weighting.

### 7.5 RDP composition

Rényi Differential Privacy costs compose additively over releases at a fixed order `r`:

```text
RDP_total(r) = Σ_t RDP_t(r)
```

The final `(ε, δ)` guarantee is obtained by converting the composed RDP curve and choosing the best supported order.

A common conversion form is:

```text
ε(δ) = min_r [ RDP_total(r) + log(1 / δ) / (r - 1) ]
```

The runtime accountant implementation, not this README summary, is authoritative for the actual reported value.

### 7.6 Target-epsilon calibration

If a target privacy budget is requested, the runtime solves for a noise multiplier that satisfies the target under the effective sample rate, release count, clipping assumptions, and target `δ`.

```yaml
dp:
  enabled: true
  update_clip_norm: 1.5
  target_epsilon: 4.0
  target_delta: 1.0e-5
```

Standalone calibration:

```bash
python scripts/calibrate_client_level_dp.py \
  --target-epsilon 4 \
  --sample-rate 0.2 \
  --rounds 50 \
  --delta 1e-5
```

When `--noise` is supplied manually, the effective configuration clears `target_epsilon` so the output does not imply that a target budget was enforced.

---

## 8. Secure and robust aggregation

Privacy, confidentiality, and Byzantine robustness are different properties. The platform keeps those boundaries explicit.

### 8.1 Pairwise-mask secure aggregation intuition

For a pairwise-mask construction, worker `i` sends a masked vector `y_i`:

```text
y_i = x_i
      + Σ_(j > i) r_ij
      - Σ_(j < i) r_ji
```

Across all workers, pairwise masks cancel:

```text
Σ_i y_i = Σ_i x_i
```

The coordinator can recover the aggregate without requiring each individual plaintext update to be directly stored by the aggregation layer under the protocol assumptions.

### 8.2 Threshold recovery

The recovery subsystem uses Shamir secret sharing for recovery material.

A secret `s` is represented as the constant term of a random polynomial:

```text
f(z) = s + a_1 z + a_2 z² + ... + a_(t-1) z^(t-1)
```

Share `i` is:

```text
(i, f(i))
```

Any valid threshold set can reconstruct `s = f(0)` using Lagrange interpolation. Fewer than the threshold number of shares are insufficient in the ideal Shamir model.

The platform's threshold dropout recovery remains an explicitly bounded/experimental surface rather than a production-grade stable capability.

### 8.3 Coordinate-wise median

For coordinate `j`, robust median aggregation computes:

```text
x̂_j = median(x_1j, x_2j, ..., x_mj)
```

This can reduce the influence of extreme coordinate outliers.

### 8.4 Trimmed mean

Sort coordinate `j` across `m` client updates:

```text
x_(1)j ≤ x_(2)j ≤ ... ≤ x_(m)j
```

Remove the `b` smallest and `b` largest values:

```text
x̂_j = [1 / (m - 2b)] · Σ_(i=b+1..m-b) x_(i)j
```

The stable v3 contract qualifies median and trimmed mean only for supported non-private synchronous execution. Robust aggregation combined with DP or secure aggregation is not generally claimed as release-qualified.

---

## 9. Client-level evaluation and fairness

Global test accuracy can hide large differences between clients. The root runtime therefore builds a held-out client view using only the official test split.

### 9.1 Held-out partition construction

After training:

1. the official test split is loaded;
2. the realized per-class training allocation is measured;
3. each test class is distributed across clients according to those realized proportions;
4. integer allocation is deterministic;
5. every test example is assigned exactly once;
6. test examples are not duplicated;
7. minimal deterministic redistribution prevents empty client evaluation sets when necessary.

No training sample is reused for held-out evaluation.

### 9.2 Client metrics

For client `k`:

```text
a_k = correct_predictions_k / n_test_k
```

Mean client accuracy:

```text
mean_accuracy = (1 / K) · Σ_k a_k
```

Held-out sample-weighted client accuracy:

```text
weighted_accuracy = [Σ_k n_test_k · a_k] / [Σ_k n_test_k]
```

Because the held-out client partitions form an exact non-overlapping cover of the official test set, the runtime validates consistency between weighted client accuracy and global test accuracy.

### 9.3 Jain fairness index

For non-negative client accuracies:

```text
J = (Σ_k a_k)² / [K · Σ_k a_k²]
```

Interpretation:

- `J` near `1` indicates comparatively even performance across clients;
- a smaller value indicates stronger concentration of performance on a subset of clients.

The runtime also reports median, p10, worst-client, best-client, standard deviation, range, and client-level loss statistics. Jain's index is not used as the only fairness measure.

---

## 10. Benchmark statistics

The benchmark runner executes each cell in a fresh process and preserves exact per-cell evidence.

Dry run:

```bash
python scripts/run_benchmark_matrix.py --dry-run
```

Example:

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

### 10.1 Mean and sample standard deviation

For observations `x_1 ... x_n`:

```text
x̄ = (1 / n) · Σ_i x_i

s = sqrt( [1 / (n - 1)] · Σ_i (x_i - x̄)² )
```

### 10.2 Matched-seed differences

When algorithms A and B use the same seeds and concrete partitions:

```text
d_i = x_i(A) - x_i(B)

d̄ = (1 / n) · Σ_i d_i
```

Paired standardized effect size:

```text
d_z = d̄ / s_d
```

where `s_d` is the sample standard deviation of the paired differences.

### 10.3 Bootstrap confidence intervals

The benchmark layer resamples seed-level observations with replacement under a deterministic bootstrap seed and records percentile-bootstrap confidence intervals.

The independent observation unit is the experiment seed/cell result, not an individual communication round.

### 10.4 Paired sign-flip test

Under the paired null model:

```text
d*_i = s_i · d_i

s_i ∈ {-1, +1}
```

The observed paired statistic is compared with the sign-flipped null distribution.

### 10.5 Holm-Bonferroni control

For ordered p-values:

```text
p_(1) ≤ p_(2) ≤ ... ≤ p_(m)
```

The sequential threshold is:

```text
α / (m - i + 1)
```

The benchmark output records adjusted values rather than presenting a large family of uncorrected significance claims.

### 10.6 Reproducibility identity

A publishable benchmark result should retain at least:

- runtime identity (`root-simulator` or `distributed-platform`)
- exact source commit SHA
- effective configuration
- dataset and official split identity
- partition method and parameters
- random seed
- exact partition hash
- algorithm and parameters
- rounds/local epochs/batch size
- client sampling strategy and rate
- aggregation weighting
- privacy inputs and accountant output
- final checkpoint
- result summary

Configuration alone is not sufficient evidence.

---

## 11. Architecture

### 11.1 Root runtime

```text
main.py
  |
  +--> configuration + runtime validation
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
  |      |      +--> clipping / private aggregation path
  |      |
  |      +--> RDP accountant
  |      +--> round metrics
  |      +--> partition artifacts
  |
  +--> final global model checkpoint
  |
  +--> held-out client partition
  |
  +--> client accuracy / loss / fairness metrics
  |
  +--> summary.md + summary.json
```

### 11.2 Distributed platform

```text
                         +-----------------------+
                         |   Control / clients   |
                         +-----------+-----------+
                                     |
                                REST / API
                                     |
                         +-----------v-----------+
                         |   Go Control Plane    |
                         | execution lifecycle   |
                         +-----+-------------+---+
                               |             |
                            gRPC|             | persistence
                               |             |
                    +----------v---+     +---v------------------+
                    | C++20        |     | PostgreSQL / Redis   |
                    | Coordinator  |     | MinIO / MLflow       |
                    +------+-------+     +----------------------+
                           |
                       gRPC / protobuf
                           |
                    +------v----------------+
                    | Python ML Worker(s)   |
                    | training / privacy    |
                    +-----------------------+

Observability: Prometheus + Grafana + OpenTelemetry
Security: mTLS identity + signed messages + replay protection
```

### 11.3 Execution lifecycle

The Go control plane maintains durable execution records under `/api/v1/executions`. Reconciliation refreshes stable runtime state while avoiding races with active lifecycle transitions such as `STARTING`, `PAUSING`, `RESUMING`, and `CANCELING`.

Local-backend pause/resume is communication-round-boundary safe. Checkpoint SHA-256 sidecars detect changed or corrupted checkpoint bytes before restore. SHA-256 is an integrity check, not keyed authenticity against an actor able to replace both the checkpoint and expected digest.

---

## 12. Installation

### Root runtime requirements

- Python 3.11+
- Git
- CPU or CUDA-capable PyTorch environment

### Full platform development requirements

- Docker Engine / Docker Desktop with Compose
- CMake
- C++20 compiler
- Go toolchain
- Protocol Buffers/gRPC development dependencies when building native RPC components directly

Clone:

```bash
git clone https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy.git
cd Federated-Learning-on-Non-IID-Data-Differential-Privacy
```

Root dependencies:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Platform development package:

```bash
python -m pip install -e './python[dev,security]'
```

Stable package runtime only:

```bash
python -m pip install -e './python[security]'
```

---

## 13. Running experiments

Desktop UI:

```bash
python main.py
```

CLI default:

```bash
python main.py --cli
```

MNIST + FedAvg + IID:

```bash
python main.py --cli \
  --dataset MNIST \
  --algo fedavg \
  --partition iid \
  --dp off
```

FashionMNIST + FedProx + Dirichlet skew:

```bash
python main.py --cli \
  --dataset FASHIONMNIST \
  --algo fedprox \
  --partition dirichlet \
  --alpha 0.1 \
  --dp off
```

CIFAR-100 + quantity skew:

```bash
python main.py --cli \
  --dataset CIFAR100 \
  --algo fedavg \
  --partition quantity_skew \
  --quantity-skew-sigma 1.5 \
  --dp off
```

CIFAR-10 + FedProx + client-level DP:

```bash
python main.py --cli \
  --dataset CIFAR10 \
  --algo fedprox \
  --partition dirichlet \
  --alpha 0.1 \
  --dp on \
  --rounds 50
```

### Dataset reference

| Dataset | Train | Test | Classes | Channels |
|---|---:|---:|---:|---:|
| MNIST | 60,000 | 10,000 | 10 | 1 |
| FashionMNIST | 60,000 | 10,000 | 10 | 1 |
| CIFAR-10 | 50,000 | 10,000 | 10 | 3 |
| CIFAR-100 | 50,000 | 10,000 | 100 | 3 |

MNIST and FashionMNIST are resized to `32x32` so the root runtime can use the same GroupNorm CNN family while selecting the appropriate classifier output dimension.

---

## 14. Configuration

Primary configuration file: [`config.yaml`](config.yaml)

Representative configuration:

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

Every root execution writes the final effective configuration after overrides to:

```text
results/_effective_runtime_config.yaml
```

For academic reporting, the effective runtime configuration is more important than the original YAML because it records the parameters actually executed.

---

## 15. Artifacts and reproducibility

A normal root run produces an auditable result directory:

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

`summary.json` is the machine-readable result interface consumed by benchmark tooling.

Benchmark directory:

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
```

---

## 16. Distributed platform

Start the development topology with:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

The development stack includes:

- C++ gRPC coordinator
- Go API/control plane
- Python worker
- Python command service
- PostgreSQL
- Redis
- MinIO
- MLflow
- Prometheus
- Grafana
- OpenTelemetry Collector

The distributed runtime includes execution persistence/reconciliation, worker identity, signed-message validation, replay protection, secure-aggregation components, deterministic fault/heterogeneity simulation, distributed metrics, and release-validation infrastructure.

A distributed-platform result and a root-simulator result are different runtime identities even when they use the same algorithm name.

---

## 17. Developer workflow

Before changing a component, answer three questions:

1. Which runtime owns the behavior?
2. What invariant must remain true?
3. Which executable test or evidence demonstrates that invariant?

Python tests:

```bash
python -m pytest tests python/tests
```

Ruff:

```bash
python -m ruff check .
python -m ruff format --check .
```

Repository/runtime documentation validation:

```bash
python scripts/validate_repository_docs.py
```

Baseline unittest:

```bash
make test-baseline
```

Protocol contracts:

```bash
make proto-check
```

PKI validation:

```bash
make pki-verify
```

Go:

```bash
cd go
go test ./...
go vet ./...
go build ./...
```

C++ Debug:

```bash
cmake -S cpp -B build/cpp-debug -DCMAKE_BUILD_TYPE=Debug
cmake --build build/cpp-debug
ctest --test-dir build/cpp-debug --output-on-failure
```

C++ Release:

```bash
cmake -S cpp -B build/cpp-release -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp-release
ctest --test-dir build/cpp-release --output-on-failure
```

Sanitizers:

```bash
make cpp-asan
make cpp-ubsan
```

Static analysis / formatting:

```bash
make cpp-format-check
make cpp-tidy
```

Native aggregation benchmark:

```bash
make cpp-benchmark
```

If a change affects an algorithm, privacy mechanism, secure protocol, runtime state transition, dataset contract, or benchmark schema, update its executable validation together with the implementation.

---

## 18. Validation and CI

Repository CI covers substantially more than unit tests. Depending on path scope, validation includes:

- Python tests
- Ruff lint
- Ruff format check
- mypy
- Go tests, race tests, vet, and build
- C++ Debug and Release builds
- CTest
- clang-format
- clang-tidy
- AddressSanitizer
- UndefinedBehaviorSanitizer
- C++ gRPC build/tests
- protobuf compatibility checks
- PKI verification
- secret scanning
- security-runtime validation
- Docker Compose validation
- infrastructure validation
- distributed runtime validation
- benchmark evidence checks
- ARM64 image validation
- supply-chain validation
- release-candidate validation
- final release qualification

A green individual job is not equivalent to a green release. Final release qualification is bound to the exact commit SHA.

---

## 19. v3.0.0 release qualification

The Python package reports:

```text
fl-platform == 3.0.0
```

The final `v3.0.0` tag is publishable only when the same tagged commit has successful runs for:

1. `ci.yml`
2. `v3-release-candidate.yml`
3. `v3-distributed-runtime.yml`
4. `v3-final-qualification.yml`

The release-artifact workflow then:

- verifies same-SHA qualification;
- downloads exact qualification evidence;
- checks package/tag parity;
- builds API and Python-worker images;
- resolves immutable image digests;
- generates the release image lock;
- renders digest-pinned deployment artifacts;
- builds wheel and source distribution artifacts;
- creates a source archive;
- generates CycloneDX SBOM data;
- writes artifact SHA-256 metadata;
- creates provenance attestations;
- publishes the GitHub Release.

### Empirical release baseline

The stable v3 qualification includes a real five-seed root-runtime baseline:

- runtime: `root-simulator`
- dataset: MNIST
- algorithm: FedAvg
- partition: IID
- privacy: disabled
- seeds: `11, 23, 37, 53, 71`
- qualification rounds: 1 per seed

This qualifies the defined stable baseline. It does not imply that every possible algorithm × dataset × privacy × attack × heterogeneity combination has been exhaustively executed.

See [`RELEASE_NOTES_v3.0.0.md`](RELEASE_NOTES_v3.0.0.md) for stable support and explicit experimental boundaries.

---

## 20. Security and threat-model boundaries

This section is intentionally conservative.

### Differential privacy is not secure aggregation

Differential privacy limits information leakage from a randomized release under a stated neighboring relation. It does not automatically hide network messages before the mechanism is applied.

### Secure aggregation is not differential privacy

Secure aggregation hides individual contributions from an aggregator under protocol assumptions. An exact aggregate can still reveal information, especially over repeated rounds or small cohorts.

### Secure aggregation is not poisoning defense

A cryptographically authenticated malicious client can still submit a harmful update. Robust aggregation, anomaly detection, admission control, and trust management address different risks.

### mTLS is not application authorization by itself

mTLS authenticates the transport endpoint. The platform additionally binds application messages to identity/signing/replay state where required.

### Hashes are not keyed authentication

SHA-256 detects changed bytes when the expected digest is trusted. It does not protect against an actor able to replace both content and expected digest metadata.

### Current non-claims

The project does not claim:

- formal cryptographic certification
- regulatory compliance
- Internet-scale production validation
- immunity to Byzantine clients
- Sybil resistance from secure aggregation alone
- root client-level-DP SCAFFOLD
- production-grade threshold secure-aggregation dropout recovery
- crash-resumable in-flight secure rounds
- verified physical edge-device energy/thermal performance

---

## 21. Known limitations

Stable support is narrower than the total amount of code in the repository.

Important boundaries:

- the root runtime is single-machine orchestration, not a physical cross-device deployment;
- root client-level DP supports FedAvg/FedProx, not SCAFFOLD;
- feature/covariate-shift partitioning is not a root partition strategy;
- FedSAM, Ditto, and Per-FedAvg are platform-worker capabilities rather than root CLI algorithms;
- true distributed asynchronous training remains experimental;
- threshold secure-aggregation dropout recovery is not promoted as a production capability;
- an in-flight secure round is not resumed after coordinator process loss;
- FEMNIST, Shakespeare, and Sent140 loaders remain outside the stable v3 scope;
- robust aggregation + DP and robust aggregation + secure aggregation are not generally release-qualified;
- physical multi-host throughput/latency guarantees are not claimed;
- physical ARM64 energy/thermal/throughput guarantees are not claimed;
- the complete attack × privacy × heterogeneity benchmark cross-product has not been empirically exhausted.

Unsupported combinations are expected to fail closed instead of being silently reported as stable.

---

## 22. Repository layout

```text
.
├── main.py                         # root entry point
├── experiment_runtime.py           # root experiment orchestration
├── config.yaml                     # root configuration
├── data/                           # root datasets/partitioning
├── federated/                      # root client/server/DP logic
├── models/                         # root neural networks
├── utils/                          # metrics/evaluation/artifacts
├── desktop/                        # PySide6 desktop UI
│
├── python/                         # platform Python package/workers
│   └── src/fl_platform/
├── cpp/                            # C++20 coordinator/aggregation/runtime
├── go/                             # Go API and execution control plane
├── proto/                          # protobuf/gRPC contracts
├── infra/                          # Docker/Kubernetes/observability
├── scripts/                        # validation/release/benchmark utilities
├── tests/                          # root tests
├── docs/                           # architecture/privacy/runtime docs
├── release/                        # release evidence/contracts
│
├── RUNTIME.md                      # executable runtime source of truth
├── RELEASE_PLAN_v3.0.0.md          # release gates
├── RELEASE_NOTES_v3.0.0.md         # v3 support contract
├── CONTRIBUTING.md
├── SECURITY.md
└── LICENSE
```

Documentation precedence:

1. executable source and enforced tests;
2. `RUNTIME.md`;
3. current release qualification documents;
4. older status/audit reports.

---

## 23. Academic reporting and citation

This repository is designed for controlled experiments where implementation, partition, privacy configuration, seed, and artifact identity are reported together.

A strong academic result should state at minimum:

- repository version or exact commit SHA
- runtime identity
- dataset and official split
- number of clients
- partition method and realized partition hash
- model architecture
- algorithm and optimizer parameters
- communication rounds
- local epochs and batch size
- client sampling strategy/rate
- aggregation weighting
- privacy unit and neighboring relation when DP is enabled
- clipping norm
- noise multiplier
- target/final `(epsilon, delta)` values when applicable
- random seeds
- number of independent runs
- mean/dispersion/confidence interval
- tail and fairness metrics when heterogeneity is central to the claim

### Suggested repository citation

```bibtex
@software{shagor2026federated,
  author  = {Md Shahanur Islam Shagor},
  title   = {Federated Learning on Non-IID Data with Differential Privacy},
  year    = {2026},
  version = {3.0.0},
  url     = {https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy}
}
```

### Reproducibility note

Do not report only a configured Dirichlet `alpha` value. Report the exact partition artifact/hash as well. Likewise, do not report only “DP enabled”; report the accountant inputs, effective runtime parameters, and achieved privacy budget.

---

## 24. Author and maintainer

**Md Shahanur Islam Shagor**  
Project Architect and Lead Developer  
Independent Software & AI Engineer / Researcher  
Affiliation: Voronezh State University of Forestry and Technologies

**Research and engineering interests**

- federated and privacy-preserving machine learning
- Non-IID optimization and personalized federated learning
- differential privacy and privacy accounting
- distributed AI/ML systems
- secure and robust aggregation
- reproducible experimentation and benchmarking
- autonomous systems and applied AI engineering

**GitHub:** [@smshagor-dev](https://github.com/smshagor-dev)  
**Email:** `smshagor.ru@gmail.com`

This repository is maintained as both an engineering codebase and an academic experiment platform. Design choices are documented with an emphasis on runtime boundaries, reproducibility, measurable evidence, and avoiding claims that exceed the validated implementation.

---

## 25. Contributing and license

Contributions are welcome when they preserve runtime correctness and include validation appropriate to the change.

Before opening a pull request, read:

- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SECURITY.md`](SECURITY.md)
- [`RUNTIME.md`](RUNTIME.md)

Security issues should follow `SECURITY.md` instead of being disclosed through a public issue when the report contains exploitable details.

### License

Copyright © 2026 **Md Shahanur Islam Shagor**.

Licensed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) for the full license text.
