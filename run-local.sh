#!/usr/bin/env bash
# run-local.sh — start KM2 with inference running entirely on this machine.
#
#   ./run-local.sh              start everything, fully local
#   ./run-local.sh restart      same (host API + UI are always relaunched)
#   ./run-local.sh --rebuild    rebuild brain-api/worker images first
#   ./run-local.sh stop         stop the KM2 stack AND the model servers
#   ./run-local.sh verify       report where each LLM call currently goes
#
# This is ./run-stack.sh plus three things it does not do on its own:
#
#   1. starts the two llama.cpp servers    (./run-local-llm-stack.sh)
#   2. layers docker/docker-compose.local-llm.yml over brain-api
#   3. exports OPENAI_BASE_URL so the host API's workflow actions go local too
#
# Use ./run-stack.sh for the normal hosted-OpenAI stack; this script never edits
# .env, .env.host, or docker-compose.yml, so switching back is just running that.
#
# ── What actually runs locally ──────────────────────────────────────────────────
#   chat  :8099  Qwen3-30B-A3B       RAG answers, workflow summarize/llm_decide/
#                                    llm_respond/llm_grade, chunk summaries,
#                                    fact extraction, agent tool calling
#   embed :8098  nomic-embed-text    retrieval, ingest, entity vectors
#
# ── What still calls out ────────────────────────────────────────────────────────
#   • Clerk — authentication (a deliberate exception; there is no local IdP here)
#   • OpenAI vision OCR — only when a document is uploaded with the `ai` extraction
#     method. The local chat model is text-only; Tesseract (`ocr`) is the local path.
#
# ── Before the first run ────────────────────────────────────────────────────────
# The documents in an org must be ingested with the SAME embedding model they are
# queried with. Vectors are stored at a fixed width (nomic 768 vs OpenAI 1536), so
# an org indexed against OpenAI returns nothing useful here until re-ingested, and
# vice versa. `verify` warns when it finds a mismatch.

set -euo pipefail
cd "$(dirname "$0")"

LLM_STACK=./run-local-llm-stack.sh
OVERRIDE=docker/docker-compose.local-llm.yml
CHAT_PORT="${CHAT_PORT:-8099}"
EMBED_PORT="${EMBED_PORT:-8098}"

say()  { printf '\033[1;35m[local]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[local]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[local] error:\033[0m %s\n' "$*" >&2; exit 1; }

MODE=start
PASS=()
for arg in "$@"; do
  case "$arg" in
    stop|verify) MODE="$arg" ;;
    *) PASS+=("$arg") ;;   # start|restart|--rebuild flow through to run-stack.sh
  esac
done

# The docker bridge gateway is how a container reaches a server on the host. Not
# hardcoded: compose recreates networks with different subnets.
gateway() {
  docker network inspect km2_network -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
}

if [ "$MODE" = "stop" ]; then
  ./run-stack.sh stop
  "$LLM_STACK" stop
  exit 0
fi

if [ "$MODE" = "verify" ]; then
  echo
  say "model servers"
  "$LLM_STACK" status || true
  echo
  say "where each service sends LLM calls"
  for v in OPENAI_BASE_URL EMBEDDING_BASE_URL OPENAI_EMBEDDING_MODEL EMBEDDING_DIMENSION; do
    printf '  brain-api  %-24s %s\n' "$v" \
      "$(docker exec km2_brain_api printenv "$v" 2>/dev/null || echo '(unset -> OpenAI)')"
  done
  api_pid=$(pgrep -f 'uvicorn api\.main:app' | head -1 || true)
  if [ -n "$api_pid" ]; then
    printf '  host api   %-24s %s\n' OPENAI_BASE_URL \
      "$(tr '\0' '\n' < "/proc/$api_pid/environ" | grep '^OPENAI_BASE_URL=' | cut -d= -f2- || echo '(unset -> OpenAI)')"
  else
    printf '  host api   not running\n'
  fi
  echo
  say "recent outbound destinations (brain-api, last 30m)"
  docker logs km2_brain_api --since 30m 2>&1 \
    | grep -oE 'https?://[^ "]*/v1/(chat/completions|embeddings)' \
    | sort | uniq -c | sed 's/^/  /' || echo "  (none yet)"
  # A call to api.openai.com here means something is still escaping — the whole
  # point of this script is that this list contains only local addresses.
  if docker logs km2_brain_api --since 30m 2>&1 | grep -q 'api\.openai\.com'; then
    warn "brain-api has called api.openai.com in the last 30m — not fully local"
  fi
  echo
  say "stored vector widths (must match EMBEDDING_DIMENSION, else retrieval is broken)"
  curl -sf http://localhost:6333/collections 2>/dev/null | python3 -c '
import json, sys, urllib.request
want = None
try:
    import subprocess
    want = subprocess.run(["docker","exec","km2_brain_api","printenv","EMBEDDING_DIMENSION"],
                          capture_output=True, text=True).stdout.strip() or None
except Exception:
    pass
rows = []
for c in json.load(sys.stdin)["result"]["collections"]:
    n = c["name"]
    try:
        d = json.loads(urllib.request.urlopen(f"http://localhost:6333/collections/{n}").read())["result"]
    except Exception:
        continue
    if not d["points_count"]:
        continue
    v = d["config"]["params"].get("vectors") or {}
    size = v.get("size") or next((x.get("size") for x in v.values() if isinstance(x, dict)), "?")
    rows.append((n, size, d["points_count"]))
for n, size, pts in sorted(rows, key=lambda r: -r[2]):
    flag = "  <-- MISMATCH, re-ingest needed" if want and str(size) != want else ""
    print(f"  {n:48} dim={size:<6} points={pts}{flag}")
' 2>/dev/null || echo "  (qdrant unreachable)"
  exit 0
fi

# --- start --------------------------------------------------------------------
[ -x "$LLM_STACK" ] || die "$LLM_STACK missing or not executable"
[ -f "$OVERRIDE" ]  || die "$OVERRIDE missing"

say "starting local model servers…"
"$LLM_STACK" start

for p in "$CHAT_PORT" "$EMBED_PORT"; do
  curl -sf -m 3 "http://127.0.0.1:$p/health" >/dev/null \
    || die "no model server on :$p — check $LLM_STACK status"
done

GW=$(gateway)
if [ -z "$GW" ]; then
  # First-ever start: the network doesn't exist until compose creates it, and the
  # compose default (172.22.0.1) is then almost always right. Nothing to do but warn.
  warn "km2_network not found yet — using the compose default gateway"
else
  say "docker gateway: $GW"
  export LOCAL_LLM_GATEWAY="$GW"
fi

# Read back the true width from the running server rather than trusting a constant:
# a wrong EMBEDDING_DIMENSION builds the vector store at the wrong size, which
# corrupts retrieval silently instead of failing.
DIM=$(curl -sf -m 30 "http://127.0.0.1:$EMBED_PORT/v1/embeddings" \
        -H 'Content-Type: application/json' -d '{"input":"probe","model":"local"}' \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]))' 2>/dev/null || true)
[ -n "$DIM" ] || die "embedding server on :$EMBED_PORT did not return a usable vector"
say "embedding dimension (measured): $DIM"
export LOCAL_EMBED_DIM="$DIM"
export LOCAL_CHAT_PORT="$CHAT_PORT" LOCAL_EMBED_PORT="$EMBED_PORT"

# brain-api gets the override; the host API inherits this exported variable from us
# (uvicorn's --env-file does not overwrite variables already in the environment).
export KM2_COMPOSE_OVERRIDE="$OVERRIDE"
export OPENAI_BASE_URL="http://127.0.0.1:$CHAT_PORT/v1"

say "starting KM2 stack with local inference…"
./run-stack.sh "${PASS[@]}"

echo
say "fully local. Verify any time with:  ./run-local.sh verify"
say "back to hosted OpenAI:              ./run-stack.sh"
