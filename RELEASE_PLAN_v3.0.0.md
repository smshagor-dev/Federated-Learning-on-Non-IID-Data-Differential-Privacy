# Federated Learning Platform v3.0.0 Release Plan

**Status:** In development — release blocked  
**Development package:** `fl-platform==3.0.0.dev0`  
**Integration branch:** `federated-platform-expansion`

v3.0.0 is a major architecture and validation release. A feature being present in source does not make it release-supported. Unsupported privacy/security combinations must fail closed, and the final tag must not be published until every release gate is green.

## Workstreams

| # | Workstream | Current v3 status | Release requirement |
|---|---|---|---|
| 1 | Async & elastic FL | **Started** — staleness-aware async model-state primitive implemented | Distributed async scheduling, durable state, dynamic membership, restart and fault tests |
| 2 | Byzantine / malicious-client defense | **Started** — coordinate median, trimmed mean, Krum and Multi-Krum implemented | Runtime integration, poisoning/backdoor scenarios and robustness benchmark evidence |
| 3 | Advanced privacy validation | **Started** — compatibility matrix fails closed | Async/robust/adaptive-clipping accounting and privacy attack evaluation must be validated before claims |
| 4 | Secure Aggregation v3 | **Started** — unsupported async/robust/threshold combinations explicitly blocked | Threshold dropout recovery, restart/key lifecycle and supported compatibility matrix |
| 5 | Algorithm suite expansion | **Started** — FedAdam/FedYogi/FedAdagrad server optimizer primitives implemented | Integrate and benchmark; FedNova/FedBN/FedRep/MOON/pFedMe remain pending |
| 6 | Realistic heterogeneity | **Started** — deterministic compute/network/availability/resource profiles | Integrate latency/dropout/bandwidth/compute scenarios into execution and benchmarks |
| 7 | Federated datasets/models | **Started** — FEMNIST/Shakespeare/Sent140 maturity manifests | Real loaders, integrity/provenance/license validation and model coverage |
| 8 | Production distributed infrastructure | **Started** — v3 requirements defined against existing Docker/Kubernetes stack | Multi-host deployment, health/readiness, recovery, quotas and durable external state evidence |
| 9 | Observability | **Started** — v3 round/communication/privacy/robustness record types | Runtime exporters, Prometheus/OpenTelemetry wiring and dashboard coverage |
| 10 | Scientific benchmark v3 | **Started** — required robustness/communication fields modeled | Multi-seed algorithm × data × DP × attack × heterogeneity matrix with CI/statistics |
| 11 | Edge/resource-constrained workers | **Started** — resource eligibility profiles | ARM64/CPU profiles, compression/quantization and constrained-network validation |
| 12 | Release security & supply chain | **Started** — release gate established | Dependency/SBOM/container/secret/provenance/signing checks green |
| 13 | CI/chaos/reliability gate | **Started** — all 13 gates represented in code | Distributed crash/restart/dropout/delay/race/sanitizer/fuzz/soak evidence green |

## v3.0.0 release gates

The canonical required gates are defined in `fl_platform.v3.release_gates.REQUIRED_V3_GATES`:

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

The release model is deliberately fail-closed: missing gates block release rather than being interpreted as optional.

## Explicit boundaries at the start of v3 development

- Async FL source primitives are not yet a claim of validated distributed async execution.
- Current secure aggregation is not advertised as compatible with async aggregation or Byzantine-robust aggregation.
- Threshold secure-aggregation recovery remains experimental until implementation and recovery tests are complete.
- Robust aggregation + differential privacy remains blocked until adjacency/accounting semantics and benchmark behavior are validated.
- FEMNIST, Shakespeare and Sent140 entries are manifests only; they are not advertised as validated loaders.
- New adaptive server optimizers are primitives until integrated into canonical execution, checkpointing and benchmark paths.
- Production v3.0.0 must not be tagged from a partially green matrix.

## Final acceptance criteria

A v3.0.0 release candidate requires all existing v2 CI gates plus v3-specific validation: async scheduling/restart tests, robust-aggregation adversarial tests, privacy regression evidence, secure-aggregation recovery tests, multi-host distributed E2E, heterogeneity and edge scenarios, benchmark-v3 statistical outputs, supply-chain evidence, and chaos/reliability testing. Only then should `3.0.0.dev0` become `3.0.0` and a GitHub release be published.
