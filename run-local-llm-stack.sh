#!/usr/bin/env bash
#
# run-local-llm-stack.sh — run KM2's inference stack locally, with no third-party calls.
#
# Serves two llama.cpp servers behind OpenAI-compatible APIs:
#
#   chat   :8099   Qwen3-30B-A3B      chat completions + tool calling
#   embed  :8098   nomic-embed-text   embeddings (retrieval, ingest, fact store)
#
# TWO servers because one llama.cpp process cannot do both: a server started for chat
# answers /v1/embeddings with "501 This server does not support embeddings", and
# --embeddings mode does not serve chat. KM2 therefore has two settings, OPENAI_BASE_URL
# and EMBEDDING_BASE_URL, and every OpenAI client in the codebase passes base_url
# explicitly so the SDK's own OPENAI_BASE_URL env-var fallback can never silently
# redirect a call to the wrong server.
#
#   ./run-local-llm-stack.sh start           # both servers
#   ./run-local-llm-stack.sh start chat      # just one
#   ./run-local-llm-stack.sh status          # health, model, VRAM, requests served
#   ./run-local-llm-stack.sh test            # real round-trip against both
#   ./run-local-llm-stack.sh env             # the settings to hand KM2
#   ./run-local-llm-stack.sh logs chat       # follow a log
#   ./run-local-llm-stack.sh models          # list .gguf files
#   ./run-local-llm-stack.sh setup           # print one-time build/download commands
#   ./run-local-llm-stack.sh stop | restart [chat|embed]
#
# This script is versioned here, but what it drives is NOT: the llama.cpp build and the
# .gguf weights are tens of gigabytes and live outside the repo in $LLM_LAB
# (default ~/llm-lab):
#
#   $LLM_LAB/llama.cpp/build/bin/llama-server     built with -DGGML_CUDA=ON
#   $LLM_LAB/models/*.gguf                        downloaded weights
#
# ⚠ CHANGING THE EMBEDDING MODEL IS A MIGRATION, NOT A CONFIG FLIP. Qdrant collections
# and the Neo4j vector index are created at a fixed width (nomic = 768, OpenAI
# text-embedding-3-small = 1536). Switching means dropping both and re-ingesting every
# document. EMBEDDING_DIMENSION must match the model or retrieval silently degrades.
#
# Why these flags (measured 25 Jul 2026, see $LLM_LAB/FINDINGS.md):
#   --host 0.0.0.0   docker containers reach the host over the bridge gateway, not 127.0.0.1
#   -ngl 99          all layers offloaded to the GPU
#   --cpu-moe        MoE expert weights stay in system RAM — this is what makes a 30B-class
#                    model fit a 6 GB card (~2.8 GB VRAM, leaving room for the embedder)
#   -c 16384         KM2 sends 50 tool schemas ≈ 8.7k tokens; at -c 8192 every agent request 400s
#   --jinja          REQUIRED for tool calling — without it the model never emits tool_calls
#   enable_thinking  off: Qwen3 otherwise emits reasoning blocks that KM2 renders as speech

set -euo pipefail

LAB="${LLM_LAB:-${LAB:-$HOME/llm-lab}}"
BIN="$LAB/llama.cpp/build/bin/llama-server"
MODEL_DIR="$LAB/models"
DOCKER_NET="${DOCKER_NET:-km2_network}"
START_TIMEOUT="${START_TIMEOUT:-300}"

CHAT_PORT="${CHAT_PORT:-8099}"
EMBED_PORT="${EMBED_PORT:-8098}"
CHAT_MODEL="${CHAT_MODEL:-Qwen3-30B-A3B-Q4_K_M.gguf}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text-v1.5.Q8_0.gguf}"
# Reported to KM2 as OPENAI_EMBEDDING_MODEL. llama.cpp ignores the requested model name
# (it serves whatever was loaded), but KM2 records it, so keep it human-readable rather
# than deriving it from the quantised filename.
EMBED_MODEL_NAME="${EMBED_MODEL_NAME:-nomic-embed-text-v1.5}"
# Must match the model. nomic-embed-text-v1.5 = 768. Verify with `test`, which reads the
# width off a real response rather than trusting this number.
EMBED_DIM="${EMBED_DIM:-768}"

ALL_SERVERS=(chat embed)

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }
die()  { red "error: $*" >&2; exit 1; }

port_of()    { case "$1" in chat) echo "$CHAT_PORT";; embed) echo "$EMBED_PORT";; esac; }
model_of()   { case "$1" in chat) echo "$CHAT_MODEL";; embed) echo "$EMBED_MODEL";; esac; }
logfile_of() { echo "$LAB/llama-$1.log"; }
pidfile_of() { echo "$LAB/llama-$1.pid"; }

# Extra llama-server flags per role. Chat needs a big context and tool-calling support;
# the embedder needs --embeddings and nothing else.
flags_of() {
  case "$1" in
    # -ncmoe 44 keeps the first 44 layers' experts on CPU and puts the last 4 on the
    # GPU — measured ~12% faster than --cpu-moe (all 48 on CPU) on a RAG-shaped prompt,
    # at 4.5 GB VRAM instead of 2.8 GB. Do not raise it further on a 6 GB card: -ncmoe 40
    # fails at startup with "failed to allocate compute pp buffers".
    # -np 2 rather than the default 4: fewer slots means a larger KV budget each and a
    # better chance the previous turn's prefix is still cached.
    # --cache-reuse makes that cached prefix actually pay off. A chat turn's prompt is
    # append-only — turn N+1 is turn N's history plus the new exchange — so without it
    # every turn re-evaluates the WHOLE conversation. Prompt eval runs at a few hundred
    # tok/s here, which is what put ~16s in front of the first generated token on a long
    # chat; with reuse only the new tail is evaluated.
    chat)  printf '%s\n' -c 16384 -np 2 -ngl 99 -ncmoe "${CHAT_NCMOE:-44}" --jinja \
             --cache-reuse "${CHAT_CACHE_REUSE:-256}" \
             --chat-template-kwargs '{"enable_thinking":false}' ;;
    embed) printf '%s\n' --embeddings -ngl 99 ;;
  esac
}

resolve_targets() {
  local t="${1:-all}"
  case "$t" in
    all|"") printf '%s\n' "${ALL_SERVERS[@]}" ;;
    chat|embed) echo "$t" ;;
    *) die "unknown server '$t' (expected: chat, embed, all)" ;;
  esac
}

# PID file is the source of truth. `pgrep -f` matches this script's own command line and
# would kill the wrong process.
server_pid() {
  local pf; pf=$(pidfile_of "$1")
  [[ -f "$pf" ]] || return 1
  local pid; pid=$(<"$pf")
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null && { echo "$pid"; return 0; }
  return 1
}

healthy() { curl -sf -m 3 "http://127.0.0.1:$(port_of "$1")/health" >/dev/null 2>&1; }

# `|| true` matters: under `set -o pipefail` an empty grep exits 1, which under `set -e`
# aborts the caller's assignment — i.e. "port is free" would silently kill the script.
port_holder() {
  ss -lptnH "sport = :$1" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1 || true
}

docker_gateway() {
  docker network inspect "$DOCKER_NET" -f '{{(index .IPAM.Config 0).Gateway}}' 2>/dev/null || true
}

resolve_model() {
  local m="$1"
  [[ -f "$m" ]] && { echo "$m"; return; }
  [[ -f "$MODEL_DIR/$m" ]] && { echo "$MODEL_DIR/$m"; return; }
  die "model not found: $m (looked in \$PWD and $MODEL_DIR — try: $0 models)"
}

start_one() {
  local name="$1" port model log pf holder
  port=$(port_of "$name"); log=$(logfile_of "$name"); pf=$(pidfile_of "$name")

  if pid=$(server_pid "$name"); then grn "$name: already running (pid $pid, port $port)"; return 0; fi
  [[ -x "$BIN" ]] || die "llama-server not built at $BIN — run: $0 setup"

  holder=$(port_holder "$port")
  [[ -n "$holder" ]] && die "$name: port $port already held by pid $holder (untracked server?).
  Inspect:  ps -fp $holder
  Reclaim:  kill $holder   then re-run $0 start $name"

  model=$(resolve_model "$(model_of "$name")")
  dim "$name: $(basename "$model") -> :$port"

  # The child records its OWN pid. `setsid ... & echo $!` records setsid's pid instead,
  # and setsid forks — so the recorded pid dies immediately and `stop` later kills a
  # corpse while the real server keeps holding the port. `exec` keeps the pid we wrote.
  local flags=(); mapfile -t flags < <(flags_of "$name")
  rm -f "$pf"
  ( cd "$LAB" && setsid bash -c '
        echo $$ > "$1"; shift
        exec "$@"
      ' _ "$pf" "$BIN" -m "$model" --host 0.0.0.0 --port "$port" --no-webui "${flags[@]}" \
      > "$log" 2>&1 < /dev/null & )

  for ((i = 0; i < 50; i++)); do [[ -s "$pf" ]] && break; sleep 0.1; done
  [[ -s "$pf" ]] || die "$name: failed to launch (see $log)"

  printf '  loading'
  for ((i = 0; i < START_TIMEOUT; i++)); do
    if healthy "$name"; then echo; grn "  $name ready on http://0.0.0.0:$port/v1"; return 0; fi
    server_pid "$name" >/dev/null || { echo; red "  $name died during startup:"; tail -20 "$log"; exit 1; }
    printf '.'; sleep 1
  done
  echo; die "$name: not healthy within ${START_TIMEOUT}s (see $log)"
}

stop_one() {
  local name="$1" pid holder
  if pid=$(server_pid "$name"); then
    kill "$pid" 2>/dev/null || true
    for ((i = 0; i < 15; i++)); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    grn "$name: stopped (pid $pid)"
  else
    dim "$name: not running"
  fi
  rm -f "$(pidfile_of "$name")"
  holder=$(port_holder "$(port_of "$name")")
  [[ -n "$holder" ]] && red "$name: warning — port still held by pid $holder"
  return 0
}

status_one() {
  local name="$1" port log pid loaded
  port=$(port_of "$name"); log=$(logfile_of "$name")
  if pid=$(server_pid "$name"); then
    grn "$name  running  pid $pid  port $port"
  else
    red "$name  NOT RUNNING  (port $port)"; return 1
  fi
  loaded=$(curl -sf -m 3 "http://127.0.0.1:$port/v1/models" \
           | grep -o '"id":"[^"]*"' | head -1 | cut -d'"' -f4 || true)
  printf '     model     %s\n' "$(basename "${loaded:-unknown}")"
  # Count `launch_slot_` (one line per accepted request). llama-server does NOT log an
  # access line like "POST /v1/chat/completions" — grepping for that silently reports 0
  # even while it is actively serving, a very convincing way to fool yourself.
  [[ -f "$log" ]] && printf '     requests  %s served since start\n' \
    "$(grep -c 'launch_slot_' "$log" || true)"
  return 0
}

cmd_status() {
  local rc=0
  for s in $(resolve_targets "${1:-all}"); do status_one "$s" || rc=1; done
  if command -v nvidia-smi >/dev/null; then
    printf '\n  vram  %s MiB used of %s MiB\n' \
      "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits)" \
      "$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits)"
  fi
  return $rc
}

cmd_env() {
  local gw; gw=$(docker_gateway)
  echo
  dim "KM2 settings for a fully-local stack (no repo file needs editing):"
  echo
  echo "  host processes (uvicorn):"
  echo "    OPENAI_BASE_URL=http://127.0.0.1:$CHAT_PORT/v1"
  echo
  if [[ -n "$gw" ]]; then
    echo "  docker containers (brain-api):"
    echo "    OPENAI_BASE_URL=http://$gw:$CHAT_PORT/v1"
    echo "    EMBEDDING_BASE_URL=http://$gw:$EMBED_PORT/v1"
    echo "    OPENAI_EMBEDDING_MODEL=$EMBED_MODEL_NAME"
    echo "    EMBEDDING_DIMENSION=$EMBED_DIM"
  else
    dim "  (docker network '$DOCKER_NET' not found — start the KM2 stack for container URLs)"
  fi
  echo
  dim "  EMBEDDING_DIMENSION must match the model, and changing it requires dropping the"
  dim "  Qdrant collections + Neo4j vector index and re-ingesting every document."
}

cmd_test() {
  local ok=0
  healthy chat && {
    dim "chat: one completion..."
    local reply
    reply=$(curl -sf -m 120 "http://127.0.0.1:$CHAT_PORT/v1/chat/completions" \
      -H 'Content-Type: application/json' \
      -d '{"model":"local","max_tokens":60,"temperature":0,"messages":[
           {"role":"system","content":"You are a robot guide at a space center. One short sentence."},
           {"role":"user","content":"What is the USS Meridian?"}]}' \
      | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip())') \
      || { red "  chat request FAILED"; ok=1; }
    [[ -n "${reply:-}" ]] && printf '  reply: %s\n' "$reply"
  } || { red "chat: not responding on :$CHAT_PORT"; ok=1; }

  healthy embed && {
    dim "embed: one embedding..."
    # Read the width off a real response — never trust the configured number.
    local got
    got=$(curl -sf -m 60 "http://127.0.0.1:$EMBED_PORT/v1/embeddings" \
      -H 'Content-Type: application/json' -d '{"input":"dimension probe","model":"local"}' \
      | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["data"][0]["embedding"]))') \
      || { red "  embedding request FAILED"; ok=1; }
    if [[ -n "${got:-}" ]]; then
      printf '  dimension: %s\n' "$got"
      [[ "$got" == "$EMBED_DIM" ]] || { red "  MISMATCH: server returns $got, EMBED_DIM=$EMBED_DIM"; ok=1; }
    fi
  } || { red "embed: not responding on :$EMBED_PORT"; ok=1; }

  [[ $ok -eq 0 ]] && grn "both servers OK"
  return $ok
}

cmd_models() {
  shopt -s nullglob
  local found=("$MODEL_DIR"/*.gguf)
  [[ ${#found[@]} -eq 0 ]] && { dim "no .gguf files in $MODEL_DIR"; return; }
  for f in "${found[@]}"; do printf '  %-42s %s\n' "$(basename "$f")" "$(du -h "$f" | cut -f1)"; done
}

# Printed, not executed: this builds a CUDA toolchain and downloads ~18 GB. Read it first,
# and check nvidia-smi works before starting — a broken driver here is a long detour.
cmd_setup() {
  cat <<SETUP
One-time setup of \$LLM_LAB ($LAB). Requires cmake, a CUDA toolkit, and ~25 GB free.

  mkdir -p "$LAB" && cd "$LAB"

  # 1. llama.cpp with CUDA
  git clone https://github.com/ggml-org/llama.cpp
  cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON -DCMAKE_BUILD_TYPE=Release
  cmake --build llama.cpp/build --config Release -j\$(nproc) --target llama-server

  mkdir -p models && cd models

  # 2. chat — Qwen3-30B-A3B. MoE: 30B total but only 3B active per token, which is what
  #    lets a 30B-class model run on a 6 GB card. ~18 GB.
  curl -L -O https://huggingface.co/Qwen/Qwen3-30B-A3B-GGUF/resolve/main/$CHAT_MODEL

  # 3. embeddings — nomic-embed-text-v1.5, 768-dim. ~140 MB.
  curl -L -O https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF/resolve/main/$EMBED_MODEL

  # 4. verify
  nvidia-smi && $0 start && $0 test
SETUP
}

case "${1:-status}" in
  start)   shift; for s in $(resolve_targets "${1:-all}"); do start_one "$s"; done; cmd_env ;;
  stop)    shift; for s in $(resolve_targets "${1:-all}"); do stop_one "$s"; done ;;
  restart) shift; t="${1:-all}"
           for s in $(resolve_targets "$t"); do stop_one "$s"; done
           for s in $(resolve_targets "$t"); do start_one "$s"; done; cmd_env ;;
  status)  shift; cmd_status "${1:-all}" ;;
  logs)    shift; tail -f "$(logfile_of "${1:-chat}")" ;;
  test)    cmd_test ;;
  env)     cmd_env ;;
  models)  cmd_models ;;
  setup)   cmd_setup ;;
  *)       sed -n '3,46p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
