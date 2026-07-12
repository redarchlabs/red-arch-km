variable "project_id" { type = string }
variable "name" { type = string }
variable "region" { type = string }
variable "api_sa_email" { type = string }
variable "vm_sa_email" { type = string }
variable "labels" { type = map(string) }

# --- Buckets ----------------------------------------------------------------
# Uploaded document originals — the ONLY copy, so versioning is on.
resource "google_storage_bucket" "documents" {
  name                        = "${var.project_id}-${var.name}-documents"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = false
  labels                      = var.labels

  versioning { enabled = true }
}

# --- HMAC identity (S3-compatible access for the app's boto3 client) --------
resource "google_service_account" "gcs" {
  account_id   = "${var.name}-gcs"
  display_name = "KM2 GCS S3-interop (HMAC) identity"
}

# The HMAC key maps to this SA, which needs object access on the buckets.
resource "google_storage_bucket_iam_member" "gcs_documents" {
  bucket = google_storage_bucket.documents.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.gcs.email}"
}

resource "google_storage_hmac_key" "app" {
  service_account_email = google_service_account.gcs.email
}

# --- STORAGE_ACCESS_KEY / STORAGE_SECRET_KEY secrets ------------------------
resource "google_secret_manager_secret" "storage_access_key" {
  secret_id = "${var.name}-storage-access-key"
  labels    = var.labels
  replication {
    auto {
    }
  }
}

resource "google_secret_manager_secret_version" "storage_access_key" {
  secret      = google_secret_manager_secret.storage_access_key.id
  secret_data = google_storage_hmac_key.app.access_id
}

resource "google_secret_manager_secret" "storage_secret_key" {
  secret_id = "${var.name}-storage-secret-key"
  labels    = var.labels
  replication {
    auto {
    }
  }
}

resource "google_secret_manager_secret_version" "storage_secret_key" {
  secret      = google_secret_manager_secret.storage_secret_key.id
  secret_data = google_storage_hmac_key.app.secret
}

locals {
  storage_secret_readers = [var.api_sa_email, var.vm_sa_email]
}

resource "google_secret_manager_secret_iam_member" "access_key_readers" {
  for_each  = toset(local.storage_secret_readers)
  secret_id = google_secret_manager_secret.storage_access_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

resource "google_secret_manager_secret_iam_member" "secret_key_readers" {
  for_each  = toset(local.storage_secret_readers)
  secret_id = google_secret_manager_secret.storage_secret_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${each.value}"
}

output "documents_bucket" {
  value = google_storage_bucket.documents.name
}

output "secret_ids" {
  value = {
    storage_access_key = google_secret_manager_secret.storage_access_key.secret_id
    storage_secret_key = google_secret_manager_secret.storage_secret_key.secret_id
  }
}
