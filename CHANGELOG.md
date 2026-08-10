# Changelog

All notable changes to the Red Arch Knowledge Management Platform are documented in
this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added — An acceptance auditor: does the delivered work answer what was asked?

The gap the other completion checks cannot close. Evidence proves *something* was
produced; the deliverable rule proves *something* was attached. Neither can tell whether
the something is what the person wanted.

The case: a person filed **"Check out SEO on redarchlabs.com and tell me what you
think."** Four levels of delegation each restated it slightly more abstractly — audit
the site → run a crawl → design a crawler → write up the crawler design — until the work
underway was crawler architecture. An adversarial review board of three agents then read
that design and argued about render-completeness heuristics and threat models for four
rounds. Every reviewer judged the design on its own terms. None asked whether anybody
wanted a crawler. Nine steps closed green and the website was never opened. No single
hop was unreasonable, which is exactly why no reviewer inside the chain could catch it:
each agent evaluates against the brief it was handed, and the brief is what drifted.

- **`services/agents/acceptance.py`** runs once, on the step that closes an order, and
  is built to be uncontaminated: it reads the **original title and body as the person
  typed them** — the one artifact in the system that never changed — plus the attached
  artifacts and the closing report. Never the task list (re-planned four times), never
  the delegation briefs, never the reasoning transcript. A reviewer that reads the
  author's reasoning adopts it, which is the documented failure mode of the existing
  review board. Platform notices (⛔/✅/⚠️/🏛️) are filtered out of what it sees, since a
  diary of bookkeeping reads as activity.
- **On FAIL the order does not close**: the transition is refused with the gap named, a
  diary line is written, and the org admins are told. Nothing is lost — the order stays
  open, so a person can redirect it or overrule the auditor.
- **It fails open.** No key, a model error, or a reply with no verdict all let the
  transition through and record that no check happened — a skip must never read as a
  pass. `AGENT_ACCEPTANCE_ENFORCE=false` records verdicts without blocking, which is how
  to try it on a live org first. Model is `AGENT_ACCEPTANCE_MODEL` (default
  `gpt-5-mini`): one short call per closing order.

Verified against the real order: `FAIL — asked for an SEO review of redarchlabs.com and
received an engineering buildability/rendering-completion report unrelated to SEO.`

### Fixed — An opinion order is not made to produce paperwork

The "last step needs something attached" rule shipped moments earlier applied to every
order, including *"tell me what you think"* — which is answered by an answer. It now
applies only when the brief promises a file, or when the agent's own plan said it would
attach one. Demanding an attachment otherwise makes an agent produce a document nobody
asked for purely to satisfy a check, which is the same drift-into-paperwork the
surrounding work exists to stop.

### Added — An agent cannot declare its own work done

An SEO work order finished with nine steps marked `done`, an adversarial review board
passed, and the thing actually asked for — open this website and audit it — never
attempted. What was delivered was a well-argued document about how one would build a
crawler. Nothing in the system could tell the difference: `done` was a string an agent
wrote about itself, and no code path anywhere could disagree. An agent's output is prose
about work, so prose about work is indistinguishable from work unless something refuses
it. These rules are taken from a definition-of-done that already works in practice
(`redarchlabs-agents/docs/agent-org/definition-of-done.md`) — *you never self-declare
done*, enforced at the transition rather than asked for in a prompt.

- **`done` requires evidence.** `update_work_order_task` takes a required sentence
  naming what was produced and where it is, which is recorded in the diary under the
  agent's own name. "done", "ok" and "completed the task" fall below the floor. Only
  `done` is gated — demanding evidence to say "I have started" is the bureaucracy that
  gets a rule routed around.
- **The last step cannot close an order with nothing attached.** When every other step
  is done or carried and the order has no output artifact, the final transition is
  refused and the agent is told to attach the deliverable or leave the step open and say
  what stopped it. Not a per-task artifact rule: plenty of real steps produce no file,
  and that rule would be satisfied by attaching junk.
- **A plan that owes an output gets a step for handing it over.** When the brief promises
  something a person opens — report, audit, CSV, design, summary — `set_work_order_tasks`
  appends a delivery step unless the agent already planned one. A plan that produces a
  report and never says "attach it" ends with the report inside the agent's own
  transcript, which is the same as never having written it. Orders that only want an
  opinion are left alone.
- The work-order prompt now states the rule up front, including the sentence that names
  the actual failure: *writing about the work is not the work.*

### Added — The Agents page shows who is working and who needs you

The roster rendered identically during a live run and at 3am — name, kind, provider,
model, all true whether or not anything was happening. The only way to learn an agent
was mid-task, or had been sitting on a question for an hour, was to open the work order
it happened to be attached to.

- **Two badges, `GET /api/agents/activity`** (`services/agents/roster_activity.py`):
  **Working** (green, spinner) when a run of theirs is queued or running, **Needs you**
  (amber, matching the header bell) when a person is the blocker — a pending approval or
  a question asked of a human. `needs_you` wins when both are true, since a second run
  can be underway while the first sits parked, and only one of those is something you
  can act on. Counts are shown when there is more than one.
- **A run parked on a peer consult is not "needs you"** — nobody is asking a person
  anything, and a badge that calls for help when none is wanted stops being read.
- Idle agents get no badge at all: a row of "idle" chips makes the two that matter
  harder to find. The endpoint returns only agents with something going on, polled every
  8s and gated on tab visibility.
- **The busy ones sort to the top** — needs-you, then working, then the rest in name
  order. An alphabetical roster buries the one agent that is stuck behind fourteen that
  are asleep. The sort is stable, so agents inside a band hold their position instead of
  reshuffling under the cursor on every poll.
- **"Needs you" is clickable and answerable in place.** It opens the agent's own pending
  approvals and questions with the same approve / deny / answer / let-it-decide actions
  the inbox has — the badge already said which agent was stuck, and making someone leave
  for a shared inbox to find that row again among everyone else's is where "I'll deal
  with it later" comes from. `ApprovalRead` gained `agent_id`/`agent_name` and
  `QuestionRead` gained `asked_by_agent_id` so a card can claim its own items.
- **A stale badge corrects itself instead of opening an empty box.** The badge is up to
  one poll behind, so it can still say "needs you" about something settled seconds ago
  in another tab. Clicking through to an empty dialog is a worse answer than closing it,
  refreshing the badge, and saying so in one line.

### Fixed — The inbox stops asking you to tick off work already done

Every approval and question notification stayed `unread` after its item was settled, and
the inbox's escalation section listed notifications of *every* kind — so the same
approval appeared twice, once as a real decision and once as a "Resolve" chore that did
nothing but clear the row. Observed live: eleven open rows, eight of them for items
decided hours earlier.

- **A notice settles with its item** (`notify.settle_notifications`). Deciding an
  approval or answering, declining, or voiding a question resolves that run's matching
  notification — guarded on nothing else of that kind being left pending, since a run may
  raise a second ask while the first is being decided. Escalations are untouched: they
  mean work stopped, and only a person decides those are done.
- **The escalation section lists escalations and reviews only.** Approvals and questions
  already have their own sections, with buttons that do something.
- **Unblocking a step retracts its alert** (`WorkOrderService.clear_blocked_alert`). The
  "needs a person before it can continue" alert stopped being true the moment the agent
  freed the step itself, but it stayed open — seen live, a step was blocked and marked
  done sixteen seconds later and the alert outlived both. Fires only when the *last*
  blocked step clears (four blocked and one freed still needs the same person), and on
  re-planning, which is how an order most often stops being blocked. Blocking again
  afterwards still alerts: the retraction is not a permanent silence.

### Fixed — `run_claude_code` tells the model how to succeed with it

An agent asked the CLI to build a Playwright crawler, hit the 300s ceiling, and
concluded from the bare timeout that it "cannot access the public web from this
environment" — then wrote a design document about crawling instead of fetching the page.
The tool could have opened that URL in fourteen seconds.

- **The description says what it is for**: one bounded job of a few minutes, and
  explicitly that it can fetch pages from the live web — which makes it the way to
  inspect a public URL when no web-research key is configured. `working_dir` now says it
  must already exist and is not created for you.
- **The timeout says what to do next** instead of only what failed: split the work, ask
  for the smallest next step, ask for the finding rather than the tooling that would
  produce it.
- **A missing `working_dir` lists the directories that do exist.** Naming only what is
  absent leaves the model with another guess; it invented `seo-crawler-playwright`, was
  told just that it was missing, and abandoned the tool rather than trying a real one.

### Fixed — An agent can see who its colleagues are

Nothing told an agent who its direct reports were. The roster existed in exactly one
place: the error you get back for naming a colleague that is not yours — which an agent
has to guess a name to see. Caught on the SEO work order: a chief-of-staff told to
"route the crawl through the engineering chain" reasoned its way to wanting the
technical-project-manager, could name nobody to send it to, escalated to a human twice,
and marked every remaining step blocked. Its own direct report owned that branch.

- **The system prompt names the direct reports and their kind** — coordinator, operator
  or advisory — because the kind is the routing fact: a coordinator passes work on, an
  advisory agent is a leaf. Consultable advisors are listed separately (`consult_peer`
  reaches any of them org-wide). Both lists cap at 20 names so a large org cannot push
  the actual work out of the context window; disabled reports are dropped, since
  delegating to one queues a run that never executes.
- **And it says that a skill further down is still reachable**: delegate to the
  coordinator whose branch owns it and let them pass it on. Without that sentence an
  agent reads a direct-reports-only list literally, concludes two levels down cannot be
  reached, and escalates instead of delegating.

### Fixed — `escalate` now wakes the supervisor it escalates to

An agent that escalated wrote a notification and nothing else. With a supervisor set,
that row was addressed to no role — so no person saw it — and no run was ever queued,
so the supervisor never woke up either. The report got back `{"status": "notified"}` and
believed it had handed the problem over. Caught live: a research-analyst offered its
human "escalate to chief-of-staff for platform access", the human picked it, the
escalation went nowhere, and the analyst then marked every remaining step `blocked`.

- **Escalating queues a run for the supervisor** (`trigger: "escalation"`, linked by
  `parent_run_id`, carrying the reporter's reason and context), the same mechanism
  delegation already used. The brief says the blocker is theirs to resolve and names the
  only three moves that change anything — a different report, a different route, or a
  person — because a supervisor handed a bare problem statement restates it and stops.
- **A human is paged only when no agent picks it up**: at the apex, when the supervisor
  is disabled (queueing a run for a disabled agent is the original bug in a new hat), or
  after `MAX_ESCALATION_HOPS`. The hop count rides in the run's input, so a
  `supervisor_id` cycle drawn by hand cannot queue runs forever, and five agents passing
  the same blocker along end at a person instead of at each other.
- **The capability warning names the keyless web route.** When an order is about the
  live web and `web_research` has no key (or nobody in reach holds it), the warning now
  also names the reachable agents that could open a page through `run_claude_code`,
  which reaches the web on the owner's Claude subscription and needs no API key. Only
  operators are listed — it is `EXECUTE`, so the kind-gate denies it to the advisory
  researcher that is the obvious agent to hand web work to. "Buy a key" was the wrong
  advice when the fix was picking a different agent.
- **Blocked means one step, not the whole list.** A run that could not finish was
  sweeping every step it had not reached to `blocked`, which hides which one actually
  needs help and reads as total failure. The work-order prompt now says to block only
  the stuck step, say what would unstick it, and escalate when a supervisor could clear
  it.

### Fixed — A blocked work order reaches a person

Seen live: "Check out SEO on redarchlabs.com" ran for five hours, asked four rounds of
questions, and ended with eight of nine steps `blocked`. Nothing said so. The stall
sweeper did write an escalation — into a list with no badge on it, on a page nobody was
on. The work order's own "Waiting on you" panel said nothing was waiting.

- **Escalations now surface where the work is.** The work order's approval queue lists
  unresolved escalations for that order, and the header bell counts them. It still does
  not count notifications generally — most are a record of something that happened and
  need nothing from you — but an escalation means work has already stopped.
- **Blocking a step raises an alert.** `update_work_order_task` moving a step to
  `blocked` writes a diary line and notifies, throttled to one alert per order until
  somebody clears it (an agent that hits a missing capability usually blocks every
  remaining step in the same turn). Blocking again after it is cleared alerts again.
- **A work order says at dispatch what its agents cannot do**
  (`services/agents/capability.py`). Reachability follows what the runtime actually
  enforces — `delegate_task` is direct-reports-only and barred to advisory agents, so an
  advisory agent is a leaf and anyone under it is unreachable. An order about the live
  web with no `web_research` in reach, a coordinator with no reports, or a request to
  change something with no operator in the chain each write one diary line and one
  notification, once. Advisory only: a wrong refusal would block real work.
- **`web_research` is `READ`, not `EXECUTE`.** The kind-gate reads `EXECUTE` as
  operator-only, so *no advisory agent could ever browse the web* whatever its grants —
  which is what left a research-analyst unable to open a single page. It is still
  grant-gated and still `side_effecting=False`.
- **Notifications from these paths can leave the app.** `WorkOrderService` now takes
  `Settings`, so a blocked step or capability gap goes out by email / the org's notify
  workflow when they are configured, instead of in-app only.
- **A message typed after the run ended is no longer thrown away.** The live panel's
  box steers a *run*; once every run had finished, the steer was refused ("that run is
  already done") and the text dropped on the floor — exactly when a person has
  something to add. It now becomes a reply on the work order: recorded in the diary,
  with a fresh run started from that history. The same fix passes through pasted
  attachments, which were parsed off the steer frame and then discarded.

### Added — `web_research` works on an Anthropic key, not just a Gemini one

- **Two backends, chosen by whichever key resolves.** Anthropic is preferred: the
  Messages API's server-side `web_search` **and** `web_fetch` mean it can open a
  *specific URL the question names*, which search-grounding cannot — and "audit this
  page" is most of what anyone asks a researcher for. Gemini's Google Search grounding
  (free tier, 1,500/day) stays as the fallback. Both return the same
  `{answer, sources, grounded}`, so nothing downstream knows which answered.
- Model is `AGENT_WEB_SEARCH_MODEL` (default `claude-opus-5`); paused server-tool turns
  are resumed, bounded; a tool-result error object is read as an error rather than
  iterated as results; a classifier refusal is reported instead of returning empty.
- With neither key configured the error **names both**, instead of sending whoever
  reads it to sign up for a vendor they may already have.

### Added — Agents can ask a question and actually get an answer

- **`ask_human` — an agent blocks for a person's typed answer.** Distinct from an approval,
  which is a yes/no on an action the agent already chose: this is the agent saying it is
  missing a fact, a preference, or a judgement only a person can supply. The run parks, the
  question lands in the agents inbox, and the answer comes back **as the result of the tool
  call that blocked**, so the same turn continues rather than restarting.
- **`consult_peer` now blocks for the peer's answer, and `reply_to_peer` delivers it.**
  Previously a consult filed a notification and returned `{"status": "sent"}` — which read as
  success while the answer went nowhere. A consult now queues a real run for the advisory
  agent (`trigger: "consult"`), and its `reply_to_peer` call resumes the agent that asked.
  Routing is unchanged (advisory targets only), plus a depth cap: a consult may not itself
  consult, so two advisors cannot ping-pong runs forever.
- **New `agent_questions` table (migration 047)** with the same hardened RLS + admin-bypass
  policy template as its siblings. `tool_call_id` is the load-bearing column — it names the
  parked call so the answer is injected into the right place in the run's resume state.
- **A question can no longer hang a run.** Every terminal transition settles both sides: a
  consult run that finishes *without* replying resumes its asker with an explicit "no answer",
  an asking run that ends voids its open questions (so a late answer cannot re-queue a dead
  run) and cancels the consult runs nobody is waiting on. Answering a run that already
  stopped records the answer and reports `resumed: false` rather than implying an agent acted
  on it.
- **Inbox UI**: `/agents/approvals` gains a "Questions for you" section with an answer box and
  a "Can't answer" action that unblocks the agent without answering. Peer consults are
  deliberately excluded — another agent owes that answer.

### Added — `SITE_ADMIN_EMAILS` pre-authorizes an admin who has never signed in

- **Listed addresses become site admins on their first sign-in.** A `user_profiles` row only
  exists after a successful login, so before this the only ways in were the one-shot first-run
  setup token or promotion by an existing admin. Inserting a placeholder row is not a
  workaround: provisioning matches on `auth_subject` alone, so the real login would insert a
  second row and collide on the UNIQUE email — turning that person's first sign-in into a 500.
- **Fails closed and never revokes.** Only an email the IdP actually asserted counts (the
  sub-derived `…@placeholder.invalid` fallback can never satisfy the list), and removing an
  address does not demote anyone — an env change is not an audited action. Demotion stays in
  the site-admin console, which records who did it.

### Fixed — View render links can use the view's slug

- **`GET /views/{ref}/render` now accepts the view's org-unique slug as well as its UUID**
  (`ViewService.get_view_by_ref`). A `row_link_template` authored as
  `/views/course_play/view?record_id={id}` — the form the LMS docs showed — previously died
  with a 422 because the path param was typed strictly as a UUID. An unknown slug is a 404;
  a malformed `record_id` still fails with 422, and the `me`/`latest` sentinels are
  unchanged. Admin CRUD endpoints remain UUID-only.

### Added — Live answer streaming for workflow-driven chat

- **An LLM step's tokens now reach the browser while the run is still executing.** A chat
  built on a workflow previously showed nothing until the run finished and its reply record
  was written. A run may now carry a caller-minted `stream_token`; the step publishes deltas
  to a Redis channel **namespaced by org** (`wf:stream:{org}:{token}`), and
  `GET /workflows/runs/live/{token}` relays it as SSE. A subscriber derives the channel from
  its own request context, so a token known to another org resolves to a channel it can never
  read.
- **Opt-in per node** (`"stream": true` in the node config). A chat answer workflow runs
  several small-LLM steps — condensing a follow-up into a search query, a not-found line, the
  answer — and streaming all of them painted an internal step's output into the chat: asking
  "tell me more about space" showed the condense step's restatement of the question as if it
  were the reply.
- **Structured actions stream only their spoken field.** `llm_respond`/`llm_decide` return
  strict JSON, so raw deltas would show `{"reply":"Hel` and leak the fields that are not
  speech (the out-of-character coach tip, the robot's internal reason). Tokens are assembled
  and one named field's value-so-far is re-read from the partial document
  (`api/services/llm_stream.py`); the raw content is still returned intact for parsing.
- The stream is strictly a **preview** — the run still writes its reply record, which remains
  the source of truth. No Redis, an older API, or a dropped connection degrades to the
  previous poll-and-wait behaviour.

### Changed — Self-hosted chat latency: prompt cache, not generation

- **The chat server now runs with `--cache-reuse` (`run-local-llm-stack.sh`, override with
  `CHAT_CACHE_REUSE`).** A chat turn's prompt is append-only, so without a cache the server
  re-evaluated the entire conversation every turn — at ~190 tok/s that put ~16s in front of
  the first generated token, while the retrieval step it blocked took 4ms. With reuse:
  `prompt eval time = 35.96 ms / 1 tokens` at `n_past = 1233` (1232 reused). Turn cost is now
  **flat as a conversation grows** — ~3–5s at both 0 and 10 messages of history, against 28s.
- **The chat element bounds the history it sends** (`MAX_HISTORY_TURNS`). With the cache in
  place this is a *context-window* budget, not a latency control — the prompt must still fit
  the server's per-slot window (`-c 16384 -np 2` = 8k per slot).
- **The saved reply is fetched the moment the run finishes** instead of waiting out the
  chat element's poll interval, which could leave a finished answer unclaimed for ~1.5s.
- Documented in [DEPLOYMENT.md](docs/DEPLOYMENT.md#prompt-cache-the-single-biggest-chat-latency-setting),
  [DEVELOPMENT.md](docs/DEVELOPMENT.md), and [ARCHITECTURE.md §9](docs/ARCHITECTURE.md).

### Added — Self-hosted inference: chat and embeddings on your own hardware

- **`OPENAI_BASE_URL` / `EMBEDDING_BASE_URL` / `EMBEDDING_DIMENSION`** — point KM2 at any
  OpenAI-compatible server (llama.cpp, vLLM, Ollama). Empty defaults preserve hosted-OpenAI
  behaviour exactly. Chat and embeddings are **separate** settings because one llama.cpp process
  cannot serve both: a chat server answers `/v1/embeddings` with
  `501 This server does not support embeddings`.
- **`base_url` is now passed explicitly at every client construction**
  (`api/services/openai_client.py`, `brain_api/openai_client.py`,
  `brain_sdk/embedding/openai_provider.py`, `ChunkSummarizer`, `TripletExtractor`). The OpenAI SDK
  falls back to the `OPENAI_BASE_URL` *environment variable* when the argument is omitted, so a
  variable intended to redirect chat silently captured embeddings and broke every document ingest.
  Regression test: `services/brain_api/tests/unit/test_embedding_endpoint.py`.
- **`OpenAIEmbeddingProvider` refuses to guess a dimension** for a self-hosted model. Guessing builds
  the vector store at the wrong width, which corrupts retrieval instead of raising.
- **`./run-local.sh`** — one command for a fully local stack, with `verify` reporting where every
  service currently sends its calls and flagging vector collections whose stored width no longer
  matches the active embedding model. **`./run-local-llm-stack.sh`** manages the two model servers.
  `run-stack.sh` gains one optional variable (`KM2_COMPOSE_OVERRIDE`); its default behaviour is
  unchanged and it remains the way back to hosted OpenAI.
- **Docs** — [ARCHITECTURE.md §9](docs/ARCHITECTURE.md), [DEPLOYMENT.md](docs/DEPLOYMENT.md)
  (Self-hosted models), [DEVELOPMENT.md](docs/DEVELOPMENT.md) (Running with local models),
  [KNOWLEDGE_ENGINE.md](docs/KNOWLEDGE_ENGINE.md).

> **Changing the embedding model is a migration, not a config flip.** Qdrant collections and the
> Neo4j `entity_embedding` index are created at a fixed width and are per-org; switching means
> dropping both and re-ingesting. The Neo4j index uses `CREATE VECTOR INDEX … IF NOT EXISTS`, so it
> is not re-dimensioned by a config change — drop it explicitly and confirm with `SHOW INDEXES`.
> Clerk and OpenAI vision OCR remain external.

### Added — Agent LLM layer: prompt caching, web grounding, batch generation

- **Anthropic prompt caching** (`services/agents/llm/caching.py`) — applied automatically inside the
  provider for Anthropic models: `cache_control` breakpoints on the stable tools+system prefix and the
  growing conversation, so repeat turns/runs read that prefix at 10% of input price. No-op below the
  model minimum (4,096 tokens for Haiku 4.5 / Opus 4.5+); cache read/write tokens surfaced in `Usage`.
- **`web_research` tool** — live-web research with citations via **Gemini + Google Search grounding** on
  the AI Studio **free 1,500/day** quota (`GEMINI_API_KEY`). A dedicated tool-less call (Gemini can't mix
  search with function tools) returning `{answer, sources}`; `EXECUTE` + `side_effecting=False` (read-only,
  runs free under high-touch), grant-gated to the research operators.
- **`batch_generate` / `check_batch` tools** — single-shot generation at the **50%-off async Batch tier**
  via the Anthropic Message Batches API (`anthropic` SDK; LiteLLM doesn't wrap it). Submits + bounded-polls,
  returning `{status:"done", text}` or `{status:"processing", batch_id}`; internal, grant-gated.
- **Config**: `AGENT_WEB_RESEARCH_MODEL` (default `gemini/gemini-2.5-flash`),
  `AGENT_BATCH_POLL_INTERVAL_SECONDS`, `AGENT_BATCH_MAX_WAIT_SECONDS`. Provisioner grants `web_research` +
  `batch_generate` to the research/content operators. Docs: [AGENT_ORG.md](docs/AGENT_ORG.md) updated.

### Added — Agent org: autonomous company, cost tiers & Claude Code CLI assistant

- **Autonomous-company provisioner** (`scripts/provision_company.py`): an idempotent, declarative
  blueprint that stands up a full traditional org (Executive, Marketing, Sales, Product, Engineering,
  Customer Support, Finance, HR, Operations, Legal, IT) of AI agents reporting to one human under
  high-touch governance. Full reference: [AGENT_ORG.md](docs/AGENT_ORG.md).
- **Native agent tools** — `list_records`/`get_record` (read) and `create_record`/`update_record`/
  `create_document` (internal writes, auto-ingested into RAG), reusing the first-party validation +
  inline-workflow + ingest paths.
- **Agent scheduler** — cron `agent_schedules` sweep (`run_due_schedules`) → internal
  `POST /api/internal/agents/run-schedules` + celery-beat `agents-run-schedules`; the existing
  `advance-runs` sweep drives the enqueued runs.
- **Centralized high-touch autonomy** — `orgs.agent_autonomy` (migration 033); the authority engine
  forces `ASK` on any side-effecting tool under high-touch while internal record/document writes run free.
- **Role-based model tiering** — Opus (apex Chief of Staff) / Sonnet (department heads + advisory
  analysts) / Haiku (operators) to cut fleet cost; the fleet runs on an Anthropic **API key**.
- **Claude Code CLI dev/ops assistant** — opt-in `run_claude_code` tool (`CLAUDE_CLI_TOOL_ENABLED`, off by
  default) that offloads dev/ops work to the local Claude Code CLI on the owner's subscription. Guardrails:
  allow-listed working dir (`CLAUDE_CLI_WORKING_DIR`, traversal refused), read-only default `--allowedTools`,
  kill-on-timeout, explicit binary path, and child-env `ANTHROPIC_API_KEY` stripping so the CLI uses the
  subscription. Console-only, granted to a single operator (`dev-ops-assistant`).
- **Docs** — new [AGENT_ORG.md](docs/AGENT_ORG.md); ARCHITECTURE.md §2.5 + README docs index updated.

### Added — Enterprise API (Phase 1: REST + API keys)

- **Org API keys**: `api_keys` table (migration 028, RLS + `FORCE ROW LEVEL SECURITY`) storing only the
  **SHA-256 hash** of a `km2_…` key — the plaintext is shown to the creating admin exactly once and never
  logged or returned on reads. Keys carry a scope set + optional expiry and are attributed to the creating
  admin. Managed under **Admin → API & Keys** (org-admin only): `GET/POST/DELETE /api/api-keys` +
  `GET /api/api-keys/scopes`.
- **Versioned public surface** `GET /api/v1/**` — a stable, API-key-authenticated contract that reuses the
  same services as the first-party UI: entities (read), records (CRUD + aggregate, with inline-workflow
  dispatch), reports (list/get/run/ad-hoc), workflows (list/get/run/runs/steps), search + RAG chat, and the
  knowledge base (folders/documents/chunks/summary). Present the key as `Authorization: Bearer km2_…` or
  `X-API-Key`.
- **Scopes**: `entities:read`, `records:read`, `records:write`, `reports:read`, `reports:run`,
  `workflows:read`, `workflows:run` (high-privilege — runs any workflow in the org), `search:read`,
  `knowledge:read`. `domain:*` / `*` wildcards supported. Org service keys act with **org-wide data
  visibility** (operations gated by scope; surfaced to admins at creation time).
- **Rate limiting**: per-key Redis fixed-window quota (`API_KEY_RATE_LIMIT_PER_MINUTE`, default 600) with
  `X-RateLimit-*` + `Retry-After` headers, plus a coarse pre-auth per-IP guard (`API_IP_RATE_LIMIT_PER_MINUTE`,
  default 1200) so an invalid-key flood can't hammer the auth lookup. Both fail open on a Redis outage.
- **Always-on docs**: Swagger UI at `/api/v1/docs` (+ `/api/v1/openapi.json`) covering only the public
  surface, gated by `API_DOCS_ENABLED`; the internal `/docs` stays debug-only.
- **Reuse refactor**: extracted shared helpers so `/api/v1` and the internal routers share one implementation
  — `services/entity_records_helpers.py`, `services/search_access.py`, `services/workflow/factory.py`
  (`build_dispatch_service`), and `services/workflow/manual_run.py` (`execute_workflow_run` +
  `resolve_published_version`); shared `SecretField` UI component for one-time secret reveal.
- New env: `API_KEY_RATE_LIMIT_PER_MINUTE`, `API_IP_RATE_LIMIT_PER_MINUTE`, `API_DOCS_ENABLED`.

### Added — Reporting engine & server-side record filtering

- **Aggregation engine**: `POST /api/entities/{slug}/aggregate` runs GROUP BY / metric queries over a custom
  entity — group by fields/relationships/base columns with optional date bucketing (`hour/day/week/month/
  quarter/year`), metrics `count/count_distinct/sum/avg/min/max`, `filters`, `having`, `order_by`, `limit`.
  Every field is whitelisted to a physical column and every op/bucket comes from a closed set, so no user
  string reaches SQL as an identifier; runs under the tenant's RLS session (`DynamicEntityRepository.
  build_aggregate` / `aggregate`).
- **Saved reports**: `reports` table (migration 026) + `GET/POST/PATCH/DELETE /api/reports`, `POST /api/reports/
  {id}/run` (with optional filter/limit overrides for dashboard drill-down) and `POST /api/reports/run`
  (ad-hoc preview). A report couples an aggregate query with a `Visualization` spec (bar/stacked/line/area/
  pie/donut/scatter/table/metric). Query + viz are validated at save. Admin-gated writes, member-gated run.
- **Server-side record filtering**: `GET /api/entities/{slug}/records` now accepts repeatable
  `filter=<field>:<op>[:<value>]` params — operators `eq/ne/gt/gte/lt/lte/in` (comma-separated), `contains`
  (text), `isnull`. Keyset pagination now works under **any** `order_by` (composite cursor), not just the
  default `created_at` sort.
- **Filterable indexes** (migration 025): a `(org_id, col DESC, id DESC)` btree per filterable scalar field
  (integer/bigint/numeric/date/timestamptz/uuid/picklist), created `CONCURRENTLY` so the backfill takes no
  table-wide write lock.
- **`report` view element**: embed a saved report on any dashboard view; renders its chart/KPI/table per the
  report's viz. Reports travel in the org **import/export** bundle (id-remapped).
- **Frontend**: a Reports page + live-preview report builder, a record-list filter bar, and a dependency-free
  SVG chart renderer (`ReportChart`) with a signed value axis for negative aggregates.

### Added — Record-state platform (workflow read/write, live status boards, inline triggers)

- **`get_record` / `update_record` workflow actions** — read a record's live fields
  into run variables (`by_id` / `latest` / `first`, optional filters) and write
  multiple fields of a targeted record. Values/filters render both `{"$ref": ...}`
  envelopes and `{{ }}` templates.
- **`record_list` view element** — a read-only, optionally-polling table of an
  entity's records (a live "status board"), with an optional per-row workflow button.
  Polling pauses on hidden tabs and backs off on error.
- **Records list `order_by` / `order_dir`** query params; view viewer honours
  `?record_id=` (entity-bound prefill + run-workflow-against-record).
- **`run_inline_on_change`** per-workflow flag — fire an entity-change workflow
  synchronously in the mutating request (no beat-sweep delay), bounded by a hard
  time budget and dedup'd against the later sweep. Settable via `PATCH /workflows`
  and MCP `km2_update_workflow`. Migrations **024** (column) and **027** (partial index).

### Added — Knowledge engine, custom entities, workflow automation, intake forms, and tenant hardening (Slices 1, 5–7)

Marks the completion of **5 major slices** adding enterprise automation and knowledge-extraction capabilities:

#### Knowledge Engine (Slice 1): Neo4j-backed fact store
- **Reified-claim architecture** (`packages/brain_sdk/facts/`): tenant-scoped fact extraction and query via Neo4j
  triplet store (design: [`docs/KNOWLEDGE_ENGINE.md`](docs/KNOWLEDGE_ENGINE.md)).
- Extractors: `pipeline.py` (doc → triplets), `predicates.py` (filtering), `resolution.py` (dedup+merge).
- Tenant labels on all nodes/rels so queries are org-isolated.

#### Custom Entities (Slice 5): Dynamic, schema-driven records
- **Entity definitions + DDL**: org admins define entities (name, fields, relationships) via
  `POST /api/entity-definitions`; the API runs physical Postgres DDL to create `ce_<slug>` tables
  matching the schema on the fly. Catalog: `entity_definitions`, `entity_fields`, `entity_relationships`.
- **Entity records CRUD**: `GET/POST /api/entities/{slug}/records` with **keyset (cursor) pagination** for scalability.
  Cursor is an opaque, URL-safe token encoding `(created_at, id)` position; no OFFSET. Search via `?q=text`.
- **Identifier safety**: `services/identifiers.py` validates slugs for SQL injection & Postgres reserved words.
- **RLS enforcement**: record access scoped by org via RLS + explicit `org_id` filtering; any org member can CRUD.
- **Schema DDL service**: `services/schema_manager.py` safely runs CREATE TABLE with type coercion, FK validation.

#### Workflow Automation (Slices 5–6): Visual workflow engine with polling-based dispatch
- **Workflow authoring**: `POST /api/workflows/{id}` create, `/versions` save drafts, `/versions/{vid}/publish` go live.
  Versions are immutable once published (DB trigger). Workflows are tied to an entity definition.
- **Triggers**: `on_record_change` (create/update/delete events from entity operations); `on_form_submission` (intake-form
  link completed). Conditions evaluated against record snapshots.
- **Actions**: `update_record_field`, `create_record`, `send_email` (HTML template, recipient validated),
  `send_webhook` (allowlisted hosts), `send_form` (mint intake-form link).
- **Execution model**: 
  - **Outbox**: entity record changes written to `workflow_outbox` in the same transaction (at-least-once semantics).
  - **Dispatch**: Celery beat sweeps `workflow_outbox` for pending events (`/api/internal/workflows/dispatch-batch`);
    dispatcher claims with `FOR UPDATE SKIP LOCKED` (exactly-once per event); per-event RLS role downgrade so actions
    write as `app_user` scoped to the event's org.
  - **Timers**: `POST /api/internal/workflows/run-timers` resumes delayed runs and fires due scheduled workflows.
- **Manual run**: `POST /api/workflows/{id}/run` executes the published version against provided inputs, gated by
  `run_permission` (org_admin only by default; widened to any_member or roles/groups).
  - Security: **record ownership validated**; **side-effecting actions rejected on free-form data** (email/webhook
    require a real record).
- **Partitioned tables**: `workflow_runs` + `workflow_run_steps` are RANGE-partitioned by `created_at` with
  month boundaries; `workflow_ensure_partitions(months_ahead)` pre-creates upcoming partitions (idempotent PL/pgSQL fn).
  Default partition catches any off-schedule inserts.
- **Monitoring**: `GET /api/workflows/{id}/runs` + `GET /workflows/runs/{run_id}/steps` list executions and steps.

#### Intake Forms (Slice 6): Public, token-linked forms
- **Form definition**: `POST /api/forms` create, `PATCH` update, `DELETE` remove. Tied to an entity. Config (JSON) defines
  field mappings and behavior.
- **Link generation**: `POST /api/forms/{id}/links` with optional recipient email + expiry. Returns an opaque **SHA-256-hashed
  token** + a public URL (Mailpit for dev, real SMTP in production).
- **Public submission**: `GET /api/public/forms/{token}` render (resolves org from token on privileged session before any RLS),
  `POST /api/public/forms/{token}` submit (single-use, checks expiry + status). On success, **triggers a workflow** (if
  `on_form_submission` rule exists) or updates the target entity record directly.
- **Email delivery**: SMTP configurable; template HTML-escaped for safety.
- **Token security**: hashed for lookup (public path resolves org from hash, then RLS-scoped); single-use (status transitions
  `pending → submitted` or `expired`/`revoked`).

#### In-API Tool-Calling Agent (Slice 7 enhancement)
- `POST /api/agent/chat/stream` — org-admin-gated SSE endpoint running OpenAI function calling in-process.
- Tools: `create_entity`, `update_entity_field`, etc. (mutates entity definitions).
- Org's per-stored OpenAI key decrypted on each request; falls back to central key if not set.
- Short-lived sessions per tool call (no connection pool saturation); tool commits are atomic.

#### Tenant Isolation Hardening
- **RLS + explicit org_id filtering** (defense in depth): repositories filter every query by `org_id` AND RLS policies
  on the tenant role (`app_user`). Privileged (BYPASSRLS) sessions used only for cross-org operations (site-admin,
  setup token, token hash → org resolution).
- **Per-org OpenAI key encryption at rest** (migration 016): `Org.openai_api_key` stored encrypted with Fernet
  (symmetric, `ORG_ENCRYPTION_KEY` config); decrypted only for worker consumption via internal endpoint.
- **Workflow dispatcher exactly-once**: claim via `FOR UPDATE SKIP LOCKED` on resume; pg_advisory_lock on scheduled workflows.

#### Per-Document Permissions (Slice 7 prerequisite)
- **Columns added** (migration 015): `documents.view_permission_masks`, `documents.contributor_permission_masks`,
  `documents.viewer_permissions_config`, `documents.contributor_permissions_config`.
- **Precedence**: per-document config + masks override folder config if set; NULL = inherit from folder (existing behavior).
- Feed access-key resolution in `brain-api` so retrieval filters by document permissions.

#### New Migrations (011–016)
- **011**: `forms` + `form_links` tables (intake-form catalog + token history).
- **012**: `workflows.run_permission` JSONB column (mode + roles/groups allowlist).
- **013**: `workflow_outbox.source` column (trigger source: `record_change` or `form_submission`).
- **014**: `workflow_runs.delay_until` + resumption logic (scheduled/delayed runs).
- **015**: Per-document permission columns (precedence over folder).
- **016**: Encrypt existing `orgs.openai_api_key` rows at rest; add `ORG_ENCRYPTION_KEY` env var.

#### Security: OpenAI Key Encryption + HTTPS Validation
- Per-org OpenAI keys are encrypted at rest (Fernet symmetric); decrypted only when needed (worker consumption).
- Workflow webhooks: allowlist validation (SSRF guard); recipient email + form link expiry validated.
- Internal API key comparison: constant-time (`hmac.compare_digest`).

### Added — File upload + OCR ingestion, folder browsing, document feedback (v1 parity)

Closes a set of gaps between Knowledge Manager v1 and v2 where the ingest,
authoring, and organize surfaces had regressed to text-paste only.

- **Binary file upload + OCR/text extraction.** New `POST /api/documents/upload`
  (multipart) streams the original to MinIO/S3-compatible object storage
  (`Document.document_url` = object key; originals retained), then dispatches a
  new Celery `task_extract_and_ingest`. The worker extracts text via **Tesseract**
  (free) or **OpenAI gpt-4.1-mini vision** (paid), selectable per upload, then
  feeds the existing text ingest pipeline. Per-org OpenAI key (`Org.openai_api_key`)
  resolved via a new internal endpoint with fallback to the central key; the key
  never rides the Celery broker. Accepts PDF/PNG/JPG/TIFF/BMP/GIF/WEBP/TXT/MD with
  a size cap and extension allowlist. `delete_document` now also purges the stored
  original. New `minio` service in `docker-compose.infra.yml`.
- **Folder browsing.** New `folders/[id]` page lists a folder's documents;
  folder-tree names are now clickable. `GET /api/documents` accepts `?folder_id=`
  to scope to one folder (Python + Go handlers).
- **Chat context scoping.** The chat window can now be scoped to a folder; the
  `folder_ids` filter (previously accepted but ignored) is translated to
  `folder:<id>` tags and applied as an OR filter in the vector store
  (new `any_tags` / `MatchAny` support), on both the `/search/chat` and
  `/chat/sessions/{id}/ask` paths.

### Fixed — document visibility, status, and feedback

- **Unfiled documents were invisible to everyone (incl. admins).** A document
  created without a folder (`folder_id IS NULL`) never matched the `folder_id IN
  (...)` list filter, so pasted docs silently vanished. `list_documents` now
  surfaces unfiled docs to org admins (`include_unfiled`); the create modal also
  gained a folder picker so docs get filed. (Python aligned with the existing Go
  `isAdmin` behavior.)
- **Status badge never showed success/failure.** The worker writes
  `SUCCESS`/`FAILED` but the UI checked `COMPLETE`/`ERROR` and the model enum was
  dead code. `ProcessingStatus` reconciled to `PENDING/PROCESSING/SUCCESS/FAILED`
  and wired into the callback validator + UI badges; documents list now
  auto-refreshes while any doc is processing.
- **Create gave no feedback.** Added `sonner` toasts on success/error; a broker
  outage during enqueue no longer 500s an already-committed document.

### Security

- Internal API key comparison is now constant-time (`hmac.compare_digest`).

### Added — First-run setup wizard + global Site Admin console (Slice 7)

Replaces the Django-admin-era global administration workflow that was lost in
the platform rewrite:

- **First-run setup wizard.** On boot with no active site admin, the API
  generates a one-time setup token (SHA-256 hash in Redis, 24h TTL, single
  use, never overwritten while unclaimed) and prints it to its logs. A signed-in Clerk user
  claims global admin at `/setup` by pasting the token, then creates the
  first organization. Endpoints: `GET /api/setup/status` (public),
  `POST /api/setup/claim` (authenticated). Orgless users are auto-redirected
  into the funnel.
- **Site Admin console** at `/site-admin` (site admins only): Organizations
  CRUD (type-to-confirm delete), Users (search, promote/demote site admins,
  deactivate/reactivate), Memberships (org-centric add/remove/org-admin
  toggle across any org), and System status (PostgreSQL, Redis, Brain API,
  worker queue depth, API version) via `GET /api/admin/system`.
- **User deactivation.** New `user_profiles.is_active` column (migration
  004); deactivated accounts are rejected at auth time (403) on both the
  Clerk and E2E auth paths. Guards: self-demotion/self-deactivation → 400,
  removing the last active site admin → 409.
- **API.** New `/api/admin` router (`GET /users`, `PATCH /users/{id}`,
  `GET /users/{id}/memberships`, `GET /system`),
  `DELETE /api/memberships/{id}`, `BrainAPIClient.healthz()`.
- **UI.** Axios client now lets a per-request `X-Org-ID` header win over the
  ambient org from localStorage (required for cross-org administration).
- **Themes.** Selectable Light / Dark / **Red Arch** themes (palette + arch
  logo from the original v1 Knowledge Manager; see `ui/LOGO-LICENSE.md`).
  Theme picker in the header, persisted in localStorage, applied pre-paint
  (no flash), first visit follows the OS preference. Previously the UI was
  locked to the OS `prefers-color-scheme`.

### Fixed

- Org-switcher dropdown was clipped under the sidebar (right-anchored popover
  on a left-edge trigger inside an `overflow-hidden` column) — now anchored
  left and fully visible.
- Fresh sessions fired org-scoped requests without `X-Org-ID` (400s on
  Documents/Folders/Chat until an org was manually picked): the resolved
  initial org is now persisted to localStorage, which is what the API client
  reads.
- Clerk users whose session token carries no username/email claims (no JWT
  template configured) are now provisioned with sub-derived fallbacks instead
  of colliding on the empty-string unique constraint (500s for every user
  after the first).

### Security

- **RED-3 — RLS fail-closed hardening.** Tenant-isolation RLS policies now
  normalise the tenant GUC with `nullif(current_setting('app.current_tenant_id',
  true), '')` before the `::uuid` cast. On a pooled connection a set-then-reverted
  GUC reads back as the empty string `''`; the previous bare `''::uuid` cast raised
  `invalid input syntax for type uuid` on the next RLS query (fail-closed but a 500
  instead of an empty result). The empty string now normalises to NULL, so an
  unset/empty tenant deterministically returns zero rows and blocks all writes —
  fail-closed and error-free. Applied to both the Python (`api`, Alembic migration
  `002_harden_rls_nullif`) and Go (`api-go`, migration `003_harden_rls_nullif`)
  schemas across all 44 `tenant_isolation_*` policies. Added integration regression
  tests for the empty-string GUC and for privileged (BYPASSRLS) cross-tenant access.

### Changed — Authentication migrated from Keycloak to Clerk

End-user authentication moved from self-hosted **Keycloak** (OIDC) to **Clerk**
(cloud identity provider). The migration ran as a dual-verify coexistence window
(backends accepted a Keycloak *or* Clerk token, routed by the token `iss`) during
a soak period; **Slice 6 completes the cutover by removing Keycloak entirely**.
Clerk is now the sole identity provider.

- **RBAC/`access_mask` and multi-tenant RLS are unchanged** — identity is
  orthogonal to authorization; only the token verifier and IdP changed.
- **Service-to-service auth is unchanged** (`BRAIN_API_KEY` / `X-API-Key`,
  `INTERNAL_API_KEY` / `X-Internal-API-Key`).

#### Removed (Slice 6)

- `keycloak-js` dependency from the UI (`ui/package.json`).
- The Keycloak JWT verify path from the Go (`services/api-go`) and Python
  (`services/api`) backends, and the dual-verify (issuer-routing) branch — the
  backends now verify Clerk session tokens only.
- The `KEYCLOAK_URL` environment variable from `docker/docker-compose.go.yml`.
- **Environment variables removed** (delete these from any `.env`):
  `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `KEYCLOAK_CLIENT_ID`, and the UI's
  `NEXT_PUBLIC_KEYCLOAK_URL` / `NEXT_PUBLIC_KEYCLOAK_REALM` /
  `NEXT_PUBLIC_KEYCLOAK_CLIENT_ID`.

#### Clerk configuration (required)

- Backend: `CLERK_JWT_ISSUER` (Clerk Frontend API URL — the token `iss`),
  `CLERK_ALLOWED_AZP` (comma-separated allowlist of UI origins; **mandatory** —
  Clerk tokens carry no `aud`, so `azp` is the security-critical origin check),
  `CLERK_SECRET_KEY`.
- UI: `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `NEXT_PUBLIC_CLERK_SIGN_IN_URL=/login`,
  `NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up`, and a Clerk JWT template
  (`NEXT_PUBLIC_CLERK_JWT_TEMPLATE=redarch-km`) emitting `email`,
  `email_verified`, and `username`.

#### Rollback note

Rollback during the soak window was a config flip (point the UI back at Keycloak;
backends still verified the Keycloak `iss`). **After Slice 6, rollback requires
restoring the Keycloak verify path, `keycloak-js`, the `KEYCLOAK_*` env, and the
Keycloak service** — this cutover was performed only after the soak was clean and
the human authorized it.

> Note: the `user_profiles.keycloak_sub` **column** is intentionally retained in
> the Python stack under its original name (it now stores the Clerk subject); the
> rename to `auth_subject` is a separate, deferred database migration.

## [2.0.0] — 2026-06-14

First production release of the rebuilt Knowledge Management Platform. The rebuild
was delivered across eight phases. Each phase below lists its scope and the key
commits that delivered it.

### Phase 1 — Monorepo Scaffold & Foundations

- `uv`-managed Python monorepo with shared packages (`access_mask`, `brain_sdk`,
  `shared_config`) and three services (`api`, `brain_api`, `worker`).
- FastAPI application skeletons, SQLAlchemy async engine, Alembic migrations.
- Observability baseline: OpenTelemetry tracing, Prometheus metrics, structured
  JSON logging.
- Key commits: `7bf9b3f` (initial monorepo scaffold),
  `1be7575` (observability: OTel tracing, Prometheus metrics, JSON logging).

### Phase 2 — Core CRUD & Authentication

- JWT/OIDC authentication via Keycloak with mock-auth fallback for local dev.
- CRUD for Users, Orgs, Documents, Folders, and Tags.
- PostgreSQL Row-Level Security (RLS) for multi-tenant isolation
  (`app.current_tenant_id`).

### Phase 3 — Folder Hierarchy

- Folder tree with parent/child relationships and drag-and-drop reparenting.
- Cycle prevention and depth validation on folder moves.
- Key commit: `a42831e` (folders: hierarchy with drag-and-drop reparenting).

### Phase 4 — Brain API & Ingestion

- Brain API service for vector search, RAG, and graph context.
- `brain_sdk`: chunking, embedding, vector store (Qdrant) and graph store (Neo4j).
- Document ingestion pipeline with hierarchical summaries and triplet extraction.
- Key commit: `d036ab5` (ingest: gpt-5-mini, hierarchical summaries, parallel
  triplets).

### Phase 5 — Chat & RAG Pipeline

- Chat session CRUD with history persisted in `chat_data` JSONB.
- RAG endpoints (`/api/v1/ask`, `/api/v1/ask/stream`) with SSE streaming.
- API search proxy (`/api/search/chat`, `/api/search/chat/stream`) to Brain API.
- Citation generation and permission-scoped retrieval (32-bit `access_mask`).
- Implemented in Python using ideal native modules (FastAPI, SQLAlchemy, Pydantic,
  async `StreamingResponse`).

### Phase 6 — Admin & Membership Management

- Admin surfaces for tags, document attributes, and member/membership management.
- Org-deletion cascade propagated to Brain API resources.
- Key commits: `a173f48` (admin: tags, document attributes, memberships),
  `1dda4b4` (org-deletion cascade to brain-api + admin inline edit).

### Phase 7 — End-to-End & Security Testing

- Brain API integration tests, load tests, and seeded Playwright E2E journeys.
- RLS isolation tests, JWT/injection/RLS-bypass security validation.
- Multi-pass audit hardening (cascades, LIKE escaping, stream cancellation,
  input validation, pagination, async wrappers).
- 80%+ coverage target with CI enforcement.
- Key commits: `193aa0f` (testing: brain-api integration, load tests, E2E),
  `a88c1ba`, `65fa344`, `d14d98b`, `b406a21` (audit passes).

### Phase 8 — Deployment & Documentation

- Documentation suite: `ARCHITECTURE.md`, `DATABASE.md`, `RBAC.md`, `API.md`,
  `DEPLOYMENT.md`, `DEVELOPMENT.md`.
- Release files: `LICENSE` (Apache 2.0), `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md`,
  `CHANGELOG.md`.
- Bootstrap fixes: observability wiring, Qdrant/UI healthchecks, membership
  relationship loading.
- Code cleanup via `ruff check --fix` and `ruff format`.
- Key commits: `66281a5` (bootstrap: observability wiring, healthchecks),
  `dd93366` (memberships: load relationships before assigning).

[2.0.0]: https://github.com/redarchlabs/red-arch-km-2/releases/tag/v2.0.0
