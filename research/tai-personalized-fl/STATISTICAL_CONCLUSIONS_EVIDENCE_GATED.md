# IEEE TAI research — evidence-gated statistical conclusions (2026-11-10)

**Classification:** VERIFIED DESCRIPTIVE RESEARCH RESULTS; NOT FULL-MATRIX OR FINAL INFERENTIAL CONCLUSIONS.

Do not silently insert this text into the manuscript as if it represented all four datasets, seven heterogeneity conditions, six algorithms, and ten independent seeds. The recorded 50-round evidence supports only a subset of those comparisons.

## Archival sources and audit

- [MNIST/IID/FedAvg/seed 11; 50 rounds](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/42889667728): original 50-round source run `42881667778`, archived evidence independent re-audit SUCCESS. Source commit `f244086da6b7454ada387af379a62f20f41021f9`.
- [MNIST/IID/Per-FedAvg/seed 11; 50 rounds](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/42882897568): original source commit `5acdeb8b70b4d374c97c656d4448466c1911c17a`; complete training saved before orchestration import error.
- [RQ4 sensitivity recovery and verified 1/3/5-step evaluation](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/42895801421): successful reuse of the *same* genuine 50-round Per-FedAvg checkpoint, training-only local adaptation and official held-out evaluation. Evaluation output is in a distinct archival artifact.
- **Exact same client train and test partition hashes** for both 50-round FedAvg and Per-FedAvg: `59dc6faa8886b65f95a4ebf4a2472337787b02f21d18d32fed178ca279e8d2b6` (training), `b2f1dedb3ea4afe81e4b3591d42a06e6715770a79a6eae1675134cb17b242c9a` (test). Each covers 20 clients. Official MNIST training split has 60,000 samples; held-out test split has 10,000 samples.
- Both source commits have the **same Git blob hashes** for `tai_pfl/engine.py`, `tai_pfl/study.py`, `data/partitioner.py` and `models/networks.py`. Only separate orchestration/workflow revisions differ.
- `rounds.csv` in each archived experiment has all 50 sequential rounds. FedAvg final optimizer steps: 18,800; Per-FedAvg: 9,600 meta steps (one local inner support pass is additional compute). Equal rounds are **not** equal compute.
- One-round pilot outputs across all four official datasets are independently archived but excluded from the primary 50-round study.

## Verified 50-round, same-seed IID descriptive outcomes

| Metric | FedAvg shared model | Per-FedAvg deployed after adaptation=1 | Per-FedAvg minus FedAvg |
| --- | ---: | ---: | ---: |
| Test micro-accuracy | 99.07% | 97.84% | −1.23 percentage points |
| Mean client accuracy | 99.0702% | 97.8403% | −1.2299 pp |
| Client tenth-percentile accuracy | 98.2032% | 96.5993% | −1.6039 pp |
| Worst-client accuracy | 97.9960% | 95.4092% | −2.5868 pp |
| Jain equality index | 0.9999685 | 0.9999164 | −0.0000521 |

This is **one independent training seed** (`11`), not 20 independent replicates. Per-client test counts are dependent on the single global training run and must not be reinterpreted as seed-level treatment replicates.

## RQ4: verified same-checkpoint training-only adaptation budget, one seed

| Metric | Step 1 | Step 3 | Step 5 |
| --- | ---: | ---: | ---: |
| Test micro-accuracy | 97.84% | 97.94% | 98.05% |
| Mean client accuracy | 97.8403% | 97.9403% | 98.0503% |
| Tenth-percentile client accuracy | 96.5993% | 96.7857% | 97.1850% |
| Worst-client accuracy | 95.4092% | 96.5932% | 96.5932% |
| Jain equality index | 0.9999164 | 0.9999404 | 0.9999481 |

In this one MNIST/IID/seed-11 trajectory, adaptation step 5 improved micro-accuracy by +0.21 percentage points, tenth-percentile accuracy by +0.5857 pp, and minimum accuracy by +1.1840 pp compared with step 1. Step 5 was nevertheless **1.02 pp below FedAvg** in micro-accuracy on the matched seed; the extra adaptation also adds local compute and should not be presented as free efficiency gains.

## What the four RQs actually establish

**RQ1 (personalization and tail performance):** The observed single-seed MNIST/IID comparison favors FedAvg over the tested first-order Per-FedAvg implementation on mean and lower-tail client accuracy. It does **not** show that personalization fails across data distributions, datasets or seeds. Ditto/FedProx/SCAFFOLD/FedSAM have no matching 50-round entries in this comparison.

**RQ2 (absolute utility and Jain relative equality):** Both methods have near-unit Jain indices on this near-saturated IID task, despite meaningful changes in absolute p10/worst-client accuracy. Jain is scale-invariant in the relevant sense and should never be interpreted alone as a guarantee of absolute predictive benefit. This is an empirical caution, not a generalized Pareto frontier or proof of causation.

**RQ3 (heterogeneity mechanism):** Not established. A full factorial interpretation requires verified non-IID conditions, realized client-class divergence and matched within-seed difference-in-differences tests; no full-protocol evidence for those comparisons is currently available.

**RQ4 (robustness and adaptation budget):** Within the one observed MNIST/IID seed, larger training-only adaptation budgets 1→3→5 improved the Per-FedAvg micro, p10 and Jain descriptive metrics. There is **no demonstrated generalization** across random seeds, other datasets, non-IID conditions or computation-normalized training budgets.

## Statistical inference gate: explicit missing quantities

- Full primary target: **1,680 independently trained 50-round cells**; verified here **2** complete cells, or exactly **1 matched FedAvg/Per-FedAvg seed pair**. These counts are not updated unless further archived results independently pass the 50-round numerical audit.
- RQ4: **280 independently trained Per-FedAvg 50-round primary cells** are included in the 1,680, and **560 additional evaluation-only sensitivity records** (steps 3 and 5) are needed. Verified so far: **2 derived sensitivity records**, both from the same one seed.
- Exact two-sided seed-paired sign-flip p-value: **not estimable** from `n_paired_seeds = 1`; do not report `p = 0`, `p = 1`, or `p < 0.05`.
- Paired-seed 95% bootstrap CI: **not estimable** from one seed; no confidence interval may be invented from client outcomes.
- Holm-corrected confirmatory p-values: **not available**. The prespecified four severe non-IID CIFAR10/CIFAR100 p10 hypotheses have zero verified 10-seed matched comparisons.
- With ten independent paired seeds, an exact two-sided sign-flip test has minimum possible p-value 2/1024 = 0.001953125 *before* multiplicity correction. That is a feature of the design, not a result of these experiments.
- Before journal submission, verify all 1,680 primary cells, 560 derived sensitivity evaluations, source/partition/protocol integrity, exact paired inference, confidence intervals, Holm adjustment and novelty of the findings.

## Current manuscript-safe statistical conclusion paragraph

> On the available matched MNIST IID experiment (20 clients, 50 communication rounds, one seed), the shared FedAvg model achieved 99.07% held-out micro-accuracy, compared with 97.84% for deployed first-order Per-FedAvg after one training-only adaptation step. The corresponding tenth-percentile client accuracies were 98.20% and 96.60%, and worst-client accuracies were 98.00% and 95.41%, respectively. In a sensitivity analysis using the *same* trained Per-FedAvg checkpoint, raising the number of local adaptation steps from one to five increased micro-accuracy to 98.05%, tenth-percentile accuracy to 97.19%, and worst-client accuracy to 96.59%. These observations illustrate that local adaptation can affect absolute and lower-tail utility even when the trained global state and held-out client partitions are fixed. However, because they arise from one seed under one IID data condition, they provide descriptive evidence only. No seed-level confidence interval, statistical significance, cross-dataset ranking, or heterogeneity-moderated conclusion is supported. The final research questions remain unresolved until the prespecified replicated multi-dataset non-IID experiments and multiplicity-controlled inference are completed.

**Important:** This paragraph is an **interim result paragraph**, not a final statistical conclusion for journal submission. It should be placed in a draft results subsection labelled preliminary/pilot and replaced or extended when the missing full study evidence exists.
