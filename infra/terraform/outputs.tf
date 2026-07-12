output "ui_url" {
  description = "Public UI URL."
  value       = local.ui_public_url
}

output "api_url" {
  description = "Public API URL."
  value       = local.api_public_url
}

output "brain_api_url" {
  description = "brain-api run.app URL (backend; key-gated)."
  value       = module.cloud_run.brain_uri
}

output "data_vm_name" {
  description = "Name of the data VM (Postgres + Qdrant + Neo4j + worker + beat)."
  value       = module.data_vm.instance_name
}

output "data_vm_internal_ip" {
  description = "Internal IP of the data VM."
  value       = var.data_vm_internal_ip
}

output "redis_host" {
  description = "Memorystore Redis host."
  value       = module.redis.host
}

output "documents_bucket" {
  description = "GCS bucket holding uploaded document originals."
  value       = module.storage.documents_bucket
}

output "backups_bucket" {
  description = "GCS bucket holding nightly pg_dump backups."
  value       = module.storage.backups_bucket
}

output "artifact_repo_url" {
  description = "Artifact Registry Docker repo URL (push target for build-images.sh)."
  value       = local.repo_url
}

output "db_bootstrap_job" {
  description = "Cloud Run job name that runs alembic migrations."
  value       = module.db_bootstrap.job_name
}

output "project_id" {
  description = "GCP project ID."
  value       = var.project_id
}

output "region" {
  description = "Primary region."
  value       = var.region
}

output "image_tag" {
  description = "Image tag deployed."
  value       = var.image_tag
}

output "secret_prefix" {
  description = "Prefix for Secret Manager secret IDs (e.g. km2-prod)."
  value       = local.name
}

output "domain" {
  description = "Custom domain (empty if using run.app URLs)."
  value       = var.domain
}

output "clerk_publishable_key" {
  description = "Clerk publishable key (browser-exposed; baked into the UI image)."
  value       = var.clerk_publishable_key
}

output "clerk_jwt_template" {
  description = "Clerk JWT template name (baked into the UI image)."
  value       = var.clerk_jwt_template
}

output "next_steps" {
  description = "What to do after apply."
  value       = <<-EOT
    1. Point DNS: ${var.domain != "" ? "A/AAAA records for ${var.domain} and api.${var.domain} to the Cloud Run domain mapping targets (see module.cloud_run.dns_records)." : "no custom domain set — using run.app URLs."}
    2. Run migrations:   ./scripts/db-init.sh
    3. In the Clerk dashboard set the allowed origin / redirect to: ${local.ui_public_url}
    4. Smoke test:       open ${local.ui_public_url} and sign in.
  EOT
}
