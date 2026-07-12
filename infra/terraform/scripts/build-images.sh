#!/usr/bin/env bash
# Build + push the four KM2 images to Artifact Registry via Cloud Build.
# Run AFTER the bootstrap apply (Artifact Registry must exist).
#
#   ./scripts/build-images.sh
#
# The UI bakes NEXT_PUBLIC_API_URL at build time. With a custom domain it is
# derived automatically (https://api.<domain>). Without a domain, deploy the api
# first, then re-run with NEXT_PUBLIC_API_URL exported to the api's run.app URL.
set -euo pipefail

TF_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$TF_DIR/../.." && pwd)"

tf() { terraform -chdir="$TF_DIR" output -raw "$1"; }

REPO_URL="$(tf artifact_repo_url)"     # <region>-docker.pkg.dev/<project>/<repo>
REGION="$(tf region)"
TAG="$(tf image_tag)"
DOMAIN="$(tf domain 2>/dev/null || true)"
CLERK_PK="$(tf clerk_publishable_key 2>/dev/null || true)"
CLERK_TPL="$(tf clerk_jwt_template 2>/dev/null || echo redarch-km)"
REPO="$(basename "$REPO_URL")"

NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-}"
if [ -z "$NEXT_PUBLIC_API_URL" ]; then
  if [ -n "$DOMAIN" ]; then
    NEXT_PUBLIC_API_URL="https://api.$DOMAIN"
  else
    echo "ERROR: no custom domain set and NEXT_PUBLIC_API_URL not exported." >&2
    echo "Deploy the api first, then re-run:" >&2
    echo "  NEXT_PUBLIC_API_URL=\$(terraform output -raw api_url) ./scripts/build-images.sh" >&2
    exit 1
  fi
fi

echo "Building images -> $REPO_URL (tag $TAG)"
echo "  NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL"

gcloud builds submit "$ROOT" \
  --config="$TF_DIR/cloudbuild.yaml" \
  --substitutions="_REGION=$REGION,_REPO=$REPO,_TAG=$TAG,_NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL,_NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=$CLERK_PK,_NEXT_PUBLIC_CLERK_JWT_TEMPLATE=$CLERK_TPL"

echo "Done."
