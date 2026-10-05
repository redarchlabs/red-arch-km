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

COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/worker/ services/worker/

# Install exactly the third-party versions pinned in uv.lock, hash-checked:
# `uv export --frozen` reads the lockfile without re-resolving (a plain
# `uv pip install -e ...` would resolve to the newest versions the pyproject
# ranges allow). Then the workspace packages themselves, editable as before,
# with --no-deps so nothing is resolved twice. Fails loudly on any drift.
RUN uv export --frozen --no-dev --no-emit-workspace --package worker -o /tmp/requirements.txt \
    && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps \
        -e ./packages/shared_config \
        -e ./packages/brain_sdk \
        -e ./services/worker \
    && rm /tmp/requirements.txt

RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

CMD ["celery", "-A", "worker.celery_app", "worker", "--loglevel=info", "--concurrency=4"]
