#!/usr/bin/env bash
# run-stack.sh — start (or stop) the full KM2 development stack.
#
#   ./run-stack.sh          start everything (always restarts host API+UI; infra idempotent)
#   ./run-stack.sh restart  alias for start (host API+UI are always killed and relaunched)
#   ./run-stack.sh stop     stop host processes + app containers (infra keeps running)
#
# Start ALWAYS kills any existing host API/UI first, so a stale dev server
# (e.g. one still serving an out-of-date .next build after a rebuild) can never
# linger on :8000/:3000 and shadow the fresh one.
#
# The dev stack is a hybrid:
#   docker : postgres(5433) redis qdrant neo4j | brain-api(8020) | celery worker + beat
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

# Free the given TCP port by killing whatever LISTENs on it (this stack owns
# :8000 and :3000). TERM first, then KILL if it clings.
free_port() {
  local port="$1" pids
  pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)"
  [ -z "$pids" ] && return 0
  kill $pids 2>/dev/null || true
  sleep 1
  pids="$(lsof -ti "tcp:${port}" -sTCP:LISTEN 2>/dev/null || true)"
  [ -n "$pids" ] && kill -9 $pids 2>/dev/null || true
  return 0
}

stop_host() {
  # API: match the uvicorn cmdline, then make sure :8000 is actually free.
  pkill -f 'uvicorn api\.main:app' 2>/dev/null || true
  free_port 8000 && say "stopped api" || true

  # UI: kill THIS repo's Next dev launcher (repo-scoped path, so we never touch
  # another project's dev server), then free :3000 — the detached 'next-server'
  # child that actually holds the port does NOT contain 'next dev' in its
  # cmdline, which is why 'pkill -f "next dev"' left stale servers behind.
  pkill -f "$PWD/ui/node_modules/.bin/next" 2>/dev/null || true
  free_port 3000 && say "stopped ui" || true
}

if [ "$MODE" = "stop" ]; then
  stop_host
  docker stop km2_brain_api km2_worker_fixed km2_beat 2>/dev/null || true
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
for pair in "km2_redis redis" "km2_qdrant qdrant" "km2_postgres postgres" "km2_neo4j neo4j" "km2_minio minio"; do
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

# --- 3b. celery beat (scheduler) ----------------------------------------------
# Beat fires the periodic tasks — the workflow outbox sweep (every 10s) and
# partition maintenance. The worker only *executes* tasks; without beat,
# sweep_outbox is never enqueued and the workflow outbox never drains (create/
# update events pile up as 'pending' and no workflow ever runs). Kept as a
# separate single-process container: one scheduler regardless of worker
# concurrency, so periodic tasks are never double-fired.
if ! docker ps --format '{{.Names}}' | grep -q '^km2_beat$'; then
  say "beat…"
  docker start km2_beat 2>/dev/null || \
    docker run -d --name km2_beat --network km2_network --env-file .env \
      -e CELERY_BROKER_URL=redis://redis:6379/0 \
      -e CELERY_RESULT_BACKEND=redis://redis:6379/1 \
      docker-worker celery -A worker.celery_app beat --loglevel=info \
        --schedule /tmp/celerybeat-schedule
fi

# --- 4. host processes (uvicorn API + next dev UI) ----------------------------
# Always kill any existing host API/UI first so a stale process can't linger.
say "stopping any existing host api/ui…"
stop_host
sleep 1

say "api…"
# 0.0.0.0 so the worker container can reach status callbacks via the docker
# gateway. Dev-only trade-off: the API is reachable on your LAN.
setsid nohup .venv/bin/uvicorn api.main:app \
  --env-file "$ENV_HOST" --host 0.0.0.0 --port 8000 \
  --app-dir services/api/src >"$API_LOG" 2>&1 </dev/null &

# --- 5. UI (next dev) ----------------------------------------------------------
say "ui…"
(cd ui && setsid nohup npm run dev >"$UI_LOG" 2>&1 </dev/null &)

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
status "beat" "docker ps --format '{{.Names}}' | grep -q km2_beat" "docker logs km2_beat"
echo
say "first-run? check '$API_LOG' for the setup token banner, then open /setup"
