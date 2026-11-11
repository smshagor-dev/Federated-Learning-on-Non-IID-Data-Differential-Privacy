# IEEE TAI full 50-round execution and recovery guide

This is an **execution protocol**, not a claim that 1,680 measured experiments have been completed.

## Primary factorial matrix

| Dimension | Values |
|---|---|
| Official datasets | MNIST, FASHIONMNIST, CIFAR10, CIFAR100 |
| Training partitions | IID; Dirichlet alpha 1.0, 0.3, 0.1, 0.05; pathological two-class; lognormal quantity skew sigma 1.0 |
| Algorithms | fedavg, fedprox, scaffold, ditto, per_fedavg, fedsam |
| Paired seeds | 11, 23, 37, 53, 71, 83, 97, 109, 127, 149 |
| Primary budget | 50 rounds, 20 clients, 20% sampling, two epochs per client, batch size 64, DP disabled |

Total: **4 × 7 × 6 × 10 = 1,680** independent *training runs*. Methods should use matched per-seed partitions and selected clients; the statistical unit of replication is the seed, **not** the client or round.

### Bounded GitHub Actions

- `IEEE TAI primary 50-round execution`: workflow `.github/workflows/tai-pfl-primary-50-round.yml`; on the execution branch, its first push launches only one 50-round MNIST/IID/FedAvg/seed11 cell. The manual workflow supports `shard_index`, `shard_count`, and `max_cells` inputs, capped to eight cells per job to protect compute resources.
- `IEEE TAI RQ4 50-round sensitivity`: workflow `.github/workflows/tai-pfl-rq4-50-round.yml`; first push runs MNIST/IID/seed11/Per-FedAvg primary followed by adaptation steps 3 and 5 using that exact completed checkpoint.
- Official four-dataset six-method **one-round** pilot: Actions run `42873837782`. That pilot **cannot** be included in the full study's 50-round cells.
- Both 50-round workflows must *successfully complete* and pass `tai_pfl.analysis._check_cell` before their outputs become eligible primary data. An active job, its plan, or a partially uploaded archive contributes **zero** completed cells.
- Each runner has a bounded time limit, and large cells may time out. GitHub Actions limits, available compute minutes and archive retention require a dedicated GPU/HPC cluster to guarantee full-scale execution. **Do not start 1,680 concurrent billable runners blindly.**

### HPC / workstation execution

The deterministic scheduler enumerates the entire matrix; `--execute` is required to train real data:

```bash
python scripts/run_tai_1680.py --shard-count 1680 --shard-index 0
python scripts/run_tai_1680.py --shard-count 1680 --shard-index 0 --max-cells 1 --execute --skip-complete --archive-incomplete
```

Indices are zero-based, deterministic and must be assigned exactly once across workers. Index 0 corresponds to MNIST/IID/FedAvg/seed11; index 40 corresponds to MNIST/IID/Per-FedAvg/seed11. Larger `shard_count` partitions the complete set by index modulo shard_count.

**Recovery behavior:** the launcher verifies completed outputs before skipping them; it refuses mismatched source commits, protocols or partitions. Incomplete cells may be moved to `_failed_attempts` for audit rather than silently reused. Currently an interrupted cell **must restart training**; round-level checkpoint resume is **not yet implemented** and must not be advertised as working. Archived GitHub Actions artifacts are isolated per workflow run and do not automatically accumulate across runs. Download and safely merge only verified cells into the eventual full dataset.

Each full training result must retain original official held-out client correct/total counts, serialized train/test indices, source SHA, requested hyperparameters, 50-round `rounds.csv`, completed checkpoint and any personalized checkpoints.

### Full matrix analysis and RQs

```bash
python -m tai_pfl.analysis --results-root benchmarks/tai-pfl
```

This must fail until every required 50-round primary cell is verified. Never pass a synthetic fixture, planned cell, partial run, or one-round pilot as measured primary evidence.

**RQ1:** paired differences in mean, p10 and worst-client accuracy across global and personalized models.  
**RQ2:** utility–fairness concordance; a larger Jain index alone is not an absolute predictive gain.  
**RQ3:** actual heterogeneity metrics plus condition×personalization interactions against IID.  
**RQ4:** cross-dataset/seed robustness plus adaptation sensitivity with steps `1,3,5`.

RQ4 needs `4 datasets × 7 conditions × 10 seeds = 280` real 50-round Per-FedAvg primary checkpoints (already included among 1,680), and `280 × 2 = 560` additional *evaluation-only* derived 3-step and 5-step results. Reusing the same **verified 50-round training** is a valid within-seed adaptation-budget comparison; it is not 560 new independently trained models.

For each verified 50-round Per-FedAvg primary cell:

```bash
python scripts/run_tai_rq4_from_primary.py \
  --primary-cell benchmarks/tai-pfl/mnist/iid/11/per_fedavg \
  --output-root benchmarks/tai-pfl-rq4 \
  --data-root data_raw \
  --steps 3 5
```

The script recomputes adaptation-step-1 predictions from the original checkpoint **before** doing 3 and 5 steps. If reference predictions differ, it fails rather than producing a misleading comparison. No official test image may be used for adaptation.

After full coverage is available:

```bash
python -m tai_pfl.sensitivity \
  --root-step-1 benchmarks/tai-pfl \
  --root-step-3 benchmarks/tai-pfl-rq4/step-3 \
  --root-step-5 benchmarks/tai-pfl-rq4/step-5
```

Inferential comparisons use matched *independent seeds*, exact two-sided sign-flip tests, paired bootstrap intervals and Holm family-wise adjustment. Descriptive single-seed findings from current runs are **not** statistical evidence of RQ1–RQ4 superiority.

## Submission gate

Insert paper Results/Discussion/Conclusion **only** when 1,680 full training cells, all 560 sensitivity evaluations, RQ3 heterogeneity descriptors, paired inference, raw evidence and independent numerical QA are available. Report compute costs, failures and limitations; no claim of decreased rejection rate is guaranteed.
