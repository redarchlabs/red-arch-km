FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy workspace definition, the uv lockfile, and the shared packages first
# so image layers cache well — service code changes don't invalidate the
# dependency layer.
COPY pyproject.toml uv.lock* ./
COPY packages/ packages/
COPY services/api/ services/api/

# Install dependencies from the workspace. --no-editable keeps the build
# reproducible; --no-dev skips test-only packages. Fail the build loudly
# if deps can't be installed — no silent fallbacks.
RUN uv pip install --system --no-cache \
        -e ./packages/access_mask \
        -e ./packages/shared_config \
        -e ./packages/brain_sdk \
        -e ./services/api

# Chown app dir so the non-root user can read it (packages are installed
# system-wide, so this is mostly cosmetic, but safer if any runtime writes).
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "600"]
