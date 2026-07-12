#!/usr/bin/env bash
# KM2 data VM bootstrap — runs on every boot (idempotent).
# Brings up Qdrant + Neo4j + Celery worker + beat via docker compose. Postgres
# lives on Cloud SQL; the worker connects there as km_app via the DATABASE_URL
# secret. Redis is Memorystore.
set -euo pipefail
exec > >(tee -a /var/log/km2-startup.log) 2>&1
echo "=== km2 startup $(date -u) ==="

export DEBIAN_FRONTEND=noninteractive

# --- 1. Base packages: Docker (+compose plugin) and gcloud -------------------
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi

if ! command -v gcloud >/dev/null 2>&1; then
  apt-get update
  apt-get install -y apt-transport-https ca-certificates gnupg curl
  curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg
  echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    > /etc/apt/sources.list.d/google-cloud-sdk.list
  apt-get update
  apt-get install -y google-cloud-cli
fi

# --- 2. Format + mount the two persistent data disks ------------------------
mount_disk() {
  dev="/dev/disk/by-id/google-$1"; mnt="$2"
  mkdir -p "$mnt"
  if ! blkid "$dev" >/dev/null 2>&1; then
    mkfs.ext4 -F "$dev"
  fi
  if ! mountpoint -q "$mnt"; then
    mount "$dev" "$mnt"
  fi
  grep -q "$mnt" /etc/fstab || echo "$dev $mnt ext4 discard,defaults,nofail 0 2" >> /etc/fstab
}
mount_disk qdrantdata /mnt/qdrant
mount_disk neo4jdata  /mnt/neo4j

# --- 3. Fetch secrets and render the environment file -----------------------
mkdir -p /opt/km2
sec() { gcloud secrets versions access latest --secret="$1" 2>/dev/null || true; }

cat > /opt/km2/.env <<ENVEOF
DATABASE_URL=$(sec ${sec_database_url})
NEO4J_USER=${neo4j_user}
NEO4J_PASSWORD=$(sec ${sec_neo4j_password})
REDIS_URL=redis://${redis_host}:${redis_port}/0
CELERY_BROKER_URL=redis://${redis_host}:${redis_port}/0
CELERY_RESULT_BACKEND=redis://${redis_host}:${redis_port}/1
QDRANT_URL=http://qdrant:6333
NEO4J_URI=bolt://neo4j:7687
BRAIN_API_URL=${brain_api_url}
API_URL=${api_url}
BRAIN_API_KEY=$(sec ${sec_brain_api_key})
INTERNAL_API_KEY=$(sec ${sec_internal_api_key})
ORG_ENCRYPTION_KEY=$(sec ${sec_org_encryption_key})
OPENAI_API_KEY=$(sec ${sec_openai_api_key})
OPENAI_CHAT_MODEL=${openai_chat_model}
OPENAI_EMBEDDING_MODEL=${openai_embedding_model}
STORAGE_ENDPOINT=https://storage.googleapis.com
STORAGE_ACCESS_KEY=$(sec ${sec_storage_access_key})
STORAGE_SECRET_KEY=$(sec ${sec_storage_secret_key})
STORAGE_BUCKET=${documents_bucket}
STORAGE_REGION=${storage_region}
WORKER_CONCURRENCY=${worker_concurrency}
LOG_LEVEL=INFO
ENVEOF
chmod 600 /opt/km2/.env

# --- 4. Compose stack (Qdrant + Neo4j + worker + beat) ----------------------
# NOTE: a doubled dollar ($$) below is rendered by Terraform as a single dollar,
# leaving a compose-style placeholder that docker compose interpolates from
# /opt/km2/.env at runtime. Single-dollar Terraform vars are rendered here.
cat > /opt/km2/docker-compose.yml <<'COMPOSEEOF'
services:
  qdrant:
    image: qdrant/qdrant:v1.12.4
    restart: unless-stopped
    volumes:
      - /mnt/qdrant:/qdrant/storage
    ulimits:
      nofile: { soft: 65535, hard: 65535 }

  neo4j:
    image: neo4j:5.25.1
    restart: unless-stopped
    environment:
      NEO4J_AUTH: "$${NEO4J_USER}/$${NEO4J_PASSWORD}"
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: "apoc.*"
    volumes:
      - /mnt/neo4j:/data

  worker:
    image: ${repo_url}/km2-worker:${image_tag}
    restart: unless-stopped
    env_file: [.env]
    command: celery -A worker.celery_app worker --loglevel=info --concurrency=${worker_concurrency}
    depends_on: [qdrant, neo4j]

  beat:
    image: ${repo_url}/km2-worker:${image_tag}
    restart: unless-stopped
    env_file: [.env]
    command: celery -A worker.celery_app beat --loglevel=info
    depends_on: [worker]
COMPOSEEOF

# --- 5. Pull + start --------------------------------------------------------
gcloud auth configure-docker ${region}-docker.pkg.dev --quiet
cd /opt/km2
docker compose pull
docker compose up -d

echo "=== km2 startup complete $(date -u) ==="
