# Federated Learning Platform

## Overview

This repository contains a multi-language Federated Learning Platform
for experimentation, evaluation, privacy engineering, and secure runtime
validation on non-IID data.

Current platform scope includes:

- federated training orchestration across a Go control plane, a C++
  coordinator, and Python workers,
- non-IID data partitioning and dataset registry support,
- multiple federated algorithms including FedAvg, FedProx, SCAFFOLD,
  FedOpt, FedSAM, Ditto, and Per-FedAvg,
- sample-level differential privacy in Python via Opacus,
- user-level differential privacy and adaptive clipping in the C++
  coordinator,
- hybrid privacy operation with separate accounting boundaries,
- authenticated transport, signed worker messages, signed coordinator
  tasks, replay protection, and signing-key lifecycle management,
- a Python-authoritative research writer and durable experiment registry,
- a Next.js dashboard for operational visibility, research workflows,
  and security surfaces,
- Docker Compose runtime validation with observability services.

The current readiness boundary is research-oriented and engineering-led,
not production-certified. The repository includes real runtime
implementations and substantial validation evidence, but it does not
claim full production hardening, dropout-resilient secure aggregation,
malicious-client robustness, or enterprise disaster recovery readiness.

## Key Capabilities

### Federated algorithms

- FedAvg
- FedProx
- SCAFFOLD
- FedOpt variants
- FedSAM
- Ditto
- Per-FedAvg

### Non-IID data support

- dataset partitioning utilities,
- dataset registry flows,
- fairness and worst-client evaluation projections.

### Privacy

- sample-level differential privacy with Opacus,
- user-level differential privacy in the coordinator,
- adaptive clipping,
- hybrid privacy mode with separate epsilon and delta accounting,
- no combined epsilon claim across mechanisms.

### Secure aggregation and security controls

- `SECAGG_NO_DROPOUT_EXPERIMENTAL` runtime,
- authenticated mTLS transport for supported control paths,
- signed worker capabilities, heartbeats, results, and privacy records,
- signed coordinator tasks,
- replay protection,
- certificate identity binding,
- signing-key rotation, revocation, and recovery flows,
- security events, audit surfaces, and operational validation hooks.

### Evaluation and reproducibility

- Python-authoritative research writer,
- durable experiment registry,
- Go read/query APIs,
- synthetic evaluation flows and multi-seed operational support,
- observability via Prometheus and Grafana when configured.

## Architecture

Primary control flow:

```text
Next.js Web Dashboard
        |
        | REST + SSE
        v
Go Control Plane
        |
        | gRPC + mTLS
        v
C++ Federated Coordinator
        |
        | gRPC + mTLS
        v
Python PyTorch Workers
```

Research registry write path:

```text
Web / External Client
        |
        v
Go Research API
        |
        | Authenticated internal command
        v
Python Research Writer
        |
        v
Durable Registry
```

High-level ownership:

- Web owns operator and researcher UI.
- Go owns HTTP APIs, auth, RBAC, audit, typed read models, and runtime
  projections.
- C++ owns coordinator runtime, aggregation, coordinator-side privacy
  enforcement, and secure aggregation execution.
- Python owns training, worker runtime behavior, authoritative research
  mutation logic, and privacy/accounting behavior on the worker side.

## Technology Stack

- C++
- Python
- Go
- Next.js
- PostgreSQL
- Redis
- Docker
- Prometheus
- Grafana
- gRPC
- Protobuf
- PyTorch
- Opacus

## Prerequisites

- Python 3.11 or newer
- Docker Desktop or Docker Engine with a reachable daemon
- `docker compose`
- Node.js and `npm` on `PATH`
- Git
- Web dependencies installed in `web/node_modules`, or use
  `python main.py --install-web`
- Development PKI material under `certs/dev/` when using secure
  launcher profiles such as `security` or `secure-cohort-handshake`

## Quick Start

The normal local developer workflow is:

```bash
python main.py
```

That command:

1. validates Python, Docker, Docker daemon, Docker Compose, Node.js,
   npm, repository files, runtime directories, Compose config, and web
   dependency state,
2. resolves the active Compose profile from real repository config,
3. starts backend Docker services,
4. waits for required backend readiness,
5. starts the Next.js web application as a separate managed local child
   process,
6. prints URLs and status,
7. keeps the launcher attached until shutdown.

Press `Ctrl+C` to stop the managed web process and backend containers
while preserving named volumes by default.

## First-Time Setup

1. Clone the repository.
2. Ensure Docker and Node.js are installed and available on `PATH`.
3. Install Python dependencies as required by your environment.
4. Install web dependencies when needed:

```bash
cd web
npm ci
cd ..
```

Or let the launcher do that on demand:

```bash
python main.py --install-web
```

5. If you intend to use secure profiles, generate or provision the dev
   PKI expected by `infra/compose/docker-compose.security.yml` and
   related overrides.

## Platform Commands

Primary and management commands:

```bash
python main.py
python main.py start
python main.py stop
python main.py restart
python main.py status
python main.py status --json
python main.py health
python main.py health --json
python main.py doctor
python main.py doctor --json
python main.py logs
python main.py logs --follow
python main.py logs api
python main.py logs coordinator
python main.py logs research-writer
python main.py logs python-worker
python main.py logs web
python main.py build
python main.py build api
python main.py build research-writer
python main.py build --no-cache
python main.py clean
python main.py clean --volumes --yes
```

Useful startup flags:

```bash
python main.py --build
python main.py --no-cache
python main.py --keep-backend
python main.py --install-web
python main.py --profile development
python main.py --profile security
python main.py --profile secure-cohort-handshake
python main.py --profile secure-user-level-dp
python main.py --profile secure-hybrid-dp
python main.py --profile secure-adaptive-clipping
python main.py --profile masked-update-runtime
python main.py --web-host 127.0.0.1
python main.py --web-port 3000
python main.py --verbose
```

## Service URLs

Default local URLs from the development Compose topology:

- Web Dashboard: `http://127.0.0.1:3000`
- Backend API: `http://127.0.0.1:8080`
- Research Runtime Health: `http://127.0.0.1:8080/api/v1/research/runtime/health`
- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3001`
- MLflow: `http://127.0.0.1:5000`
- MinIO API: `http://127.0.0.1:9000`
- MinIO Console: `http://127.0.0.1:9001`

Configured ports may override these defaults.

## Development Profiles

The launcher supports the profiles derived from real Compose files:

- `development`
- `security`
- `secure-cohort-handshake`
- `secure-user-level-dp`
- `secure-hybrid-dp`
- `secure-adaptive-clipping`
- `masked-update-runtime`

## Web Application

The web app starts automatically through:

```bash
python main.py
```

It remains a separate managed local process and is not launched from the
Compose `web` service during normal launcher startup.

Standalone web commands remain available:

```bash
cd web
npm run lint
npm run typecheck
npm run test
npm run build
```

## Backend Services

The development topology discovered from Compose currently includes:

- `postgres`
- `redis`
- `coordinator`
- `api`
- `research-writer`
- `python-worker`
- `prometheus`
- `grafana`
- `minio`
- `mlflow`
- `otel-collector`

The launcher excludes the Compose `web` service and starts the local
Next.js process instead.

## Testing

Common validation commands:

```bash
# Launcher and Python
python main.py --help
python -m pytest -q python/tests/test_platform_launcher_cli.py python/tests/test_repository_docs.py
python -m ruff check main.py python/src/fl_platform/cli python/tests
python -m ruff format --check main.py python/src/fl_platform/cli python/tests
python -m mypy main.py python/src/fl_platform/cli

# Repository text and documentation
python scripts/check_project_terminology.py
python scripts/validate_repository_docs.py

# Go
cd go
go fmt ./...
go vet ./...
go test ./...
go build ./...
cd ..

# Web
cd web
npm run lint
npm run typecheck
npm run test
npm run build
cd ..

# Docker / Compose
docker compose -f infra/compose/docker-compose.dev.yml config
docker compose -f infra/compose/docker-compose.dev.yml config --services
docker compose -f infra/compose/docker-compose.dev.yml config --volumes
```

## Security Model

Current security model includes:

- mTLS for supported service-to-service gRPC paths,
- certificate identity binding,
- worker signing identities,
- coordinator signing identities,
- replay protection,
- signed worker messages,
- signed privacy records,
- signed coordinator tasks,
- signing-key lifecycle management,
- role-aware security APIs,
- Python-authoritative registry mutation through the research writer.

Trust assumptions and deferred security work remain documented in the
security and limitation reports linked below.

## Privacy Model

The platform keeps privacy mechanisms distinct:

- sample-level privacy protects individual examples in worker-local
  training,
- user-level privacy protects a client contribution at the coordinator
  boundary,
- hybrid privacy runs both mechanisms without collapsing them into one
  epsilon,
- adaptive clipping protects the clipping statistic itself,
- epsilon and delta values remain mechanism-specific.

## Secure Aggregation

Current secure aggregation status:

- `SECAGG_NO_DROPOUT_EXPERIMENTAL` is implemented,
- a complete frozen cohort is required,
- dropout recovery is not available,
- threshold recovery remains blocked,
- partial-cohort unmasking is unavailable,
- malicious-client security is not claimed.

## Current Status

- Foundation: complete
- Aggregation core: complete
- Coordinator runtime: complete for the approved local/runtime scope
- Algorithms: complete for the currently implemented set
- Privacy engineering: complete for sample-level, user-level, adaptive,
  and hybrid modes
- Security surfaces: substantially implemented for the approved runtime
  scope
- No-dropout secure aggregation: implemented
- Evaluation platform: partial and actively evolving
- Distributed execution: not started
- Enterprise platform: not started
- Production hardening: not started

## Known Limitations

Major current limitations include:

- full dropout-resilient secure aggregation is not implemented,
- malicious-client secure aggregation is not claimed,
- production HA, DR, and incident-response hardening are out of scope,
- some live validation can be blocked by host-specific port conflicts,
- secure profiles require dev PKI material that is not committed.

See [docs/known-limitations.md](docs/known-limitations.md) for the
authoritative consolidated list.

## Documentation

Key documents:

- [plan.md](plan.md)
- [docs/platform-launcher.md](docs/platform-launcher.md)
- [docs/local-development.md](docs/local-development.md)
- [docs/web-development.md](docs/web-development.md)
- [docs/docker-runtime.md](docs/docker-runtime.md)
- [docs/privacy-engineering-report.md](docs/privacy-engineering-report.md)
- [docs/privacy-mathematics.md](docs/privacy-mathematics.md)
- [docs/secure-aggregation-threat-model.md](docs/secure-aggregation-threat-model.md)
- [docs/security-runtime-completion-report.md](docs/security-runtime-completion-report.md)
- [docs/security-runtime-validation.md](docs/security-runtime-validation.md)
- [docs/experiment-registry-report.md](docs/experiment-registry-report.md)
- [docs/known-limitations.md](docs/known-limitations.md)

## Contributing and Development Notes

- Preserve repository-owned terminology rules enforced by
  `scripts/check_project_terminology.py`.
- Do not assume the root launcher can kill unrelated host processes; it
  fails clearly on port conflicts instead.
- Keep the web app as a separate managed local process for the normal
  launcher workflow.
- Avoid mixing unrelated large working-tree changes into a single task
  commit.

## License

This repository includes the [Apache License 2.0](LICENSE).
