#!/usr/bin/env bash
set -euo pipefail

REPO="redarchlabs/red-arch-km-2"
ROOT="/home/jblair/github/redarchlabs/red-arch-km-2"
UI_ENV="$ROOT/ui/.env.local"
API_ENV="$ROOT/.env"

# Extract one KEY=value from an env file (value = everything after the first '=').
getval() { grep -m1 "^$1=" "$2" | cut -d= -f2-; }

PK="$(getval NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY "$UI_ENV")"
SK="$(getval CLERK_SECRET_KEY                  "$UI_ENV")"
TS="$(getval API_E2E_TEST_SECRET               "$API_ENV")"

# Refuse to set an empty secret (without ever printing the values).
for pair in "E2E_CLERK_PUBLISHABLE_KEY=$PK" "E2E_CLERK_SECRET_KEY=$SK" "E2E_TEST_SECRET=$TS"; do
  name="${pair%%=*}"; val="${pair#*=}"
  [ -n "$val" ] || { echo "ERROR: empty value for $name — check the source env file" >&2; exit 1; }
done

# Pipe via stdin so values never land in argv or shell history.
printf '%s' "$PK" | gh secret set E2E_CLERK_PUBLISHABLE_KEY --repo "$REPO"
printf '%s' "$SK" | gh secret set E2E_CLERK_SECRET_KEY      --repo "$REPO"
printf '%s' "$TS" | gh secret set E2E_TEST_SECRET           --repo "$REPO"

echo "Done. Verifying (names only, values are masked):"
gh secret list --repo "$REPO"
