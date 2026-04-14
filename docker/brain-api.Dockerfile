FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
COPY services/brain_api/ services/brain_api/

RUN uv pip install --system --no-cache \
        -e ./packages/shared_config \
        -e ./packages/brain_sdk \
        -e ./services/brain_api

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8020

CMD ["uvicorn", "brain_api.main:app", "--host", "0.0.0.0", "--port", "8020", "--timeout-keep-alive", "600"]
