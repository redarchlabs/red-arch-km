# Agent Org

A first-class, multi-tenant **agent organization** layered on the platform: arbitrary
org charts of AI agents that plan, delegate, research, and act on the org's own data —
governed by a `deny > ask > allow` authority engine that funnels every outbound action
to a single human approval inbox. It is the substrate for the **autonomous company**
blueprint (an entire traditional org staffed by agents, run by one human).

Code lives under `services/api/src/api/services/agents/`. Everything is org-scoped
(RLS + explicit `org_id` in every repo), so one platform org = one company.

---

## 1. Roster & governance kinds

Each agent has a governance **kind** that hard-caps which tool *categories* it may ever
use (`services/agents/kind_gate.py`), independent of its grants:

| Kind | May use | Role |
|------|---------|------|
| **coordinator** | read, plan, delegate, escalate | Plans and routes work; must delegate all execution. A department head or apex. |
| **advisory** | read, escalate | Researches and recommends; can never mutate or take a side-effecting action. |
| **operator** | read, write, execute, delegate, escalate, plan | Carries out hands-on work (still subject to grants + approval). |

Agents form a tree via `supervisor_id`. Delegation is direct-report-only and escalation
bubbles up the chain, so a task flows *head → apex → human (org_admin)*.

---

## 2. Authority engine (`services/agents/authority.py`)

Precedence, highest first: **deny > ask > allow > (default deny)**.

1. **Kind-gate** — a hard role restriction can `DENY` outright.
2. **Availability** — read tools (`always_allowed`) and role-provided coordination
   tools are always available; `write`/`execute` tools must be listed in the agent's
   `grants.tools` (write also needs the `records_write` flag). MCP tools may be granted
   by a server wildcard `mcp__<server>__*` (or the catch-all `mcp__*`), so a whole
   server can be pre-authorized before it is connected. Anything else is `DENY`.
3. **Approval** — an available tool in `grants.approval_required` returns `ASK`.
4. **High-touch overlay** — `orgs.agent_autonomy` (`high_touch` | `balanced` |
   `hands_off`, default `high_touch`, migration 033) gates side-effecting tools. Under
   **high-touch**, any `side_effecting=True` tool is forced to `ASK` even if not listed
   per-agent — so a single human gates every *outbound* action (email, Slack, publish,
   external MCP calls) while *internal* writes (record/document tools, `side_effecting=False`)
   stay `ALLOW`. This is why filing an issue or saving research runs freely but sending
   an email parks for approval.

Tool categories (`services/agents/tools/spec.py`): `READ`, `WRITE`, `EXECUTE`,
`DELEGATE`, `ESCALATE`, `PLAN`.

---

## 3. Runtime & the two run paths

The provider-agnostic loop `run_agent_loop` (`services/agents/runtime.py`) streams a
turn, authority-gates **every** requested tool call *before any executes* (so a run that
parks on `ASK` has taken no partial side effects), then executes and feeds results back.
Two entry points share it:

- **Interactive console** (`services/agents/console.py`) — runs **in-process on the API**
  and streams events over SSE. The human is present, so its approval strategy
  (`_approve_inline`) **auto-approves** `ASK` tools while still emitting the
  `approval_required`/`tool_call` frames the operator sees live.
- **Worker executor** (`services/agents/run_executor.py`) — the beat calls
  `/api/internal/agents/advance-runs`, which claims queued runs
  (`FOR UPDATE SKIP LOCKED`, cross-org) and drives each. Here an `ASK` records an
  `AgentApproval` and **parks** the run (`waiting`, via `RunParked`) until a human
  resolves it; a stale wait is re-bubbled by an escalation backstop.

---

## 4. Providers & model tiering

Multi-provider via **LiteLLM**. The catalog (`services/agents/llm/catalog.py`) maps a
LiteLLM model id to its provider; `resolve_provider_key` uses the org's own encrypted
key first, else the central settings key (`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` /
`GEMINI_API_KEY`). Set a per-org key via `POST /api/agents/providers/credentials`.

| Provider | Example models |
|----------|----------------|
| Anthropic | `anthropic/claude-opus-4-8`, `anthropic/claude-sonnet-5`, `anthropic/claude-haiku-4-5-20251001` |
| OpenAI | `gpt-5`, `gpt-5-mini`, `gpt-5-nano` |
| Google | `gemini/gemini-2.5-pro`, `gemini/gemini-2.5-flash` |

**Cost tiering** (applied by the provisioner) assigns models by role — reasoning where it
pays off, cheap execution everywhere else:

| Tier | Model | Who |
|------|-------|-----|
| Apex | Opus | the coordinating hub (Chief of Staff) |
| Judgment | Sonnet | department heads (coordinators) + advisory analysts |
| Execution | Haiku | operators |

The model is a column on the agent row, so re-tiering is a DB update and takes effect on
the next run with no code deploy.

---

## 5. Tools

Assembled per run by `services/agents/tools/loader.py`: the base set
(`tools/registry.py:base_tool_specs`) + coordination primitives + this agent's MCP tools,
then filtered by the authority engine (listing a tool never grants it).

- **Read** (always available): `search_knowledge` (RAG over the org's docs),
  `list_records`, `get_record`, `list_workflows`.
- **Write** (operator + `records_write`, internal / not side-effecting):
  `create_record`, `update_record`, `create_document` (auto-ingested into RAG). These
  reuse the exact validation + inline-workflow + ingest paths the first-party UI uses.
- **Execute** (operator, side-effecting → gated under high-touch): `run_workflow`, and
  any connected **MCP** tool.
- **Coordination** (role-provided): delegate, escalate, work-order/plan tools.

### Claude Code CLI dev/ops assistant (opt-in)

`run_claude_code` (`services/agents/tools/claude_code.py`) lets **one** granted operator
offload heavy coding/ops work to the local **Claude Code CLI**, so the owner's Claude
subscription (Max plan) does the work while the KM2 agent orchestrates. It is deliberately
constrained:

- **Off by default** — registered only when `CLAUDE_CLI_TOOL_ENABLED=true`, and even then
  only *offered* to an agent that also holds the `run_claude_code` grant.
- `EXECUTE` + `side_effecting` → `ASK` under high-touch (console auto-approves with the
  human watching; the worker parks). Kind-gated to operators.
- Shells `claude -p --output-format json` via `create_subprocess_exec` (never a shell)
  inside an **allow-listed working directory** (`CLAUDE_CLI_WORKING_DIR`; traversal is
  refused), with a **read-only default** `--allowedTools` (`CLAUDE_CLI_ALLOWED_TOOLS`,
  widen to `Edit`/`Bash` only as a deliberate opt-in), a kill-on-timeout
  (`CLAUDE_CLI_TIMEOUT_SECONDS`), and an explicit binary path (`CLAUDE_CLI_PATH`).
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

---

## 6. Scheduler

`agent_schedules` (cron + task per agent) is swept by
`services/agents/scheduler.py:run_due_schedules`, reusing the workflow engine's
`is_schedule_due`. Due schedules enqueue a `schedule`-triggered `AgentRun(status="queued")`;
the `advance-runs` sweep then drives it. Wired as internal endpoint
`POST /api/internal/agents/run-schedules` + celery-beat entry `agents-run-schedules`
(alongside `agents-advance-runs`). **A running worker/beat is required** for schedules to
fire — the endpoint alone does nothing on a cadence.

---

## 7. MCP & the "Connect" OAuth flow

Agents reach external tools over the **Model Context Protocol**. Servers are connected
per-org or per-user via an OAuth "Connect" flow (`services/agents/mcp/`), and a server's
id is added to an agent's `mcp_server_ids`. A tool's `readOnlyHint` (or a server-level
`read_only`) marks it non-side-effecting, so read-only search (e.g. Perplexity) runs free
under high-touch while writes still ask.

---

## 8. Delegation, work orders & approvals

Coordinators delegate to direct reports (spawning child runs on **work orders**);
unresolved work escalates up to the human. Every gated action lands in the org's
**approvals inbox** (`/api/agents/approvals`, `agent_approvals` router) with a preview;
notifications fan out via `services/agents/notify.py`. A delegated child records its
result back on the work order for its supervisor.

---

## 9. Provisioner & the autonomous company

`scripts/provision_company.py` is the reusable **company blueprint**: a declarative roster
(department → head + team → kinds, grants, MCP pre-authorizations, model tier, schedules)
stood up idempotently via the `AgentService` (matched by name; re-runs update in place and
wire the org chart). The reference deployment is a full traditional org (Executive,
Marketing, Sales, Product, Engineering, Customer Support, Finance, HR, Operations, Legal,
IT) — one apex Chief of Staff, department heads, advisory analysts, and operators, plus the
owner's `dev-ops-assistant` — all reporting to a single human `org_admin` under high-touch.

Run against the local dev DB:

```bash
DATABASE_URL=postgresql+asyncpg://…@localhost:5433/redarch_km \
  PYTHONPATH=services/api/src python -m scripts.provision_company [--dry-run]
```

---

## Key files

| Area | Path |
|------|------|
| Authority + kind-gate | `services/agents/authority.py`, `kind_gate.py` |
| Runtime loop | `services/agents/runtime.py` |
| Run paths | `services/agents/console.py` (interactive), `run_executor.py` (worker) |
| Scheduler | `services/agents/scheduler.py` |
| Providers/keys | `services/agents/llm/catalog.py`, `llm/keys.py` |
| Tools | `services/agents/tools/{spec,registry,loader,records,documents,knowledge,workflows,claude_code}.py` |
| MCP | `services/agents/mcp/` |
| Routers | `routers/{agents,agent_console,agent_approvals,mcp_servers}.py`, `routers/internal.py` |
| Blueprint/provisioner | `scripts/provision_company.py` |
