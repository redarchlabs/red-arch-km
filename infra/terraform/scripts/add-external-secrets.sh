#!/usr/bin/env bash
# Populate the external SaaS secrets (kept out of Terraform state). Run once
# after the bootstrap apply, before the full apply.
#
#   OPENAI_API_KEY=sk-... CLERK_SECRET_KEY=sk_live_... ./scripts/add-external-secrets.sh
#
# Any value not provided via env is prompted for (hidden input). Re-running adds
# a new secret version (safe; "latest" is what the services read).
set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PREFIX="$(terraform -chdir="$TF_DIR" output -raw secret_prefix)"

add_secret() {
  local logical="$1" value="$2"
  local secret="$PREFIX-$logical"
  if [ -z "$value" ]; then
    read -rsp "Value for $secret (blank to skip): " value; echo
  fi
  if [ -z "$value" ]; then
    echo "  skipped $secret"
    return
  fi
  printf '%s' "$value" | gcloud secrets versions add "$secret" --data-file=-
  echo "  added version to $secret"
}

add_secret "openai-api-key"   "${OPENAI_API_KEY:-}"
add_secret "clerk-secret-key" "${CLERK_SECRET_KEY:-}"

echo "External secrets updated."
