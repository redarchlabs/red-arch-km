# Remote state on GCS. The state contains Terraform-generated secrets
# (Postgres/Neo4j passwords, API_SECRET_KEY, etc.), so the state bucket MUST be
# private with tightly restricted IAM and object versioning enabled.
#
# Chicken-and-egg: Terraform cannot create its own backend bucket. Create it
# once, out of band, then uncomment the block below and run:
#
#   gcloud storage buckets create gs://<PROJECT_ID>-km2-tfstate \
#       --location=<REGION> --uniform-bucket-level-access
#   gcloud storage buckets update gs://<PROJECT_ID>-km2-tfstate --versioning
#   terraform init -backend-config="bucket=<PROJECT_ID>-km2-tfstate"
#
# Until then, state is kept locally (terraform.tfstate) — fine for a first
# apply, but switch to GCS before this is a shared/production deployment.

# terraform {
#   backend "gcs" {
#     prefix = "km2/state"
#     # bucket supplied via: terraform init -backend-config="bucket=..."
#   }
# }
