FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
COPY packages/ packages/
COPY services/brain_api/ services/brain_api/

RUN cd services/brain_api && uv sync --frozen --no-dev 2>/dev/null || uv pip install --system -e ".[all]" 2>/dev/null || pip install -e .

RUN useradd --create-home appuser
USER appuser

EXPOSE 8020

CMD ["uvicorn", "brain_api.main:app", "--host", "0.0.0.0", "--port", "8020", "--timeout-keep-alive", "600"]
