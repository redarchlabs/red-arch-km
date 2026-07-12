variable "name" { type = string }
variable "region" { type = string }
variable "tier" { type = string }
variable "memory_gb" { type = number }
variable "network_id" { type = string }
variable "psa_connection" {
  type        = string
  description = "PSA connection id — passed to force ordering after the peering exists."
}
variable "labels" { type = map(string) }

# Non-cluster Memorystore instance so the app's logical DBs (redis://.../0 broker,
# /1 result backend) work. Reached over Private Service Access from the VPC.
resource "google_redis_instance" "this" {
  name           = "${var.name}-redis"
  tier           = var.tier
  memory_size_gb = var.memory_gb
  region         = var.region

  authorized_network      = var.network_id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  redis_version           = "REDIS_7_2"
  transit_encryption_mode = "DISABLED"

  labels = var.labels

  # Ensure the PSA peering exists before the instance tries to allocate from it.
  depends_on = [var.psa_connection]
}

output "host" {
  value = google_redis_instance.this.host
}

output "port" {
  value = google_redis_instance.this.port
}
