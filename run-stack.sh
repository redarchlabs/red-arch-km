#!/usr/bin/env bash
# run-stack.sh — start (or stop) the full KM2 development stack.
#
#   ./run-stack.sh          start everything (idempotent — skips what's running)
#   ./run-stack.sh restart  force-restart the host API and UI processes
#   ./run-stack.sh stop     stop host processes + app containers (infra keeps running)
#
# The dev stack is a hybrid:
#   docker : postgres(5433) redis qdrant neo4j | brain-api(8020) | celery worker
#   host   : FastAPI api via uvicorn (8000)    | Next.js UI dev server (3000)
#
# Host processes read .env.host (localhost URLs, Clerk issuer, e2e test mode);
# containers read .env. Keep OPENAI_API_KEY/BRAIN_API_KEY in sync between them.
set -euo pipefail
cd "$(dirname "$0")"

ENV_HOST=.env.host
API_LOG=/tmp/km2_api_dev.log
UI_LOG=/tmp/km2_ui_dev.log
MODE="${1:-start}"

say() { printf '\033[1;36m[stack]\033[0m %s\n' "$*"; }

api_up() { curl -sf -m 2 http://localhost:8000/healthz >/dev/null 2>&1; }
ui_up()  { curl -sf -m 2 -o /dev/null http://localhost:3000/login 2>/dev/null; }

stop_host() {
  pkill -f 'uvicorn api\.main:app' 2>/dev/null && say "stopped api" || true
  pkill -f 'next dev' 2>/dev/null && say "stopped ui" || true
}

if [ "$MODE" = "stop" ]; then
  stop_host
  docker stop km2_brain_api km2_worker_fixed 2>/dev/null || true
  say "app stopped (infra containers left running; 'make down' stops those too)"
  exit 0
fi

if [ ! -f "$ENV_HOST" ]; then
  echo "ERROR: $ENV_HOST missing — copy .env and adjust URLs to localhost (see docs/DEVELOPMENT.md)" >&2
  exit 1
fi

# --- 1. infrastructure containers -------------------------------------------
say "infrastructure (postgres/redis/qdrant/neo4j)…"
docker compose --env-file .env -f docker/docker-compose.infra.yml up -d

# Service-name DNS aliases: the app containers reach these as redis/qdrant/
# postgres/neo4j. Compose-created containers can lack the aliases when stacks
# were mixed, so re-assert them idempotently.
for pair in "km2_redis redis" "km2_qdrant qdrant" "km2_postgres postgres" "km2_neo4j neo4j"; do
  set -- $pair
  cname=$1; alias=$2
  if ! docker inspect -f '{{json .NetworkSettings.Networks.km2_network.Aliases}}' "$cname" 2>/dev/null | grep -q "\"$alias\""; then
    docker network disconnect km2_network "$cname" 2>/dev/null || true
    docker network connect --alias "$alias" --alias "$cname" km2_network "$cname"
    say "added network alias '$alias' -> $cname"
  fi
done

# --- 2. brain-api (python, RAG) ----------------------------------------------
say "brain-api…"
docker compose --env-file .env -f docker/docker-compose.yml up -d --no-deps brain-api

# --- 3. celery worker ---------------------------------------------------------
# Custom container: the compose service's URLs point at the wrong hosts for
# this hybrid layout (host api, dockerized brain).
if ! docker ps --format '{{.Names}}' | grep -q '^km2_worker_fixed$'; then
  say "worker…"
  docker start km2_worker_fixed 2>/dev/null || {
    GATEWAY=$(docker network inspect km2_network -f '{{(index .IPAM.Config 0).Gateway}}')
    docker run -d --name km2_worker_fixed --network km2_network --env-file .env \
      -e CELERY_BROKER_URL=redis://redis:6379/0 \
      -e CELERY_RESULT_BACKEND=redis://redis:6379/1 \
      -e DATABASE_URL="postgresql+asyncpg://redarch:redarch123@postgres:5432/redarch_km" \
      -e BRAIN_API_URL=http://brain-api:8020 \
      -e API_URL="http://${GATEWAY}:8000" \
      docker-worker celery -A worker.celery_app worker --loglevel=info --concurrency=4
  }
fi

# --- 4. host API (uvicorn) ----------------------------------------------------
if [ "$MODE" = "restart" ]; then stop_host; sleep 1; fi
if api_up; then
  say "api already running on :8000"
else
  say "api…"
  # 0.0.0.0 so the worker container can reach status callbacks via the docker
  # gateway. Dev-only trade-off: the API is reachable on your LAN.
  setsid nohup .venv/bin/uvicorn api.main:app \
    --env-file "$ENV_HOST" --host 0.0.0.0 --port 8000 \
    --app-dir services/api/src >"$API_LOG" 2>&1 </dev/null &
fi

# --- 5. UI (next dev) ----------------------------------------------------------
if ui_up; then
  say "ui already running on :3000"
else
  say "ui…"
  (cd ui && setsid nohup npm run dev >"$UI_LOG" 2>&1 </dev/null &)
fi

# --- 6. wait + report -----------------------------------------------------------
say "waiting for health…"
for _ in $(seq 1 30); do
  api_up && ui_up && break
  sleep 1
done

status() { if eval "$2"; then echo "  ✅ $1"; else echo "  ❌ $1  (log: $3)"; fi; }
echo
status "api        http://localhost:8000" api_up "$API_LOG"
status "ui         http://localhost:3000" ui_up "$UI_LOG"
status "brain-api  http://localhost:8020" "curl -sf -m 2 http://localhost:8020/healthz >/dev/null" "docker logs km2_brain_api"
status "worker" "docker ps --format '{{.Names}}' | grep -q km2_worker_fixed" "docker logs km2_worker_fixed"
echo
say "first-run? check '$API_LOG' for the setup token banner, then open /setup"
