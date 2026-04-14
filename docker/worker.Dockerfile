FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
COPY packages/ packages/
COPY services/worker/ services/worker/

RUN cd services/worker && uv sync --frozen --no-dev 2>/dev/null || uv pip install --system -e ".[all]" 2>/dev/null || pip install -e .

RUN useradd --create-home appuser
USER appuser

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
