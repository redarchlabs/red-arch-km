variable "region" { type = string }
variable "name" { type = string }
variable "repo_url" { type = string }
variable "image_tag" { type = string }
variable "subnet_id" { type = string }
variable "sa_email" { type = string }
variable "secret_ids" { type = map(string) }

# One-shot job that runs Alembic migrations against Cloud SQL as the ADMIN user
# (cloudsqlsuperuser) — it creates app_user + RLS policies (migration 007/034),
# makes the partition fn SECURITY DEFINER, and grants km_app its memberships
# (migration 035). Not run by Terraform — execute on demand via scripts/db-init.sh:
#   gcloud run jobs execute <name> --region <r> --wait
resource "google_cloud_run_v2_job" "migrate" {
  name                = "${var.name}-db-migrate"
  location            = var.region
  deletion_protection = false

  template {
    template {
      service_account = var.sa_email
      max_retries     = 1
      timeout         = "600s"

      vpc_access {
        network_interfaces {
          subnetwork = var.subnet_id
        }
        egress = "PRIVATE_RANGES_ONLY"
      }

      containers {
        image   = "${var.repo_url}/km2-api:${var.image_tag}"
        command = ["sh", "-c", "cd /app/services/api && alembic upgrade head"]

        env {
          name = "DATABASE_URL"
          value_source {
            secret_key_ref {
              secret  = var.secret_ids["admin_database_url"]
              version = "latest"
            }
          }
        }
      }
    }
  }
}

output "job_name" {
  value = google_cloud_run_v2_job.migrate.name
}
