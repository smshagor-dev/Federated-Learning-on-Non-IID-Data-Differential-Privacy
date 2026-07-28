# Federated Learning Super System - Master Plan

**Repository:** `smshagor-dev/Federated-Learning-on-Non-IID-Data-Differential-Privacy`  
**Document:** `plan.md`  
**Last updated:** July 28, 2026  
**Current category:** Research Evaluation, Benchmarking, and Reproducibility  
**Current objective:** Research Writer Live Docker and Runtime Validation Closure  
**Repository state:** Large uncommitted working tree; do not reset, clean, stash, discard, or commit unrelated changes.  
**Current readiness:** `RESEARCH_SECURITY_READY`  
**Primary secure aggregation provider:** `SECAGG_NO_DROPOUT_EXPERIMENTAL`

## Current Developer Entry Point

Normal full-platform local development now begins with:

```text
python main.py
```

The root launcher resolves the supported Compose chain, starts backend
containers, waits for required service health, and then starts the
Next.js web application as a separate managed child process. The legacy
prototype is preserved under `legacy/python-research-studio/` and is no
longer the primary root entrypoint workflow.

---

## Purpose and Maintenance Rules

- `plan.md` is the repository's current master plan and current status document.
- It contains current capabilities, current limitations, current execution order, and current evidence only.
- Detailed implementation history belongs in capability-specific reports under `docs/`.
- Historical status snapshots are not maintained inline here.
- Every material status claim must point to code, a report, tests, or fresh validation evidence.
- Configuration is not runtime evidence.
- Unit tests are not live integration evidence.
- CI workflow YAML is not proof of a remote CI pass.
- Validation status must not be upgraded without fresh evidence appropriate to the scope.
- Historical counts must include a date.
- Superseded statements should be removed rather than kept as stale inline history.

---

## System Overview

Current federated runtime architecture:

```text
Next.js Web
-> Go Control Plane
-> C++ Coordinator
-> Python Workers
```

Current research-registry write path:

```text
External Client
-> Go Research API
-> Python Research Writer
-> Durable Research Registry
```

Ownership boundary:

- Go owns public HTTP APIs, auth, RBAC, typed read models, runtime-health projection, and operational read views.
- Python owns authoritative research validation, canonical hashing, durable registry mutation, idempotency, and synthetic experiment mutation semantics.
- C++ owns coordinator runtime, aggregation, privacy-accounting enforcement for the coordinator-side mechanisms, and secure-aggregation protocol execution.
- The web UI talks to Go only.

This repository is no longer a Python-only research prototype. It is a substantial multi-language research platform with:

- a real Go control plane,
- a real C++ coordinator runtime,
- real Python worker training,
- layered differential privacy,
- signed and authenticated control paths,
- a live no-dropout secure-aggregation runtime,
- and an emerging research-evaluation and reproducibility platform.

---

## Current Readiness

Current readiness is:

```text
RESEARCH_SECURITY_READY
```

Meaning:

- The approved research security scope is materially implemented.
- Authenticated transport is implemented.
- Worker and coordinator message authenticity are implemented for the approved critical paths.
- Replay protection is implemented for the approved signed streams.
- Worker and coordinator signing-key lifecycle is implemented.
- The no-dropout secure-aggregation provider is implemented and runtime validated.
- Layered differential privacy under the approved research scope is implemented and runtime validated.
- Security APIs, journals, metrics, browser surfaces, and runtime-validation evidence exist.

It does **not** mean:

- production ready,
- dropout resilient,
- malicious-client secure,
- independently reviewed,
- high availability validated,
- disaster recovery validated,
- enterprise persistence complete,
- production-scale benchmarking complete,
- or publication-ready research-evaluation workflows complete.

---

## Canonical Engineering Categories

- Foundation
- Aggregation Core
- Coordinator Runtime
- Algorithm Expansion
- Privacy Engineering
- Secure Aggregation and Cryptographic Protocols
- Research Evaluation, Benchmarking, and Reproducibility
- Distributed Execution
- Enterprise Platform
- Observability and Operations
- Production Hardening

---

## Current Status Summary

| Category | Status | Last verified | Current summary | Authoritative evidence |
|---|---|---|---|---|
| Foundation | COMPLETE | July 28, 2026 | Multi-language monorepo, contracts, tooling, dev runtime scaffolding, and terminology policy are established and actively used. | [README.md](README.md), [docs/docker-runtime.md](docs/docker-runtime.md) |
| Aggregation Core | COMPLETE | July 28, 2026 | C++ aggregation, optimizer state, validation, checkpointing, and Python parity are implemented and tested. | [README.md](README.md) |
| Coordinator Runtime | COMPLETE | July 28, 2026 | Go -> gRPC -> C++ runtime, task lifecycle, checkpoints, restart behavior, and Docker runtime are implemented with live validation evidence. | [docs/docker-runtime.md](docs/docker-runtime.md), [docs/known-limitations.md](docs/known-limitations.md) |
| Algorithm Expansion | COMPLETE | July 28, 2026 | FedSAM, Ditto, Per-FedAvg, personalization registries, fairness projections, and related APIs are implemented. | [README.md](README.md) |
| Privacy Engineering | COMPLETE | July 28, 2026 | Sample-level DP, user-level DP, adaptive clipping, hybrid DP, separate accounting, and privacy UI/reporting are implemented. | [README.md](README.md), [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md) |
| Secure Aggregation and Cryptographic Protocols | COMPLETE | July 28, 2026 | Approved no-dropout secure aggregation, signed cohort handshake, masked update finalization, security APIs, runtime harness, and browser validation are complete for the approved research scope; dropout recovery remains BLOCKED and several advanced assurances remain DEFERRED. | [docs/security-runtime-completion-report.md](docs/security-runtime-completion-report.md), [docs/security-runtime-validation.md](docs/security-runtime-validation.md), [docs/secure-aggregation-threat-model.md](docs/secure-aggregation-threat-model.md) |
| Research Evaluation, Benchmarking, and Reproducibility | PARTIAL | July 28, 2026 | Typed specification, durable Python registry, typed Go read layer, writer service, and live Docker write-path validation now exist; broader post-fix closure evidence remains incomplete. | [docs/research-evaluation-existing-capabilities-audit.md](docs/research-evaluation-existing-capabilities-audit.md), [docs/experiment-registry-report.md](docs/experiment-registry-report.md) |
| Distributed Execution | NOT STARTED | July 28, 2026 | No general distributed scheduling layer, async execution mode, or real scale-out benchmark runner is implemented. | [docs/known-limitations.md](docs/known-limitations.md) |
| Enterprise Platform | NOT STARTED | July 28, 2026 | Durable enterprise persistence services exist in Compose but are not the authoritative application persistence layer for production use. | [docs/docker-runtime.md](docs/docker-runtime.md), [docs/known-limitations.md](docs/known-limitations.md) |
| Observability and Operations | PARTIAL | July 28, 2026 | Prometheus, journals, dashboards, runtime harnesses, and release-evidence tooling exist, but production-grade operations remain incomplete. | [docs/security-capability-inventory.md](docs/security-capability-inventory.md), [docs/security-ci.md](docs/security-ci.md) |
| Production Hardening | NOT STARTED | July 28, 2026 | Independent review, HA, DR, signed releases, incident response, and production rollout controls remain future work. | [docs/known-limitations.md](docs/known-limitations.md) |

---

## Completed Platform Capabilities

### Foundation

Status: COMPLETE

Major completed capabilities:

- multi-language repository structure,
- shared protobuf contracts,
- C++, Go, Python, and web build/test foundations,
- terminology policy enforcement via `scripts/check_project_terminology.py`,
- repository-wide documentation structure with capability-specific reports,
- Docker Compose developer topology for the core stack.

Evidence:

- [README.md](README.md)
- [docs/docker-runtime.md](docs/docker-runtime.md)

### Aggregation Core

Status: COMPLETE

Major completed capabilities:

- FedAvg, FedProx, SCAFFOLD, FedAdagrad, FedAdam, and FedYogi aggregation math,
- bounded weighting strategies,
- update validation and manifest enforcement,
- checkpoint persistence with corruption detection,
- Python-to-C++ parity coverage for the aggregation path.

Evidence:

- [README.md](README.md)

### Coordinator Runtime

Status: COMPLETE

Major completed capabilities:

- real C++ gRPC coordinator runtime,
- Go control-plane integration,
- Python worker runtime loop,
- task issuance, result submission, and event streaming surfaces,
- checkpoint and crash-recovery behavior,
- Docker validation of the real Go -> C++ -> Python path.

Important current limitation:

- not every runtime surface is production-hardened,
- but the approved coordinator runtime is implemented and validated.

Evidence:

- [docs/docker-runtime.md](docs/docker-runtime.md)
- [docs/known-limitations.md](docs/known-limitations.md)

### Algorithm Expansion

Status: COMPLETE

Major completed capabilities:

- FedSAM, Ditto, and Per-FedAvg local training,
- personalization model storage and summaries,
- dataset and model registries,
- fairness and worst-client metrics,
- Go and web operational visibility for the expanded algorithm set.

Evidence:

- [README.md](README.md)

### Privacy Engineering

Status: COMPLETE

Major completed capabilities:

- sample-level DP with Opacus on the worker,
- user-level DP on the coordinator path,
- adaptive clipping,
- hybrid DP,
- strict separation of privacy-accounting outputs,
- privacy ledgers, health, metrics, and public projections.

Critical current boundary:

- no combined epsilon is supported or claimed,
- each privacy mechanism remains separately accounted and separately reported.

Evidence:

- [README.md](README.md)
- [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md)

### Security and Cryptographic Runtime

Status: COMPLETE

Major completed capabilities:

- development PKI and mTLS-secured validated runtime paths,
- certificate identity binding,
- worker signing identities and signed capabilities,
- signed worker messages for the approved paths,
- coordinator signing identities and signed coordinator tasks,
- replay protection for the approved streams,
- worker and coordinator signing-key lifecycle,
- Go security APIs with permission controls,
- durable security event and audit journals,
- Web Security Center with browser validation,
- runtime-validation harness and release-evidence tooling.

Precise current claim:

- authenticated transport is implemented,
- worker and coordinator message authenticity are implemented for the approved critical paths,
- replay protection is implemented for the approved streams,
- worker and coordinator signing-key lifecycle is implemented.

Evidence:

- [docs/security-capability-inventory.md](docs/security-capability-inventory.md)
- [docs/security-runtime-completion-report.md](docs/security-runtime-completion-report.md)
- [docs/security-runtime-validation.md](docs/security-runtime-validation.md)
- [docs/security-ci.md](docs/security-ci.md)

### No-Dropout Secure Aggregation

Status: COMPLETE

Precise current claim:

```text
Experimental complete-cohort no-dropout secure aggregation is implemented and validated.
Dropout-resilient secure aggregation remains blocked.
```

Implemented and validated within the approved research scope:

- `SECAGG_NO_DROPOUT_EXPERIMENTAL`,
- signed cohort handshake,
- coordinator-signed frozen roster,
- X25519 session keys,
- pairwise tensor masking,
- pairwise fixed-weight masking,
- signed masked updates,
- complete-cohort secure FedAvg finalization,
- cleartext fallback prohibition for secure-bound rounds,
- security events and runtime evidence for the live no-dropout flow.

Explicitly unsupported here:

- dropout recovery,
- threshold reconstruction,
- partial-cohort unmasking,
- malicious-client-secure aggregation,
- Byzantine robustness,
- production privacy readiness.

Evidence:

- [docs/secure-cohort-handshake-report.md](docs/secure-cohort-handshake-report.md)
- [docs/secure-aggregation-masked-runtime-report.md](docs/secure-aggregation-masked-runtime-report.md)
- [docs/secure-aggregation-threat-model.md](docs/secure-aggregation-threat-model.md)

### Secure Privacy Under Aggregation

#### Sample-level

Status: COMPLETE

- sample-level DP remains worker-side,
- secure aggregation protects the masked update path rather than replacing sample-level accounting,
- compatibility is explicitly documented and validated in the secure runtime stack.

Evidence:

- [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md)
- [docs/secure-hybrid-dp-runtime-report.md](docs/secure-hybrid-dp-runtime-report.md)

#### User-level

Status: COMPLETE

- secure user-level DP is implemented for the approved no-dropout provider,
- fixed user weighting is enforced,
- privacy compatibility and rejection conditions are explicitly documented,
- runtime and operational evidence exists.

Evidence:

- [docs/secure-user-level-dp-runtime-report.md](docs/secure-user-level-dp-runtime-report.md)
- [docs/secure-user-level-operations-report.md](docs/secure-user-level-operations-report.md)
- [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md)

#### Hybrid

Status: COMPLETE

- hybrid DP under secure aggregation is implemented,
- sample-level and user-level layers remain separately accounted,
- no combined epsilon is introduced,
- the secure path validates both layers' bindings and reporting contracts.

Evidence:

- [docs/secure-hybrid-dp-runtime-report.md](docs/secure-hybrid-dp-runtime-report.md)
- [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md)

#### Adaptive clipping

Status: COMPLETE

Current accurate summary:

- local binary clipping indicator,
- separate pairwise indicator-mask domain,
- complete-cohort aggregate indicator reconstruction,
- indicator noise,
- next-round-only bound update,
- current-round bound immutability,
- separate mechanism accounting,
- `USER_LEVEL_DP` compatibility,
- `HYBRID_DP` compatibility,
- honest-client dependency,
- no individual norm or indicator exposure,
- fresh live validation evidence on July 28, 2026.

Evidence:

- [docs/secure-adaptive-clipping-runtime-report.md](docs/secure-adaptive-clipping-runtime-report.md)
- [docs/secure-adaptive-clipping-runtime-audit.md](docs/secure-adaptive-clipping-runtime-audit.md)
- [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md)

---

## Active Work

## Research Evaluation, Benchmarking, and Reproducibility

Status: PARTIAL

### Implemented foundation

Implemented and targeted-tested:

- typed Python `ExperimentSpecification`,
- deterministic specification hashing,
- dataset identity and checksum validation,
- partition-manifest binding,
- privacy compatibility validation,
- secure-aggregation compatibility validation,
- explicit `combined_epsilon` rejection,
- explicit dropout-recovery rejection,
- deterministic `quantity_skew` partitioning,
- richer partition manifests with heterogeneity metadata,
- durable Python experiment registry,
- immutable specification snapshots,
- per-seed run records,
- event and metric journals,
- environment and artifact manifests,
- corruption detection,
- restart recovery on the Python authoritative path,
- cancellation,
- retry lineage,
- bounded synthetic multi-seed orchestration,
- typed Go research models,
- typed Go read repository,
- Go read and runtime-health APIs,
- Python-authoritative writer policy and command-service design.

Evidence:

- [docs/research-evaluation-existing-capabilities-audit.md](docs/research-evaluation-existing-capabilities-audit.md)
- [docs/experiment-specification.md](docs/experiment-specification.md)
- [docs/experiment-registry-design.md](docs/experiment-registry-design.md)
- [docs/experiment-registry-report.md](docs/experiment-registry-report.md)
- [docs/experiment-go-integration-design.md](docs/experiment-go-integration-design.md)
- [docs/experiment-cross-language-contract.md](docs/experiment-cross-language-contract.md)

### Implemented operational wiring

Implemented:

- research-writer entrypoint,
- internal command authentication,
- writer health route,
- durable Python command mutation path,
- Go command client and public mutation routes,
- Compose dev wiring for `research-writer`,
- shared control-plane volume for Go reader plus Python writer,
- runtime-validation group registration,
- CI configuration for the research-registry runtime group.

Evidence:

- [docs/experiment-command-service-design.md](docs/experiment-command-service-design.md)
- [docs/experiment-registry-report.md](docs/experiment-registry-report.md)
- [docs/docker-runtime.md](docs/docker-runtime.md)

### Current validation status

Status: PARTIAL

Fresh local evidence on July 28, 2026 shows:

- the `research-writer` image builds,
- the Compose stack can start `research-writer`, `api`, `coordinator`, `postgres`, and `redis`,
- runtime health from the public API reports the writer path reachable,
- public `POST /api/v1/research/experiments/validate` succeeds,
- public `POST /api/v1/research/experiments` succeeds durably,
- exact create replay is idempotent,
- the registered `research-registry` runtime group now passes cleanly.

Freshly closed inside the active slice:

- `request_payload_hash_mismatch` on live validate/create,
- public Go write-path acceptance by the Python authoritative writer,
- authoritative Python durable create through the public path,
- passing registered research-registry runtime group.

This means:

- the foundation is IMPLEMENTED,
- the operational wiring is IMPLEMENTED,
- and the broader research-evaluation category is still PARTIAL because
  not every requested post-fix closure gate has fresh dedicated evidence
  yet.

### Immediate objective

Current immediate objective:

```text
Research Writer Live Docker and Runtime Validation Closure
```

Required closure tasks:

- fresh writer and Go API image build,
- live Compose startup,
- writer health,
- internal authentication,
- public validation API,
- public creation API,
- authoritative Python mutation,
- exact idempotent replay,
- idempotency conflict,
- synthetic start,
- failed-run preservation,
- public cancellation,
- cancellation replay,
- read-after-write,
- restart persistence,
- writer-unavailable behavior,
- corruption fail-closed behavior,
- `ADMIN` / `RESEARCHER` / `VIEWER` / `SERVICE` RBAC,
- Go read-only storage enforcement,
- runtime-validation group execution,
- CI-equivalent validation,
- artifact sanitation,
- documentation closure.

---

## Planned Research Platform Work

### Statistical analysis

Status: NOT STARTED

Required capabilities:

- mean,
- standard deviation,
- median,
- quantiles,
- confidence intervals,
- effect sizes,
- paired and unpaired comparisons,
- multiple-comparison correction,
- failed-seed disclosure,
- incompatible-result rejection.

### Real multi-seed benchmarking

Status: NOT STARTED

Required capabilities:

- real datasets,
- stable partition manifests,
- multiple independent seeds,
- reproducible environment manifests,
- failed-run preservation,
- safe cancellation,
- no silent retry.

### Research analyses

Status: NOT STARTED

Required analyses:

- IID versus non-IID,
- privacy-utility,
- fairness,
- convergence,
- secure aggregation overhead,
- adaptive clipping,
- communication cost,
- runtime behavior.

### Publication output

Status: NOT STARTED

Required outputs:

- scripted SVG and PDF figures,
- CSV, Markdown, and LaTeX tables,
- source experiment IDs,
- confidence intervals,
- no manual number editing.

### Web research dashboard

Status: NOT STARTED

Required capabilities:

- experiment registry views,
- run detail,
- failed-seed visibility,
- metrics,
- statistical results,
- figures,
- tables,
- reproducibility manifests,
- RBAC,
- browser tests.

---

## Blocked Capabilities

| Capability | Status | Blocking reason | Safe current behavior | Re-evaluation condition |
|---|---|---|---|---|
| Threshold recovery | BLOCKED | Dependency evaluation completed on July 28, 2026 with `NO_ACCEPTABLE_DEPENDENCY_FOUND`. No candidate cleared the security, maintenance, interoperability, and provenance bar. | Complete-cohort no-dropout secure aggregation remains the only supported provider. | A new candidate stack must pass a fresh evidence-based evaluation and supply-chain review. |
| Dropout recovery | BLOCKED | Depends on threshold recovery; no acceptable dependency exists and custom threshold cryptography is prohibited. | Abort the secure session instead of partial finalization. | Same as above plus a dedicated implementation and validation pass. |
| Partial-cohort unmasking | BLOCKED | Unsafe without an approved recovery design and approved dependency stack. | Fail closed; no degraded aggregate is emitted. | Same as threshold-recovery re-evaluation. |
| Malicious-client-secure aggregation | BLOCKED | Current protocol authenticates origin and structure, not semantic correctness or poisoning resistance. | Honest-but-curious coordinator protection only; poisoning remains outside the protocol guarantee. | A distinct malicious-client-secure protocol and validation program. |
| Verifiable clipping | BLOCKED | No cryptographic clipping-proof mechanism is implemented or approved. | Honest-client clipping assumption remains explicit. | An approved proof system and verification design with bounded operational cost. |
| Verifiable adaptive indicators | BLOCKED | No cryptographic proof that a worker's clipping indicator truthfully reflects its unclipped norm. | Indicators are privately aggregated but still honest-client dependent. | Same as above with a dedicated adaptive-indicator proof design. |
| Worker attestation | BLOCKED | No remote-attestation architecture, hardware policy, or issuance flow is integrated. | Workers are authenticated, not proven honest. | A future attestation design, hardware trust decision, and runtime integration. |
| Production privacy readiness | BLOCKED | Independent review, production persistence, HA, DR, and production operations are incomplete. | Research-scope privacy claims only. | Independent security and privacy review plus production controls and rollout approval. |

---

## Deferred Capabilities

Status: DEFERRED

Intentionally deferred, with no immediate execution requirement:

- distributed execution,
- asynchronous and semi-synchronous execution,
- enterprise persistence,
- high availability,
- disaster recovery,
- production Kubernetes,
- remote attestation deployment,
- TEE and TPM integration,
- Byzantine robustness,
- production release engineering.

These are deferred, not blocked:

- they are not waiting on one single external cryptographic decision the way dropout recovery is,
- but they are outside the current immediate research-security and research-evaluation closure scope.

---

## Trust Model and Unsupported Claims

Supported claims:

- Workers are authenticated.
- Signed messages prove origin and structural binding for the approved paths.
- Replay protection is implemented for the approved streams.
- The coordinator does not learn individual clear updates in the approved no-dropout secure complete-cohort flow, subject to the documented implementation and threat model.
- Sample-level and user-level guarantees protect different privacy units.
- No combined epsilon exists.

Unsupported claims:

- Workers are not proven honest.
- Signed claims prove origin, not semantic correctness.
- Sample-DP accounting is worker-generated and not independently recomputed by the coordinator.
- User clipping under secure aggregation is honest-client dependent.
- Adaptive indicators are not cryptographically verified.
- No-dropout secure aggregation requires the complete frozen cohort.
- Dropout-resilient confidentiality is unavailable.
- Malicious-client-secure aggregation is unavailable.
- Production privacy, security, and availability claims are unsupported.
- Secure aggregation does not provide Byzantine robustness or poisoning resistance.
- Secure aggregation does not replace TLS, worker authentication, or operator identity governance.

Evidence:

- [docs/secure-aggregation-threat-model.md](docs/secure-aggregation-threat-model.md)
- [docs/threshold-recovery-threat-model.md](docs/threshold-recovery-threat-model.md)
- [docs/known-limitations.md](docs/known-limitations.md)

---

## Validation Evidence Summary

| Capability | Evidence type | Latest known result | Date | Report |
|---|---|---|---|---|
| Security runtime and browser matrix | Live Docker runtime, browser validation, harness | `37 PASS, 0 FAIL, 0 BLOCKED, 57 DEFERRED, 0 SKIPPED`; Playwright `20/20 passed` | July 28, 2026 | [docs/security-runtime-validation.md](docs/security-runtime-validation.md), [docs/security-runtime-completion-report.md](docs/security-runtime-completion-report.md) |
| Signed cohort handshake | Live 3-worker Docker validation | `7/7 checks passed` | July 28, 2026 | [docs/secure-cohort-handshake-report.md](docs/secure-cohort-handshake-report.md) |
| Masked update runtime and secure FedAvg finalization | Live 3-worker Docker validation | `15/15 checks passed` | July 28, 2026 | [docs/secure-aggregation-masked-runtime-report.md](docs/secure-aggregation-masked-runtime-report.md) |
| Secure user-level DP under aggregation | Live Docker validation | `22/22 checks passed` | July 28, 2026 | [docs/secure-user-level-dp-runtime-report.md](docs/secure-user-level-dp-runtime-report.md) |
| Secure user-level operations and observability | Live harness and browser validation | `12/12` live runtime group | July 28, 2026 | [docs/secure-user-level-operations-report.md](docs/secure-user-level-operations-report.md) |
| Secure hybrid DP under aggregation | Live Docker validation | `38/38 checks passed` | July 28, 2026 | [docs/secure-hybrid-dp-runtime-report.md](docs/secure-hybrid-dp-runtime-report.md) |
| Secure adaptive clipping under aggregation | Live Docker validation | `46/46 checks passed` | July 28, 2026 | [docs/secure-adaptive-clipping-runtime-report.md](docs/secure-adaptive-clipping-runtime-report.md) |
| Threshold-recovery decision | Evaluation and dependency review | `NO_ACCEPTABLE_DEPENDENCY_FOUND` | July 28, 2026 | [docs/threshold-recovery-dependency-decision.md](docs/threshold-recovery-dependency-decision.md), [docs/threshold-recovery-evaluation-report.md](docs/threshold-recovery-evaluation-report.md) |
| Research specification and Python registry foundation | Targeted Python tests and docs | Implemented foundation; targeted tests green in the slice report | July 28, 2026 | [docs/experiment-specification.md](docs/experiment-specification.md), [docs/experiment-registry-design.md](docs/experiment-registry-design.md), [docs/experiment-registry-report.md](docs/experiment-registry-report.md) |
| Research Go integration and writer wiring | Go full tests, targeted Python tests, Compose config | `27 passed` on targeted Python research tests; Go full test/build/vet passed; Compose config passed | July 28, 2026 | [docs/experiment-registry-report.md](docs/experiment-registry-report.md), [docs/experiment-command-service-design.md](docs/experiment-command-service-design.md) |
| Research writer live runtime closure | Fresh local Docker/runtime validation | Live validate/create now succeed, exact create replay is idempotent, and the registered `research-registry` runtime group passes `3 PASS, 0 FAIL, 0 BLOCKED, 0 DEFERRED, 0 SKIPPED`; broader post-fix closure evidence remains partial | July 28, 2026 | [docs/experiment-registry-report.md](docs/experiment-registry-report.md), [docs/research-command-hash-mismatch-audit.md](docs/research-command-hash-mismatch-audit.md) |
| Security CI | Local execution of CI-equivalent commands; workflow config | Implemented locally, not yet observed on a real GitHub Actions run | July 28, 2026 | [docs/security-ci.md](docs/security-ci.md) |

---

## Immediate Execution Order

Current execution order:

```text
Research Writer Live Runtime Closure
-> Statistical Analysis Engine
-> Real Multi-Seed Federated Benchmark Runner
-> Privacy-Utility Analysis
-> Client Fairness Analysis
-> Convergence Analysis
-> Secure Aggregation Overhead Analysis
-> Adaptive Clipping Analysis
-> Publication Figures and Tables
-> Web Research Dashboard
-> Distributed Execution
-> Enterprise Platform
-> Observability and Operations Completion
-> Production Hardening
-> Independent Security, Privacy, and Scientific Review
```

This replaces the repository's older security-first execution order. The security foundation for the approved research scope is already in place; the current bottleneck is the research-evaluation platform.

---

## Readiness Levels

### Research security ready

Status: COMPLETE

Achieved for the approved scope when all of the following are true:

- authenticated transport is implemented,
- approved signing and replay protections are implemented,
- approved no-dropout secure aggregation is runtime validated,
- layered DP runtime is validated,
- security APIs and observability exist,
- blocked items remain honestly blocked rather than silently bypassed.

It does not imply production readiness or dropout resilience.

### Research evaluation ready

Status: PARTIAL

Requires:

- live writer runtime closure,
- real multi-seed execution,
- statistical analysis,
- reproducibility manifests,
- artifact sanitation,
- publication evidence.

### Internal pilot ready

Status: NOT STARTED

Requires:

- enterprise persistence,
- backup and restore,
- service reliability controls,
- operational monitoring,
- staging,
- a small external worker fleet,
- explicit trusted-coordinator and no-dropout acceptance.

### Production ready

Status: NOT STARTED

Requires:

- distributed execution,
- high availability,
- disaster recovery,
- independent security review,
- independent privacy review,
- penetration testing,
- load, soak, and chaos testing,
- production secrets handling,
- signed releases,
- SBOM,
- incident response,
- formal approval.

Production readiness does not automatically require dropout recovery if product policy explicitly accepts no-dropout operation, but that limitation must be approved, documented, and operationally acceptable.

---

## Repository and Git Policy

- The working tree is large and uncommitted.
- No automated cleanup is allowed from this plan alone.
- No commit, push, tag, or pull request should happen without explicit approval.
- Generated artifacts and secrets must remain excluded from source control.
- Validation evidence must be sanitized before publication or upload.
- Unrelated user changes must not be reverted or overwritten while updating this plan.

---

## Documentation Map

| Capability | Authoritative document |
|---|---|
| General current limitations | [docs/known-limitations.md](docs/known-limitations.md) |
| Docker topology and runtime notes | [docs/docker-runtime.md](docs/docker-runtime.md) |
| Security capability inventory | [docs/security-capability-inventory.md](docs/security-capability-inventory.md) |
| Security runtime closure and browser evidence | [docs/security-runtime-completion-report.md](docs/security-runtime-completion-report.md) |
| Security runtime harness results | [docs/security-runtime-validation.md](docs/security-runtime-validation.md) |
| Security CI boundaries | [docs/security-ci.md](docs/security-ci.md) |
| No-dropout secure-aggregation trust model | [docs/secure-aggregation-threat-model.md](docs/secure-aggregation-threat-model.md) |
| Signed cohort handshake runtime | [docs/secure-cohort-handshake-report.md](docs/secure-cohort-handshake-report.md) |
| Masked update runtime and secure finalization | [docs/secure-aggregation-masked-runtime-report.md](docs/secure-aggregation-masked-runtime-report.md) |
| Privacy-mode compatibility under secure aggregation | [docs/secure-aggregation-privacy-compatibility.md](docs/secure-aggregation-privacy-compatibility.md) |
| Secure user-level DP runtime | [docs/secure-user-level-dp-runtime-report.md](docs/secure-user-level-dp-runtime-report.md) |
| Secure user-level DP operations | [docs/secure-user-level-operations-report.md](docs/secure-user-level-operations-report.md) |
| Secure hybrid DP runtime | [docs/secure-hybrid-dp-runtime-report.md](docs/secure-hybrid-dp-runtime-report.md) |
| Secure adaptive clipping runtime | [docs/secure-adaptive-clipping-runtime-report.md](docs/secure-adaptive-clipping-runtime-report.md) |
| Secure adaptive clipping audit and semantics boundary | [docs/secure-adaptive-clipping-runtime-audit.md](docs/secure-adaptive-clipping-runtime-audit.md) |
| Threshold-recovery decision | [docs/threshold-recovery-dependency-decision.md](docs/threshold-recovery-dependency-decision.md) |
| Threshold-recovery evaluation report | [docs/threshold-recovery-evaluation-report.md](docs/threshold-recovery-evaluation-report.md) |
| Threshold-recovery threat model | [docs/threshold-recovery-threat-model.md](docs/threshold-recovery-threat-model.md) |
| Threshold-recovery architecture | [docs/threshold-recovery-protocol-architecture.md](docs/threshold-recovery-protocol-architecture.md) |
| Threshold-recovery candidate review | [docs/threshold-recovery-dependency-candidates.md](docs/threshold-recovery-dependency-candidates.md) |
| Threshold-recovery supply-chain review | [docs/threshold-recovery-supply-chain-review.md](docs/threshold-recovery-supply-chain-review.md) |
| Research foundation audit | [docs/research-evaluation-existing-capabilities-audit.md](docs/research-evaluation-existing-capabilities-audit.md) |
| Experiment specification foundation | [docs/experiment-specification.md](docs/experiment-specification.md) |
| Experiment registry design | [docs/experiment-registry-design.md](docs/experiment-registry-design.md) |
| Experiment registry infrastructure audit | [docs/experiment-registry-existing-infrastructure-audit.md](docs/experiment-registry-existing-infrastructure-audit.md) |
| Go research integration design | [docs/experiment-go-integration-design.md](docs/experiment-go-integration-design.md) |
| Cross-language research contract | [docs/experiment-cross-language-contract.md](docs/experiment-cross-language-contract.md) |
| Research registry status report | [docs/experiment-registry-report.md](docs/experiment-registry-report.md) |
| Research writer command-service boundary | [docs/experiment-command-service-design.md](docs/experiment-command-service-design.md) |

This map is the mechanism that should prevent future duplication inside `plan.md`.

---

## Plan Maintenance Checklist

- Update the date.
- Update the current category.
- Update the current objective.
- Update the status summary table.
- Link fresh evidence for every changed status.
- Distinguish IMPLEMENTED from VALIDATED.
- Distinguish PARTIAL from BLOCKED.
- Keep BLOCKED reasons explicit.
- Keep DEFERRED work separate from BLOCKED work.
- Remove superseded limitations.
- Remove obsolete execution order text.
- Run `python scripts/check_project_terminology.py`.
- Check Markdown links.
- Review `git diff -- plan.md`.
- Do not paste full historical completion reports into this file.

---

## Current-State Verdict

The repository is already a substantial research-grade federated-learning platform with a real Go control plane, a real C++ coordinator runtime, real Python worker training, layered differential privacy, authenticated and signed control paths, and a validated no-dropout secure-aggregation provider.

Within the approved research scope:

- the no-dropout secure-aggregation runtime is implemented and validated,
- secure sample-level, secure user-level, secure hybrid, and secure adaptive-clipping paths are implemented and validated,
- dropout recovery remains BLOCKED because the threshold-recovery evaluation ended with `NO_ACCEPTABLE_DEPENDENCY_FOUND`,
- the active focus is now the research-evaluation platform rather than foundational security.

The immediate gap is:

```text
Research Writer Live Docker and Runtime Validation Closure
```

What still remains after that:

- statistical analysis,
- real repeated benchmarking,
- publication figures and tables,
- a research web dashboard,
- distributed execution,
- enterprise platform capabilities,
- and production hardening plus independent review.
