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
#   1. starts the llama.cpp servers         (./run-local-llm-stack.sh)
#   2. layers docker/docker-compose.local-llm.yml over brain-api
#   3. exports OPENAI_BASE_URL (+ OPENAI_MODEL_ROUTES) so the host API's workflow
#      actions go local too
#
# Use ./run-stack.sh for the normal hosted-OpenAI stack; this script never edits
# .env, .env.host, or docker-compose.yml, so switching back is just running that.
#
# ── What actually runs locally ──────────────────────────────────────────────────
#   chat  :8099  Qwen3-30B-A3B       RAG answers, workflow summarize/llm_decide/
#                                    llm_respond/llm_grade, chunk summaries,
#                                    fact extraction, agent tool calling
#   embed :8098  nomic-embed-text    retrieval, ingest, entity vectors
#   fast  :8097  Qwen3-4B-Instruct   short spoken answers (the summarize action), when a
#                                    caller asks for the fast model id. Optional: if this
#                                    server is not up, those calls fall back to :8099.
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
FAST_PORT="${FAST_PORT:-8097}"
RERANK_PORT="${RERANK_PORT:-8096}"
# Routing key for the small model, not a name llama.cpp honours — it serves whatever it
# loaded. Must match run-local-llm-stack.sh's FAST_MODEL_NAME and the answer-model list
# on a chat element.
FAST_MODEL_NAME="${FAST_MODEL_NAME:-qwen3-4b-fast}"
# Same idea for the big local model. OPENAI_BASE_URL already sends *unrouted* calls to the
# chat server, so this name is not needed to reach it — it exists so a workflow node can
# say "local, explicitly" rather than "whatever the deployment default happens to be",
# which is the whole point of comparing an org on local against an org on hosted OpenAI.
CHAT_MODEL_NAME="${CHAT_MODEL_NAME:-qwen3-30b}"
# Model ids that must reach HOSTED OpenAI even though OPENAI_BASE_URL points at llama.cpp.
#
# Routes are model→URL and win over OPENAI_BASE_URL, so naming api.openai.com here is what
# lets ONE deployment serve both: an org whose LLM nodes ask for `gpt-4.1-mini` goes out to
# OpenAI, an org whose nodes ask for `qwen3-30b` (or name nothing at all) stays on this box.
# Inverting it instead — clearing OPENAI_BASE_URL so hosted is the default — would silently
# move every existing org onto OpenAI, so the local default is deliberately left alone.
HOSTED_OPENAI_MODELS="${HOSTED_OPENAI_MODELS:-gpt-4.1-mini gpt-5-mini gpt-5-nano}"
HOSTED_OPENAI_URL="${HOSTED_OPENAI_URL:-https://api.openai.com/v1}"

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

# Per-model endpoints. Built as a list so local and hosted routes compose: every entry is
# "<model id>=<base url>", and api/services/openai_client.py resolves a call's endpoint from
# the model id the caller asked for, falling back to OPENAI_BASE_URL when the id is unrouted.
ROUTES=("$CHAT_MODEL_NAME=http://127.0.0.1:$CHAT_PORT/v1")

# The fast server is OPTIONAL — it only serves calls that explicitly ask for its model
# id, and an unrouted id falls back to the chat server. So a missing 4B model file
# degrades speed, never correctness, and must not stop the stack from starting.
if curl -sf -m 3 "http://127.0.0.1:$FAST_PORT/health" >/dev/null; then
  ROUTES+=("$FAST_MODEL_NAME=http://127.0.0.1:$FAST_PORT/v1")
  say "fast model server on :$FAST_PORT — routing '$FAST_MODEL_NAME' there"
else
  warn "no fast model server on :$FAST_PORT — '$FAST_MODEL_NAME' will fall back to :$CHAT_PORT"
fi

# Hosted OpenAI, reachable by model id. Skipped entirely without a key: routing an id at
# api.openai.com with nothing to authenticate with turns a clear KM2-side "no key" error
# into a 401 from OpenAI, which is a worse thing to debug mid-demo.
#
# The key is read out of .env.host rather than the environment because uvicorn — not this
# script — is what loads that file, so OPENAI_API_KEY is not set here. An org supplying its
# OWN key in the UI still works without a central one; this check only decides whether to
# advertise the hosted route at all.
HAVE_OPENAI_KEY=$(grep -sE '^OPENAI_API_KEY=.+' .env.host >/dev/null && echo 1 || echo "")
if [[ -n "$HAVE_OPENAI_KEY" && -n "$HOSTED_OPENAI_MODELS" ]]; then
  for m in $HOSTED_OPENAI_MODELS; do ROUTES+=("$m=$HOSTED_OPENAI_URL"); done
  say "hosted OpenAI routed for: $HOSTED_OPENAI_MODELS"
else
  warn "no OPENAI_API_KEY — hosted OpenAI models unrouted, every org stays local"
fi

export OPENAI_MODEL_ROUTES="${ROUTES[*]}"

GW=$(gateway)
GW_ADDR="${GW:-172.22.0.1}"

# The same routes from the CONTAINER's perspective: brain-api resolves the per-org
# model pin (orgs.default_llm_model) itself, and 127.0.0.1 inside a container is the
# container — local entries swap in the docker-network gateway, hosted entries pass
# through unchanged. Consumed by docker-compose.local-llm.yml.
export BRAIN_OPENAI_MODEL_ROUTES="${OPENAI_MODEL_ROUTES//http:\/\/127.0.0.1:/http:\/\/$GW_ADDR:}"

# The reranker is OPTIONAL in the same way the fast server is: brain-api treats an empty
# RERANK_BASE_URL as "keep the dense order", which is how retrieval behaved before it
# existed. Pointing it at a dead port instead would make every single search log a failed
# rerank and then fall back anyway — same answers, noisier.
if curl -sf -m 3 "http://127.0.0.1:$RERANK_PORT/health" >/dev/null; then
  export LOCAL_RERANK_URL="http://$GW_ADDR:$RERANK_PORT/v1"
  say "rerank server on :$RERANK_PORT — cross-encoder reranking enabled"
else
  export LOCAL_RERANK_URL=""
  warn "no rerank server on :$RERANK_PORT — retrieval falls back to dense ranking"
fi

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
