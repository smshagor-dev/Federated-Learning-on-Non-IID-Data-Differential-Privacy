# IEEE TAI personalized federated learning — evidence audit

Audit date: 2026-11-10. Target manuscript: `TAI-2026-Sep-A-02187`, *Personalized Federated Learning Under Severe Statistical Heterogeneity: A Multi-Dataset Analysis of Accuracy, Tail Performance, and Client Fairness*.

This is an **evidence inventory and experimental research plan**, not a claim of scientific validation, a replacement for experimental results, or a software quality-assurance report. It distinguishes demonstrated measurements, executable source code, CI definitions, illustrative fixtures, and missing peer-review evidence.

## 1. Repository evidence that actually exists

| Evidence | Source | Scientific scope / limitations |
|---|---|---|
| Four dataset loaders: MNIST, FashionMNIST, CIFAR-10, CIFAR-100 | `data/partitioner.py` | Real torchvision datasets are available to the root runtime; existence does not prove that all were trained |
| IID, Dirichlet, pathological, quantity skew generators | `data/partitioner.py` | Deterministic per-seed partitions, provenance and realized heterogeneity descriptors; not empirical algorithm outcomes |
| FedAvg, FedProx, SCAFFOLD root training | `experiment_runtime.py`, `federated/` | Can run on real torchvision datasets using the root CLI without DP for all three |
| FedSAM, Ditto, first-order Per-FedAvg algorithms | `python/src/fl_platform/algorithms/` | Implemented in the **distributed worker** path, which currently uses synthetic image shards; **not** interchangeable with the real-dataset root benchmark |
| Post-run held-out per-client evaluation | `main.py` -> `utils/client_evaluation.py` | Root CLI evaluates final global checkpoint on disjoint official test data and augments `summary.json` (`schema_version >= 2`). This is **implemented**; do not mistake the earlier `run_cli()` summary for final saved evidence |
| Fairness metrics | `utils/metrics.py`; `python/src/fl_platform/personalization/metrics.py` | Mean, median, p10, worst-client, SD, and Jain computation, but metrics are not evidence of an experimental advantage without observations |
| Paired statistics and multiple-testing correction | `python/src/fl_platform/benchmark/statistics.py`, `results.py` | Bootstrap CI, paired sign-flip test, effect sizes, Holm adjustment, strict partition-hash comparisons |
| Five-seed MNIST / FedAvg / IID / one-round CI qualification definition | `.github/workflows/v3-final-qualification.yml` | Workflow is executable and uploads artifacts when run. Its source definition is **not** a verified complete archived numeric result for the target paper |
| Four-algorithm local CPU training timings (synthetic samples) | `docs/benchmarking.md`, `scripts/benchmark_algorithms.py` | Measured medians: GroupNormCNN FedAvg 93.518ms; FedSAM 173.862ms; Ditto 200.031ms; Per-FedAvg 129.004ms. These are **local training timings**, not test accuracy, generalization, or fairness |
| Byzantine robust aggregation supplementary summary data | `research/native-byzantine-robust-aggregation/`, v3.0.1 release assets | A **different paper**; must not enter the TAI PFL accuracy/fairness results |
| Research registry runtime `tmp/research-registry-runtime/summary.json` | Registry smoke/permissions/health checks | Not a federated model experiment |
| CI matrix manifest | `.github/workflows/v3-benchmark.yml` | Explicitly requires `evidence_complete == false` for generated plan; not empirical results |

A repository branch inventory returned **65 accessible branches** on 2026-11-10. Git trees were scanned for tracked CSV/TSV/JSONL/Parquet/NumPy/checkpoint and results/artifacts paths, including the research and benchmark branches. **No tracked complete, matched six-algorithm, four-dataset PFL accuracy/fairness matrix was found.** Git-ignored local runs and workflow artifacts were not fully accessible through this branch-tree inspection; absence from tracked trees is not proof no training ever occurred.

## 2. What is still missing for the manuscript

1. A **single comparable real-dataset experimental execution path** for all six claimed algorithms. The root runner rejects `fedsam`, `ditto`, and `per_fedavg`; the worker algorithms currently train on synthetic shards, not the four official image datasets.
2. Archived actual predictions/client metrics for *every* planned dataset, algorithm, partition and seed, including independent held-out client evaluation for both shared and personalized models. Global-model fairness is not a substitute for personalized-model fairness.
3. Evidence of comparable architecture, optimizer/training budget, client participation, personalization adaptation budget, validation-set tuning, and partition identity for all methods. CPU local-call timings do not establish experimental fairness of optimization budget.
4. Complete numeric final accuracy, mean and median client accuracy, p10 and minimum, relative dispersion/Jain, and **paired** effect estimates and uncertainty, without discarding poor or failed runs.
5. Archived exact configuration, code revision, data version, random seed, training **and held-out** partition hashes, per-client test sample counts, raw seed-level outcomes and source run logs.
6. An explicit rule for handling small or missing held-out client subsets, since p10 and minimum can otherwise be unstable; report sample counts and sensitivity analysis.
7. A defensible inferential design: with 5 paired seeds, a two-sided exact sign-flip test cannot attain p < 0.05 (minimum 2/2^5 = 0.0625) even without multiple-testing correction. Expand independent seeds and report effect sizes/CI; do not claim significance with the present five-seed design.

The manuscript currently describes an evaluation protocol and mathematical identities. The identities relating the Dirichlet concentration, Jain fairness and matched-seed variance are **known mathematical properties**, not by themselves novel multi-dataset empirical discoveries. Demonstrating an original research contribution needs **measured findings** not manufactured numbers.

## 3. Scientific gaps to investigate (hypotheses, not proven novelty)

- **G1: Mean versus lower-tail effect:** Does personalization improve the worst-served clients on held-out data, or do gains concentrate among already strong clients?
- **G2: Utility–fairness divergence:** When Jain equality increases, does absolute mean and p10 accuracy stay constant or improve, or is fairness merely equalized degradation?
- **G3: Mechanism-specific heterogeneity:** Are outcomes explained differently by label concentration, class-support mismatch and client sample-count skew, beyond nominal Dirichlet alpha?
- **G4: Evaluation sensitivity:** How robust are ranking and paired effects to the realized partition, independent seed, local adaptation budget and test-client sample size?

Whether these are gaps *relative to the state of the art* requires a separately dated literature review. Repository inspection establishes an **experimental evidence gap**, not novelty relative to published literature.

## 4. Candidate research questions and required numerical evidence

| RQ | Question | Required measurements |
|---|---|---|
| RQ1 | Under matched severe non-IID partitions, how do personalized methods change mean, p10 and worst-client held-out accuracy compared with global baselines? | Seed-level paired differences on official held-out subsets, CI, effect size; raw client outcomes |
| RQ2 | Does lower client-accuracy dispersion/Jain improvement coincide with non-decreasing absolute predictive utility? | Joint distribution of mean, p10, worst-client, SD and Jain; utility–fairness frontier; paired contrasts |
| RQ3 | How do label-skew severity, pathological label restriction and quantity skew modify the effects in RQ1–RQ2? | Realized label concentration/entropy/JS, client counts, stratified effects, interaction analysis |
| RQ4 | Are the conclusions stable across datasets, seeds and local adaptation budgets? | Full factorial replicated comparisons, budget-matched sensitivity, training cost, CI and corrected tests |

Keep algorithm-specific outcomes only if the same real dataset/holdout protocol is implemented. Explicitly label FedSAM a sharpness-aware *global* comparator rather than inherently personalized.

## 5. Proposed primary experiment matrix

- Real datasets: MNIST, FashionMNIST, CIFAR-10, CIFAR-100.
- Methods: FedAvg, FedProx, SCAFFOLD, Ditto, first-order Per-FedAvg, FedSAM.
- Conditions: IID, Dirichlet alpha in {1.0, 0.3, 0.1, 0.05}, pathological restricted class skew, quantity skew (define sigma and minimum client size).
- 20 clients, matched actual training partitions **and independently generated held-out client partitions**, identical seed per method.
- The existing manuscript's five seeds imply `4 datasets × 7 conditions × 6 methods × 5 seeds = 840` planned runs. These are **planned**, not executed.
- At least 10 independent paired seeds are recommended for uncertainty and multiple-testing analysis (would imply 1,680 runs), subject to computation/power planning; select and publish seeds/protocol **before** examining held-out outcomes.
- Non-DP primary study: turn off DP unless privacy is explicitly a research factor. The default `config.yaml` has DP enabled; the runner's `--epsilons none` overrides this.
- Hyperparameters must be selected using a validation split made **only from training data**, never by optimizing on the final held-out test. Match training/adaptation costs and explicitly document differences.
- Do not infer that a run was performed just because it appears in a matrix, workflow or README.

## 6. Run currently executable real-dataset baselines (not the full six-method study)

```bash
python scripts/run_benchmark_matrix.py \
  --benchmark-id tai-global-baselines \
  --datasets MNIST,FASHIONMNIST,CIFAR10,CIFAR100 \
  --algorithms fedavg,fedprox,scaffold \
  --partitions iid,dirichlet:1.0,dirichlet:0.3,dirichlet:0.1,dirichlet:0.05,pathological:2,quantity_skew:1.0 \
  --epsilons none \
  --seeds 11,23,37,53,71,83,97,109,127,149 \
  --rounds 50 \
  --minimum-replicates 10 \
  --resume
```

This is a proposed run command. It is **not evidence that the experiments have run**. The changed runner requires a completion receipt matching the exact effective request, source Git commit, and train/test partition hashes before `--resume` accepts an earlier result. Older results lacking a receipt must be examined separately, not silently inherited.

## 7. Publication gate

Do **not** mark the target study empirical-complete or add any `% accuracy`, `p`, `fairness improvement` or superiority claim to the manuscript until every reported value has an auditable source run, data split, seed, model identity, validation protocol, and comparison. Synthetic tests, CI success, unit tests, code, prefilled plans and unrelated timing tables must **never** fill empty paper results.
