# Federated Learning on Non-IID Data with Differential Privacy

A modular PyTorch simulation engine for the comparative study:

> **"Federated Learning on Non-IID Data with Differential Privacy: A Comparative
> Analysis of FedAvg, FedProx, and SCAFFOLD under Strict Differential Privacy
> Constraints"**

The engine simulates client heterogeneity via Dirichlet(α) label skew,
applies client-level differential privacy (L2 update clipping + Gaussian
noise), tracks the cumulative (ε, δ) budget with an RDP/moments accountant,
and compares the convergence stability of three aggregation algorithms.

---

## 1. Project layout

```
federated_dp_research/
├── config.yaml               # All hyperparameters and the experiment matrix
├── requirements.txt
├── main.py                   # Orchestration + CLI
├── data/
│   └── partitioner.py        # CIFAR-10/MNIST loading, Dirichlet & pathological splits
├── models/
│   └── networks.py           # GroupNorm CNN (no BatchNorm — FL-safe)
├── federated/
│   ├── client.py             # Local SGD, FedProx term, SCAFFOLD correction, DP clip+noise
│   ├── server.py             # FedAvg / FedProx / SCAFFOLD aggregation
│   └── dp_accountant.py      # Subsampled-Gaussian RDP (moments) accountant
├── utils/
│   ├── metrics.py            # Test evaluation, weight variance, client drift
│   └── logger.py             # CSV logs + publication-quality plots
└── README.md
```

## 2. Setup

Python 3.10+ is required.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

The first run downloads CIFAR-10 (~170 MB) or MNIST (~12 MB) into
`./data_raw/`.

## 3. Running experiments

```bash
# Launch the desktop GUI
python main.py
python main.py --gui

# Run the default terminal experiment from config.yaml
python main.py --cli

# Full three-algorithm comparison under identical conditions
python main.py --algo all

# Single algorithms
python main.py --algo fedavg
python main.py --algo scaffold --rounds 100

# Vary heterogeneity: 0.1 = extreme non-IID, 100 = near-IID
python main.py --algo all --alpha 0.5

# Vary the privacy level
python main.py --algo all --noise 1.2          # stronger privacy, lower ε
python main.py --algo all --dp off             # non-private upper baseline

# Faster smoke test
python main.py --dataset MNIST --rounds 10 --algo all
```

CLI flags: `--algo {fedavg,fedprox,scaffold,all}`, `--alpha FLOAT`,
`--dp {on,off}`, `--noise FLOAT`, `--rounds INT`, `--dataset {CIFAR10,MNIST}`,
`--seed INT`, `--config PATH`, `--gui`, `--cli`. Anything not overridden comes from
`config.yaml`.

The GUI exposes the experiment settings in a form, launches `main.py`,
streams live logs, and shows the latest `results/summary.md` in the same
window.

## 4. Outputs (written to `results/`)

| File | Content |
|---|---|
| `distribution.png` | Stacked bars of class counts per client — visual proof of the non-IID split |
| `run_<algo>.csv` | Per-round log: accuracy, loss, ε, weight variance, client drift |
| `accuracy_vs_rounds.png` | FedAvg vs FedProx vs SCAFFOLD convergence curves |
| `privacy_loss_tradeoff.png` | Accuracy as a function of spent privacy budget ε |
| `weight_variance.png` | Weight variance and client-drift curves (log scale) |
| `summary.md` | Formatted Markdown results table (also printed to stdout) |

## 5. Method notes

**Non-IID partitioning.** For every class, client shares are drawn from
Dir(α·1). At α = 0.1 most clients hold only 1–3 dominant classes; the split
is re-drawn until every client owns at least `min_partition_size` samples.
A pathological k-classes-per-client split is also available
(`partition: pathological`).

**Model.** BatchNorm is excluded on purpose: running statistics diverge
across heterogeneous clients and are corrupted by parameter averaging. All
normalization uses `GroupNorm(num_groups=2)`, which is batch-independent.

**Differential privacy (DP-FedAvg style, client-level).** Per-batch
gradients are clipped to C during local SGD for stability; the transmitted
model update Δ = w_local − w_global is clipped to L2 norm C (bounding each
client's sensitivity) and perturbed with Gaussian noise N(0, (σC)²I) before
leaving the client. One communication round = one step of the subsampled
Gaussian mechanism with sampling rate q = clients-per-round / total-clients.

**Privacy accounting.** `dp_accountant.py` implements the RDP form of the
moments accountant: closed-form integer-order RDP of the subsampled Gaussian
(Mironov et al., 2019), additive composition over rounds, and conversion
ε = min_α [T·ε_RDP(α) + log(1/δ)/(α−1)] at δ = `target_delta`.

**SCAFFOLD.** Server keeps a global control variate c and one cᵢ per client
(zero-initialized). Clients correct each SGD step with (c − cᵢ) and refresh
cᵢ via Option II: cᵢ⁺ = cᵢ − c + (x − yᵢ)/(K·η). Momentum is disabled for
SCAFFOLD since the correction assumes plain SGD steps. The cᵢ update uses
the already-noised update, so no extra privacy is leaked.

## 6. Interpreting the results

- **accuracy_vs_rounds** — Under extreme skew (α = 0.1) FedAvg typically
  oscillates; FedProx (μ = 0.01) damps oscillation by penalizing local
  divergence; SCAFFOLD corrects drift directly but is more sensitive to DP
  noise because noise enters its control variates.
- **privacy_loss_tradeoff** — Read "what accuracy does each ε buy". Curves
  further up-and-left are better. Compare σ ∈ {0.6, 0.8, 1.2} to trace the
  frontier; ε at δ = 1e-5 after 50 rounds with q = 0.2, σ = 0.8 is printed
  each round.
- **weight_variance** — Rising variance/drift signals client divergence.
  Expect SCAFFOLD < FedProx < FedAvg without DP; with DP the injected noise
  puts a floor under both curves (noise dominates once updates are small).

## 7. Reproducibility

Seeds for `random`, NumPy, and PyTorch (CPU + CUDA) are fixed from
`config.yaml` (`seed: 42`), cuDNN runs in deterministic mode, and every
algorithm in a comparison starts from the identical model initialization and
identical data partition. Exact per-round metrics are preserved in the run
CSVs.

## 8. Extending the engine

- **New aggregation algorithm** — add a branch in `Server.aggregate` and (if
  it changes the local objective) in `Client.train`; register the name in
  `SUPPORTED_ALGORITHMS`.
- **New dataset** — add a loader branch in `data/partitioner.py::get_dataset`
  returning `(train, test, num_classes, in_channels)`.
- **New model** — register it in `models/networks.py::build_model` (keep it
  BatchNorm-free).
- **Sweeps** — loop `python main.py --noise ...` / `--alpha ...` from a shell
  script; every run writes an independent CSV.
