#!/usr/bin/env bash
# Build km2-mcp.mcpb — a one-file Claude Desktop extension bundle.
#
# The bundle carries dist/ + production node_modules + manifest.json, so the
# recipient needs only Claude Desktop and Chrome (Desktop supplies the Node
# runtime; the connector drives installed Chrome, so Playwright's browser
# download is skipped). Output lands next to this package as km2-mcp.mcpb.
#
# The checked-in manifest targets a local dev stack. To pack a bundle for a
# real deployment, pass its URLs via environment — they are stamped into the
# staged manifest only, keeping deployment hostnames out of the repo:
#
#   KM2_APP_URL=https://km.example.org \
#   KM2_API_URL=https://km.example.org/api \
#   npm run pack:mcpb
set -euo pipefail
cd "$(dirname "$0")/.."

npm run build

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
cp -r dist package.json package-lock.json README.md manifest.json "$STAGE"/
node - "$STAGE/manifest.json" <<'EOF'
const fs = require("fs");
const path = process.argv[2];
const manifest = JSON.parse(fs.readFileSync(path, "utf8"));
const env = manifest.server.mcp_config.env;
for (const key of ["KM2_APP_URL", "KM2_API_URL", "KM2_CLERK_JWT_TEMPLATE", "KM2_BROWSER_CHANNEL"]) {
  if (process.env[key]) env[key] = process.env[key];
}
fs.writeFileSync(path, JSON.stringify(manifest, null, 2) + "\n");
console.log("bundle targets:", env.KM2_APP_URL);
EOF
(cd "$STAGE" && PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 npm ci --omit=dev --ignore-scripts)

npx --yes @anthropic-ai/mcpb validate "$STAGE/manifest.json"
npx --yes @anthropic-ai/mcpb pack "$STAGE" "$(pwd)/km2-mcp.mcpb"
echo "Bundle: $(pwd)/km2-mcp.mcpb"
