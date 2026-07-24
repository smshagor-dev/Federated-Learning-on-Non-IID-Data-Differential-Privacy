# Federated Learning Super System

This repository now begins a staged migration from a single-process Python research prototype into a production-oriented federated learning platform.

## Current Status

- Root-level Python prototype remains available for compatibility.
- Legacy-preserved copy exists at `legacy/python-research-studio/`.
- **Milestone 1** (release-gate hardening): Go control plane
  (project/experiment/run bookkeeping, auth, audit log), a web dashboard,
  Docker/Kubernetes scaffolding, baseline deterministic tests.
- **Milestone 2** (C++ aggregation core): FedAvg/FedProx/FedOpt/SCAFFOLD
  aggregation math, checkpoint store, cross-language golden parity tests.
- **Milestone 3** (coordinator runtime): a real C++ coordinator (gRPC
  server + local-dev CLI bridge) driving run/round/task lifecycle,
  checkpoint/crash recovery, per-client SCAFFOLD state persistence,
  real-time event streaming; a PyTorch worker; Go↔coordinator gRPC
  integration; Docker Compose services for both; cross-language
  integration tests. See
  [docs/milestone-3-report.md](docs/milestone-3-report.md) for the full
  writeup and [docs/known-limitations.md](docs/known-limitations.md) for
  what's still deferred.

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

- `docs/current-system-audit.md`, `docs/current-architecture.md`, `docs/privacy-audit.md`, `docs/migration-strategy.md`, `docs/risk-register.md`, `docs/deployment-foundation.md` — Milestone 1
- `docs/cpp-aggregation-architecture.md`, `docs/scaffold-state.md`, `docs/fedopt.md`, `docs/checkpoint-format.md`, `docs/milestone-2-report.md` — Milestone 2
- `docs/milestone-3-architecture.md`, `docs/coordinator-runtime.md`, `docs/python-worker.md`, `docs/go-coordinator-integration.md`, `docs/grpc-contracts.md`, `docs/task-leasing.md`, `docs/worker-lifecycle.md`, `docs/scaffold-client-state.md`, `docs/coordinator-recovery.md`, `docs/event-streaming.md`, `docs/docker-runtime.md`, `docs/milestone-3-validation.md`, `docs/milestone-3-report.md` — Milestone 3
- `docs/known-limitations.md` — consolidated, all milestones

## Validation

```bash
# Python
python -m pytest -q
ruff check . && ruff format --check .
mypy --exclude 'generated' --follow-imports=silent python/src

# C++
cmake -S cpp -B build/cpp-debug -DCMAKE_BUILD_TYPE=Debug
cmake --build build/cpp-debug --config Debug
ctest --test-dir build/cpp-debug -C Debug --output-on-failure

# Go
cd go && gofmt -l . && go vet ./... && go build ./... && go test ./...

# Web
cd web && npm run typecheck && npm run lint && npm run test && npm run build

# Protobuf contracts (no protoc required)
python scripts/verify_proto_contracts.py
```

See [docs/milestone-3-validation.md](docs/milestone-3-validation.md) for
the full command-by-command results, including what's CI-only (`go test
-race`, C++ AddressSanitizer/ThreadSanitizer — no cgo/Clang locally).

## Deployment / Docker Compose

```bash
docker compose config
docker compose build            # coordinator, api, web, python-worker, mlflow
docker compose up -d
docker compose ps
docker compose down -v
```

`coordinator` (the real C++ gRPC server) and `python-worker` (the
PyTorch worker) are new in Milestone 3 — see
[docs/docker-runtime.md](docs/docker-runtime.md). See
`docs/deployment-foundation.md` for the original Milestone 1 scope and
Kubernetes baseline.
