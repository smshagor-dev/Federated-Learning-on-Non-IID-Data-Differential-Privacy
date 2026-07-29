# Federated Learning on Non-IID Data with Differential Privacy

Desktop-first federated learning studio for studying non-IID optimization, client drift, and central differential privacy under a trusted-server simulation.

## Overview

The active root workflow is launched with `python main.py`. It opens a PySide6 desktop application or runs the simulator directly in CLI mode, depending on flags. The root runtime covers:

- MNIST and CIFAR-10 classification.
- Dirichlet and pathological non-IID partitioning.
- FedAvg, FedProx, and SCAFFOLD in the active simulator.
- Poisson or fixed-without-replacement client sampling.
- Central client-level differential privacy for the trusted-server path.
- CSV, plots, distribution artifacts, and markdown summaries.

The repository also contains auxiliary Python, C++, and Go subsystems under [python/src/fl_platform](python/src/fl_platform), [cpp](cpp), and [go](go). Those subsystems are useful for broader experimentation, but they are not the default codepath executed by the root desktop runtime.

## Entry Points

- Desktop or auto mode: `python main.py`
- Explicit CLI mode: `python main.py --cli --config config.yaml`
- Optional GUI flag: `python main.py --gui`

Core files:

- [main.py](main.py)
- [experiment_runtime.py](experiment_runtime.py)
- [federated/client.py](federated/client.py)
- [federated/server.py](federated/server.py)
- [federated/dp_accountant.py](federated/dp_accountant.py)
- [data/partitioner.py](data/partitioner.py)
- [utils/metrics.py](utils/metrics.py)
- [utils/logger.py](utils/logger.py)
- [desktop](desktop)

## Problem Formulation

Assume $K$ federated clients. Client $k$ owns dataset

$$
\mathcal{D}_k = \{(x_i, y_i)\}_{i=1}^{n_k}.
$$

The total sample count is

$$
N = \sum_{k=1}^{K} n_k.
$$

The client-local empirical risk is

$$
F_k(w) = \frac{1}{n_k}\sum_{(x_i, y_i)\in \mathcal{D}_k}\ell(w; x_i, y_i).
$$

The global objective is

$$
F(w) = \sum_{k=1}^{K} p_k F_k(w),
\qquad
p_k = \frac{n_k}{N}.
$$

Where:

- $w$ is the global model parameter vector.
- $F_k$ is client-local empirical risk.
- $F$ is the weighted federated objective.
- $n_k$ is the number of samples on client $k$.
- $N$ is the total number of samples.
- $p_k$ is the sample-proportional aggregation weight.
- $\ell$ is the classification loss, implemented as cross-entropy in the root simulator.

## Runtime Mathematics

At communication round $t$, the server holds $w_t$. For each selected client $k \in S_t$:

$$
w_{t,0}^{k} = w_t.
$$

Local optimization follows

$$
w_{t,e+1}^{k} = w_{t,e}^{k} - \eta \nabla \ell(w_{t,e}^{k}; B_e).
$$

After local training, the transmitted client update is

$$
\Delta_k = w_{t,E}^{k} - w_t.
$$

FedProx adds the local proximal penalty

$$
\ell_{\text{prox}} = \ell + \frac{\mu}{2}\lVert w - w_t \rVert_2^2.
$$

SCAFFOLD keeps global and local control variates $c$ and $c_k$, and updates the client control state using the executed local step count $\tau_k$:

$$
c_k^{+} = c_k - c - \frac{\Delta_k}{\tau_k \eta}.
$$

The root implementation uses `tau_k = local_steps` from the actual client loop.

## Aggregation Semantics in the Root Runtime

The active runtime supports two aggregation weightings, with validation rules enforced in [experiment_runtime.py](experiment_runtime.py).

### Uniform weighting

For `federated.aggregation_weighting: uniform`, the aggregate update is

$$
\bar{\Delta}_t = \frac{1}{|S_t|}\sum_{k \in S_t}\Delta_k.
$$

This is the only weighting allowed when root differential privacy is enabled, and it is also the only weighting allowed for SCAFFOLD.

### Sample-count weighting

For `federated.aggregation_weighting: sample_count`, the aggregate update is

$$
\bar{\Delta}_t = \sum_{k \in S_t}\frac{n_k}{\sum_{j \in S_t} n_j}\Delta_k.
$$

This weighting is available for FedAvg and FedProx when differential privacy is disabled.

### Server update

The server applies

$$
w_{t+1} = w_t + \eta_s \bar{\Delta}_t.
$$

In code, that update is performed by `Server.aggregate()` and `Server._apply_delta()` in [federated/server.py](federated/server.py).

## Differential Privacy in the Root Runtime

The root runtime implements central client-level DP under a trusted-server assumption. The privacy mechanism is:

1. Each selected client computes its raw model delta.
2. The client delta is clipped to norm $C$ (configured by `dp.update_clip_norm`) when DP is enabled.
3. The server sums the clipped deltas.
4. The server adds one Gaussian noise draw to that aggregate sum.
5. The server divides by the cohort size to form the released average update.

With uniform weighting, the privatized sum is

$$
\widetilde{\Delta}_t=\sum_{k\in S_t}\mathrm{clip}\left(\Delta_k,C\right)+\mathcal{N}\left(0,\sigma^2C^2I\right),
$$

and the released update is

$$
\bar{\Delta}_t = \frac{1}{|S_t|}\widetilde{\Delta}_t.
$$

Important scope notes:

- This is not local DP. Noise is not added independently on each client in the root runtime.
- This is not secure aggregation in the active root codepath.
- `optimizer.grad_clip_norm` and `dp.update_clip_norm` are separate controls.
- `optimizer.grad_clip_norm` clips optimization gradients before the optimizer step.
- `dp.update_clip_norm` clips the final transmitted client update before server aggregation.

## Privacy Accounting

The root runtime uses the RDP moments accountant in [federated/dp_accountant.py](federated/dp_accountant.py). For sampling rate $q$, noise multiplier $\sigma$, and target $\delta$, the reported privacy value is obtained by composing per-round RDP and converting back to $(\epsilon, \delta)$.

At the documentation level, the conversion is

$$
\epsilon(\delta) = \min_{\alpha > 1}\left(\epsilon_{\mathrm{RDP}}(\alpha) + \frac{\log(1/\delta)}{\alpha - 1}\right).
$$

Across multiple rounds, the RDP curves add. When `algorithm: all` is used, the root summary composes the final RDP curves from each released run and reports a combined epsilon for all released outputs.

Current validation rules:

- DP requires `federated.sampling_strategy: poisson`.
- DP requires `federated.aggregation_weighting: uniform`.
- SCAFFOLD requires `federated.aggregation_weighting: uniform`.
- Deterministic noise mode is test-only and requires `dp.test_noise_seed`.

## Sampling Semantics

The runtime supports:

- `poisson`: each client is independently included with probability $q$.
- `fixed_without_replacement`: a rounded cohort size is sampled without replacement.

When DP is enabled, only Poisson sampling is accepted by config validation.

## Metrics Logged by the Root Runtime

Per-round CSV output includes:

- `test_acc`
- `test_loss`
- `epsilon`
- `weight_variance`
- `raw_client_drift`
- `clipped_client_drift`
- `mean_unclipped_update_norm`
- `mean_clipping_factor`
- `fraction_clients_clipped`
- `aggregate_noise_norm`
- `avg_client_loss`
- `cohort_size`
- `participation_rate`

Interpretation:

- `raw_client_drift` measures disagreement before DP clipping.
- `clipped_client_drift` measures disagreement after DP clipping.
- `aggregate_noise_norm` is the norm of the single Gaussian noise draw added at server level.

## Non-IID Data Partitioning

The active root simulator provides:

- Dirichlet label skew through `partition_dirichlet`.
- Pathological shard-style partitioning through `partition_pathological`.

These are implemented in [data/partitioner.py](data/partitioner.py).

## Desktop Workflow

The desktop app under [desktop](desktop) writes validated configuration snapshots, launches the simulator as a subprocess, and renders experiment outputs from generated result artifacts. The root UI is intended to stay operational even when experiment settings change, so the configuration service normalizes and validates YAML before runtime launch.

## Auxiliary Components

The repository also contains:

- Additional privacy and orchestration code in [python/src/fl_platform](python/src/fl_platform)
- C++ aggregation and coordinator code in [cpp](cpp)
- Go service and transport code in [go](go)

Those components may expose extra algorithms or security primitives, but they should not be assumed to match the exact semantics of the root desktop runtime unless they are being invoked directly in their own execution path.

## Validation and Reproducibility

Useful commands:

```bash
python main.py --help
python main.py --cli --config config.yaml
python -m pytest tests python/tests -q
python scripts/validate_repository_docs.py
```

Generated root artifacts include CSV logs, plots, distribution summaries, and `summary.md` files describing the completed run.
