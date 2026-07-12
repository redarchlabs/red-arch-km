# KM2 on Google Cloud — Terraform

Managed-hybrid, small-production deployment of KM2 on GCP.

- **Cloud Run**: `ui` (public), `api` (public), `brain-api` (all-ingress, gated by `BRAIN_API_KEY`)
- **GCE data VM** (`e2-standard-4`, docker compose): `postgres:18` + `qdrant` + `neo4j` + Celery `worker` + `beat`, on persistent SSDs with daily snapshots + nightly `pg_dump`→GCS
- **Memorystore Redis** — Celery broker/backend + rate-limit cache
- **GCS** — document originals (S3-compat via HMAC) + backups
- **Secret Manager**, **Artifact Registry**, **Cloud Build**, VPC + Cloud NAT

Deploys the **Python** service tier. Estimated GCP cost **~$280–320/mo** plus usage-based **OpenAI** and **Clerk**.

> **Why Postgres runs on the VM, not Cloud SQL:** KM2 uses `FORCE ROW LEVEL SECURITY` and a `BYPASSRLS` connection role for its cross-org auth path. Cloud SQL's `cloudsqlsuperuser` cannot hold `BYPASSRLS`, so on Cloud SQL every authenticated request would 403. A self-hosted `postgres:18` superuser has `BYPASSRLS` implicitly — same model as your dev/compose stack, zero code changes.

---

## Prerequisites

- A GCP project with **billing enabled**.
- `gcloud` + `terraform` (>= 1.5) installed and authenticated:
  ```bash
  gcloud auth login
  gcloud auth application-default login
  gcloud config set project <PROJECT_ID>
  ```
- **Clerk production** instance: publishable key (`pk_...`), secret key (`sk_...`), Frontend API URL (issuer), and a JWT template that emits `email`, `email_verified`, `username`.
- An **OpenAI** API key.
- (Recommended) a **custom domain** you control DNS for.

## Configure

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: project_id, domain, clerk_jwt_issuer, clerk_publishable_key
```

(Recommended) put Terraform state in GCS — see `backend.tf`.

## Deploy (ordered — images must exist before Cloud Run/VM pull)

```bash
# 1. Bootstrap: APIs, Artifact Registry, network, secret containers
terraform init
terraform apply \
  -target=module.project_services \
  -target=module.artifact_registry \
  -target=module.network \
  -target=module.iam \
  -target=module.secrets

# 2. Populate external SaaS secrets (kept out of TF state)
OPENAI_API_KEY=sk-...  CLERK_SECRET_KEY=sk_live_...  ./scripts/add-external-secrets.sh

# 3. Build + push the four images
./scripts/build-images.sh          # derives NEXT_PUBLIC_API_URL from your domain

# 4. Full apply (data VM, Redis, storage, Cloud Run, migration job)
terraform apply

# 5. Run DB migrations (once the VM Postgres is up — give it ~2-3 min after apply)
./scripts/db-init.sh

# 6. Point DNS (custom domain) + configure Clerk
terraform output next_steps
terraform output -json   # see cloud_run.dns_records for the domain-mapping targets
```

In the **Clerk dashboard**, set the allowed origin / redirect URLs to your UI URL
(`terraform output -raw ui_url`) and confirm `CLERK_ALLOWED_AZP` matches it byte-for-byte.

### No custom domain?

Leave `domain = ""`. Then the UI's api URL isn't known until the api is deployed, so build in two phases:

```bash
terraform apply                       # deploy everything (UI built with a placeholder api URL)
NEXT_PUBLIC_API_URL=$(terraform output -raw api_url) ./scripts/build-images.sh
terraform apply                       # redeploy UI with the correct baked api URL
```

## Verify

```bash
open "$(terraform output -raw ui_url)"                 # sign-in loads (Clerk)
curl -fsS "$(terraform output -raw api_url)/healthz"   # 200
```

End-to-end: sign in → upload a document → the Celery worker (on the VM) OCRs +
ingests it → Qdrant collection + Neo4j nodes populate → search/chat returns
cited results. This exercises Postgres, Redis, GCS, Qdrant, Neo4j, and OpenAI.

Inspect the VM:
```bash
gcloud compute ssh "$(terraform output -raw data_vm_name)" --zone <ZONE> --tunnel-through-iap
sudo tail -f /var/log/km2-startup.log
sudo docker compose -f /opt/km2/docker-compose.yml ps
```

## Operations

- **Redeploy a new image tag**: bump `image_tag`, `./scripts/build-images.sh`, `terraform apply`. Cloud Run rolls automatically; the **VM re-pulls on reboot** — force it with `gcloud compute instances reset <vm>` (or SSH + `docker compose pull && docker compose up -d`).
- **DB backups**: nightly `pg_dump`→`gs://…-backups/pg/` (08:00 UTC) + daily disk snapshots (14-day retention). Restore: `gcloud storage cp gs://…/pg/<file>.sql.gz - | gunzip | docker compose exec -T postgres psql -U redarch redarch_km`.
- **Scale**: raise `data_vm_machine_type`, `redis_memory_gb`/`redis_tier`, or `cloud_run_min_instances`. Growth path — move `worker`+`beat` off the data VM to a dedicated VM (edit the compose in `modules/data_vm/startup.sh.tpl`).
- **Logs**: Cloud Logging for Cloud Run; `journalctl` / `/var/log/km2-*.log` on the VM.

## Hardening backlog (documented, not enabled)

- `brain-api` is `ingress=all` (gated by `BRAIN_API_KEY`) so the VM worker can reach it. Tighten to internal ingress + an internal HTTP LB (serverless NEG) once you want to remove its public surface.
- Move Terraform state to GCS with restricted IAM (`backend.tf`).
- Enable Cloud Armor / WAF in front of the public services.
- Rotate the `.env.host` OpenAI key that exists in the repo working tree.

## Teardown

```bash
terraform destroy
```

Buckets have `force_destroy = false` — empty and delete them manually if you
intend to remove document originals and backups.
