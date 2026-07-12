#!/usr/bin/env bash
# Run Alembic migrations against the VM Postgres via the db-migrate Cloud Run
# job. Run after the full apply, once the data VM is up. Idempotent.
#
#   ./scripts/db-init.sh
set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
JOB="$(terraform -chdir="$TF_DIR" output -raw db_bootstrap_job)"
REGION="$(terraform -chdir="$TF_DIR" output -raw region)"

echo "Executing migration job $JOB in $REGION ..."
gcloud run jobs execute "$JOB" --region "$REGION" --wait
echo "Migrations complete."
