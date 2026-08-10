#!/usr/bin/env bash
#
# run-local-llm-stack.sh — run KM2's inference stack locally, with no third-party calls.
#
# Serves four llama.cpp servers behind OpenAI-compatible APIs:
#
#   chat   :8099   Qwen3-30B-A3B      chat completions + tool calling
#   embed  :8098   nomic-embed-text   embeddings (retrieval, ingest, fact store)
#   fast   :8097   Qwen3-4B-Instruct  short generation-bound calls (spoken answers)
#   rerank :8096   bge-reranker-v2-m3 cross-encoder re-scoring of retrieved passages
#
# The `fast` server exists because latency on the big model is GENERATION-bound, not
# prompt-bound: measured ~8.5 tok/s on Qwen3-30B here, so a detailed 70-word spoken
# answer costs ~13s no matter how well the prompt caches. Condensing already-retrieved
# passages into one spoken reply does not need a 30B model, and a 4B one generates it
# several times faster. KM2 picks per call via OPENAI_MODEL_ROUTES (model id → endpoint),
# surfaced as the chat element's answer-model dropdown.
#
# TWO servers because one llama.cpp process cannot do both: a server started for chat
# answers /v1/embeddings with "501 This server does not support embeddings", and
# --embeddings mode does not serve chat. KM2 therefore has two settings, OPENAI_BASE_URL
# and EMBEDDING_BASE_URL, and every OpenAI client in the codebase passes base_url
# explicitly so the SDK's own OPENAI_BASE_URL env-var fallback can never silently
# redirect a call to the wrong server.
#
#   ./run-local-llm-stack.sh start           # all servers
#   ./run-local-llm-stack.sh start chat      # just one (chat|embed|fast|rerank)
#   ./run-local-llm-stack.sh status          # health, model, VRAM, requests served
#   ./run-local-llm-stack.sh test            # real round-trip against both
#   ./run-local-llm-stack.sh env             # the settings to hand KM2
#   ./run-local-llm-stack.sh logs chat       # follow a log
#   ./run-local-llm-stack.sh models          # list .gguf files
#   ./run-local-llm-stack.sh setup           # print one-time build/download commands
#   ./run-local-llm-stack.sh stop | restart [chat|embed|fast|rerank]
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
#   --spec-type      speculative decoding, OFF by default — measured slower here in all three
#                    modes (see SPEC_TYPE below). Warm generation is ~23 tok/s, so a 70-word
#                    spoken answer is ~4s of generation; the latency that remains is the COLD
#                    prefill of a new context (~220 tok/s), which prompt-prefix caching, not
#                    speculation, is what fixes
#   --mlock          pins the weights in RAM so an IDLE server does not go cold (CHAT_MLOCK)
#   --no-mmap        the other half of that: weights become anonymous memory, ~14 GB rather
#                    than the whole 18.6 GB file, and llama.cpp asks for it anyway with
#                    tensor overrides (-ncmoe) in play
#   enable_thinking  off: Qwen3 otherwise emits reasoning blocks that KM2 renders as speech

set -euo pipefail

LAB="${LLM_LAB:-${LAB:-$HOME/llm-lab}}"
BIN="$LAB/llama.cpp/build/bin/llama-server"
MODEL_DIR="$LAB/models"
DOCKER_NET="${DOCKER_NET:-km2_network}"
START_TIMEOUT="${START_TIMEOUT:-300}"

CHAT_PORT="${CHAT_PORT:-8099}"
EMBED_PORT="${EMBED_PORT:-8098}"
FAST_PORT="${FAST_PORT:-8097}"
RERANK_PORT="${RERANK_PORT:-8096}"
CHAT_MODEL="${CHAT_MODEL:-Qwen3-30B-A3B-Q4_K_M.gguf}"
# --- keeping the chat server warm ----------------------------------------------------------
# An IDLE chat server goes COLD, and the bill lands on whoever asks the next question.
# MEASURED 1 Aug 2026 on a ~2.9k-token RAG prompt after ~15 hours idle:
#
#   warm    610 tok/s prefill    24.6 tok/s generation      9.5s answer
#   cold     30 tok/s prefill     4.6 tok/s generation    102.8s answer     <-- 20x
#
# Nothing about the request changed — retrieval took 2.3s of that in both cases. The weights
# were simply no longer in memory. `-ncmoe 44` keeps ~14 GB of expert tensors on the CPU side,
# and mmap makes them FILE-backed: with free RAM at 1 GB and swap 7/7 GB full (VS Code, Chrome,
# a browser full of tabs), the kernel reclaims them for free during a long idle gap, and the
# next prefill faults all 14 GB back off disk. The tell in llama-chat.log is the first batch
# eating nearly all the time — `prompt processing, n_tokens = 2048, progress = 0.69, t = 91.47 s`.
#
# --mlock pins them so reclaim cannot take them. --no-mmap goes with it and is not optional
# here: with mmap the lock covers the whole 18.6 GB file, without it host RAM holds only what
# did not go to the card (~14 GB, the GPU's ~4.5 GB is copied and dropped). llama.cpp asks for
# --no-mmap on its own once -ncmoe is in play ("tensor overrides to CPU are used with mmap
# enabled - consider using --no-mmap"). Cost is a slower START — the file is read rather than
# mapped — which is why START_TIMEOUT is generous.
#
# This needs RLIMIT_MEMLOCK raised, which is root's to grant and NOT something this script can
# do for you; start_one refuses rather than let llama.cpp warn and silently run unpinned. Set
# CHAT_MLOCK=0 to go back to mmap (correct on a box with real memory pressure — locked pages
# are pages the kernel can never reclaim, so pinning 14 GB it cannot spare pushes the pain
# somewhere else).
CHAT_MLOCK="${CHAT_MLOCK:-1}"
# --- speculative decoding (chat server) ---------------------------------------------------
# GENERATION is the wall on this box: ~8.5 tok/s, so a detailed 70-word spoken answer spends
# ~11s producing tokens. Speculation proposes several tokens at once and lets the 30B verify
# them in ONE forward pass. Output is bit-identical to running the 30B alone — a throughput
# trick, not a quality trade.
#
# MEASURED HERE (30 Jul 2026, warm KV prefix, 121 generated tokens over 3 questions), and the
# answer was NO — every mode is slower than plain decoding on this box:
#
#   none          5.26s   23.0 tok/s     <-- default
#   ngram-mod     5.91s   20.5 tok/s
#   ngram-simple  6.85s   17.7 tok/s
#   draft-simple 12.75s    9.5 tok/s     (0.6B draft, q4 KV, -ngld 99)
#
# The premise of speculation is that verifying k proposed tokens costs about what generating one
# does. That holds when the weights are on the GPU. It does NOT hold for a MoE whose experts sit
# in system RAM (-ncmoe 44): verifying a batch touches those CPU-side experts for every proposed
# position, so a rejected run costs real time and even an accepted one saves little — and the
# draft model competes for the same CPU and the same 6 GB card. Left wired, defaulting to off:
# on a machine that fits the whole model in VRAM this is one env var away.
#
# SPEC_TYPE picks the source of the proposals:
#   ngram-mod / ngram-simple  proposals are looked up in the PROMPT — no draft model, no extra
#                             VRAM, which is what makes it viable on a 6 GB card that already
#                             holds the 30B. Suited to this workload: a grounded answer quotes
#                             names, numbers and registries straight out of the passages.
#   draft-simple              a small model drafts. Needs its weights AND its KV cache in VRAM
#                             (the draft inherits -c 16384; at f16 that KV alone asked for
#                             1.8 GB and failed to allocate here — hence the q4 draft KV below).
#   none                      plain decoding.
SPEC_TYPE="${SPEC_TYPE:-none}"
# Draft model, used only by the draft-* types. Must share the target's tokenizer — both Qwen3,
# so the vocabularies match.
DRAFT_MODEL="${DRAFT_MODEL-Qwen3-0.6B-Q4_K_M.gguf}"
DRAFT_NGL="${DRAFT_NGL:-99}"
# Quantised draft KV: the difference between the draft fitting the leftover VRAM and not.
DRAFT_KV="${DRAFT_KV:-q4_0}"
# Tokens proposed per step. Higher wins more on predictable text and wastes more when the
# proposal is wrong.
DRAFT_N_MAX="${DRAFT_N_MAX:-5}"
EMBED_MODEL="${EMBED_MODEL:-nomic-embed-text-v1.5.Q8_0.gguf}"
FAST_MODEL="${FAST_MODEL:-Qwen3-4B-Instruct-2507-Q4_K_M.gguf}"
# The model id KM2 asks for when it wants the fast server. llama.cpp ignores the
# requested name (it serves what it loaded), so this is purely the routing key that
# must match OPENAI_MODEL_ROUTES and the chat element's answer-model list.
FAST_MODEL_NAME="${FAST_MODEL_NAME:-qwen3-4b-fast}"
# The routing key for the BIG model, same idea. Unrouted calls already reach the chat
# server via OPENAI_BASE_URL, so this name is not how you get here — it is how a workflow
# node pins itself to local inference explicitly, which is what makes a local-vs-hosted
# comparison mean anything.
CHAT_MODEL_NAME="${CHAT_MODEL_NAME:-qwen3-30b}"
# Reported to KM2 as OPENAI_EMBEDDING_MODEL. llama.cpp ignores the requested model name
# (it serves whatever was loaded), but KM2 records it, so keep it human-readable rather
# than deriving it from the quantised filename.
EMBED_MODEL_NAME="${EMBED_MODEL_NAME:-nomic-embed-text-v1.5}"
# Must match the model. nomic-embed-text-v1.5 = 768. Verify with `test`, which reads the
# width off a real response rather than trusting this number.
EMBED_DIM="${EMBED_DIM:-768}"

# --- reranking ------------------------------------------------------------------------
# A cross-encoder that scores (query, passage) PAIRS, which is the thing dense retrieval
# structurally cannot do: the embedder encodes query and passage separately, so it ranks
# on topical similarity and misses paraphrase. Measured on this box's Robots org — asked
# "how many people can each ship handle", dense top-5 returned a booking FAQ about crew
# limits and the fleet document's INTRO, while the per-ship sections holding the actual
# answer ("**Standard Crew Complement:** 5,500 officers and crew") ranked nowhere in the
# top 10. Reranking re-scores a wider shortlist (RERANK_CANDIDATES, default 30) so those
# passages can be promoted into the prompt.
#
# bge-reranker-v2-m3 is 568M params — small next to the 30B, and it runs once per query
# over ~30 short passages. Q8_0 (~640 MB) rather than a smaller quant: ranking quality is
# the entire product here, and the file is small enough that the saving is not worth it.
#
# ⚠ VRAM on a 6 GB card is already tight (chat ~4.5 GB at -ncmoe 44, plus the embedder).
# RERANK_NGL=0 moves the reranker to CPU if it fails to allocate; raising CHAT_NCMOE or
# shrinking the chat model frees room to keep it on the GPU.
RERANK_MODEL="${RERANK_MODEL:-bge-reranker-v2-m3-Q8_0.gguf}"
RERANK_MODEL_NAME="${RERANK_MODEL_NAME:-bge-reranker-v2-m3}"
RERANK_NGL="${RERANK_NGL:-99}"

ALL_SERVERS=(chat embed fast rerank)

red()  { printf '\033[31m%s\033[0m\n' "$*"; }
grn()  { printf '\033[32m%s\033[0m\n' "$*"; }
dim()  { printf '\033[2m%s\033[0m\n' "$*"; }
die()  { red "error: $*" >&2; exit 1; }

port_of()    { case "$1" in chat) echo "$CHAT_PORT";; embed) echo "$EMBED_PORT";; fast) echo "$FAST_PORT";; rerank) echo "$RERANK_PORT";; esac; }
model_of()   { case "$1" in chat) echo "$CHAT_MODEL";; embed) echo "$EMBED_MODEL";; fast) echo "$FAST_MODEL";; rerank) echo "$RERANK_MODEL";; esac; }
logfile_of() { echo "$LAB/llama-$1.log"; }
pidfile_of() { echo "$LAB/llama-$1.pid"; }

# Extra llama-server flags per role. Chat needs a big context and tool-calling support;
# the embedder needs --embeddings and nothing else.
flags_of() {
  case "$1" in
    # -ncmoe 44 keeps the first 44 layers' experts on CPU and puts the last 4 on the GPU.
    #
    # An earlier note here claimed ~12% faster than --cpu-moe (all 48 on CPU). RE-MEASURED
    # 31 Jul 2026 as an adjacent A/B/A (44 → 48 → 44, three runs each, -ub 2048) and it
    # does NOT hold: 25.5 / 24.9 / 24.4 tok/s. Four expert layers cost 1492 MiB of VRAM
    # (5682 vs 4190) and bought under 2% — inside the run-to-run noise.
    #
    # That is the important thing to know before spending money on this box: partial
    # expert offload scales badly, because generation waits on whichever experts are
    # still in system RAM. 44 of 48 still commuting means you keep essentially all of
    # the cost. The speedup is a STEP, not a slope — it arrives when the last expert
    # lands on the card (~18 GB of weights + KV, so a 24 GB GPU), not gradually as
    # layers move. Doubling to 12 GB would move ~16 layers (≈373 MiB each) and still
    # leave 28 on the CPU path.
    #
    # 44 is kept because it costs nothing to keep and would pay off on a bigger card.
    # Do not lower it here: -ncmoe 43 dies with "CUDA error: out of memory" once
    # -ub 2048's compute buffers are allocated.
    # -np 2 rather than the default 4: fewer slots means a larger KV budget each and a
    # better chance the previous turn's prefix is still cached.
    # --cache-reuse makes that cached prefix actually pay off. A chat turn's prompt is
    # append-only — turn N+1 is turn N's history plus the new exchange — so without it
    # every turn re-evaluates the WHOLE conversation. Prompt eval runs at a few hundred
    # tok/s here, which is what put ~16s in front of the first generated token on a long
    # chat; with reuse only the new tail is evaluated.
    # -ub sizes the PREFILL batch — how many prompt tokens are evaluated per pass.
    # llama.cpp defaults to 512, which badly under-uses the card on a RAG prompt.
    # MEASURED here (31 Jul 2026, 2.5k-token RAG prompt, 3 cold runs each — every run
    # led with a unique nonce so --cache-reuse could not serve the prefix and flatter
    # the number):
    #
    #   -ub 512   300 tok/s   8.53s prefill      <- llama.cpp default
    #   -ub 1024  458 tok/s   5.64s
    #   -ub 2048  641 tok/s   4.01s              <- 2.1x, and what KM2 now runs
    #
    # Generation was FLAT across all three (25.8 / 26.8 / 28.5 tok/s), which is the
    # expected result and worth stating: prefill evaluates many tokens in one batch and
    # is compute-bound, decode produces one token at a time and is memory-bandwidth-
    # bound. -b/-ub cannot touch decode. Only -ncmoe — expert weights on the card
    # instead of in system RAM — moves that number.
    #
    # And the two COMPETE for the same 6 GB: -ub 2048's compute buffers are what make
    # -ncmoe 43 fail with "CUDA error: out of memory" (44 is already the floor at
    # -ub 512). The batch wins that trade by a wide margin — 4.5s of prefill against
    # the ~2% one more expert layer would give decode.
    chat)  printf '%s\n' -c 16384 -np 2 -ngl 99 -ncmoe "${CHAT_NCMOE:-44}" --jinja \
             -b "${CHAT_BATCH:-2048}" -ub "${CHAT_UBATCH:-2048}" \
             --cache-reuse "${CHAT_CACHE_REUSE:-256}" \
             --chat-template-kwargs '{"enable_thinking":false}'
           # Keeps an idle server from going cold — see CHAT_MLOCK above for the numbers.
           [[ "$CHAT_MLOCK" == 1 ]] && printf '%s\n' --mlock --no-mmap
           # Appended separately so that SPEC_TYPE=none emits nothing at all: llama-server
           # rejects an empty --spec-type/-md rather than ignoring it.
           if [[ "$SPEC_TYPE" != "none" ]]; then
             printf '%s\n' --spec-type "$SPEC_TYPE" --spec-draft-n-max "$DRAFT_N_MAX"
             # Only the draft-* types need a model; the ngram ones read the prompt.
             if [[ "$SPEC_TYPE" == draft-* && -n "$DRAFT_MODEL" && -f "$MODEL_DIR/$DRAFT_MODEL" ]]; then
               printf '%s\n' -md "$MODEL_DIR/$DRAFT_MODEL" -ngld "$DRAFT_NGL" \
                 -ctkd "$DRAFT_KV" -ctvd "$DRAFT_KV"
             fi
           fi
           : ;;
    # -ub/-b sized to the model's context, NOT left at the default. In embeddings mode
    # llama.cpp clamps n_batch down to n_ubatch (512 by default) and then REJECTS any single
    # input longer than that with a 500: "input (557 tokens) is too large to process. increase
    # the physical batch size". A non-causal embedder has to see the whole sequence in one
    # ubatch, so this is a hard floor, not a throughput knob. It bit 3 of the Robots org's 49
    # documents — chunks are cut to 500 *tiktoken* tokens, and nomic's tokenizer makes more of
    # them from the same text, so anything near the limit failed and those documents were
    # silently absent from retrieval (status FAILED, no vectors).
    embed) printf '%s\n' --embeddings -ngl 99 -b "${EMBED_BATCH:-2048}" -ub "${EMBED_BATCH:-2048}" ;;
    # A dense 4B at Q4 is ~2.5 GB of weights, which does NOT fit beside the 30B's 4.3 GB
    # on a 6 GB card, so FAST_NGL defaults to 0 (CPU).
    #
    # MEASURED, and counter-intuitive: CPU-only, this 4B is SLOWER than the 30B MoE on the
    # GPU (28.4s vs 6.1s on the same 8.4 KB spoken-answer prompt) — the MoE activates only
    # ~3B params per token and has its attention layers on the card, which beats a dense 4B
    # on CPU. Partial offload needs KV room too: FAST_NGL=11 with the default -c 8192 dies
    # in "failed to allocate buffer for kv cache", hence FAST_CTX. Only worth enabling if
    # you free real VRAM (a smaller chat model, --cpu-moe, or a second card).
    # Same --cache-reuse rationale as chat: retrieved passages lead the prompt and repeat.
    fast)  printf '%s\n' -c "${FAST_CTX:-8192}" -np 2 -ngl "${FAST_NGL:-0}" --jinja \
             --cache-reuse "${CHAT_CACHE_REUSE:-256}" \
             --chat-template-kwargs '{"enable_thinking":false}' ;;
    # --reranking turns on the /v1/rerank endpoint and puts the model in RANK pooling.
    # -b/-ub sized up for the SAME reason as the embedder, and it is the same class of
    # bug: a reranker is a non-causal BERT that has to see each (query, passage) pair in
    # one ubatch, so at the 512 default any pair longer than that comes back a 500 —
    # which would look like "reranking randomly drops passages" rather than an error.
    rerank) printf '%s\n' --reranking -ngl "$RERANK_NGL" -c "${RERANK_CTX:-8192}" \
              -b "${RERANK_BATCH:-4096}" -ub "${RERANK_BATCH:-4096}" ;;
  esac
}

resolve_targets() {
  local t="${1:-all}"
  case "$t" in
    all|"") printf '%s\n' "${ALL_SERVERS[@]}" ;;
    chat|embed|fast|rerank) echo "$t" ;;
    *) die "unknown server '$t' (expected: chat, embed, fast, rerank, all)" ;;
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

# --mlock is two limits, and only one of them is ours. The SOFT limit we raise here to
# whatever the login session allows; the HARD limit is root's, and llama.cpp's response to
# an insufficient one is a warning on stderr followed by running UNPINNED — i.e. exactly the
# cold-start problem CHAT_MLOCK exists to fix, minus any sign that it did not work. So refuse.
require_memlock() {
  local model="$1" need hard
  need=$(stat -c %s "$model")
  hard=$(ulimit -Hl)                     # KiB, or the literal "unlimited"
  if [[ "$hard" != unlimited ]] && (( hard * 1024 < need )); then
    die "chat: --mlock needs RLIMIT_MEMLOCK ≥ $(( need / 1024**3 )) GiB, this session's hard limit is $(( hard / 1024**2 )) GiB.

  Grant it once, as root (applies to your NEXT login):
      echo '$USER  -  memlock  unlimited' | sudo tee /etc/security/limits.d/90-llama-memlock.conf

  Or for THIS shell, without logging out — then re-run this command from the same shell:
      sudo prlimit --pid \$\$ --memlock=unlimited

  Or start unpinned and accept a cold first request after an idle gap:
      CHAT_MLOCK=0 $0 start chat"
  fi
  ulimit -Sl "$hard" 2>/dev/null || true   # the child inherits it across setsid/exec
}

# Wrapped in a function rather than inlined at both call sites: as the last statement of a
# `for` body, a bare `[[ … ]] && …` makes the whole loop exit 1 and `set -e` kills the script.
memlock_gate() {
  if [[ "$1" == chat && "$CHAT_MLOCK" == 1 ]]; then
    require_memlock "$(resolve_model "$(model_of chat)")"
  fi
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
  memlock_gate "$name"
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
  # Resident vs LOCKED, because --mlock failing is not fatal to llama.cpp — it warns once and
  # serves happily unpinned, and the only symptom is a 100s answer a day later. `locked 0 GiB`
  # against a running chat server means CHAT_MLOCK did not take.
  if [[ -r "/proc/$pid/status" ]]; then
    awk '/^VmRSS:/{r=$2} /^VmLck:/{l=$2} END{printf "     memory    %.1f GiB resident, %.1f GiB locked\n", r/1048576, l/1048576}' \
      "/proc/$pid/status"
  fi
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
  echo "    OPENAI_MODEL_ROUTES=$CHAT_MODEL_NAME=http://127.0.0.1:$CHAT_PORT/v1, $FAST_MODEL_NAME=http://127.0.0.1:$FAST_PORT/v1, gpt-5.6-luna=https://api.openai.com/v1"
  echo
  dim "  A route wins over OPENAI_BASE_URL, so adding a hosted id to that list is how one"
  dim "  org runs on OpenAI while the rest stay on this box — the model name a workflow's"
  dim "  LLM node asks for is the whole switch. Hosted ids need an org or central API key."
  echo
  if [[ -n "$gw" ]]; then
    echo "  docker containers (brain-api):"
    echo "    OPENAI_BASE_URL=http://$gw:$CHAT_PORT/v1"
    echo "    EMBEDDING_BASE_URL=http://$gw:$EMBED_PORT/v1"
    echo "    OPENAI_EMBEDDING_MODEL=$EMBED_MODEL_NAME"
    echo "    EMBEDDING_DIMENSION=$EMBED_DIM"
    echo "    RERANK_BASE_URL=http://$gw:$RERANK_PORT/v1"
    echo "    RERANK_MODEL=$RERANK_MODEL_NAME"
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

  healthy rerank && {
    dim "rerank: scoring two passages..."
    # A real relevance judgement, not just a 200: the crew-complement passage must beat
    # the unrelated one. This is the exact failure the reranker was added to fix, so the
    # test asserts the ORDER rather than trusting that the endpoint responded.
    local winner
    winner=$(curl -sf -m 60 "http://127.0.0.1:$RERANK_PORT/v1/rerank" \
      -H 'Content-Type: application/json' \
      -d '{"model":"local","query":"how many people can each ship handle","documents":[
           "The gift shop opens at 9am and sells uniforms, patches and freeze-dried ice cream.",
           "USS Meridian — Heavy Class Carrier. Standard Crew Complement: 5,500 officers and crew."]}' \
      | python3 -c 'import json,sys; r=json.load(sys.stdin)["results"]; print(max(r, key=lambda x: x.get("relevance_score", x.get("score", 0)))["index"])') \
      || { red "  rerank request FAILED"; ok=1; }
    if [[ -n "${winner:-}" ]]; then
      if [[ "$winner" == "1" ]]; then
        grn "  ranked the crew-complement passage first"
      else
        red "  WRONG passage ranked first (index $winner) — check the model is a reranker"; ok=1
      fi
    fi
  } || { red "rerank: not responding on :$RERANK_PORT"; ok=1; }

  [[ $ok -eq 0 ]] && grn "all servers OK"
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

  # 4. fast — Qwen3-4B-Instruct for short spoken answers. ~2.5 GB. (Qwen's own GGUF repo
  #    for this one 401s; the unsloth mirror carries the identical Q4_K_M file.)
  curl -L -O https://huggingface.co/unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/$FAST_MODEL

  # 5. rerank — bge-reranker-v2-m3, a 568M cross-encoder. ~640 MB.
  curl -L -O https://huggingface.co/gpustack/bge-reranker-v2-m3-GGUF/resolve/main/$RERANK_MODEL

  # 6. verify
  nvidia-smi && $0 start && $0 test
SETUP
}

case "${1:-status}" in
  start)   shift; for s in $(resolve_targets "${1:-all}"); do start_one "$s"; done; cmd_env ;;
  stop)    shift; for s in $(resolve_targets "${1:-all}"); do stop_one "$s"; done ;;
  restart) shift; t="${1:-all}"
           # Before stopping anything: a preflight that refuses AFTER the stop loop would
           # leave the server down rather than restarted.
           for s in $(resolve_targets "$t"); do memlock_gate "$s"; done
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
