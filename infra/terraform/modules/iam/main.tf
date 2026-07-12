variable "project_id" { type = string }
variable "name" { type = string }

locals {
  # accountId must be <= 30 chars; keep the suffixes short.
  accounts = {
    api       = "api"
    brain     = "brain"
    ui        = "ui"
    vm        = "vm"
    bootstrap = "boot"
  }
}

resource "google_service_account" "sa" {
  for_each = local.accounts

  account_id   = "${var.name}-${each.value}"
  display_name = "KM2 ${each.key} service account"
}

# Baseline telemetry for every workload identity.
locals {
  baseline_roles = [
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ]

  # cartesian product of (sa, role)
  sa_role_pairs = flatten([
    for sa_key, sa in google_service_account.sa : [
      for role in local.baseline_roles : {
        key   = "${sa_key}-${role}"
        email = sa.email
        role  = role
      }
    ]
  ])
}

resource "google_project_iam_member" "baseline" {
  for_each = { for p in local.sa_role_pairs : p.key => p }

  project = var.project_id
  role    = each.value.role
  member  = "serviceAccount:${each.value.email}"
}

output "api_sa_email" {
  value = google_service_account.sa["api"].email
}
output "brain_sa_email" {
  value = google_service_account.sa["brain"].email
}
output "ui_sa_email" {
  value = google_service_account.sa["ui"].email
}
output "vm_sa_email" {
  value = google_service_account.sa["vm"].email
}
output "bootstrap_sa_email" {
  value = google_service_account.sa["bootstrap"].email
}
