locals {
  name = "${var.name_prefix}-${var.environment}"

  repo_url = "${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_repo_id}"

  # brain-api has no custom domain (backend-only); always its run.app URL.
  # Public UI/API URLs prefer the custom domain, else the Cloud Run run.app URL.
  api_public_url = var.domain != "" ? "https://api.${var.domain}" : module.cloud_run.api_uri
  ui_public_url  = var.domain != "" ? "https://${var.domain}" : module.cloud_run.ui_uri
}

# ---------------------------------------------------------------------------
# Enable required APIs first — everything else depends on this.
# ---------------------------------------------------------------------------
module "project_services" {
  source     = "./modules/project_services"
  project_id = var.project_id
}

# ---------------------------------------------------------------------------
# Networking: VPC, subnet (Direct VPC egress + PGA), PSA peering (Memorystore),
# Cloud NAT, firewall, reserved internal IP for the data VM.
# ---------------------------------------------------------------------------
module "network" {
  source              = "./modules/network"
  name                = local.name
  region              = var.region
  subnet_cidr         = var.subnet_cidr
  psa_cidr            = var.psa_cidr
  data_vm_internal_ip = var.data_vm_internal_ip
  labels              = var.labels

  depends_on = [module.project_services]
}

# ---------------------------------------------------------------------------
# Service accounts (least-privilege identities per service).
# ---------------------------------------------------------------------------
module "iam" {
  source     = "./modules/iam"
  project_id = var.project_id
  name       = local.name

  depends_on = [module.project_services]
}

# ---------------------------------------------------------------------------
# Secret Manager: internal secrets (random) + external containers (populated by
# scripts/add-external-secrets.sh) + the composed DATABASE_URL.
# ---------------------------------------------------------------------------
module "secrets" {
  source              = "./modules/secrets"
  name                = local.name
  postgres_user       = var.postgres_user
  postgres_db         = var.postgres_db
  data_vm_internal_ip = var.data_vm_internal_ip
  labels              = var.labels

  api_sa_email       = module.iam.api_sa_email
  brain_sa_email     = module.iam.brain_sa_email
  vm_sa_email        = module.iam.vm_sa_email
  ui_sa_email        = module.iam.ui_sa_email
  bootstrap_sa_email = module.iam.bootstrap_sa_email

  depends_on = [module.project_services]
}

# ---------------------------------------------------------------------------
# Object storage: documents bucket + backups bucket + HMAC key (S3-compat) and
# the STORAGE_ACCESS_KEY / STORAGE_SECRET_KEY secrets.
# ---------------------------------------------------------------------------
module "storage" {
  source       = "./modules/storage"
  project_id   = var.project_id
  name         = local.name
  region       = var.region
  api_sa_email = module.iam.api_sa_email
  vm_sa_email  = module.iam.vm_sa_email
  labels       = var.labels

  depends_on = [module.project_services]
}

# ---------------------------------------------------------------------------
# Artifact Registry Docker repo (images pushed by scripts/build-images.sh).
# ---------------------------------------------------------------------------
module "artifact_registry" {
  source  = "./modules/artifact_registry"
  name    = local.name
  region  = var.region
  repo_id = var.artifact_repo_id
  labels  = var.labels
  reader_sa_emails = [
    module.iam.api_sa_email,
    module.iam.brain_sa_email,
    module.iam.ui_sa_email,
    module.iam.vm_sa_email,
    module.iam.bootstrap_sa_email,
  ]

  depends_on = [module.project_services]
}

# ---------------------------------------------------------------------------
# Memorystore for Redis (Celery broker/backend + rate-limit cache).
# ---------------------------------------------------------------------------
module "redis" {
  source         = "./modules/redis"
  name           = local.name
  region         = var.region
  tier           = var.redis_tier
  memory_gb      = var.redis_memory_gb
  network_id     = module.network.network_id
  labels         = var.labels
  psa_connection = module.network.psa_connection_id

  depends_on = [module.project_services, module.network]
}

# ---------------------------------------------------------------------------
# Cloud Run app tier: ui, api (public), brain-api (all-ingress, key-gated).
# ---------------------------------------------------------------------------
module "cloud_run" {
  source     = "./modules/cloud_run"
  project_id = var.project_id
  region     = var.region
  name       = local.name
  domain     = var.domain

  repo_url  = local.repo_url
  image_tag = var.image_tag

  subnet_id           = module.network.subnet_id
  data_vm_internal_ip = var.data_vm_internal_ip
  redis_host          = module.redis.host
  redis_port          = module.redis.port

  storage_bucket = module.storage.documents_bucket
  storage_region = var.region

  secret_ids = merge(module.secrets.secret_ids, module.storage.secret_ids)

  api_sa_email   = module.iam.api_sa_email
  brain_sa_email = module.iam.brain_sa_email
  ui_sa_email    = module.iam.ui_sa_email

  min_instances       = var.cloud_run_min_instances
  brain_min_instances = var.brain_min_instances

  neo4j_user                 = var.neo4j_user
  clerk_jwt_issuer           = var.clerk_jwt_issuer
  clerk_publishable_key      = var.clerk_publishable_key
  openai_chat_model          = var.openai_chat_model
  openai_embedding_model     = var.openai_embedding_model
  api_docs_enabled           = var.api_docs_enabled
  workflow_webhook_allowlist = var.workflow_webhook_allowlist

  depends_on = [module.secrets, module.storage, module.redis, module.artifact_registry]
}

# ---------------------------------------------------------------------------
# Data VM: Postgres + Qdrant + Neo4j + Celery worker + beat (docker compose).
# ---------------------------------------------------------------------------
module "data_vm" {
  source     = "./modules/data_vm"
  project_id = var.project_id
  region     = var.region
  zone       = var.zone
  name       = local.name

  machine_type = var.data_vm_machine_type
  image        = var.data_vm_image
  subnet_id    = module.network.subnet_id
  internal_ip  = var.data_vm_internal_ip

  postgres_disk_gb = var.postgres_disk_gb
  qdrant_disk_gb   = var.qdrant_disk_gb
  neo4j_disk_gb    = var.neo4j_disk_gb

  repo_url  = local.repo_url
  image_tag = var.image_tag

  redis_host = module.redis.host
  redis_port = module.redis.port

  brain_api_url = module.cloud_run.brain_uri
  api_url       = local.api_public_url

  vm_sa_email = module.iam.vm_sa_email

  postgres_user      = var.postgres_user
  postgres_db        = var.postgres_db
  neo4j_user         = var.neo4j_user
  worker_concurrency = var.worker_concurrency

  openai_chat_model      = var.openai_chat_model
  openai_embedding_model = var.openai_embedding_model

  documents_bucket = module.storage.documents_bucket
  backups_bucket   = module.storage.backups_bucket
  storage_region   = var.region

  secret_ids = merge(module.secrets.secret_ids, module.storage.secret_ids)
  labels     = var.labels

  depends_on = [module.cloud_run, module.redis, module.secrets, module.storage, module.artifact_registry]
}

# ---------------------------------------------------------------------------
# One-shot Cloud Run job that runs `alembic upgrade head` against the VM
# Postgres. Executed on demand by scripts/db-init.sh (not auto-run).
# ---------------------------------------------------------------------------
module "db_bootstrap" {
  source     = "./modules/db_bootstrap"
  region     = var.region
  name       = local.name
  repo_url   = local.repo_url
  image_tag  = var.image_tag
  subnet_id  = module.network.subnet_id
  sa_email   = module.iam.bootstrap_sa_email
  secret_ids = module.secrets.secret_ids

  depends_on = [module.secrets, module.artifact_registry]
}
