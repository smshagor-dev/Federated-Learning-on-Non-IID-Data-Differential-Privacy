# Migration Strategy

## Goal

Evolve the current Python simulator into a production-oriented federated learning platform without breaking the existing research workflow before validated replacements exist.

## Guiding Principles

- Preserve the existing legacy prototype.
- Add new foundations beside the current workflow, not through a risky rewrite.
- Introduce stable contracts first.
- Validate parity before changing control ownership.
- Keep algorithm behavior regression-tested with deterministic fixtures.

## Milestone Sequence

### Milestone 1

- Audit and preserve the legacy system.
- Create monorepo structure.
- Add deterministic baseline tests.
- Add protobuf contracts.
- Add build foundations for C++, Python, Go, and web.
- Add local infrastructure scaffolding.

### Milestone 2

- Implement C++ tensor and aggregation core.
- Add golden compatibility tests against legacy Python aggregation behavior.

### Milestone 3

- Add C++ coordinator and Python worker RPC path.
- Keep legacy CLI available while new coordinator is validated.

### Milestone 4+

- Add advanced algorithms, privacy upgrades, service APIs, dashboard, and observability incrementally behind new interfaces.

## Preservation Plan

- The current prototype is copied into `legacy/python-research-studio/`.
- The root-level prototype remains available during Milestone 1 for compatibility.
- New services are scaffolded under `cpp/`, `python/`, `go/`, `web/`, and `proto/`.

## Compatibility Targets

- Deterministic model initialization
- Deterministic partitioning under fixed seeds
- Deterministic aggregation on synthetic inputs
- Stable client drift and weight variance calculations
- Stable accountant calculations for fixed parameters

## Replacement Order

1. Shared contracts
2. Deterministic test fixtures
3. C++ aggregation parity
4. Coordinator state machine
5. Python worker service
6. Go control plane
7. Web dashboard

## Deferred Work

The following are intentionally not implemented in Milestone 1:

- FedSAM
- Ditto
- Per-FedAvg
- Opacus
- Secure aggregation
- Asynchronous scheduling
- MLflow integration
- Prometheus/Grafana dashboards
- Kubernetes deployment logic

## Success Criteria for Milestone 1

- Legacy app preserved
- Audit docs written
- Monorepo scaffold committed
- Deterministic baseline tests added
- Initial build systems added
- Available validations executed and recorded
