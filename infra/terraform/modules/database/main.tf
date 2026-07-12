variable "project_id" { type = string }
variable "name" { type = string }
variable "region" { type = string }
variable "network_id" {
  type        = string
  description = "VPC self_link for the private IP."
}
variable "psa_connection" {
  type        = string
  description = "PSA connection id — passed to force ordering after the peering exists."
}
variable "db_name" { type = string }
variable "km_app_user" { type = string }
variable "admin_user" {
  type    = string
  default = "postgres"
}
variable "postgres_version" { type = string }
variable "tier" { type = string }
variable "availability_type" { type = string }
variable "disk_gb" { type = number }
variable "deletion_protection" { type = bool }
variable "labels" { type = map(string) }
variable "api_sa_email" { type = string }
variable "vm_sa_email" { type = string }
variable "bootstrap_sa_email" { type = string }

# URL-safe (no special chars) so the passwords embed cleanly in DATABASE_URL.
resource "random_password" "km_app" {
  length  = 48
  special = false
}
resource "random_password" "admin" {
  length  = 48
  special = false
}

# --- Cloud SQL for PostgreSQL (private IP) ----------------------------------
resource "google_sql_database_instance" "pg" {
  name             = "${var.name}-pg"
  database_version = var.postgres_version
  region           = var.region

  deletion_protection = var.deletion_protection

  settings {
    tier              = var.tier
    availability_type = var.availability_type # ZONAL (small prod) or REGIONAL (HA)
    disk_type         = "PD_SSD"
    disk_size         = var.disk_gb
    disk_autoresize   = true
    user_labels       = var.labels

    ip_configuration {
      ipv4_enabled    = false
      private_network = var.network_id
    }

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      start_time                     = "07:00"
    }

    maintenance_window {
      day  = 7 # Sunday
      hour = 8
    }
  }
}

resource "google_sql_database" "app" {
  name     = var.db_name
  instance = google_sql_database_instance.pg.name
}

# Runtime login role — the app connects as this (non-superuser). Its app_user
# membership + CREATE-on-schema grants are applied by Alembic migration 035
# (run by the bootstrap job as the admin user).
resource "google_sql_user" "km_app" {
  name     = var.km_app_user
  instance = google_sql_database_instance.pg.name
  password = random_password.km_app.result
}

# Admin/migration role — gets cloudsqlsuperuser (CREATEROLE, CREATEDB, LOGIN);
# owns the schema and runs migrations (creates app_user, policies, DDL).
resource "google_sql_user" "admin" {
  name     = var.admin_user
  instance = google_sql_database_instance.pg.name
  password = random_password.admin.result
}

# --- Connection-string secrets (this module owns them; it has both IP + pw) --
locals {
  private_ip   = google_sql_database_instance.pg.private_ip_address
  database_url = "postgresql+asyncpg://${var.km_app_user}:${random_password.km_app.result}@${local.private_ip}:5432/${var.db_name}"
  admin_db_url = "postgresql+asyncpg://${var.admin_user}:${random_password.admin.result}@${local.private_ip}:5432/${var.db_name}"
  url_secrets  = { database_url = local.database_url, admin_database_url = local.admin_db_url }
  # who may read each URL secret
  url_readers = {
    database_url       = [var.api_sa_email, var.vm_sa_email]
    admin_database_url = [var.bootstrap_sa_email]
  }
  url_accessor_pairs = flatten([
    for skey, members in local.url_readers : [
      for m in members : { key = "${skey}::${m}", secret = skey, member = m }
    ]
  ])
}

resource "google_secret_manager_secret" "url" {
  for_each = local.url_secrets

  secret_id = "${var.name}-${replace(each.key, "_", "-")}"
  labels    = var.labels
  replication {
    auto {
    }
  }
}

resource "google_secret_manager_secret_version" "url" {
  for_each = local.url_secrets

  secret      = google_secret_manager_secret.url[each.key].id
  secret_data = each.value
}

resource "google_secret_manager_secret_iam_member" "url_accessor" {
  for_each = { for p in local.url_accessor_pairs : p.key => p }

  secret_id = google_secret_manager_secret.url[each.value.secret].secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value.member}"
}

output "private_ip" {
  value = google_sql_database_instance.pg.private_ip_address
}

output "connection_name" {
  value = google_sql_database_instance.pg.connection_name
}

output "instance_name" {
  value = google_sql_database_instance.pg.name
}

# logical key -> short secret_id (merged with the other modules' secret_ids)
output "secret_ids" {
  value = { for k, s in google_secret_manager_secret.url : k => s.secret_id }
}
