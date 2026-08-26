# Federated Learning on Non-IID Data with Differential Privacy

A reproducible federated-learning platform for studying **data heterogeneity, privacy, robustness, distributed execution, secure aggregation, fairness, and release-grade experiment evidence**.

This repository contains a practical single-machine research runtime and a separate distributed platform built with Python, C++20, Go, gRPC, Docker, PostgreSQL, Redis, MLflow, Prometheus, Grafana, and OpenTelemetry. The project is deliberately strict about runtime boundaries: a feature is only described as supported when the corresponding executable path and validation evidence exist.

[![CI](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-ML%20Runtime-EE4C2C?logo=pytorch&logoColor=white)
![C++](https://img.shields.io/badge/C%2B%2B-20-00599C?logo=cplusplus&logoColor=white)
![Go](https://img.shields.io/badge/Go-Control%20Plane-00ADD8?logo=go&logoColor=white)
![gRPC](https://img.shields.io/badge/gRPC-Protocol%20Buffers-244C5A)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License](https://img.shields.io/github/license/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy)

**Source version:** `3.0.0`  
**Primary research themes:** Federated Learning · Non-IID Data · Client-Level Differential Privacy · Robust Aggregation · Secure Aggregation · Fairness · Reproducible Benchmarking · Distributed ML Systems

---

## Contents

- [1. What this project is](#1-what-this-project-is)
- [2. Runtime model: two execution paths](#2-runtime-model-two-execution-paths)
- [3. Capability map](#3-capability-map)
- [4. Federated-learning problem formulation](#4-federated-learning-problem-formulation)
- [5. Algorithms and update equations](#5-algorithms-and-update-equations)
- [6. Modeling Non-IID client data](#6-modeling-non-iid-client-data)
- [7. Differential privacy model](#7-differential-privacy-model)
- [8. Secure and robust aggregation](#8-secure-and-robust-aggregation)
- [9. Client-level evaluation and fairness](#9-client-level-evaluation-and-fairness)
- [10. Benchmark statistics and reproducibility](#10-benchmark-statistics-and-reproducibility)
- [11. Architecture](#11-architecture)
- [12. Installation](#12-installation)
- [13. Running the root research runtime](#13-running-the-root-research-runtime)
- [14. Configuration](#14-configuration)
- [15. Generated artifacts](#15-generated-artifacts)
- [16. Distributed platform](#16-distributed-platform)
- [17. Developer workflow](#17-developer-workflow)
- [18. Validation and CI](#18-validation-and-ci)
- [19. v3.0.0 release qualification](#19-v300-release-qualification)
- [20. Security, privacy, and threat-model boundaries](#20-security-privacy-and-threat-model-boundaries)
- [21. Known limitations](#21-known-limitations)
- [22. Repository layout](#22-repository-layout)
- [23. Academic use and citation](#23-academic-use-and-citation)
- [24. Author and maintainer](#24-author-and-maintainer)
- [25. Contributing and license](#25-contributing-and-license)

---

## 1. What this project is

Federated learning is often summarized as “train locally, aggregate globally.” That description is correct but incomplete. Real federated systems become difficult when clients have different class distributions, different amounts of data, different compute or network behavior, and different privacy or trust requirements.

This repository is built to make those differences explicit and measurable.

The project supports experiments around four questions:

1. **Utility:** how well does a global or personalized model learn when client data are heterogeneous?
2. **Privacy:** what utility is lost when client updates are clipped and randomized under a defined client-level privacy mechanism?
3. **Reliability and security:** what happens when workers are slow, unavailable, adversarial, duplicated, replayed, or dropped?
4. **Reproducibility:** can a result be tied to an exact dataset split, seed, runtime configuration, commit, privacy budget, and output artifact set?

The repository is not a single monolithic simulator. It contains two runtime identities with different purposes and different support boundaries.

---

## 2. Runtime model: two execution paths

### Root research runtime

The root runtime is the shortest path from an experiment idea to a reproducible result:

```bash
python main.py --cli
```

It performs real PyTorch training on torchvision datasets and provides deterministic client partitioning, FedAvg/FedProx/SCAFFOLD, client-level central differential privacy for supported combinations, held-out client evaluation, fairness metrics, checkpoints, plots, manifests, and multi-seed benchmark execution.

Primary files:

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

### Distributed platform runtime

The distributed platform is a separate multi-service system:

```bash
docker compose -f infra/compose/docker-compose.dev.yml up --build
```

It combines a Go control plane, C++ coordinator, Python workers, protobuf/gRPC contracts, persistence, service security, observability, and release infrastructure.

Primary areas:

```text
cpp/
go/
python/
proto/
infra/
web/
```

> A capability implemented in one runtime must not be assumed to exist in the other. `RUNTIME.md` is the repository source of truth when a runtime boundary is unclear.

---

## 3. Capability map

### Root runtime

| Area | Capability | Status |
|---|---|---|
| Datasets | MNIST | Supported |
| Datasets | FashionMNIST | Supported |
| Datasets | CIFAR-10 | Supported |
| Datasets | CIFAR-100 | Supported |
| Partitioning | IID | Supported |
| Partitioning | Dirichlet label skew | Supported |
| Partitioning | Pathological class skew | Supported |
| Partitioning | Quantity skew | Supported |
| Algorithms | FedAvg | Supported |
| Algorithms | FedProx | Supported |
| Algorithms | SCAFFOLD | Supported without client-level DP |
| Privacy | Client-level central DP | FedAvg/FedProx supported |
| Privacy | Target-epsilon calibration | Supported |
| Privacy | RDP accounting | Supported |
| Sampling | Poisson client sampling | Supported |
| Sampling | Fixed-size sampling | Supported when DP is disabled |
| Evaluation | Global test evaluation | Supported |
| Evaluation | Held-out per-client evaluation | Supported |
| Fairness | p10/worst-client/Jain metrics | Supported |
| Reproducibility | Exact partition manifests | Supported |
| Reproducibility | Final model checkpoints | Supported |
| Reproducibility | Machine-readable summaries | Supported |
| Benchmarking | Multi-seed matrix | Supported |
| Statistics | Bootstrap confidence intervals | Supported |
| Statistics | Matched-seed comparisons | Supported |
| UI | PySide6 desktop interface | Supported |

### v3 distributed/worker support contract

The v3 platform contains canonical worker support for:

- FedAvg
- FedProx
- SCAFFOLD
- FedSAM
- Ditto
- Per-FedAvg
- median and trimmed-mean robust aggregation for supported non-private synchronous paths
- deterministic compute/network/availability/payload heterogeneity simulation
- mTLS identity and signed-message validation
- replay protection
- privacy/accounting validation
- distributed metrics and observability primitives
- ARM64 worker-image build/self-test compatibility
- release artifact hashing, SBOM generation, immutable image locks, and provenance attestations

Some advanced code exists outside the stable support contract. Those boundaries are listed under [Known limitations](#21-known-limitations) and in `RELEASE_NOTES_v3.0.0.md`.

---

## 4. Federated-learning problem formulation

Assume there are \(K\) clients. Client \(k\) owns a local dataset

$$
\mathcal{D}_k = \{(x_i, y_i)\}_{i=1}^{n_k}
$$

with \(n_k = |\mathcal{D}_k|\). The local empirical objective is

$$
F_k(w)=\frac{1}{n_k}\sum_{(x,y)\in\mathcal{D}_k}\ell(w;x,y),
$$

where \(w\in\mathbb{R}^d\) contains the model parameters and \(\ell\) is the training loss.

A standard global federated objective is

$$
\min_w F(w),
\qquad
F(w)=\sum_{k=1}^{K}p_kF_k(w),
$$

with non-negative client weights satisfying

$$
\sum_{k=1}^{K}p_k=1.
$$

For data-size weighting,

$$
p_k=\frac{n_k}{\sum_{j=1}^{K}n_j}.
$$

For uniform client weighting,

$$
p_k=\frac{1}{K}.
$$

The configured runtime decides which weighting rule is valid for the current experiment. This matters for privacy: changing weighting changes the sensitivity analysis of the released aggregate.

### One communication round

At round \(t\):

1. the server holds global parameters \(w_t\);
2. a client subset \(S_t\subseteq\{1,\ldots,K\}\) is sampled;
3. every selected client starts from \(w_t\);
4. client \(k\in S_t\) performs local optimization and produces \(w_{t,E}^{(k)}\);
5. the client update is

$$
\Delta_t^{(k)}=w_{t,E}^{(k)}-w_t;
$$

6. the server aggregates the selected updates;
7. the resulting model becomes \(w_{t+1}\).

For a local SGD step,

$$
w_{t,e+1}^{(k)}
=
w_{t,e}^{(k)}-\eta\nabla F_k\left(w_{t,e}^{(k)}\right),
$$

where \(\eta\) is the local learning rate.

The main source of federated difficulty is that, under Non-IID data,

$$
F_k(w)\neq F_j(w)
$$

for many client pairs \(k\neq j\). Local gradients therefore point in systematically different directions, producing client drift.

---

## 5. Algorithms and update equations

### 5.1 FedAvg

FedAvg averages client contributions after local training. A general server update is

$$
w_{t+1}
=
w_t+\eta_s\sum_{k\in S_t}a_k\Delta_t^{(k)},
$$

where \(\eta_s\) is the server learning rate and the normalized aggregation weights satisfy

$$
\sum_{k\in S_t}a_k=1.
$$

With sample-count weighting,

$$
a_k=\frac{n_k}{\sum_{j\in S_t}n_j}.
$$

With uniform weighting,

$$
a_k=\frac{1}{|S_t|}.
$$

FedAvg is the reference algorithm for most baseline experiments in this repository.

### 5.2 FedProx

FedProx modifies the client objective by penalizing movement away from the current global model:

$$
F_k^{\text{prox}}(w;w_t)
=
F_k(w)+\frac{\mu}{2}\|w-w_t\|_2^2.
$$

Its local gradient is

$$
\nabla F_k^{\text{prox}}(w;w_t)
=
\nabla F_k(w)+\mu(w-w_t).
$$

The hyperparameter \(\mu\ge0\) controls the strength of the proximal constraint. Setting \(\mu=0\) recovers the ordinary local objective.

Example:

```yaml
algorithm:
  name: fedprox
  mu: 0.01
```

### 5.3 SCAFFOLD

SCAFFOLD introduces server and client control variates to reduce update bias caused by client drift. A local step can be written as

$$
w\leftarrow
w-\eta\left(\nabla F_k(w)-c_k+c\right),
$$

where \(c\) is the server control variate and \(c_k\) is the client control variate.

In the root runtime, non-private SCAFFOLD is supported. Client-level-DP SCAFFOLD is intentionally rejected because the current privacy guarantee does not cover the additional control-variate release/state path.

### 5.4 FedSAM

The worker platform includes FedSAM support. Sharpness-Aware Minimization approximates

$$
\min_w\max_{\|\epsilon\|_2\le\rho}F_k(w+\epsilon),
$$

where \(\rho\) controls the local perturbation radius. FedSAM is part of the v3 worker capability surface; it is not a root-CLI algorithm.

### 5.5 Ditto

Ditto separates a shared global model from a client-personalized model. A common personalized objective is

$$
\min_{v_k}
F_k(v_k)+\frac{\lambda}{2}\|v_k-w\|_2^2,
$$

where \(w\) is the global reference model and \(v_k\) is the personalized model for client \(k\).

Ditto is available through the platform worker implementation, not the root simulator path.

### 5.6 Per-FedAvg

Per-FedAvg is a personalization/meta-learning approach that optimizes a model for rapid client adaptation. Conceptually, if

$$
w_k' = w-\alpha\nabla F_k(w),
$$

then the outer objective evaluates the client after the adaptation step:

$$
\min_w\sum_k p_kF_k(w_k').
$$

The exact worker implementation and supported parameter combinations are governed by the platform capability matrix.

---

## 6. Modeling Non-IID client data

A configuration label such as `dirichlet: alpha=0.1` is not enough to reproduce a concrete partition. The runtime therefore archives the exact assigned sample indices and measured heterogeneity statistics.

### 6.1 IID partition

Training indices are shuffled with a deterministic seed and divided among clients. The goal is approximately equal empirical class distributions across clients, subject to finite-sample variation.

### 6.2 Dirichlet label skew

For each class \(c\), client allocation probabilities are sampled as

$$
\pi_c\sim\operatorname{Dirichlet}(\alpha\mathbf{1}_K).
$$

Class-\(c\) samples are then distributed according to \(\pi_c\).

Interpretation:

- large \(\alpha\): allocations become more balanced;
- small \(\alpha\): allocations become concentrated on fewer clients;
- \(\alpha\to0\): increasingly extreme label skew.

Example:

```yaml
data:
  partition: dirichlet
  alpha: 0.1
```

### 6.3 Pathological class skew

Samples are ordered by label, split into shards, and assigned so that each client receives a restricted subset of classes.

```yaml
data:
  partition: pathological
  classes_per_client: 2
```

This creates a controlled form of label-support mismatch.

### 6.4 Quantity skew

Client sample weights are drawn from a log-normal distribution:

$$
q_k\sim\operatorname{LogNormal}(0,\sigma_q^2),
$$

and normalized into approximate client sample counts

$$
n_k\approx N\frac{q_k}{\sum_j q_j},
$$

where \(N\) is the total training-set size.

Larger \(\sigma_q\) produces stronger sample-count imbalance.

```yaml
data:
  partition: quantity_skew
  quantity_skew_sigma: 1.0
```

### 6.5 Measured heterogeneity

The partition manifest records the realized split, not only the requested partition family.

Useful quantities include:

**Quantity coefficient of variation**

$$
\operatorname{CV}(n)
=
\frac{\operatorname{std}(n_1,\ldots,n_K)}{\operatorname{mean}(n_1,\ldots,n_K)}.
$$

**Client label entropy**

For client label probabilities \(p_{k,c}\),

$$
H_k=-\sum_{c=1}^{C}p_{k,c}\log p_{k,c}.
$$

A normalized entropy is

$$
\widetilde H_k=\frac{H_k}{\log C}.
$$

**Effective label count**

$$
N_{\text{eff},k}=e^{H_k}.
$$

**Jensen-Shannon divergence** between a client label distribution \(P_k\) and the global label distribution \(P_g\):

$$
\operatorname{JS}(P_k\|P_g)
=
\frac12\operatorname{KL}(P_k\|M)
+
\frac12\operatorname{KL}(P_g\|M),
$$

with

$$
M=\frac12(P_k+P_g).
$$

The runtime records these quantities so two nominally identical configurations can be checked for the same realized data split.

---

## 7. Differential privacy model

### 7.1 Privacy unit

The root private path implements **trusted-server client-level central differential privacy** for supported FedAvg/FedProx configurations.

The neighboring relation is defined at the client level, not at the individual training-example level. In other words, the privacy unit is a client's contribution to a communication round under the runtime's configured sampling and weighting assumptions.

The project does **not** use the phrase “differentially private” as a generic synonym for “noise was added.” A valid privacy result depends on the exact mechanism and accountant inputs.

### 7.2 Update clipping

For a client update \(\Delta_k\), global L2 clipping at bound \(C\) is

$$
\bar\Delta_k
=
\Delta_k\cdot
\min\left(1,\frac{C}{\|\Delta_k\|_2}\right).
$$

Therefore,

$$
\|\bar\Delta_k\|_2\le C.
$$

Clipping bounds the influence of any single participating client before the released aggregate is randomized.

### 7.3 Gaussian mechanism

A released aggregate can be represented generically as

$$
\widetilde A_t
=
A_t(\bar\Delta_1,\ldots,\bar\Delta_m)
+
\mathcal N\left(0,(\sigma S_t)^2I\right),
$$

where:

- \(A_t\) is the permitted clipped aggregation rule;
- \(S_t\) is the L2 sensitivity implied by the adjacency, weighting, and normalization convention;
- \(\sigma\) is the noise multiplier.

The repository does not hard-code a README-only sensitivity claim. Runtime validation and accounting remain authoritative because sensitivity changes when sampling, weighting, or adjacency assumptions change.

### 7.4 Poisson client sampling

For private root execution, a client may be independently sampled with probability

$$
q\in(0,1].
$$

The expected number of selected clients is

$$
\mathbb E[|S_t|]=qK.
$$

The privacy accountant must use the same sampling model used by training. A fixed-size sampler cannot be silently treated as a Poisson sampler merely because both select a similar average number of clients.

### 7.5 Rényi Differential Privacy composition

For Rényi order \(\alpha>1\), suppose round \(t\) incurs RDP cost \(\varepsilon_t^{\text{RDP}}(\alpha)\). Sequential composition gives

$$
\varepsilon_{1:T}^{\text{RDP}}(\alpha)
=
\sum_{t=1}^{T}
\varepsilon_t^{\text{RDP}}(\alpha).
$$

Conversion to an \((\varepsilon,\delta)\) guarantee uses the best supported order:

$$
\varepsilon(\delta)
=
\min_{\alpha>1}
\left[
\varepsilon_{1:T}^{\text{RDP}}(\alpha)
+
\frac{\log(1/\delta)}{\alpha-1}
\right].
$$

The accountant uses the actual subsampled mechanism implementation rather than a README approximation for per-round RDP.

### 7.6 Target-epsilon calibration

A user can specify a desired final budget:

```yaml
dp:
  enabled: true
  update_clip_norm: 1.5
  target_epsilon: 4.0
  target_delta: 1.0e-5
```

The runtime recalibrates the noise multiplier **after** CLI/runtime overrides are applied. This prevents a sigma calibrated for one round count or sampling rate from being reused while still claiming the old target budget.

Standalone calibration:

```bash
python scripts/calibrate_client_level_dp.py \
  --target-epsilon 4 \
  --sample-rate 0.2 \
  --rounds 50 \
  --delta 1e-5
```

Manual override:

```bash
python main.py --cli --noise 3.0
```

When `--noise` is explicitly provided, `target_epsilon` is cleared from the effective runtime configuration so the result does not imply that a target budget was enforced.

### 7.7 Privacy compatibility rules

The private root path currently supports:

- FedAvg + client-level DP
- FedProx + client-level DP
- Poisson client sampling
- supported privacy-aware weighting assumptions
- RDP composition across released rounds

It intentionally rejects unsupported combinations instead of silently downgrading the guarantee.

---

## 8. Secure and robust aggregation

Privacy, secure aggregation, and Byzantine robustness solve different problems. They should not be treated as interchangeable.

### 8.1 Pairwise-mask cancellation

A standard pairwise secure-aggregation idea lets client \(i\) send

$$
y_i
=
x_i
+
\sum_{j>i}r_{ij}
-
\sum_{j<i}r_{ji},
$$

where a pair of clients derives the same mask \(r_{ij}=r_{ji}\).

Summing all client messages gives

$$
\sum_i y_i
=
\sum_i x_i,
$$

because every pairwise mask appears once with a positive sign and once with a negative sign.

The platform contains secure-aggregation protocol components, authenticated relay/recovery messages, replay isolation, encrypted recovery-share relay storage, and threshold-recovery primitives. The v3.0.0 stable support boundary does **not** promote threshold dropout recovery to a production capability.

### 8.2 Shamir threshold recovery mathematics

For threshold \(t\), a secret \(s\) is represented as the constant coefficient of a random degree-\((t-1)\) polynomial over a finite field:

$$
f(z)=s+a_1z+a_2z^2+\cdots+a_{t-1}z^{t-1}\pmod p.
$$

Holder \(i\) receives share

$$
(x_i,y_i)=(x_i,f(x_i)).
$$

Any \(t\) valid shares can reconstruct

$$
s=f(0)
=
\sum_{i=1}^{t}
y_i\lambda_i(0)\pmod p,
$$

where the Lagrange coefficient is

$$
\lambda_i(0)
=
\prod_{j\ne i}
\frac{-x_j}{x_i-x_j}
\pmod p.
$$

In this repository's recovery path, the reconstructed secret is tied to a session-specific ephemeral X25519 key and verified against the frozen participant roster. Raw recovery shares are not intended to be persisted by the coordinator.

### 8.3 Coordinate-wise median

For client values \(x_{1,j},\ldots,x_{m,j}\) in coordinate \(j\), the median aggregator is

$$
\widehat x_j
=
\operatorname{median}
\{x_{1,j},\ldots,x_{m,j}\}.
$$

Median aggregation can reduce the influence of extreme coordinate outliers, but it is not automatically compatible with every DP, secure-aggregation, or asynchronous protocol.

### 8.4 Trimmed mean

For each coordinate, sort the client values and remove \(b\) smallest and \(b\) largest observations. If

$$
x_{(1),j}\le\cdots\le x_{(m),j},
$$

then

$$
\widehat x_j
=
\frac{1}{m-2b}
\sum_{i=b+1}^{m-b}x_{(i),j}.
$$

The v3 stable contract validates median and trimmed mean only for supported non-private synchronous execution. Combined robust aggregation + differential privacy or robust aggregation + secure aggregation is not claimed as generally supported.

---

## 9. Client-level evaluation and fairness

A high global test accuracy can hide clients with very poor performance. The root runtime therefore evaluates the final global model on a held-out client view constructed from the official dataset test split.

### 9.1 Matched held-out partition

The process is:

1. train using the official training split only;
2. measure each class's realized training allocation across clients;
3. allocate examples from the official test split according to those realized class proportions;
4. use deterministic integer allocation;
5. assign every test example exactly once;
6. avoid duplication;
7. perform minimal deterministic redistribution only when needed to avoid empty held-out clients.

No training example is reused for held-out evaluation.

### 9.2 Global and client metrics

For client \(k\), let

$$
a_k=\frac{\text{correct predictions on client }k}{n_k^{\text{test}}}.
$$

The unweighted mean client accuracy is

$$
\bar a=\frac{1}{K}\sum_{k=1}^{K}a_k.
$$

The held-out sample-weighted accuracy is

$$
a_{\text{weighted}}
=
\frac{\sum_k n_k^{\text{test}}a_k}
{\sum_k n_k^{\text{test}}}.
$$

Because the client test partitions form an exact non-overlapping cover of the official test set, the runtime verifies consistency between this weighted value and global test accuracy.

### 9.3 Jain fairness index

For non-negative client accuracies,

$$
J(a_1,\ldots,a_K)
=
\frac{\left(\sum_{k=1}^{K}a_k\right)^2}
{K\sum_{k=1}^{K}a_k^2}.
$$

Interpretation:

- \(J\approx1\): client performance is comparatively even;
- smaller \(J\): performance is concentrated on a subset of clients.

A fairness result should never be reduced to Jain's index alone. The runtime also reports median, p10, worst-client, best-client, standard deviation, range, and corresponding loss statistics.

---

## 10. Benchmark statistics and reproducibility

The benchmark runner executes each benchmark cell in a fresh process and preserves exact per-cell evidence.

Plan only:

```bash
python scripts/run_benchmark_matrix.py --dry-run
```

Example execution:

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

For observations \(x_1,\ldots,x_n\),

$$
\bar x=\frac1n\sum_{i=1}^{n}x_i,
$$

and

$$
s=\sqrt{\frac{1}{n-1}\sum_{i=1}^{n}(x_i-\bar x)^2}.
$$

### 10.2 Matched-seed differences

When algorithm A and B use the same seeds and concrete partitions, define

$$
d_i=x_i^{(A)}-x_i^{(B)}.
$$

Then

$$
\bar d=\frac1n\sum_i d_i.
$$

The paired standardized effect size reported by the benchmark layer is Cohen's \(d_z\):

$$
d_z=\frac{\bar d}{s_d},
$$

where \(s_d\) is the sample standard deviation of the paired differences.

### 10.3 Bootstrap confidence intervals

The aggregation layer provides deterministic percentile-bootstrap confidence intervals by repeatedly resampling the seed-level observations with replacement under a controlled bootstrap seed.

The point is not to manufacture significance from repeated rounds. The independent unit for these benchmark summaries is the configured experiment seed/cell observation.

### 10.4 Paired sign-flip tests

For paired differences \(d_i\), the null distribution is generated by sign flips

$$
d_i^{*}=s_i d_i,
\qquad s_i\in\{-1,+1\},
$$

and compared against the observed paired statistic.

### 10.5 Multiple-comparison control

Holm-Bonferroni adjustment orders raw p-values

$$
p_{(1)}\le\cdots\le p_{(m)}
$$

and compares them sequentially against

$$
\frac{\alpha}{m-i+1}.
$$

The benchmark implementation records adjusted p-values rather than presenting a large matrix of uncorrected significance claims.

### 10.6 Reproducibility identity

A meaningful benchmark cell should retain at least:

- runtime identity (`root-simulator` or `distributed-platform`)
- source commit SHA
- effective configuration
- dataset name and official split identity
- partition strategy and parameters
- random seed
- exact partition hash
- algorithm and algorithm parameters
- number of rounds/local epochs
- sampling strategy
- privacy parameters and final accountant result
- final model checkpoint
- result summary

Configuration alone is not considered sufficient evidence because two executions may request the same abstract partition parameters but realize different sample assignments if the seed or code changes.

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
  |      +--> official test split only
  |      +--> training-derived class allocation proportions
  |
  +--> client accuracy/loss/fairness metrics
  |
  +--> summary.md + summary.json
```

### 11.2 Distributed platform

```text
                           +----------------------+
                           |      Web / UI        |
                           +----------+-----------+
                                      |
                                  REST / WS
                                      |
                           +----------v-----------+
                           |   Go Control Plane   |
                           | execution lifecycle  |
                           +----+-------------+---+
                                |             |
                         gRPC   |             | persistence / tracking
                                |             |
                     +----------v---+     +---v-------------------+
                     | C++20        |     | PostgreSQL / Redis    |
                     | Coordinator  |     | MinIO / MLflow        |
                     +------+-------+     +-----------------------+
                            |
                        gRPC / protobuf
                            |
                     +------v----------------+
                     | Python ML Worker(s)   |
                     | training / privacy    |
                     +-----------------------+

Observability: Prometheus + Grafana + OpenTelemetry
Security: mTLS identities + signed messages + replay protection
```

### 11.3 Control-plane lifecycle

The Go API maintains durable execution records under `/api/v1/executions`. Runtime reconciliation refreshes stable executions while avoiding races with active lifecycle transitions such as `STARTING`, `PAUSING`, `RESUMING`, and `CANCELING`.

Local-backend pause/resume is communication-round-boundary safe. Checkpoint SHA-256 sidecars detect changed/corrupted checkpoint bytes before restore; SHA-256 is an integrity check, not keyed authenticity against an actor able to replace both checkpoint and digest.

---

## 12. Installation

### Requirements

For the root runtime:

- Python 3.11+
- Git
- CPU or CUDA-capable PyTorch environment

For full platform development:

- Docker Engine / Docker Desktop with Compose
- CMake
- C++20 compiler
- Go toolchain
- Protocol Buffers/gRPC development dependencies when building native RPC components directly

### Clone

```bash
git clone https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy.git
cd Federated-Learning-on-Non-IID-Data-Differential-Privacy
```

### Root Python dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Platform Python package

```bash
python -m pip install -e './python[dev,security]'
```

If you only need the stable package runtime rather than development tooling:

```bash
python -m pip install -e './python[security]'
```

---

## 13. Running the root research runtime

### Desktop

```bash
python main.py
```

The desktop UI launches the same underlying CLI runtime used by terminal execution.

### CLI default

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

### FashionMNIST + FedProx + strong Dirichlet skew

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

### Datasets

The root runtime uses official torchvision train/test splits.

| Dataset | Train | Test | Classes | Channels |
|---|---:|---:|---:|---:|
| MNIST | 60,000 | 10,000 | 10 | 1 |
| FashionMNIST | 60,000 | 10,000 | 10 | 1 |
| CIFAR-10 | 50,000 | 10,000 | 10 | 3 |
| CIFAR-100 | 50,000 | 10,000 | 100 | 3 |

MNIST and FashionMNIST are resized to `32x32` so the root runtime can use the same GroupNorm CNN family across the supported image workloads while selecting the correct output dimension for the dataset class count.

---

## 14. Configuration

Primary configuration file: [`config.yaml`](config.yaml)

A representative configuration is:

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

That file, not the original YAML alone, should be used when reporting the exact executed experiment.

---

## 15. Generated artifacts

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

The training partition manifest includes, where applicable:

- dataset
- strategy and parameters
- partition seed
- partition SHA-256
- per-client sample counts
- per-client label histograms
- quantity coefficient of variation
- normalized label entropy
- Jensen-Shannon divergence
- class coverage
- effective label count

`summary.json` is the machine-readable result interface consumed by benchmark tooling.

### Benchmark directory

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
- web application
- PostgreSQL
- Redis
- MinIO
- MLflow
- Prometheus
- Grafana
- OpenTelemetry Collector

The distributed runtime contains execution persistence/reconciliation, worker identity, signed-message validation, replay protection, secure-aggregation components, failure/heterogeneity simulation, distributed metrics, and release-validation infrastructure.

Do not compare a distributed-platform result with a root-simulator result without recording the runtime identity. They are different execution systems even when algorithm names are the same.

---

## 17. Developer workflow

A contributor should be able to answer three questions before modifying a component:

1. Which runtime owns this behavior?
2. What invariant must remain true?
3. Which test or evidence demonstrates that invariant?

### Python root tests

```bash
python -m pytest tests python/tests
```

### Ruff

```bash
python -m ruff check .
python -m ruff format --check .
```

### Repository documentation/runtime contract

```bash
python scripts/validate_repository_docs.py
```

### Baseline unittest target

```bash
make test-baseline
```

### Protocol contracts

```bash
make proto-check
```

### PKI validation

```bash
make pki-verify
```

### Go

```bash
cd go
go test ./...
go vet ./...
go build ./...
```

### C++ debug build

```bash
cmake -S cpp -B build/cpp-debug -DCMAKE_BUILD_TYPE=Debug
cmake --build build/cpp-debug
ctest --test-dir build/cpp-debug --output-on-failure
```

### C++ release build

```bash
cmake -S cpp -B build/cpp-release -DCMAKE_BUILD_TYPE=Release
cmake --build build/cpp-release
ctest --test-dir build/cpp-release --output-on-failure
```

### Sanitizers

```bash
make cpp-asan
make cpp-ubsan
```

### Static analysis and formatting

```bash
make cpp-format-check
make cpp-tidy
```

### Native aggregation benchmark

```bash
make cpp-benchmark
```

### Development rule of thumb

If a change affects an algorithm, privacy mechanism, secure protocol, runtime state transition, dataset contract, or benchmark schema, update its executable validation together with the implementation. Documentation-only support claims are not accepted as runtime evidence.

---

## 18. Validation and CI

The repository CI exercises more than unit tests. Depending on changed paths and workflow scope, validation includes:

- Python tests
- Ruff lint/format
- type checking
- Go tests/vet/build
- C++ debug/release builds
- CTest
- clang-format
- clang-tidy
- AddressSanitizer
- UndefinedBehaviorSanitizer
- protobuf compatibility checks
- PKI verification
- secret/security checks
- distributed runtime validation
- infrastructure validation
- benchmark evidence checks
- release qualification checks
- supply-chain artifact checks

A green individual job is not equivalent to a green release. Release qualification is bound to the exact commit SHA.

---

## 19. v3.0.0 release qualification

The source package reports:

```text
fl-platform == 3.0.0
```

The final `v3.0.0` tag is publishable only when the **same tagged commit** has successful runs for all required release workflows:

1. `ci.yml`
2. `v3-release-candidate.yml`
3. `v3-distributed-runtime.yml`
4. `v3-final-qualification.yml`

The release-artifact workflow then:

- verifies same-SHA qualification;
- downloads exact final qualification evidence;
- checks package/tag parity;
- builds API and Python-worker images;
- resolves immutable image digests;
- generates the release image lock;
- renders digest-pinned deployment artifacts;
- builds wheel and sdist artifacts;
- creates a source archive;
- generates CycloneDX SBOM data;
- writes artifact SHA-256 metadata;
- creates provenance attestations;
- publishes the GitHub release.

### Empirical qualification baseline

The stable v3 contract includes a real five-seed root-runtime baseline:

- runtime: `root-simulator`
- dataset: MNIST
- algorithm: FedAvg
- partition: IID
- privacy: non-private
- seeds: `11, 23, 37, 53, 71`
- qualification rounds: 1 per seed

This qualifies the defined stable baseline. It does **not** imply that every algorithm × dataset × privacy × attack × heterogeneity combination has been exhaustively executed.

See [`RELEASE_NOTES_v3.0.0.md`](RELEASE_NOTES_v3.0.0.md) for the stable support contract and explicit experimental boundaries.

---

## 20. Security, privacy, and threat-model boundaries

This section is intentionally conservative.

### Differential privacy does not imply secure aggregation

DP limits information leakage from a released randomized mechanism under a stated neighboring relation. It does not hide raw network messages before the mechanism runs.

### Secure aggregation does not imply differential privacy

Secure aggregation hides individual values from an aggregator under its protocol assumptions. An exact aggregate can still leak information, especially across repeated rounds or small cohorts.

### Secure aggregation does not stop poisoning

A malicious client can submit a harmful update while remaining cryptographically authenticated. Robust aggregation, anomaly detection, admission control, and trust management address different parts of that problem.

### mTLS is not application authorization by itself

mTLS establishes authenticated transport identity. The platform additionally binds application messages to worker identity/signing-key/replay state where required.

### Hashes are not keyed authentication

SHA-256 artifact or checkpoint hashes detect changed bytes when the expected digest is trusted. They do not protect against an attacker who can replace both content and digest metadata.

### Current non-claims

The project does not claim:

- formal cryptographic certification;
- regulatory compliance;
- Internet-scale production validation;
- immunity to Byzantine clients;
- Sybil resistance by secure aggregation alone;
- private SCAFFOLD under the root client-level-DP guarantee;
- production-grade threshold secure-aggregation dropout recovery;
- crash-resumable in-flight secure rounds;
- verified physical edge energy/thermal performance.

---

## 21. Known limitations

Stable support is narrower than the total amount of code in the repository.

Current important boundaries include:

- the root runtime is single-machine orchestration, not a physical cross-device deployment;
- root client-level DP supports FedAvg/FedProx, not SCAFFOLD;
- feature/covariate-shift partitioning is not a root partition strategy;
- FedSAM, Ditto, and Per-FedAvg are platform-worker capabilities rather than root CLI algorithms;
- true distributed asynchronous training remains experimental;
- threshold secure-aggregation dropout recovery is not promoted as a production capability;
- an in-flight secure round is not resumed after coordinator process loss;
- FEMNIST, Shakespeare, and Sent140 loaders remain outside the stable v3 release scope;
- combined robust aggregation + DP and robust aggregation + secure aggregation are not generally release-qualified;
- physical multi-host throughput/latency guarantees are not claimed;
- physical ARM64 edge energy/thermal/throughput guarantees are not claimed;
- the complete attack × privacy × heterogeneity benchmark cross-product has not been empirically exhausted.

Unsupported combinations are expected to fail closed instead of being reported as silently supported.

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
├── web/                            # dashboard/frontend
├── infra/                          # Docker/Kubernetes/observability
├── scripts/                        # validation, release, benchmark utilities
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

When documentation disagrees, the precedence is:

1. executable source and enforced tests;
2. `RUNTIME.md` runtime contract;
3. current release qualification documents;
4. older status/audit reports.

Historical reports are useful context, but they should not override newer executable evidence.

---

## 23. Academic use and citation

This repository is suitable for controlled experiments where the exact implementation, partition, privacy configuration, seed, and artifact set are reported together.

For academic work, a result should state at minimum:

- repository version or commit SHA;
- runtime identity;
- dataset and official split;
- client count;
- partition method and realized partition hash;
- model architecture;
- algorithm and optimizer parameters;
- number of communication rounds;
- local epochs and batch size;
- client sampling method/rate;
- aggregation weighting;
- privacy unit and neighboring relation if DP is enabled;
- clipping norm;
- noise multiplier;
- target/final \((\varepsilon,\delta)\) if applicable;
- random seeds;
- number of independent runs;
- mean/dispersion/confidence interval;
- client tail/fairness metrics when heterogeneity is central to the claim.

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

Do not report only a configured Dirichlet \(\alpha\) value. Report the exact partition artifact/hash as well. The same principle applies to privacy: report the accountant inputs and achieved budget, not only a label such as “DP enabled.”

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

This repository is maintained as both an engineering codebase and an academic experiment platform. Design choices are documented with an emphasis on implementation boundaries, reproducibility, measurable evidence, and avoiding claims that exceed the validated runtime.

---

## 25. Contributing and license

Contributions are welcome when they preserve runtime correctness and include validation appropriate to the change.

Before opening a pull request, read:

- [`CONTRIBUTING.md`](CONTRIBUTING.md)
- [`SECURITY.md`](SECURITY.md)
- [`RUNTIME.md`](RUNTIME.md)

Security issues should follow the process in `SECURITY.md` rather than being disclosed through a public issue when the report contains exploitable details.

### License

Copyright © 2026 **Md Shahanur Islam Shagor**.

Licensed under the **Apache License 2.0**. See [`LICENSE`](LICENSE) for the full license text.
