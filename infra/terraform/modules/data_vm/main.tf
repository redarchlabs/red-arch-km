variable "project_id" { type = string }
variable "region" { type = string }
variable "zone" { type = string }
variable "name" { type = string }
variable "machine_type" { type = string }
variable "image" { type = string }
variable "subnet_id" { type = string }
variable "internal_ip" { type = string }
variable "qdrant_disk_gb" { type = number }
variable "neo4j_disk_gb" { type = number }
variable "repo_url" { type = string }
variable "image_tag" { type = string }
variable "redis_host" { type = string }
variable "redis_port" { type = number }
variable "brain_api_url" { type = string }
variable "api_url" { type = string }
variable "vm_sa_email" { type = string }
variable "neo4j_user" { type = string }
variable "worker_concurrency" { type = number }
variable "openai_chat_model" { type = string }
variable "openai_embedding_model" { type = string }
variable "documents_bucket" { type = string }
variable "storage_region" { type = string }
variable "secret_ids" { type = map(string) }
variable "labels" { type = map(string) }

# --- Persistent data disks --------------------------------------------------
resource "google_compute_disk" "qdrant" {
  name   = "${var.name}-qdrant-data"
  type   = "pd-ssd"
  zone   = var.zone
  size   = var.qdrant_disk_gb
  labels = var.labels
}

resource "google_compute_disk" "neo4j" {
  name   = "${var.name}-neo4j-data"
  type   = "pd-ssd"
  zone   = var.zone
  size   = var.neo4j_disk_gb
  labels = var.labels
}

# --- Daily snapshot policy for the data disks -------------------------------
resource "google_compute_resource_policy" "snapshots" {
  name   = "${var.name}-daily-snapshots"
  region = var.region

  snapshot_schedule_policy {
    schedule {
      daily_schedule {
        days_in_cycle = 1
        start_time    = "07:00" # UTC
      }
    }
    retention_policy {
      max_retention_days    = 14
      on_source_disk_delete = "KEEP_AUTO_SNAPSHOTS"
    }
    snapshot_properties {
      labels = var.labels
    }
  }
}

resource "google_compute_disk_resource_policy_attachment" "qdrant" {
  name = google_compute_resource_policy.snapshots.name
  disk = google_compute_disk.qdrant.name
  zone = var.zone
}

resource "google_compute_disk_resource_policy_attachment" "neo4j" {
  name = google_compute_resource_policy.snapshots.name
  disk = google_compute_disk.neo4j.name
  zone = var.zone
}

# --- The VM -----------------------------------------------------------------
resource "google_compute_instance" "data" {
  name         = "${var.name}-data-vm"
  machine_type = var.machine_type
  zone         = var.zone
  labels       = var.labels
  tags         = ["km2-data"]

  # Data disks are attached and (re)mounted by the startup script; changing the
  # image should not force a data-disk recreate.
  allow_stopping_for_update = true

  boot_disk {
    initialize_params {
      image = var.image
      size  = 30
      type  = "pd-balanced"
    }
  }

  attached_disk {
    source      = google_compute_disk.qdrant.id
    device_name = "qdrantdata"
  }
  attached_disk {
    source      = google_compute_disk.neo4j.id
    device_name = "neo4jdata"
  }

  network_interface {
    subnetwork = var.subnet_id
    network_ip = var.internal_ip
    # No access_config => no external IP. Egress via Cloud NAT.
  }

  service_account {
    email  = var.vm_sa_email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata = {
    enable-oslogin = "TRUE"
  }

  metadata_startup_script = templatefile("${path.module}/startup.sh.tpl", {
    region                 = var.region
    repo_url               = var.repo_url
    image_tag              = var.image_tag
    neo4j_user             = var.neo4j_user
    worker_concurrency     = var.worker_concurrency
    redis_host             = var.redis_host
    redis_port             = var.redis_port
    brain_api_url          = var.brain_api_url
    api_url                = var.api_url
    documents_bucket       = var.documents_bucket
    storage_region         = var.storage_region
    openai_chat_model      = var.openai_chat_model
    openai_embedding_model = var.openai_embedding_model
    sec_database_url       = var.secret_ids["database_url"]
    sec_neo4j_password     = var.secret_ids["neo4j_password"]
    sec_openai_api_key     = var.secret_ids["openai_api_key"]
    sec_brain_api_key      = var.secret_ids["brain_api_key"]
    sec_internal_api_key   = var.secret_ids["internal_api_key"]
    sec_org_encryption_key = var.secret_ids["org_encryption_key"]
    sec_storage_access_key = var.secret_ids["storage_access_key"]
    sec_storage_secret_key = var.secret_ids["storage_secret_key"]
  })
}

output "instance_name" {
  value = google_compute_instance.data.name
}

output "internal_ip" {
  value = var.internal_ip
}
