FROM python:3.11-slim
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash curl protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

COPY python ./python
COPY proto ./proto
COPY scripts/generate_protos.sh ./scripts/generate_protos.sh
COPY federated ./federated

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir numpy "scipy>=1.10.0" \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -e "./python[security]" \
    && pip install --no-cache-dir prometheus_client

RUN bash scripts/generate_protos.sh generated

ENV FL_RESEARCH_COMMAND_PORT=8090
EXPOSE 8090
CMD ["python", "-m", "fl_platform.research"]
