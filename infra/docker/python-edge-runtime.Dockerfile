FROM python:3.12-slim

WORKDIR /app
COPY python ./python
COPY federated ./federated

RUN python -m pip install --no-cache-dir --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir ./python

RUN python - <<'PY'
from fl_platform.v3.edge_runtime import Int8UpdateCodec

codec = Int8UpdateCodec()
source = tuple((index - 64) / 16.0 for index in range(128))
encoded = codec.encode(source)
restored = codec.decode(encoded)
assert len(restored) == len(source)
assert encoded.compressed_bytes < encoded.dense_float64_bytes
assert max(abs(a - b) for a, b in zip(source, restored, strict=True)) <= (
    encoded.maximum_absolute_error + 1e-12
)
print("edge runtime self-test passed")
PY

CMD ["python", "-c", "from fl_platform.v3.edge_runtime import EDGE_CODEC; print(EDGE_CODEC)"]
