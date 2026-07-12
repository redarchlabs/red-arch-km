variable "name" { type = string }
variable "region" { type = string }
variable "repo_id" { type = string }
variable "labels" { type = map(string) }
variable "reader_sa_emails" { type = list(string) }

data "google_project" "this" {}

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = var.repo_id
  description   = "KM2 container images"
  format        = "DOCKER"
  labels        = var.labels
}

locals {
  # Runtime SAs (VM pulls via docker login) + the Cloud Run service agent, which
  # is what actually pulls images for Cloud Run services/jobs.
  reader_members = concat(
    [for e in var.reader_sa_emails : "serviceAccount:${e}"],
    ["serviceAccount:service-${data.google_project.this.number}@serverless-robot-prod.iam.gserviceaccount.com"],
  )
}

resource "google_artifact_registry_repository_iam_member" "readers" {
  for_each = toset(local.reader_members)

  location   = google_artifact_registry_repository.docker.location
  repository = google_artifact_registry_repository.docker.name
  role       = "roles/artifactregistry.reader"
  member     = each.value
}

output "repository_id" {
  value = google_artifact_registry_repository.docker.repository_id
}
