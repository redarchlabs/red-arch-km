# Agent Org

A first-class, multi-tenant **agent organization** layered on KM2: arbitrary org
charts of AI agents that plan, delegate, research, and act on the org's own data,
governed by a `deny > ask > allow` authority engine that funnels every outbound
action to one human approval inbox. This doc is for engineers working on the agent
runtime, its tools, or the autonomous-company blueprint. Code lives under
`services/api/src/api/services/agents/`; everything is org-scoped (RLS + explicit
`org_id` in every repo), so one platform org = one company.

## Two different agents (don't conflate them)

KM2 has **two** independent agent systems. This document covers only the first.

| | **Agent org fleet** (this doc) | **Assistant-mode chat agent** |
|---|---|---|
| What | A persistent roster of governed agents (coordinators/advisory/operators) that run scheduled + delegated work | The single in-app AI assistant that helps a signed-in user build their workspace |
| Entry | `POST /api/agents/{agent_id}/console/stream`, worker sweeps | `POST /api/agent/chat/stream` (`routers/agent.py`) |
| Engine | `services/agents/runtime.py` (`run_agent_loop`), provider-agnostic via LiteLLM | `services/agent.py` (`AgentService`), OpenAI tool-calling in-process |
| Governance | Authority engine (kind-gate + grants + high-touch) | Acts with the **caller's** permissions; authoring tools gated to org admins inside the service (`AgentService._ADMIN_ONLY_TOOLS`) |
| Tools | records/docs/knowledge/workflows/web/batch + MCP + coordination | entity/form/view/workflow **authoring** + documents/folders + `generate_course` |

The assistant's authoring tool surface is documented in
[FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md) ("AI agent tools"); its `generate_course`
tool is documented in [LMS.md](LMS.md). It is **not** subject to the kind-gate,
grants, or high-touch autonomy described below.

## Table of Contents

- [1. Roster & governance kinds](#1-roster--governance-kinds)
- [2. Authority engine](#2-authority-engine)
- [3. Runtime & the two run paths](#3-runtime--the-two-run-paths)
- [4. LLM layer: providers, tiering, caching](#4-llm-layer-providers-tiering-caching)
- [5. Tools](#5-tools)
- [6. Scheduler](#6-scheduler)
- [7. MCP & the "Connect" OAuth flow](#7-mcp--the-connect-oauth-flow)
- [8. Delegation, work orders & approvals](#8-delegation-work-orders--approvals)
- [9. Provisioner & the autonomous company](#9-provisioner--the-autonomous-company)
- [10. Data model & API surface](#10-data-model--api-surface)
- [Key files](#key-files)
- [Cross-links](#cross-links)

## 1. Roster & governance kinds

Each agent carries a governance **kind** that hard-caps which tool *categories* it may
ever use (`services/agents/kind_gate.py`), independent of its per-agent grants:

| Kind | May use | Role |
|------|---------|------|
| **coordinator** | read, plan, delegate, escalate | Plans and routes work; must delegate all execution. A department head or apex. |
| **advisory** | read, escalate | Researches and recommends; can never mutate or take a side-effecting action. |
| **operator** | read, write, execute, delegate, escalate, plan | Carries out hands-on work (still subject to grants + approval). |

Agents form a tree via `supervisor_id`. Delegation is direct-report-only and escalation
bubbles up the chain, so a task flows *head → apex → human (org_admin)*.

Governance categories (`services/agents/tools/spec.py:Category`) are `read`, `write`,
`execute`, `delegate`, `escalate`, `plan`. **`plan` is defined and kind-gated but no
tool uses it today** — work orders are managed through the `/api/work-orders` REST
surface and the delegation tools, not an agent-facing `plan` tool.

## 2. Authority engine

`services/agents/authority.py:decide` resolves each requested tool call to
`ALLOW | ASK | DENY`. Precedence, highest first: **deny > ask > allow > (default deny)**.

1. **Kind-gate** (`kind_gate.py`) — a hard role restriction can `DENY` outright.
2. **Availability** — read tools (`always_allowed`) and role-provided coordination
   tools (`delegate`/`escalate`/`plan`) are always available; `write`/`execute` tools
   must be listed in the agent's `grants.tools` (write also needs `grants.records_write`).
   MCP tools may be granted by a server wildcard `mcp__<server>__*` (or the catch-all
   `mcp__*`), so a whole server can be pre-authorized before it is connected. Anything
   else is `DENY`.
3. **Approval** — an available tool named in `grants.approval_required` returns `ASK`.
4. **High-touch overlay** — `orgs.agent_autonomy` (`high_touch` | `balanced` |
   `hands_off`, default `high_touch`, migration `033_org_agent_autonomy`). Under
   **high-touch**, any tool with `side_effecting=True` is forced to `ASK` even if not
   listed per-agent, so a single human gates every side-effecting action while
   `side_effecting=False` tools stay `ALLOW`.

`side_effecting` is the flag that decides gating — it is set per `ToolSpec`, not inferred:

| `side_effecting=True` (ASK under high-touch) | `side_effecting=False` (runs free) |
|---|---|
| `run_workflow`, `run_claude_code`, connected external MCP tools, `delegate_task` | `create_record`, `update_record`, `create_document`, `web_research`, `batch_generate`, `check_batch`, `escalate`, `consult_peer`, `request_review`, read tools |

So internal record/document writes and read-only research run freely, while running a
workflow, calling an external MCP tool, or **spawning downstream work via
`delegate_task`** parks for approval. (`delegate_task` is deliberately side-effecting:
queuing an autonomous run for another agent is an action a human may want to gate.)

## 3. Runtime & the two run paths

The provider-agnostic loop `run_agent_loop` (`services/agents/runtime.py`) streams a
turn, then handles tools in **two phases**: it authority-gates **every** requested tool
call *before any executes*, so a run that parks on `ASK` has taken no partial side
effects. It then executes the plan and feeds results back, repeating until the model
stops calling tools or the iteration budget is exhausted. I/O is injected (`emit` sink
for SSE/step events; `approval_strategy` for what `ASK` does), so one loop serves both
entry points:

- **Interactive console** (`services/agents/console.py`) — runs **in-process on the API**
  and streams events over SSE (`POST /api/agents/{agent_id}/console/stream`). The human
  is present, so its approval strategy **auto-approves** `ASK` tools while still emitting
  the `approval_required` / `tool_call` frames the operator sees live.
- **Worker executor** (`services/agents/run_executor.py`) — the beat calls
  `POST /api/internal/agents/advance-runs`, which claims queued runs
  (`FOR UPDATE SKIP LOCKED`, cross-org) and drives each. Here an `ASK` records an
  `AgentApproval` and **parks** the run (`waiting`, via `RunParked`) until a human
  resolves it; the loop persists resume state (`messages` + `pending` tool calls) so the
  exact same turn continues after approval. A stale wait is re-bubbled by an escalation
  backstop.

## 4. LLM layer: providers, tiering, caching

Multi-provider via **LiteLLM**, which normalizes Anthropic / OpenAI / Gemini onto the
OpenAI chat + tool-calling shape (`services/agents/llm/provider.py:LLMProvider`). The
catalog (`llm/catalog.py`) maps a LiteLLM model id to its provider;
`llm/keys.py:resolve_provider_key` prefers the org's own encrypted key
(`org_provider_credentials`, migration `029`) and falls back to the central settings key
(`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `GEMINI_API_KEY`). Set a per-org key via
`POST /api/agents/providers/credentials`.

| Provider | Example models (`llm/catalog.py`) |
|----------|-----------------------------------|
| Anthropic | `anthropic/claude-opus-4-8`, `anthropic/claude-sonnet-5`, `anthropic/claude-haiku-4-5-20251001` |
| OpenAI | `gpt-5`, `gpt-5-mini`, `gpt-5-nano` |
| Google | `gemini/gemini-2.5-pro`, `gemini/gemini-2.5-flash` |

**Cost tiering** (applied by the provisioner) assigns models by role — reasoning where it
pays off, cheap execution everywhere else. The model is a column on the agent row, so
re-tiering is a DB update that takes effect on the next run with no code deploy.

| Tier | Model | Who |
|------|-------|-----|
| Apex | Opus | the coordinating hub (Chief of Staff) |
| Judgment | Sonnet | department heads (coordinators) + advisory analysts |
| Execution | Haiku | operators |

**Prompt caching** (Anthropic) is applied automatically inside the provider
(`llm/caching.py:with_cache_breakpoints`), per-call at request time so persisted resume
state stays plain strings. It marks `cache_control` breakpoints on the stable
tools+system prefix (via the system message) and on the growing conversation (last
message), so repeat turns within a run — and repeat runs within Anthropic's 5-minute
window — read that prefix at 10% of input price. It is a **no-op for non-Anthropic
models and below the model minimum** (a conservative 4,096-token threshold, since Haiku
4.5 / Opus 4.5+ require 4,096), so it mainly benefits Sonnet/Opus and tool-heavy
prompts. Cache read/write tokens surface on `Usage` (`cache_read_tokens` /
`cache_write_tokens`) and are logged.

## 5. Tools

Assembled per run by `services/agents/tools/loader.py:load_agent_tools`: the base set
(`tools/registry.py:base_tool_specs`) + coordination primitives
(`delegation.py:delegation_tool_specs`) + this agent's MCP tools
(`mcp/registry.py:build_mcp_tool_specs`), then filtered by the authority engine (listing
a tool never grants it).

| Group | Tools | Category / gating |
|-------|-------|-------------------|
| **Read** (always available) | `search_knowledge` (RAG over the org's docs), `list_records`, `get_record`, `list_workflows` | `READ`, `always_allowed` |
| **Write** (operator + `records_write`) | `create_record`, `update_record`, `create_document` (auto-ingested into RAG) | `WRITE`, `side_effecting=False` — internal, runs free |
| **Execute** (operator-only via kind-gate) | `run_workflow` (side-effecting), `web_research`, `batch_generate` / `check_batch`, connected MCP tools, opt-in `run_claude_code` | `EXECUTE`; grant-gated |
| **Coordination** (role-provided) | `delegate_task`, `escalate`, `consult_peer`, `request_review` | `DELEGATE` / `ESCALATE`; kind-gated |

The write tools reuse the exact validation + inline-workflow + ingest paths the
first-party UI uses (`tools/records.py`, `tools/documents.py`).

### Web research — `web_research` (`tools/web_research.py`)

Live-web research with citations via **Gemini + Google Search grounding** on the AI
Studio **free tier (1,500 grounding requests/day)** through the wired `GEMINI_API_KEY`
(or the org's own Gemini key). Because Gemini cannot mix Google Search with function
tools in one request, this is a dedicated **tool-less** call
(`LLMProvider.complete` with only the `googleSearch` tool) that returns
`{answer, sources, grounded}` (title + url), mirroring `search_knowledge` so citations
flow through the normal `tool_result` path and persist in `AgentRunStep`. `EXECUTE` +
**`side_effecting=False`** (read-only → runs free under high-touch), operator-only,
grant-gated; provisioned to research/content operators. On quota exhaustion it returns a
clear message rather than silently spending. Model is `AGENT_WEB_RESEARCH_MODEL`
(default `gemini/gemini-2.5-flash`). (Vertex is a future switchable backend — paid, no
free quota — not wired here.)

### Batch generation — `batch_generate` / `check_batch` (`tools/batch_generate.py`)

Single-shot text generation at the **50%-off async Batch tier** for latency-tolerant
work (bulk drafts, digests, descriptions). Uses the **Anthropic Message Batches API**
via the `anthropic` SDK directly (`LLMProvider.complete_batch` / `retrieve_batch` —
LiteLLM does not wrap it) on the calling agent's own Anthropic model, so a Haiku operator
batches on Haiku at half price. `batch_generate` submits a one-request batch and
bounded-polls (`AGENT_BATCH_POLL_INTERVAL_SECONDS`, default 10;
`AGENT_BATCH_MAX_WAIT_SECONDS`, default 180), returning `{status:"done", text}` or, if
still running at the cap, `{status:"processing", batch_id}` to fetch later with
`check_batch`. `EXECUTE` + `side_effecting=False`, operator-only, grant-gated. The
bounded poll holds the run's DB session open, so it is best used from
scheduled/background runs; a non-blocking submit + beat-driven retrieve is the
future-hardening path.

### Claude Code CLI dev/ops assistant (opt-in)

`run_claude_code` (`tools/claude_code.py`) lets **one** granted operator offload heavy
coding/ops work to the local **Claude Code CLI**, so the owner's Claude subscription
(Max plan) does the work while the KM2 agent orchestrates. It is deliberately
constrained:

- **Off by default** — registered only when `CLAUDE_CLI_TOOL_ENABLED=true`, and even then
  only *offered* to an agent that also holds the `run_claude_code` grant.
- `EXECUTE` + `side_effecting=True` → `ASK` under high-touch (console auto-approves with
  the human watching; the worker parks). Kind-gated to operators.
- Shells `claude -p --output-format json` via `create_subprocess_exec` (never a shell)
  inside an **allow-listed working directory** (`CLAUDE_CLI_WORKING_DIR`; traversal is
  refused), with a **read-only default** `--allowedTools`
  (`CLAUDE_CLI_ALLOWED_TOOLS`, default `Read,Grep,Glob,WebFetch`; widen to `Edit`/`Bash`
  only as a deliberate opt-in), a kill-on-timeout (`CLAUDE_CLI_TIMEOUT_SECONDS`, default
  300), and an explicit binary path (`CLAUDE_CLI_PATH`).
- **Strips `ANTHROPIC_API_KEY` from the child env** so the CLI authenticates with the
  subscription, not a central API key (which would bill the API and defeat the purpose).

Because it shells out on the host, it works only via the **console** (the worker runs in
a container with no CLI); the provisioned `dev-ops-assistant` agent is therefore
console-only (no schedule) and reports to the human, outside the business org chart.

> **Note (policy):** the fleet runs on an Anthropic **API key** — Anthropic's Consumer
> Terms bar scripted subscription access "except via an Anthropic API Key," and
> programmatic subscription use is metered separately. The CLI dev-agent is a single,
> human-driven, first-party-CLI exception for the owner's own work, and its
> subscription-vs-metered status can change with Anthropic's policy.

## 6. Scheduler

`agent_schedules` (cron + task per agent) is swept by
`services/agents/scheduler.py:run_due_schedules`, reusing the workflow engine's pure,
unit-tested `is_schedule_due`. Due schedules enqueue a `schedule`-triggered
`AgentRun(status="queued")`; the `advance-runs` sweep then drives it. Wired as internal
endpoint `POST /api/internal/agents/run-schedules` + celery-beat entry
`agents-run-schedules` (alongside `agents-advance-runs`). The sweep scans enabled
schedules cross-org under an RLS bypass, then re-scopes each fire to the schedule's own
`org_id`. **A running worker/beat is required** for schedules to fire — the endpoint
alone does nothing on a cadence.

## 7. MCP & the "Connect" OAuth flow

Agents reach external tools over the **Model Context Protocol**. Servers are registered
per org (`mcp_servers`, migration `030`; OAuth columns + `mcp_server_user_tokens` added
by migration `032_mcp_oauth`) and connected through an OAuth 2.1 (PKCE) browser flow
(`services/agents/mcp/oauth_service.py`, router `routers/mcp_servers.py` under
`/api/agents/mcp-servers`). A connection is either **org-scoped** (one shared install;
tokens on the `mcp_servers` row) or **user-scoped** (one token per user in
`mcp_server_user_tokens`); all secrets/tokens are Fernet-encrypted and a valid access
token is minted on demand. A connected server's id is added to an agent's
`mcp_server_ids`. A tool's `readOnlyHint` (or a server-level `read_only`) marks it
non-side-effecting, so read-only search (e.g. Perplexity) runs free under high-touch
while writes still ask.

This flow is co-owned with [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) (which
holds the full schema for migration `032`); this doc owns the agent-facing behavior.

## 8. Delegation, work orders & approvals

Coordinators delegate to direct reports via `delegate_task`, which spawns a child
`AgentRun` on a **work order** (`work_orders` + `work_order_tasks` /
`work_order_entries` / `work_order_artifacts`, migration `030`; REST surface
`/api/work-orders`). Blockers escalate up the chain via `escalate` (or `request_review`
/ `consult_peer`); unresolved work reaches the human `org_admin`. A delegated child
records its result back on the work order for its supervisor.

Every gated action lands in the org's **approvals inbox**
(`GET /api/agents/approvals`, approve/deny via
`POST /api/agents/approvals/{id}/{approve|deny}`, router `agent_approvals.py`) with a
preview. Notifications fan out via `services/agents/notify.py`: an out-of-band email
(when SMTP is configured) plus an optional org **notify workflow**
(`orgs.agent_notify_workflow_id`, migration `031_org_agent_notify_workflow`) for
Slack/Teams/SMS delivery. In-app notifications are served from
`GET /api/agents/notifications`.

## 9. Provisioner & the autonomous company

`scripts/provision_company.py` is the reusable **company blueprint**: a declarative
roster (department → head + team → kinds, grants, MCP pre-authorizations, model tier,
schedules) stood up idempotently via `AgentService` (matched by name; re-runs update in
place and wire the org chart). The reference deployment is a full traditional org
(Executive, Marketing, Sales, Product, Engineering, Customer Support, Finance, HR,
Operations, Legal, IT) — one apex Chief of Staff on Opus (with a weekday-morning
briefing schedule), department heads and advisory analysts on Sonnet, operators on
Haiku, plus the owner's console-only `dev-ops-assistant` (`run_claude_code` grant) —
all reporting to a single human `org_admin` under high-touch.

Run against the local dev DB:

```bash
DATABASE_URL=postgresql+asyncpg://…@localhost:5433/redarch_km \
  PYTHONPATH=services/api/src python -m scripts.provision_company [--dry-run]
```

## 10. Data model & API surface

Tables and the migrations that introduced them (all org-scoped under RLS):

| Table | Purpose | Migration |
|-------|---------|-----------|
| `agents` | Agent roster: kind, `supervisor_id`, `provider`/`model`, `grants`, `mcp_server_ids`, `enabled` | `030_agents_domain` |
| `agent_runs`, `agent_run_steps` | Run records + per-step tool/usage trace | `030` |
| `agent_schedules` | Cron + task per agent (`last_run_at` / `next_run_at`) | `030` |
| `agent_approvals` | Parked `ASK` verdicts awaiting a human | `030` |
| `agent_notifications` | In-app notification feed | `030` |
| `work_orders`, `work_order_tasks`, `work_order_entries`, `work_order_artifacts` | Delegated-work ledger | `030` |
| `mcp_servers` (+ OAuth columns), `mcp_server_user_tokens` | Connected MCP servers + per-user tokens | `030`, `032_mcp_oauth` |
| `orgs.agent_notify_workflow_id` | Optional Slack/Teams/SMS notify workflow | `031_org_agent_notify_workflow` |
| `orgs.agent_autonomy` | High-touch posture (`high_touch` default) | `033_org_agent_autonomy` |
| `org_provider_credentials` | Per-org encrypted LLM keys | `029` |

Router surface (all under the Clerk-authenticated first-party API):

| Router | Prefix | Covers |
|--------|--------|--------|
| `agents.py` | `/api/agents` | Agent CRUD, providers list, per-org provider credentials |
| `agent_console.py` | `/api/agents` | Console SSE stream, run + step listings |
| `agent_approvals.py` | `/api/agents` | Approvals inbox + notifications |
| `mcp_servers.py` | `/api/agents/mcp-servers` | MCP server CRUD, presets, OAuth start/callback/disconnect, test |
| `work_orders.py` | `/api/work-orders` | Work-order CRUD, status/assignment, tasks |
| `internal.py` | `/api/internal/agents` | `advance-runs`, `run-schedules` beat sweeps |
| `agent.py` | `/api/agent` | The separate **assistant-mode** chat agent (see top) |

## Key files

| Area | Path |
|------|------|
| Authority + kind-gate | `services/agents/authority.py`, `kind_gate.py` |
| Runtime loop | `services/agents/runtime.py` |
| Run paths | `services/agents/console.py` (interactive), `run_executor.py` (worker) |
| Scheduler | `services/agents/scheduler.py` |
| LLM layer | `services/agents/llm/{provider,catalog,keys,caching}.py` |
| Tools | `services/agents/tools/{spec,registry,loader,records,documents,knowledge,workflows,web_research,batch_generate,claude_code}.py` |
| Coordination | `services/agents/delegation.py` |
| MCP | `services/agents/mcp/` |
| Approvals / notify | `services/agents/approvals.py`, `notify.py` |
| Blueprint/provisioner | `scripts/provision_company.py` |
| Routers | `routers/{agents,agent_console,agent_approvals,mcp_servers,work_orders,internal}.py` |
| Assistant agent (separate) | `services/agent.py`, `routers/agent.py` |

## Cross-links

- [MCP_AND_INTEGRATIONS.md](MCP_AND_INTEGRATIONS.md) — the "Connect" OAuth flow schema and
  the `km2-mcp` developer tool.
- [RBAC.md](RBAC.md) — org roles, membership, and the RLS model the fleet runs under.
- [ARCHITECTURE.md](ARCHITECTURE.md) — where the agents domain sits across the services.
- [FORMS_AND_VIEWS.md](FORMS_AND_VIEWS.md) — the assistant-mode agent's authoring tools.
- [LMS.md](LMS.md) — the assistant's `generate_course` tool.
- [WORKFLOW_ENGINE.md](WORKFLOW_ENGINE.md) — the workflow engine `run_workflow` drives and
  the shared cron/`is_schedule_due` machinery.
- [README](../README.md) — docs index and project overview.

> Reviewed 2026-07-16 against Alembic migration 039. Source of truth is the code; if this doc disagrees with the code, the code wins.
