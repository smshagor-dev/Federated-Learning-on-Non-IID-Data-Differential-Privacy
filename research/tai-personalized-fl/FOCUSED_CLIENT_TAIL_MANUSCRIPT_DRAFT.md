# Client-Tail Utility of Personalized Federated Learning under Severe Statistical Heterogeneity: A Matched Empirical Study

**Manuscript status:** Research draft — NOT submission-ready. Double-anonymized main manuscript; author identities and repository links must remain outside the submitted main text.

## Abstract

Personalized federated learning (PFL) is motivated by the observation that one global model may not serve heterogeneous clients equally well. However, a method's effect on average client accuracy need not agree with its effect on the lower tail of the client-utility distribution. We investigate this distinction in a controlled comparison of FedAvg, Ditto, and first-order Per-FedAvg on CIFAR-10 and CIFAR-100 under severe label heterogeneity induced by Dirichlet concentration α = 0.1. The protocol fixes 20 clients, 50 communication rounds, 20% participation per round, two local epochs, and matched train/test client partitions within each independent seed. The prespecified primary outcome is the tenth percentile (P10) of held-out client accuracy; secondary outcomes include mean and minimum client accuracy, pooled test accuracy, and Jain's relative equality index. We use whole training seeds—not clients or communication rounds—as independent replicates, with paired effect estimation, exact two-sided sign-flip tests, paired bootstrap confidence intervals, and multiplicity control. **The current evidence comprises three independently audited matched seed groups; the prespecified ten-seed confirmatory analysis is not yet complete.** Accordingly, this draft documents the research design and preliminary evidence boundary without reporting unverified inferential claims. The completed study will test whether personalization changes lower-tail utility and whether any observed improvement survives independent-seed uncertainty and additional local computation costs.

**Keywords:** personalized federated learning; non-IID data; client-tail accuracy; Ditto; Per-FedAvg; empirical reproducibility.

## 1. Introduction

Federated learning coordinates model training without centralizing client training examples. Under heterogeneous client label distributions, an aggregate model may achieve reasonable pooled accuracy while providing weaker performance to some clients. This creates a distinction between population-weighted predictive utility and the distribution of utility across clients.

Personalized federated learning explicitly accommodates client-specific adaptation. Yet comparisons that emphasize pooled or mean accuracy alone can miss a consequential question: do the least-served clients benefit? A higher Jain equality index, moreover, does not by itself establish an improvement in absolute predictive performance. Comparing methods at an equal number of communication rounds also does not imply equal computation, because personalization may add local optimization or adaptation.

This paper focuses on a deliberately narrow, falsifiable question: under a fixed severe non-IID condition, do Ditto and first-order Per-FedAvg improve the tenth percentile of held-out client accuracy relative to FedAvg, and does that answer differ between CIFAR-10 and CIFAR-100? This scope does **not** support claims about all heterogeneity regimes, all datasets, privacy, or computational efficiency.

### 1.1 Research gap and intended contribution

The contribution is not the invention of a new FL algorithm. Instead, the proposed empirical contribution is a seed-matched, evidence-audited evaluation of **absolute lower-tail client utility**, with attention to (i) client-specific deployed model evaluation, (ii) identical client train/test partitions across compared methods, (iii) independence at the seed level, (iv) multiplicity-aware inference, and (v) optimization and adaptation budget disclosure. Whether this is sufficiently novel relative to current literature remains subject to a documented literature review.

### 1.2 Research questions and prespecified hypotheses

**RQ1.** Under Dirichlet α = 0.1, how do Ditto and Per-FedAvg change P10 held-out client accuracy relative to FedAvg on CIFAR-10 and CIFAR-100?

**RQ2.** Do observed P10 changes align with mean, minimum, pooled accuracy, and Jain equality, or do these metrics reveal utility–equality trade-offs?

**RQ3.** Are method effects consistent across independent partition/training seeds, or do they vary materially with the realized client label and test-count distributions?

**RQ4 (focused sensitivity).** Are conclusions robust to disclosed local optimization/adaptation budgets? This question is exploratory until matching additional budget experiments are audited.

Four prespecified confirmatory comparisons are: CIFAR-10 Ditto minus FedAvg; CIFAR-10 Per-FedAvg minus FedAvg; CIFAR-100 Ditto minus FedAvg; and CIFAR-100 Per-FedAvg minus FedAvg, each measured in percentage points of seed-level P10 accuracy. Directional superiority is **not assumed**.

## 2. Methods

### 2.1 Datasets and client partitions

Use the official torchvision CIFAR-10 and CIFAR-100 training and test splits. For each seed, assign training examples among 20 clients with a Dirichlet label-skew partition at α = 0.1. Assign official test examples to held-out client evaluation sets using the prespecified procedure that mirrors training label proportions without fitting on official test examples. Keep each client's training and test indices and their cryptographic hashes. Within a seed, FedAvg, Ditto, and Per-FedAvg must use identical partitions and comparable client-selection schedules. Different seeds are independent experimental replicates.

### 2.2 Training and deployed-model evaluation

All compared primary runs use 50 sequential communication rounds, 20 clients, 20% client participation, two local epochs, batch size 64, and the immutable training implementation. FedAvg evaluates its shared model. Ditto evaluates the deployed personalized client model. First-order Per-FedAvg evaluates a freshly adapted client model using **training-only** examples; official test examples are reserved strictly for prediction evaluation. Record optimizer steps and actual local adaptation work. No differential privacy is enabled in this experiment.

A completed cell requires the original 50-round journal, source commit identity, exact client train/test partition hashes, 20 held-out client outcomes with integer correct and total predictions, resource/optimization accounting, and checkpoint digest validation. Incomplete, duplicate, source-mismatched, or partition-mismatched cells are excluded rather than silently imputed.

### 2.3 Outcomes

For seed s, method m, and client i, define a_i = correct_i / total_i with total_i > 0. Let P10_s,m be the prespecified empirical tenth percentile of the 20 client accuracies, expressed in percentage points. Secondary outcomes are the arithmetic mean across clients, the minimum client accuracy, the pooled accuracy Σcorrect_i / Σtotal_i, and Jain's index (Σa_i)^2 / (20 Σa_i^2). Report client test-set sizes because precision differs when client denominators differ.

For each dataset and personalized method, define the paired effect Δ_s = P10_s,personalized − P10_s,FedAvg. Pairing is permitted only when the source and client partition evidence match and both methods pass the 50-round audit.

### 2.4 Inference and multiplicity

The **seed** is the independent unit (ten prespecified seeds: 11, 23, 37, 53, 71, 83, 97, 109, 127, 149). Do not treat the 20 clients or 50 rounds as independent treatment replicates. Report mean paired effects, individual seed effects, a seed-paired bootstrap 95% confidence interval (10,000 resamples), and an exact two-sided sign-flip test over the paired seed effects. Adjust the four primary hypothesis p-values using Holm's step-down method. The confirmatory inference gate remains closed until all ten matched seed groups and their original evidence are independently verified. Secondary outcomes and sensitivity analyses are labeled exploratory.

The exact sign-flip procedure requires an explicit symmetry/exchangeability assumption for the paired differences under the null; interpret p-values accordingly. A ten-seed sample remains modest and cannot guarantee high statistical power.

### 2.5 Evidence integrity and reproducibility

The frozen training source is commit `a6641509c2b8e6557101499db553177d70a6a3e2`. Record the actual dependency versions and GitHub Actions run identity for each completed cell. Independently recover raw archived metrics and checkpoint artifacts; compare recorded model SHA-256 digests with recovered model files; audit the full 50-round sequence, request configuration, client counts and held-out correct/total values; and reject conflicting duplicate run identifiers. Maintain a machine-readable ledger separating completed training from independently verified results.

## 3. Results — preliminary, evidence-gated

### 3.1 Coverage and inclusion

As of the most recent independent audit, matched CIFAR-10/CIFAR-100 × FedAvg/Ditto/Per-FedAvg results for seeds **11, 23, and 37** have passed the research evidence gate (18 focused cells). The broader repository ledger also includes other experiments; these must **not** be pooled into the four focused comparisons. Seed 53 FedAvg training has completed, but it is not a complete personalized-method matched group and is excluded from the present confirmatory effects.

**Table 1. Confirmatory evidence coverage**

| Dataset | Methods | Verified matched seeds | Target seeds | Inferential status |
| --- | --- | --- | --- | --- |
| CIFAR-10 | FedAvg, Ditto, Per-FedAvg | 11, 23, 37 | 10 | Preliminary; no confirmatory p-value |
| CIFAR-100 | FedAvg, Ditto, Per-FedAvg | 11, 23, 37 | 10 | Preliminary; no confirmatory p-value |

### 3.2 Descriptive lower-tail findings

The audited seed-11 descriptive differences previously recovered from raw research evidence were:

| Dataset | Comparison | Seed-11 Δ P10 (percentage points) |
| --- | --- | ---: |
| CIFAR-10 | Ditto − FedAvg | +42.94 |
| CIFAR-10 | Per-FedAvg − FedAvg | +39.17 |
| CIFAR-100 | Ditto − FedAvg | +23.18 |
| CIFAR-100 | Per-FedAvg − FedAvg | +9.07 |

**Important:** These are single-seed descriptive effects, not a replicated mean, a confidence interval, or a significance claim. Before publication, the precise values must be independently regenerated from the archived seed-11 client-level correct/total counts and checked against the original audit. Seed-23 and seed-37 numerical effects are intentionally **not filled in** until their complete client-level values are extracted and recomputed in the same manuscript-generation process. No between-dataset ranking or generalized superiority is asserted.

### 3.3 Pending inferential results

| Prespecified hypothesis | 10-seed mean paired ΔP10 | Bootstrap 95% CI | Exact two-sided p | Holm-adjusted p |
| --- | --- | --- | --- | --- |
| CIFAR-10 Ditto vs FedAvg | Pending | Pending | Pending | Pending |
| CIFAR-10 Per-FedAvg vs FedAvg | Pending | Pending | Pending | Pending |
| CIFAR-100 Ditto vs FedAvg | Pending | Pending | Pending | Pending |
| CIFAR-100 Per-FedAvg vs FedAvg | Pending | Pending | Pending | Pending |

**No hypothesis is currently accepted or rejected.**

## 4. Discussion

The preliminary seed-11 observations motivate examining lower-tail client utility rather than relying only on pooled test accuracy. Their magnitude alone does not establish reproducibility: partition realizations, finite held-out test counts, stochastic optimization, and personalized adaptation can all affect observed P10. The comparison must therefore be evaluated across the complete prespecified independent-seed set.

Jain's index is a relative equality metric and should be interpreted alongside absolute utility. A method can equalize low accuracies without improving predictive benefit. In addition, communication-round matching is not compute matching; Ditto's additional personalized optimization and Per-FedAvg's adaptation must be disclosed when discussing practical deployment.

The present focused design cannot answer whether effects persist under IID, other Dirichlet concentrations, pathological splits, quantity skew, MNIST, or Fashion-MNIST. It also does not establish privacy guarantees or superiority over FedProx, SCAFFOLD, or FedSAM. Those questions belong to the separate full-matrix research plan, not this paper's confirmatory scope.

## 5. Threats to validity

**Internal:** Small held-out client sets, potential imbalances in client label support, and implementation-specific adaptation can influence P10. Partition and source identity checks reduce but do not eliminate these threats.

**Statistical:** Three complete seeds are inadequate for the prespecified ten-seed confirmatory inference. A bootstrap from clients would be pseudoreplication; none is used. Exact sign-flip inference has assumptions and limited resolution at small sample size.

**External:** Only two image datasets and one severe label-skew setting are covered. Conclusions must be confined to this scope.

**Construct:** P10 is sensitive to the empirical quantile definition and client test denominators. Report the quantile implementation, raw counts, and alternative worst-client/mean metrics.

**Efficiency:** Different methods consume unequal local optimization work despite equal communication rounds. Do not claim efficiency or fair compute comparison without a matched-budget sensitivity study.

## 6. Conclusion

This paper establishes an auditable experimental framework for evaluating whether personalized federated learning improves the lower tail of held-out client utility under severe label heterogeneity. The available matched seed groups provide a basis for preliminary descriptive analysis, but they do **not** yet support confirmatory inference or general method-superiority claims. Final conclusions will be written only after all ten seed groups pass independent evidence audit and the prespecified paired, multiplicity-controlled analysis is reproduced.

## References — verified bibliographic entries to check against publisher records before submission

1. McMahan, H. B., Moore, E., Ramage, D., Hampson, S., and Agüera y Arcas, B. (2017). Communication-Efficient Learning of Deep Networks from Decentralized Data. *Proceedings of AISTATS*, PMLR 54, 1273–1282.
2. Li, T., Hu, S., Beirami, A., and Smith, V. (2021). Ditto: Fair and Robust Federated Learning Through Personalization. *Proceedings of ICML*, PMLR 139, 6357–6368.
3. Fallah, A., Mokhtari, A., and Ozdaglar, A. (2020). Personalized Federated Learning with Theoretical Guarantees: A Model-Agnostic Meta-Learning Approach. *Advances in Neural Information Processing Systems 33*.
4. Kairouz, P., et al. (2021). Advances and Open Problems in Federated Learning. *Foundations and Trends in Machine Learning*, 14(1–2), 1–210.

**Citation gate:** This is a starting bibliography, not a completed systematic novelty review. Check publisher metadata, add relevant 2023–2026 client-tail/fairness PFL literature, and insert properly placed in-text citations before submission. Do not fabricate references.

## Outstanding submission gates

- Recover and independently recompute all 18 focused cells' seed-level P10 and secondary outcomes from archived client correct/total counts.
- Complete and independently audit the remaining seven seed groups (seed 53 currently has only completed FedAvg baselines).
- Run the four exact paired tests, bootstrap CIs, Holm correction, raw-count sensitivity, and compute-budget comparisons.
- Verify recent related work, novelty, reference metadata, figure/table citations, anonymization, and IEEE TAI formatting.
- Distinguish this focused paper from the broader 1,680-primary/560-RQ4 plan; do not imply full-matrix completion.
