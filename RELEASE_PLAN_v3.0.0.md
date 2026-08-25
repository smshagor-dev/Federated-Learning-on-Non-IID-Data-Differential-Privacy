# Federated Learning Platform v3.0.0 Release Plan

**Status:** Release candidate — stable scope frozen; same-SHA qualification required  
**Package:** `fl-platform==3.0.0`

v3.0.0 is qualified against an explicit stable-support contract. Source-code presence is not release support. A gate may qualify either by demonstrating a stable capability or by proving that an experimental surface is explicitly excluded and fails closed. The release must never convert an unvalidated combination into a stable claim merely to make a checklist green.

## Stable-support gate contract

`fl_platform.v3.release_gates.REQUIRED_V3_GATES` remains the canonical 13-gate registry. `fl_platform.v3.release_support.GATE_QUALIFICATIONS` defines the stable claim, experimental exclusions, and executable checks for each gate.

| Gate | v3.0.0 stable qualification | Explicit non-stable boundary |
|---|---|---|
| `async-runtime` | Durable model/version checkpoints, replay protection, and lease-membership primitives | True distributed asynchronous training remains experimental; async + secure aggregation is rejected |
| `robust-aggregation` | Median/trimmed-mean robust aggregation for supported non-private synchronous paths plus deterministic attack validation | Robust + DP and robust + secure aggregation remain rejected |
| `privacy-validation` | DP accounting, budget enforcement, statistical validation, and release-validated FedAvg/FedProx privacy combinations | Unvalidated privacy compositions remain fail-closed |
| `secure-aggregation` | Authenticated protocol/wire primitives, encrypted recovery-share relay, replay/integrity validation, and live recovery test coverage | Threshold dropout recovery is not promoted to a stable public capability; in-flight secure-round restart is not guaranteed |
| `algorithm-suite` | FedAvg, FedProx, SCAFFOLD, FedSAM, Ditto, and Per-FedAvg worker implementations and canonical registry behavior | Newer algorithms without release qualification are not promoted to stable claims |
| `system-heterogeneity` | Deterministic compute/network/availability/payload heterogeneity simulation and execution policies | Physical heterogeneous-fleet performance is not claimed |
| `federated-workloads` | MNIST, FashionMNIST, CIFAR10, and CIFAR100 workload/partition contracts | FEMNIST, Shakespeare, and Sent140 remain experimental loaders |
| `distributed-infrastructure` | Containerized coordinator/API/worker stack, mTLS identities, signed messages, replay protection, centralized events, Kubernetes contract, and restart validation | Geographic multi-host performance is not claimed |
| `observability` | Privacy-safe aggregate metrics plus distributed security/audit event validation | A hosted collector/dashboard is not bundled as an operational guarantee |
| `benchmark-matrix` | Real five-seed MNIST/FedAvg/IID stable-baseline evidence plus fail-closed matrix planning/provenance checks | Completion of the entire attack × privacy × heterogeneity cross-product is not claimed |
| `edge-runtime` | ARM64 OCI image build/self-test and edge resource/payload-policy validation | Physical edge-device throughput, energy, and thermal performance are not claimed |
| `supply-chain-security` | Immutable image digests, digest-pinned Kubernetes bundle, CycloneDX SBOM, artifact hashes, and GitHub attestations | No mutable release image is accepted |
| `chaos-reliability` | Deterministic 500-seed chaos soak plus real container restart scenarios | Long-duration physical multi-host soak is not a v3.0.0 stability claim |

## Empirical benchmark gate

The final release commit runs `scripts/run_benchmark_matrix.py` against the real root runtime using `release/v3.0.0-benchmark.yaml` and exactly five seeds: `11,23,37,53,71`.

The qualifying baseline is deliberately narrow and reproducible:

- runtime: `root-simulator`;
- dataset: MNIST;
- algorithm: FedAvg;
- partition: IID;
- privacy: non-private;
- one qualification round per seed.

`scripts/validate_v3_release_benchmark.py` requires all five cells to complete, binds every observation to the exact Git commit, requires global accuracy/loss/wall-clock metrics for every seed, and emits `evidence_complete=true` only for this stable baseline. The broader v3 matrix specification remains useful for extended evaluation but is not misrepresented as fully executed release evidence.

## Same-SHA release rule

The final `v3.0.0` tag may only point at a commit already contained in `main` for which all of these workflows completed successfully on that exact SHA:

1. `ci.yml` — full repository CI;
2. `v3-release-candidate.yml` — software, secure-aggregation, chaos, ARM64, and container release candidate gates;
3. `v3-distributed-runtime.yml` — real containerized mTLS/distributed/security and restart evidence;
4. `v3-final-qualification.yml` — version/support-contract checks and real five-seed benchmark evidence.

The tag workflow verifies these same-SHA conclusions before it builds or publishes release artifacts. PR-head success is not a substitute for the final merge SHA.

## Current fail-closed boundaries

- True distributed asynchronous training is experimental. The durable async primitives do not advertise remote asynchronous orchestration as stable.
- Secure aggregation is not advertised as compatible with asynchronous or Byzantine-robust aggregation.
- Threshold recovery remains experimental in the public capability matrix even though authenticated recovery, X25519 reconstruction, and encrypted share relay implementations are validated by tests.
- An in-flight secure round is not promised resumable after coordinator process loss. The stable contract treats this as an explicit non-resumable boundary rather than pretending the volatile session state is durable.
- Robust aggregation + differential privacy remains blocked.
- FEMNIST, Shakespeare, and Sent140 loaders remain non-stable until their provenance/license and broader execution qualification is complete.
- ARM64 build compatibility is stable; physical device performance is outside the v3.0.0 claim.
- Containerized distributed validation is stable evidence for the packaged deployment path; independent geographic multi-host performance is outside the v3.0.0 claim.

## Release artifacts

After the same-SHA gate rule passes, `.github/workflows/v3-release-artifacts.yml` must:

- require package/tag parity at `3.0.0` / `v3.0.0`;
- download the exact final-qualification benchmark/gate evidence;
- build and push API and Python-worker images;
- resolve immutable first-party and third-party image digests;
- render and validate digest-pinned Kubernetes manifests;
- build wheel/sdist and archive the exact source commit;
- emit a CycloneDX SBOM and artifact hash manifest;
- create GitHub provenance/SBOM attestations;
- publish the GitHub Release with release notes and evidence artifacts.

## Final acceptance criteria

v3.0.0 is ready only when the final merge SHA has all four required workflows green, the five-seed empirical baseline is complete and commit-bound, the package/module versions are both exactly `3.0.0`, and the tag release-artifact workflow succeeds. Experimental surfaces listed above remain explicitly non-stable; their presence does not widen the stable support contract.
