FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy workspace definition, the uv lockfile, and the shared packages first
# so image layers cache well — service code changes don't invalidate the
# dependency layer.
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/api/ services/api/

# Install exactly the third-party versions pinned in uv.lock, hash-checked:
# `uv export --frozen` reads the lockfile without re-resolving (a plain
# `uv pip install -e ...` would resolve to the newest versions the pyproject
# ranges allow). Then the workspace packages themselves, editable as before,
# with --no-deps so nothing is resolved twice. Fails loudly on any drift.
RUN uv export --frozen --no-dev --no-emit-workspace --package api -o /tmp/requirements.txt \
    && uv pip install --system --no-cache --require-hashes -r /tmp/requirements.txt \
    && uv pip install --system --no-cache --no-deps \
        -e ./packages/access_mask \
        -e ./packages/shared_config \
        -e ./packages/brain_sdk \
        -e ./services/api \
    && rm /tmp/requirements.txt

# Chown app dir so the non-root user can read it (packages are installed
# system-wide, so this is mostly cosmetic, but safer if any runtime writes).
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
