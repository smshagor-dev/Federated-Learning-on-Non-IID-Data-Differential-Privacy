# IEEE TAI TAI-2026-Sep-A-02187 — empirical protocol

**State:** prespecified experiment design, NOT completed empirical evidence. No manuscript accuracy, uncertainty, p-value, or superiority statement may be invented from a benchmark plan, mathematical identities, CI or a synthetic test.

## Research contribution and questions

Hypothesis to test, **not established novelty**: personalization's effect on the *lower tail* of client utility may diverge from average utility and Jain relative fairness, and this divergence may depend on the mechanism and realized severity of non-IID data.

| RQ | Question | Required evidence |
|----|----------|------------------|
| RQ1 | Does Ditto or first-order Per-FedAvg improve mean, tenth percentile and minimum held-out client accuracy relative to FedAvg, FedProx and SCAFFOLD? | Matched seed-level paired effects and 95% CI, overall micro accuracy and raw client counts |
| RQ2 | When Jain's equality index improves, does absolute utility also improve? | Paired mean, p10, worst-client, dispersion, Jain and Pareto utility–fairness analysis |
| RQ3 | Are effects moderated by the *realized* class concentration, support mismatch and client-size dispersion rather than just nominal Dirichlet alpha? | Observed client class histograms, client counts, distribution divergences and partition-interaction effects |
| RQ4 | Are rankings stable across dataset, random seed and local adaptation/computation budget? | Full independent-seed comparisons, ranking stability, adaptation-step {1,3,5} and budget-matched sensitivity |

## Required primary design

- **Actual torchvision:** MNIST, FashionMNIST, CIFAR-10, CIFAR-100. Synthetic distributed-worker shards are NEVER scientific evidence for these datasets.
- **Six methods:** FedAvg, FedProx, SCAFFOLD (shared baselines), FedSAM (sharpness-aware shared comparator), Ditto (persistent personalized models), first-order Per-FedAvg (fresh post-adaptation model).
- **Seven partition conditions:** IID; Dirichlet alpha 1.0, 0.3, 0.1, 0.05; pathological two-class skew; log-normal quantity skew sigma=1.0.
- 20 clients, 20% participation, 50 communication rounds, two local epochs, batch size 64; same initialization, actual partitions and selected client cohort for each paired comparison.
- Ten seeds: 11, 23, 37, 53, 71, 83, 97, 109, 127, 149. Five seeds do not suffice for p < 0.05 using the two-sided *exact* paired sign-flip test (minimum 0.0625).
- Main study is strictly non-private: DP off.
- **Full primary matrix: 4 datasets x 7 conditions x 6 methods x 10 seeds = 1,680 runs.** This is a target, not execution evidence.
- Use official training examples exclusively for model updates and Per-FedAvg's test-time local adaptation. Official test examples are disjoint and used only to compute held-out prediction metrics; allocation to clients mirrors training label proportions without fitting on test examples.
- Primary reported metric for Ditto/Per-FedAvg uses their deployed *client-specific* model; shared global-only metrics for these methods are separate comparators. Other methods use their global checkpoint.
- Equal communication rounds do not imply equal computation: Ditto trains two models and SAM uses two backward passes. Report all optimizer steps, wall-clock resources and match computation in sensitivity tests before claiming efficiency.

## Statistics and claim rules

Use whole training **seed** as the independent replicate, not client or round. Compare methods on identical seed and exact matching train/test partition hashes. Use exact two-sided paired sign-flip tests and seed-paired 10,000-resample bootstrap 95% CIs. Holm-adjust within each prespecified metric family over method, dataset and heterogeneity comparisons. Report all raw and adjusted p-values and the entire distribution of client test sizes. With 10 seeds, the minimum exact p-value is 2/1024=0.001953125, still subject to multiplicity correction.

Jain fairness is a **relative** equality measure; it does not imply non-decreasing mean or p10 predictive quality. Never identify a rise in Jain alone as absolute benefit. Very small held-out client test subsets require sample-size and sensitivity reporting; do not discard poorly performing clients silently.

## Machine-auditable reporting gate

For every dataset/condition/seed/method:
- Archive full request, Git commit, training and official held-out partition indices and hashes, final global checkpoint and deployed personalized checkpoints where applicable.
- Archive client-level correct/total predictions, raw counts, optimizer step/participation totals, run/failure logs, initial model identity and exact hyperparameters.
- Reject incomplete/duplicated cells, mixed source commits within a *single* result, mismatched paired partitions, non-finite results, and missing client outcomes.
- The official 1,680-cell matrix must be verified before labeling the paper a *completed* four-dataset six-method experiment. An optional partial exploration is not a primary research result.
- Hyperparameters must be sealed before looking at the official test data; validation tuning may use only held-out portions of the **training** split.
- Run distinct adaptation-budget experiments under separate output roots; do not overwrite the primary matrix or combine tuned and untuned results.

## Running the pipeline

From a checkout containing this code with all dependencies and dataset downloads permitted:

    python -m tai_pfl.study --dataset MNIST --condition dirichlet:0.1 --seed 11 --rounds 50 --clients 20 --methods fedavg,fedprox,scaffold,ditto,per_fedavg,fedsam --output-dir benchmarks/tai-pfl

Repeat across all four datasets, seven partitions, ten seeds. Use --plan-only for preview; this does NOT train.

    python -m tai_pfl.analysis --results-root benchmarks/tai-pfl

The analyzer fails closed if the matrix is incomplete. The --allow-partial-exploratory flag is debugging-only.

## Manuscript integration

Use this protocol to draft Methods/RQs and limitations, **not Results**. Fill results tables/figures only from verified complete numeric outputs. The anonymized double-review manuscript must not contain names, email, identifiable links or document author metadata; submit the title page separately. Validate every literature citation. Report any substantive AI-generated content as required by IEEE policy.

**Current hard blockers:** official training executions, raw 1,680-cell results, RQ3 realized-heterogeneity interaction tests, RQ4 compute/adaptation ablations, latest literature novelty assessment, and manuscript empirical Results/Discussion. Neither experiment code nor CI tests remove these blockers.
