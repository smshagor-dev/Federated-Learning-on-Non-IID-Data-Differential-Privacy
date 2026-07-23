# Federated Learning Super System

This repository now begins a staged migration from a single-process Python research prototype into a production-oriented federated learning platform.

## Current Status

- Root-level Python prototype remains available for compatibility.
- Legacy-preserved copy exists at `legacy/python-research-studio/`.
- Milestone 1 scaffolding has been added for:
  - C++20 federated core foundation
  - Python package foundation
  - Go control-plane foundation
  - Next.js web foundation
  - Protobuf contracts
  - Infrastructure scaffolding
  - Baseline deterministic tests
  - Audit and migration documentation

## Legacy Compatibility

The legacy prototype still runs from the repository root:

```bash
python main.py
python main.py --cli
python main.py --cli --dataset MNIST --rounds 1 --algo fedavg --dp off
```

The preserved copy is also available under:

```text
legacy/python-research-studio/
```

## Milestone 1 Layout

```text
cpp/
python/
go/
web/
proto/
infra/
docs/
legacy/
scripts/
tests/
```

## Key Docs

- `docs/current-system-audit.md`
- `docs/current-architecture.md`
- `docs/privacy-audit.md`
- `docs/migration-strategy.md`
- `docs/risk-register.md`
- `docs/deployment-foundation.md`

## Validation

Available local validation commands for Milestone 1 include:

```bash
python -m unittest discover -s tests -p "test_*.py"
cmake -S cpp -B build/cpp
cmake --build build/cpp
ctest --test-dir build/cpp --output-on-failure
go test ./...
```

Additional commands for protobuf generation, web validation, Docker validation, and future service integration are scaffolded but may require extra local toolchains that are not guaranteed to be present yet.

## Deployment Foundation

Infrastructure scaffolding is now available for local compose-based development and a first-pass Kubernetes baseline.

```bash
docker compose config
docker build -f infra/docker/go-api.Dockerfile .
docker build -f infra/docker/python-worker.Dockerfile .
docker build -f infra/docker/web.Dockerfile .
```

See `docs/deployment-foundation.md` for the current deployment scope and known gaps.
