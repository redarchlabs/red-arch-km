variable "project_id" { type = string }
variable "region" { type = string }
variable "name" { type = string }
variable "domain" { type = string }
variable "repo_url" { type = string }
variable "image_tag" { type = string }
variable "subnet_id" { type = string }
variable "data_vm_internal_ip" { type = string }
variable "redis_host" { type = string }
variable "redis_port" { type = number }
variable "storage_bucket" { type = string }
variable "storage_region" { type = string }
variable "secret_ids" { type = map(string) }
variable "api_sa_email" { type = string }
variable "brain_sa_email" { type = string }
variable "ui_sa_email" { type = string }
variable "min_instances" { type = number }
variable "brain_min_instances" { type = number }
variable "neo4j_user" { type = string }
variable "clerk_jwt_issuer" { type = string }
variable "clerk_publishable_key" { type = string }
variable "openai_chat_model" { type = string }
variable "openai_embedding_model" { type = string }
variable "api_docs_enabled" { type = bool }
variable "workflow_webhook_allowlist" { type = string }

locals {
  redis_url    = "redis://${var.redis_host}:${var.redis_port}/0"
  celery_bkr   = "redis://${var.redis_host}:${var.redis_port}/0"
  celery_bake  = "redis://${var.redis_host}:${var.redis_port}/1"
  qdrant_url   = "http://${var.data_vm_internal_ip}:6333"
  neo4j_uri    = "bolt://${var.data_vm_internal_ip}:7687"
  has_domain = var.domain != ""
  ui_public  = local.has_domain ? "https://${var.domain}" : google_cloud_run_v2_service.ui.uri
  # Domain-only to avoid a self-reference cycle (api -> local.api_public -> api).
  # Without a domain, API_PUBLIC_URL is left empty; it only feeds the optional
  # MCP-OAuth callback URL, which needs a stable public domain anyway.
  api_public   = local.has_domain ? "https://api.${var.domain}" : ""
  storage_host = "https://storage.googleapis.com"
}

# ---------------------------------------------------------------------------
# brain-api (backend RAG service). ingress=all but gated by BRAIN_API_KEY so
# the VM-hosted worker can reach it without an internal load balancer.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "brain" {
  name                = "${var.name}-brain"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = var.brain_sa_email
    timeout         = "600s"

    scaling {
      min_instance_count = var.brain_min_instances
      max_instance_count = 4
    }

    vpc_access {
      network_interfaces {
        subnetwork = var.subnet_id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "${var.repo_url}/km2-brain-api:${var.image_tag}"
      ports { container_port = 8020 }

      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }

      env {
        name  = "QDRANT_URL"
        value = local.qdrant_url
      }
      env {
        name  = "NEO4J_URI"
        value = local.neo4j_uri
      }
      env {
        name  = "NEO4J_USER"
        value = var.neo4j_user
      }
      env {
        name  = "OPENAI_CHAT_MODEL"
        value = var.openai_chat_model
      }
      env {
        name  = "OPENAI_EMBEDDING_MODEL"
        value = var.openai_embedding_model
      }
      env {
        name = "BRAIN_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["brain_api_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "NEO4J_PASSWORD"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["neo4j_password"]
            version = "latest"
          }
        }
      }
      env {
        name = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["openai_api_key"]
            version = "latest"
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# ui (Next.js). NEXT_PUBLIC_* are baked into the image at build time; the UI
# never references the api resource, which keeps the graph acyclic. Only the
# server-side @clerk/nextjs secret is provided at runtime.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "ui" {
  name                = "${var.name}-ui"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = var.ui_sa_email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = 10
    }

    containers {
      image = "${var.repo_url}/km2-ui:${var.image_tag}"
      ports { container_port = 3000 }

      resources {
        limits = { cpu = "1", memory = "512Mi" }
      }

      env {
        name  = "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
        value = var.clerk_publishable_key
      }
      env {
        name = "CLERK_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["clerk_secret_key"]
            version = "latest"
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# api (FastAPI). Public; reaches Postgres (VM) + Redis (Memorystore) over the
# VPC and brain-api over public HTTPS (BRAIN_API_KEY). References ui.uri for
# CORS/PUBLIC_BASE_URL (one-directional — no cycle).
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "api" {
  name                = "${var.name}-api"
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = false

  template {
    service_account = var.api_sa_email
    timeout         = "600s"

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = 10
    }

    vpc_access {
      network_interfaces {
        subnetwork = var.subnet_id
      }
      egress = "PRIVATE_RANGES_ONLY"
    }

    containers {
      image = "${var.repo_url}/km2-api:${var.image_tag}"
      ports { container_port = 8000 }

      resources {
        limits = { cpu = "1", memory = "1Gi" }
      }

      env {
        name  = "REDIS_URL"
        value = local.redis_url
      }
      env {
        name  = "CELERY_BROKER_URL"
        value = local.celery_bkr
      }
      env {
        name  = "CELERY_RESULT_BACKEND"
        value = local.celery_bake
      }
      # api reads the brain URL as API_BRAIN_API_URL (env_prefix API_, no alias).
      env {
        name  = "API_BRAIN_API_URL"
        value = google_cloud_run_v2_service.brain.uri
      }
      env {
        name  = "API_CORS_ORIGINS"
        value = jsonencode([local.ui_public])
      }
      env {
        name  = "PUBLIC_BASE_URL"
        value = local.ui_public
      }
      env {
        name  = "API_PUBLIC_URL"
        value = local.api_public
      }
      env {
        name  = "CLERK_JWT_ISSUER"
        value = var.clerk_jwt_issuer
      }
      env {
        name  = "CLERK_ALLOWED_AZP"
        value = local.ui_public
      }
      env {
        name  = "STORAGE_ENDPOINT"
        value = local.storage_host
      }
      env {
        name  = "STORAGE_BUCKET"
        value = var.storage_bucket
      }
      env {
        name  = "STORAGE_REGION"
        value = var.storage_region
      }
      env {
        name  = "API_DEBUG"
        value = "false"
      }
      env {
        name  = "API_DOCS_ENABLED"
        value = tostring(var.api_docs_enabled)
      }
      env {
        name  = "OPENAI_CHAT_MODEL"
        value = var.openai_chat_model
      }
      env {
        name  = "OPENAI_EMBEDDING_MODEL"
        value = var.openai_embedding_model
      }
      env {
        name  = "WORKFLOW_WEBHOOK_ALLOWLIST"
        value = var.workflow_webhook_allowlist
      }
      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }

      # --- secrets ---
      env {
        name = "DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["database_url"]
            version = "latest"
          }
        }
      }
      env {
        name = "API_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["api_secret_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "BRAIN_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["brain_api_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "INTERNAL_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["internal_api_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "ORG_ENCRYPTION_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["org_encryption_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "OPENAI_API_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["openai_api_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "CLERK_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["clerk_secret_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "STORAGE_ACCESS_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["storage_access_key"]
            version = "latest"
          }
        }
      }
      env {
        name = "STORAGE_SECRET_KEY"
        value_source {
          secret_key_ref {
            secret  = var.secret_ids["storage_secret_key"]
            version = "latest"
          }
        }
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Public (unauthenticated at the platform layer) invocation. App-level auth
# (Clerk for ui/api, BRAIN_API_KEY for brain) is enforced inside the services.
# ---------------------------------------------------------------------------
resource "google_cloud_run_v2_service_iam_member" "ui_public" {
  location = google_cloud_run_v2_service.ui.location
  name     = google_cloud_run_v2_service.ui.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "api_public" {
  location = google_cloud_run_v2_service.api.location
  name     = google_cloud_run_v2_service.api.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "brain_public" {
  location = google_cloud_run_v2_service.brain.location
  name     = google_cloud_run_v2_service.brain.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ---------------------------------------------------------------------------
# Custom domain mappings (only when var.domain is set).
# ---------------------------------------------------------------------------
resource "google_cloud_run_domain_mapping" "ui" {
  count    = local.has_domain ? 1 : 0
  location = var.region
  name     = var.domain

  metadata {
    namespace = var.project_id
  }
  spec {
    route_name = google_cloud_run_v2_service.ui.name
  }
}

resource "google_cloud_run_domain_mapping" "api" {
  count    = local.has_domain ? 1 : 0
  location = var.region
  name     = "api.${var.domain}"

  metadata {
    namespace = var.project_id
  }
  spec {
    route_name = google_cloud_run_v2_service.api.name
  }
}

output "api_uri" {
  value = google_cloud_run_v2_service.api.uri
}

output "brain_uri" {
  value = google_cloud_run_v2_service.brain.uri
}

output "ui_uri" {
  value = google_cloud_run_v2_service.ui.uri
}

output "dns_records" {
  description = "DNS records to create when using a custom domain."
  value = local.has_domain ? {
    ui  = google_cloud_run_domain_mapping.ui[0].status
    api = google_cloud_run_domain_mapping.api[0].status
  } : null
}
