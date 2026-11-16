# Seed 149 archive integrity audit — evidence handoff (2026-11-16)

**Scope:** CIFAR10 and CIFAR100, Dirichlet alpha 0.1, 20 clients, 50 rounds, Ditto and Per-FedAvg. This is an **archive-level audit handoff**, not a raw torchvision replay or a new training run.

## Provenance
- Original training workflow: https://github.com/smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy/actions/runs/43731822341
- Frozen training source SHA recorded in original payload: `a6641509c2b8e6557101499db553177d70a6a3e2`
- Run head SHA: `9b4e98bac657d3de47eb845340a1e2f53b3e54df`
- Four evidence artifacts: CIFAR10 Ditto `14170751445`, CIFAR10 Per-FedAvg `14171989346`, CIFAR100 Ditto `14175333577`, CIFAR100 Per-FedAvg `14177785827`.
- Corresponding model artifacts: `14170836121`, `14171984385`, `14175164182`, `14176879700`. GitHub artifact metadata checked 2026-11-16; checkpoint archives expire on **2026-11-22**.
- Local machine-readable archive audit receipt: `ieee_tai_seed149_local_audit.json` (generated in an earlier execution, **not committed here**). Preserve this original receipt and checkpoint bytes before expiration.

## Previously independently recomputed from recovered archive payloads

| Dataset | Method | Held-out correct/total | Micro accuracy | P10 accuracy | Optimizer steps |
|---|---|---:|---:|---:|---:|
| CIFAR10 | Ditto | 8205/10000 | 0.8205 | 0.7344388163 | 33264 |
| CIFAR10 | Per-FedAvg | 7623/10000 | 0.7623 | 0.6942784310 | 8420 |
| CIFAR100 | Ditto | 4458/10000 | 0.4458 | 0.3627362270 | 31952 |
| CIFAR100 | Per-FedAvg | 2895/10000 | 0.2895 | 0.1966 (rounded) | 8122 |

Prior local archive audit reported checks for 50 sequential rounds, 20 distinct clients, four selected per round, integer correct/total, recomputed micro and per-client accuracy, optimizer steps, checkpoint SHA-256 and size, recorded source SHA and matching **recorded** train/test partition hashes between methods for each dataset. These checks do **not** independently establish that the recorded hashes match partitions recomputed from official torchvision raw data.

## Evidence gates still outstanding
1. Preserve raw model checkpoint ZIPs beyond their 2026-11-22 expiry, with their original GitHub artifact SHA-256 metadata and file-level checkpoint digests.
2. Reconstruct train and test partitions directly from pinned official torchvision raw archives; compare each SHA-256 to the recovered payload's hash and verify identical partitions across FedAvg, Ditto and Per-FedAvg.
3. Independently audit both seed-149 FedAvg baselines (original run `43720500250`) and reconcile the complete six-cell matched seed against the immutable ledger by unique dataset/alpha/seed/method/protocol identity.
4. Check per-cell compute budgets, training-only adaptation and strict held-out isolation from original source and raw per-round records.
5. Only then increment the primary evidence ledger. Do not treat an audit receipt as an extra training cell or a one-round CI test as 50-round evidence.
6. Perform ten-independent-seed paired effects, exact two-sided sign-flip tests, paired bootstrap 95% CIs and Holm adjustment only after all matched seeds pass the same gates.

**Ledger status:** previous reconciled totals **65/1680 primary**, **2/560 RQ4**, **9/10 fully matched confirmatory seeds**; this report **does not change those totals**.

No statistical significance, ten-seed completion, or publication readiness is claimed here.
