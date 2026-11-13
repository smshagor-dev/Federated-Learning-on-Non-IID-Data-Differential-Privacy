# IEEE TAI focused CIFAR replication: November 13, 2026

## Research claim boundary

The focused study evaluates **FedAvg, Ditto, and first-order Per-FedAvg** on official
**CIFAR-10/CIFAR-100**, **Dirichlet alpha = 0.1**, **20 clients**, **50 rounds**,
**20% participation**, **two local epochs**, and ten prespecified independent
seeds (11, 23, 37, 53, 71, 83, 97, 109, 127, 149).

This work does not prove an accuracy, fairness, or significance improvement by
itself. No outcome can enter the paper until the original artifacts are
independently checked and all matched seeds needed for the claim have passed.

## Evidence known at this check

- Latest focused manuscript draft documents three independently audited matched
  seed groups: 11, 23, and 37 (18 focused cells). This record is not a fresh
  re-audit of those underlying archives.
- Seed-53 **FedAvg** on CIFAR-10 and CIFAR-100 completed in GitHub Actions
  run [43144949475](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/43144949475).
  Training success is **not** independently verified matched three-method
  evidence until paired Ditto/Per-FedAvg archives and original baseline files
  pass separate audit.
- The scheduled November 13 six-method scoped run
  [43412370599](https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/43412370599)
  had five successful CIFAR-10/seed-23 jobs; the FedSAM job failed because
  torchvision raised `RuntimeError: File not found or corrupted` while fetching
  CIFAR-10, not because any trained model failed an empirical test.
  Failed jobs were explicitly requested for rerun.

## Bounded next training batch

The separate [focused seed-53/71 workflow](../../.github/workflows/tai-pfl-focused-seeds-53-71.yml)
attempts these **ten** original 50-round training cells on the pinned
`a6641509c2b8e6557101499db553177d70a6a3e2` research code:

- Seed 53: CIFAR-10/CIFAR-100 × Ditto/Per-FedAvg (four missing
  personalized-method cells; do not recompute already trained FedAvg).
- Seed 71: CIFAR-10/CIFAR-100 × FedAvg/Ditto/Per-FedAvg (six cells).

These are **training requests**, not completed results. The workflow checks
exact requested dataset/condition/method/seed/shard identity, uses pinned
dependencies, retries checksum-validated official dataset downloads, verifies
the 50-round result and per-client held-out records, and archives original
numeric outputs, exact partitions, and source/checkpoint provenance.

A green GitHub Actions job alone is not independent archive verification.
Before adding numerical claims, re-download the original raw result,
round journal, per-client correct/total, partitions and checkpoints; verify
SHA-256 against recorded model hashes; reject stale source SHA, duplicate
run IDs with conflicting content, missing clients, missing rounds or mismatched
test partitions. Record the re-audit workflow and output digest.

## Next research gates

1. Independently audit both seed-53 baselines and four new personalized
   results, and the complete six-cell seed-71 cohort.
2. Execute and independently audit seeds 83, 97, 109, 127, and 149, then
   confirm seeds 11, 23, and 37 remain comparable under the frozen protocol.
3. Apply the **prespecified ten-seed**, two-sided exact paired sign-flip tests,
   paired-bootstrap 95% confidence intervals and Holm correction to each of
   the four CIFAR P10 contrasts. Do not turn preliminary 3-seed differences
   into inferential results.
4. Regenerate paper tables/figures **from audited archived client correct/total**,
   account for extra local optimizer/adaptation work, check recent literature
   for adequate originality, and prepare the double-anonymized IEEE manuscript.

The complete 1,680-cell/four-dataset matrix is a distinct larger study.
Neither this workflow nor the 60-cell focused study establishes completion of
that broader experiment.
