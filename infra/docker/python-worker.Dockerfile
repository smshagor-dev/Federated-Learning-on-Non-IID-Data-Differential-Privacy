# See python/src/fl_platform/worker/__main__.py's module docstring for
# what this container does: when FL_WORKER_RUN_ID is set, it runs the
# real register->acquire->train->submit loop against a live gRPC
# coordinator (see docs/create-run-wire-mapping.md); otherwise it falls
# back to Health()-only polling.
FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY python ./python
COPY proto ./proto
COPY scripts/generate_protos.sh ./scripts/generate_protos.sh
# task_runner.py deliberately reuses the legacy prototype's proven
# FedAvg/FedProx/SCAFFOLD local-training implementation (federated.client)
# rather than reimplementing it — see task_runner.py's module docstring.
# `python -m` adds the current working directory (/app, per WORKDIR
# above) to sys.path, so this is importable as top-level `federated`
# without any separate install step.
COPY federated ./federated

# CPU-only torch: coordinator_client.py imports torch at module level
# (for tensor-dict type hints shared with task_runner.py's real training
# path), so it's required even for this container's current health-check
# entrypoint — see python/src/fl_platform/worker/__main__.py.
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir numpy "scipy>=1.10.0" \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    # Security Runtime Completion slice: the base `-e ./python` install
    # alone omits the `security` extra (pynacl, cryptography --
    # python/pyproject.toml), so every module that imports `nacl` at
    # module level (fl_platform.security.capability_statement, imported
    # transitively by fl_platform.security.__init__ and therefore by
    # __main__.py's signing_identity import) crashed this container on
    # startup with ModuleNotFoundError before this fix -- caught by this
    # slice's live Docker Compose validation, not by any unit test,
    # since unit tests run against a host environment that already has
    # pynacl installed.
    && pip install --no-cache-dir -e "./python[security]" \
    && pip install --no-cache-dir grpcio grpcio-tools \
    # Privacy Engineering phase: opacus is what makes
    # supports_sample_level_dp truthfully True in this container's
    # advertised WorkerPrivacyCapabilities (see
    # fl_platform.privacy.accounting.opacus_capabilities) — without it
    # here, this worker correctly (and safely) never receives a
    # sample-level/hybrid-DP task, it just can't do private training at
    # all. prometheus_client backs fl_platform.privacy.metrics, imported
    # unconditionally (not lazily) by fl_platform.privacy — required for
    # ANY worker, private or not, to start at all.
    && pip install --no-cache-dir opacus prometheus_client

# generate_protos.sh writes Python bindings to
# python/src/fl_platform/generated/, matching where
# fl_platform.rpc.ensure_generated_on_path() looks for them — see
# scripts/generate_protos.sh's module comment.
RUN bash scripts/generate_protos.sh generated

ENV FL_WORKER_COORDINATOR_ADDRESS=coordinator:50051
ENV FL_WORKER_WORKER_ID=python-worker-1
CMD ["python", "-m", "fl_platform.worker"]
