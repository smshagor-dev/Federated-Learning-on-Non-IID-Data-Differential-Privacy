# Federated Learning Platform v3.0.0

Version 3.0.0 is the first release qualified against the v3 stable-support contract. It strengthens the platform around privacy validation, resilient distributed execution, secure protocol boundaries, reproducible benchmarking, edge compatibility, and verifiable release artifacts.

## Stable release scope

The v3.0.0 stable support contract includes:

- FedAvg, FedProx, SCAFFOLD, FedSAM, Ditto, and Per-FedAvg worker implementations with canonical capability discovery.
- Differential-privacy accounting, budget enforcement, statistical validation, and fail-closed combination checks for release-validated paths.
- Median and trimmed-mean robust aggregation for supported non-private synchronous execution, with deterministic adversarial validation.
- Deterministic compute, network, availability, and payload heterogeneity simulation.
- Validated image-workload contracts for MNIST, FashionMNIST, CIFAR10, and CIFAR100.
- Containerized coordinator, API, and Python-worker deployment with mTLS identity, signed-message verification, replay protection, centralized security/audit events, and restart validation.
- Privacy-safe aggregate observability primitives and distributed metrics validation.
- ARM64 OCI worker-image build and self-test compatibility.
- Deterministic 500-seed chaos-soak validation and container restart scenarios.
- Immutable release image locks, CycloneDX SBOM generation, artifact hashing, and GitHub provenance attestations.

## Empirical release qualification

The final release commit must pass a real five-seed baseline against the root runtime:

- Runtime: `root-simulator`
- Dataset: MNIST
- Algorithm: FedAvg
- Partition: IID
- Privacy: non-private
- Seeds: 11, 23, 37, 53, 71
- Qualification rounds: 1 per seed

The benchmark evidence is bound to the exact release commit and includes the plan, per-cell status, observations, summary, and evidence hashes. This baseline qualifies the stable runtime path; it does not claim that every optional benchmark cross-product has been empirically executed.

## Explicit experimental boundaries

The following surfaces are present for continued engineering and validation but are **not** promoted to stable v3.0.0 support:

- True distributed asynchronous training.
- Threshold secure-aggregation dropout recovery as a production capability.
- Resuming an in-flight secure round after coordinator process loss.
- FEMNIST, Shakespeare, and Sent140 federated loaders.
- New algorithm combinations that are not release-qualified by the stable capability matrix.
- Combined robust aggregation with differential privacy or secure aggregation.
- Physical multi-host throughput/latency guarantees.
- Physical edge-device energy, thermal, or throughput guarantees.
- Completion of the full attack × privacy × heterogeneity benchmark cross-product.

Unsupported combinations remain fail-closed rather than being silently treated as stable.

## Release evidence and supply chain

A v3.0.0 tag is publishable only when the exact tagged commit has successful runs for:

- the full repository `ci` workflow;
- `v3 release candidate`;
- `v3 distributed runtime evidence`;
- `v3 final qualification`.

The tag workflow then builds and publishes immutable API and Python-worker images, resolves third-party image digests, renders digest-pinned Kubernetes manifests, builds Python wheel/sdist artifacts, creates a source archive, emits a CycloneDX SBOM and artifact hash manifest, attaches GitHub attestations, and publishes the GitHub Release.

## Upgrade notes

- Python package version is `3.0.0` and requires Python 3.11 or newer.
- Existing v2 synchronous training paths remain available; v3 capability validation adds stricter rejection of unqualified combinations.
- Deployments should use the digest-pinned release Kubernetes bundle instead of mutable image tags.
- Consumers relying on experimental async, threshold-recovery, or LEAF-loader surfaces should treat those interfaces as non-stable and pin the exact release if evaluating them.
