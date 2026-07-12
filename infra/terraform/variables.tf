# ---------------------------------------------------------------------------
# Core project / location
# ---------------------------------------------------------------------------
variable "project_id" {
  description = "GCP project ID to deploy into (billing must be enabled)."
  type        = string
}

variable "region" {
  description = "Primary GCP region for all regional resources."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Zone for the data VM (must be within var.region)."
  type        = string
  default     = "us-central1-a"
}

variable "name_prefix" {
  description = "Short prefix for resource names."
  type        = string
  default     = "km2"
}

variable "environment" {
  description = "Environment name (prod, staging, ...). Part of resource names."
  type        = string
  default     = "prod"
}

variable "labels" {
  description = "Labels applied to all supporting resources."
  type        = map(string)
  default     = { app = "km2", managed-by = "terraform" }
}

# ---------------------------------------------------------------------------
# Custom domain (STRONGLY recommended). When set:
#   ui  is served at https://<domain>
#   api is served at https://api.<domain>
# and the UI image is built with NEXT_PUBLIC_API_URL=https://api.<domain>.
# When empty, Cloud Run run.app URLs are used and the UI must be rebuilt after
# the api URL is known (two-phase — see README).
# ---------------------------------------------------------------------------
variable "domain" {
  description = "Apex domain for the UI (api served on the api.<domain> subdomain). Empty = use run.app URLs."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Container images (built + pushed by scripts/build-images.sh)
# ---------------------------------------------------------------------------
variable "image_tag" {
  description = "Tag for all four service images in Artifact Registry."
  type        = string
  default     = "2.0.0"
}

variable "artifact_repo_id" {
  description = "Artifact Registry Docker repository ID."
  type        = string
  default     = "km2"
}

# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------
variable "subnet_cidr" {
  description = "Primary subnet CIDR (used by the VM and Cloud Run Direct VPC egress)."
  type        = string
  default     = "10.10.0.0/24"
}

variable "psa_cidr" {
  description = "Private Services Access range reserved for managed services (Memorystore)."
  type        = string
  default     = "10.20.0.0/20"
}

variable "data_vm_internal_ip" {
  description = "Static internal IP reserved for the data VM (must be inside subnet_cidr)."
  type        = string
  default     = "10.10.0.10"
}

# ---------------------------------------------------------------------------
# Data VM sizing (runs Postgres + Qdrant + Neo4j + Celery worker + beat)
# ---------------------------------------------------------------------------
variable "data_vm_machine_type" {
  description = "Machine type for the data VM."
  type        = string
  default     = "e2-standard-4"
}

variable "data_vm_image" {
  description = "Boot image for the data VM."
  type        = string
  default     = "ubuntu-os-cloud/ubuntu-2204-lts"
}

variable "qdrant_disk_gb" {
  description = "Persistent SSD size for Qdrant storage."
  type        = number
  default     = 20
}

variable "neo4j_disk_gb" {
  description = "Persistent SSD size for Neo4j data."
  type        = number
  default     = 20
}

variable "worker_concurrency" {
  description = "Celery worker concurrency on the data VM (kept modest to limit contention with the co-located data stores)."
  type        = number
  default     = 2
}

# ---------------------------------------------------------------------------
# Memorystore (Redis)
# ---------------------------------------------------------------------------
variable "redis_tier" {
  description = "Memorystore tier: BASIC (single node) or STANDARD_HA."
  type        = string
  default     = "BASIC"
}

variable "redis_memory_gb" {
  description = "Memorystore capacity in GB."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# Cloud Run app tier
# ---------------------------------------------------------------------------
variable "cloud_run_min_instances" {
  description = "Minimum instances for the public services (ui, api). 1 keeps them warm for small production."
  type        = number
  default     = 1
}

variable "brain_min_instances" {
  description = "Minimum instances for brain-api."
  type        = number
  default     = 1
}

# ---------------------------------------------------------------------------
# Cloud SQL for PostgreSQL (managed; the app's FORCE-RLS model runs on a
# non-superuser km_app role, so no BYPASSRLS connection is needed).
# ---------------------------------------------------------------------------
variable "postgres_db" {
  description = "Application database name."
  type        = string
  default     = "redarch_km"
}

variable "km_app_user" {
  description = "Non-superuser runtime DB role the app connects as (RLS-enforced)."
  type        = string
  default     = "km_app"
}

variable "postgres_admin_user" {
  description = "Cloud SQL admin/migration role (gets cloudsqlsuperuser). Runs Alembic."
  type        = string
  default     = "postgres"
}

variable "postgres_version" {
  description = "Cloud SQL Postgres version. RLS + pg_trgm work on 15+; 17 is a safe default (raise to POSTGRES_18 if available in your region)."
  type        = string
  default     = "POSTGRES_17"
}

variable "cloudsql_tier" {
  description = "Cloud SQL machine tier."
  type        = string
  default     = "db-custom-1-3840"
}

variable "cloudsql_availability_type" {
  description = "ZONAL (small prod) or REGIONAL (HA)."
  type        = string
  default     = "ZONAL"
}

variable "cloudsql_disk_gb" {
  description = "Cloud SQL data disk size (GB, autoresizes)."
  type        = number
  default     = 50
}

variable "cloudsql_deletion_protection" {
  description = "Block accidental deletion of the Cloud SQL instance."
  type        = bool
  default     = true
}

variable "neo4j_user" {
  description = "Neo4j username."
  type        = string
  default     = "neo4j"
}

# ---------------------------------------------------------------------------
# Clerk (identity provider). Publishable key is browser-exposed (not secret) and
# is baked into the UI image at build time. Secret key + issuer are runtime.
# ---------------------------------------------------------------------------
variable "clerk_jwt_issuer" {
  description = "Clerk Frontend API URL (the token 'iss'), e.g. https://<slug>.clerk.accounts.dev."
  type        = string
  default     = ""
}

variable "clerk_publishable_key" {
  description = "Clerk publishable key (pk_...). Browser-exposed; baked into the UI image."
  type        = string
  default     = ""
}

variable "clerk_jwt_template" {
  description = "Name of the Clerk JWT template that injects email/email_verified/username claims."
  type        = string
  default     = "redarch-km"
}

# ---------------------------------------------------------------------------
# Application knobs surfaced from the app's env contract
# ---------------------------------------------------------------------------
variable "openai_chat_model" {
  description = "OpenAI chat model."
  type        = string
  default     = "gpt-5-mini"
}

variable "openai_embedding_model" {
  description = "OpenAI embedding model."
  type        = string
  default     = "text-embedding-3-small"
}

variable "api_docs_enabled" {
  description = "Serve /api/v1/docs. Set false to hide in hardened prod."
  type        = bool
  default     = false
}

variable "workflow_webhook_allowlist" {
  description = "Comma-separated allow-list of hosts for workflow send_webhook actions (SSRF guard). Empty disables outbound webhooks."
  type        = string
  default     = ""
}
