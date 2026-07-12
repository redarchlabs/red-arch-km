variable "name" { type = string }
variable "region" { type = string }
variable "subnet_cidr" { type = string }
variable "psa_cidr" { type = string }
variable "data_vm_internal_ip" { type = string }
variable "labels" { type = map(string) }

# --- VPC + subnet -----------------------------------------------------------
resource "google_compute_network" "vpc" {
  name                    = "${var.name}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "${var.name}-subnet"
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
}

# --- Private Services Access (peering range for Memorystore) -----------------
resource "google_compute_global_address" "psa" {
  name          = "${var.name}-psa-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  address       = split("/", var.psa_cidr)[0]
  prefix_length = tonumber(split("/", var.psa_cidr)[1])
  network       = google_compute_network.vpc.id
}

resource "google_service_networking_connection" "psa" {
  network                 = google_compute_network.vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.psa.name]
}

# --- Reserved internal IP for the data VM -----------------------------------
resource "google_compute_address" "data_vm" {
  name         = "${var.name}-data-vm-ip"
  address_type = "INTERNAL"
  subnetwork   = google_compute_subnetwork.subnet.id
  address      = var.data_vm_internal_ip
  region       = var.region
}

# --- Cloud NAT so the VM (no external IP) can reach the internet + registry --
resource "google_compute_router" "router" {
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

resource "google_compute_router_nat" "nat" {
  name                               = "${var.name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = false
    filter = "ERRORS_ONLY"
  }
}

# --- Firewall ---------------------------------------------------------------
# Internal traffic within the subnet: covers VM-to-VM and Cloud Run Direct VPC
# egress (its source IPs are drawn from this subnet) reaching Postgres 5432,
# Qdrant 6333, Neo4j 7687 on the data VM.
resource "google_compute_firewall" "internal" {
  name    = "${var.name}-allow-internal"
  network = google_compute_network.vpc.id

  direction     = "INGRESS"
  source_ranges = [var.subnet_cidr]

  allow {
    protocol = "tcp"
    ports    = ["0-65535"]
  }
  allow {
    protocol = "udp"
    ports    = ["0-65535"]
  }
  allow {
    protocol = "icmp"
  }
}

# SSH via IAP only (no public SSH). Connect with: gcloud compute ssh <vm> --tunnel-through-iap
resource "google_compute_firewall" "iap_ssh" {
  name    = "${var.name}-allow-iap-ssh"
  network = google_compute_network.vpc.id

  direction     = "INGRESS"
  source_ranges = ["35.235.240.0/20"]

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
}

output "network_id" {
  value = google_compute_network.vpc.id
}

output "network_self_link" {
  value = google_compute_network.vpc.self_link
}

output "subnet_id" {
  value = google_compute_subnetwork.subnet.id
}

output "psa_connection_id" {
  value = google_service_networking_connection.psa.id
}

output "data_vm_ip" {
  value = google_compute_address.data_vm.address
}
