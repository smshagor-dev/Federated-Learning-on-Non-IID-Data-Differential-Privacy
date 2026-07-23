# Deployment Foundation

This milestone establishes a local-first deployment baseline for the federated learning platform.

## Container Images

- `infra/docker/go-api.Dockerfile`
  - Multi-stage Go build for the control-plane API
  - Debian runtime with `curl` so Compose health checks can execute in-container
- `infra/docker/web.Dockerfile`
  - Multi-stage Next.js production image
- `infra/docker/python-worker.Dockerfile`
  - Python worker scaffold image for orchestration and future training jobs
- `infra/docker/cpp-coordinator.Dockerfile`
  - C++ coordinator build image for future native runtime packaging
- `infra/mlflow/Dockerfile`
  - MLflow tracking server image pinned to a known version

## Local Stack

The development stack is defined in:

- `infra/compose/docker-compose.dev.yml`
- `docker-compose.yml`

Included services:

- PostgreSQL
- Redis
- MinIO
- MLflow
- Go API
- Next.js web app
- Python worker
- Prometheus
- Grafana
- OpenTelemetry Collector

## Kubernetes Baseline

Initial manifests are available under `infra/kubernetes/` for:

- namespace creation
- shared configuration
- API deployment
- web deployment
- Python worker deployment
- stateful platform services

These manifests are intentionally conservative and serve as the first deployment baseline rather than a final production topology.

## Validated Commands

The following command was validated locally during this milestone:

```bash
docker compose config
```

The container build commands are prepared and documented, but local validation was blocked by a Docker Desktop daemon API failure in this environment:

```bash
docker build -f infra/docker/go-api.Dockerfile .
docker build -f infra/docker/python-worker.Dockerfile .
docker build -f infra/docker/web.Dockerfile .
```

## Known Gaps

- `protoc`-driven contract generation still depends on a local Protocol Buffers installation.
- Compose file parsing is validated, but full multi-service runtime bring-up is not yet exercised end-to-end.
- Kubernetes manifests have not yet been applied to a live cluster in this repository.
