FROM python:3.11-slim
WORKDIR /app
COPY python/pyproject.toml ./pyproject.toml
COPY python/src ./src
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && pip install --no-cache-dir -e .
CMD ["python", "-c", "print('python worker scaffold container ready')"]
