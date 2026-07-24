# See python/src/fl_platform/worker/__main__.py's module docstring for
# what this container actually does in Milestone 3: real gRPC connectivity
# to the coordinator (Health() polling), not full round execution — the
# rest of GrpcCoordinatorClient is intentionally deferred.
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY python ./python
COPY proto ./proto
COPY scripts/generate_protos.sh ./scripts/generate_protos.sh

# CPU-only torch: coordinator_client.py imports torch at module level
# (for tensor-dict type hints shared with task_runner.py's real training
# path), so it's required even for this container's current health-check
# entrypoint — see python/src/fl_platform/worker/__main__.py.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir numpy \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -e ./python \
    && pip install --no-cache-dir grpcio grpcio-tools

# generate_protos.sh writes Python bindings to
# python/src/fl_platform/generated/, matching where
# fl_platform.rpc.ensure_generated_on_path() looks for them — see
# scripts/generate_protos.sh's module comment.
RUN bash scripts/generate_protos.sh generated

ENV FL_WORKER_COORDINATOR_ADDRESS=coordinator:50051
ENV FL_WORKER_WORKER_ID=python-worker-1
CMD ["python", "-m", "fl_platform.worker"]
