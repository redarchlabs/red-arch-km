FROM python:3.12-slim AS base

# OCR deps: pytesseract needs the tesseract binary; pdf2image needs poppler-utils.
# antiword extracts text from legacy .doc (Word 97-2003) files.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    antiword \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
COPY services/worker/ services/worker/

RUN uv pip install --system --no-cache \
        -e ./packages/shared_config \
        -e ./packages/brain_sdk \
        -e ./services/worker

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
