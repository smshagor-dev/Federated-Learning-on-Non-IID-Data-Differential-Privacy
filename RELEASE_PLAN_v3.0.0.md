# Federated Learning Platform v3.0.0 Release Plan

**Status:** In development — release blocked  
**Development package:** `fl-platform==3.0.0.dev0`

v3.0.0 is a major architecture, security, and validation release. Source-code presence is not release support. Unsupported privacy/security/runtime combinations must fail closed, and the final tag must not be published until every release gate has executable evidence.

## Workstreams

| # | Workstream | Current v3 status | Remaining release requirement |
|---|---|---|---|
| 1 | Async & elastic FL | **In progress** — staleness-aware model state and execution modes exist | Distributed async scheduling, durable state, dynamic membership, restart/fault evidence |
| 2 | Byzantine / malicious-client defense | **In progress** — coordinate median, trimmed mean, Krum/Multi-Krum, poisoning transforms and robustness harness exist | Full runtime attack matrix and robust-vs-clean evidence across supported workloads |
| 3 | Advanced privacy validation | **In progress** — privacy compatibility, leakage/membership checks and ledger-resume validation exist | Validate any newly supported async/robust/adaptive-clipping composition before enabling it |
| 4 | Secure Aggregation v3 | **In progress** — threshold recovery primitives and commitment-only restart receipts exist | Live coordinator/worker dropout-recovery wire integration, key lifecycle and restart evidence |
| 5 | Algorithm suite expansion | **In progress** — adaptive server optimizers, FedNova aggregation, FedBN/FedRep partitions, MOON and pFedMe primitives exist | Canonical worker/runtime registration plus benchmark evidence for each newly advertised algorithm |
| 6 | Realistic heterogeneity | **In progress** — deterministic compute/network/availability/resource admission is integrated into execution | Multi-host timing/dropout validation and benchmark evidence |
| 7 | Federated datasets/models | **In progress** — local LEAF-style loaders, SHA-256 integrity and license/provenance gates exist for FEMNIST/Shakespeare/Sent140 | Archive verified provenance/license evidence and model coverage before release-validating these workloads |
| 8 | Production distributed infrastructure | **In progress** — persistent state, Secret references, probes, replicas, topology spread, quotas, PDBs and HPAs are modeled and contract-checked | Real multi-host deployment/recovery evidence plus immutable release image references |
| 9 | Observability | **In progress** — aggregate Prometheus text, metric-event and JSONL exporters exist | Deployed collector/dashboard evidence and operational validation |
| 10 | Scientific benchmark v3 | **In progress** — attack × privacy × aggregation × heterogeneity × seed planning and completeness/statistics gate implemented | Execute the release matrix and provide complete multi-seed observations for every runnable cell/metric |
| 11 | Edge/resource-constrained workers | **In progress** — resource gating and qint8+zlib bounded update transport exist | ARM64 portability CI, compressed wire integration and physical/constrained-device evidence |
| 12 | Release security & supply chain | **In progress** — dependency consistency, SBOM generation, secret scans and infrastructure credential checks exist | Immutable image pinning, provenance/signing and final release artifact verification |
| 13 | CI/chaos/reliability gate | **In progress** — all 13 gates represented and targeted workflows are being added | Distributed crash/restart/dropout/delay/race/fuzz/soak evidence green |

## Canonical v3.0.0 release gates

`fl_platform.v3.release_gates.REQUIRED_V3_GATES` defines exactly these required gates:

1. `async-runtime`
2. `robust-aggregation`
3. `privacy-validation`
4. `secure-aggregation`
5. `algorithm-suite`
6. `system-heterogeneity`
7. `federated-workloads`
8. `distributed-infrastructure`
9. `observability`
10. `benchmark-matrix`
11. `edge-runtime`
12. `supply-chain-security`
13. `chaos-reliability`

Missing or incomplete gates block release rather than being treated as optional.

## Current fail-closed boundaries

- Async source primitives are not a claim of validated distributed async execution.
- Secure aggregation is not advertised as compatible with asynchronous or Byzantine-robust aggregation.
- Threshold recovery remains experimental until the live coordinator/worker protocol performs real dropout recovery. Durable recovery snapshots contain commitments/metadata, not raw Shamir share values; actual shares must be resubmitted after restart.
- Robust aggregation + differential privacy remains blocked until its composition semantics and benchmark behavior are validated.
- The canonical worker algorithm registry currently exposes `fedavg`, `fedprox`, `scaffold`, `fedsam`, `ditto`, and `per_fedavg`. Primitive-only v3 algorithms must not be marked benchmark-runnable until registered in the real worker runtime.
- FEMNIST, Shakespeare, and Sent140 now have local integrity-checked loaders, but remain non-release-validated until source/license/provenance evidence is archived. The loader does not silently auto-download unverified corpora.
- Edge qint8+zlib transport is an adapter boundary, not yet a claim that the existing distributed wire protocol transmits compressed updates.
- Benchmark matrix manifests are specifications only. They intentionally set `evidence_complete=false`; only complete per-cell observations can satisfy the benchmark gate.
- Kubernetes hardening does not replace real multi-host recovery tests, and mutable `:latest` image references remain a release-security blocker.
- Production v3.0.0 must not be tagged from a partially green matrix.

## Final acceptance criteria

A v3.0.0 release candidate requires all existing v2 gates plus v3-specific evidence: distributed async scheduling/restart, adversarial robust aggregation, privacy regression, live threshold secure-aggregation recovery, multi-host infrastructure recovery, heterogeneity/edge scenarios, complete benchmark-v3 statistical outputs, immutable supply-chain artifacts, and chaos/reliability validation. Only after every required gate is green should `3.0.0.dev0` become `3.0.0` and a GitHub release be published.
