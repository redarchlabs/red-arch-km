FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy workspace definition and shared packages first (cache layer)
COPY pyproject.toml ./
COPY packages/ packages/
COPY services/api/ services/api/

RUN cd services/api && uv sync --frozen --no-dev 2>/dev/null || uv pip install --system -e ".[all]" 2>/dev/null || pip install -e .

# Create non-root user
RUN useradd --create-home appuser
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
